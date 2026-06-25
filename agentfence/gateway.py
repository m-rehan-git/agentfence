"""
Gateway - Production FastAPI application.

Sits between AI agents and tool execution. Provides:
  - POST /v1/execute    : Execute a tool call with budget enforcement and tracing.
  - GET  /v1/tasks      : List all tasks with summary info.
  - GET  /v1/tasks/{id}/trace       : Get full trace for a task.
  - GET  /v1/tasks/{id}/budget      : Get budget state.
  - GET  -/v1/tasks/{id}/replay/*   : Trace replay controls.
  - GET  /health         : Health check with dependency status.

Features:
  - Structured logging with request IDs
  - Global exception handling
  - Lifespan context manager for startup/shutdown
  - CORS middleware
  - Request timing middleware
  - Graceful shutdown
  - Configurable via environment variables
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from agentfence.config import get_config
from agentfence.budget_enforcer import BudgetEnforcer
from agentfence.cost_engine import calculate_actual_cost, count_tokens, estimate_cost
from agentfence.models import (
    CircuitBreakerException,
    ToolRequest,
    ToolResponse,
    TraceStep,
)
from agentfence.replay import ReplayEngine
from agentfence.tracer import Tracer
from agentfence.security import (
    ToolSandbox,
    RateLimiter,
    AuditLogger,
    InputValidator,
    SecurityEventType,
    RiskLevel,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application state (initialized in lifespan)
# ---------------------------------------------------------------------------
_budget_enforcer: Optional[BudgetEnforcer] = None
_tracer: Optional[Tracer] = None
_sandbox: Optional[ToolSandbox] = None
_rate_limiter: Optional[RateLimiter] = None
_audit: Optional[AuditLogger] = None
_startup_time: float = 0.0


# ---------------------------------------------------------------------------
# Lifespan context manager
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: initialize resources on startup, cleanup on shutdown."""
    global _budget_enforcer, _tracer, _sandbox, _rate_limiter, _audit, _startup_time

    cfg = get_config()
    _startup_time = time.time()

    logger.info(
        "AgentFence starting up",
        extra={
            "version": "0.2.0",
            "mock_mode": cfg.is_mock_mode,
            "database": cfg.database_url,
        },
    )

    try:
        _budget_enforcer = BudgetEnforcer()
        _tracer = Tracer()
        _sandbox = ToolSandbox()
        _rate_limiter = RateLimiter(
            requests_per_minute=cfg.gateway.rate_limit_rpm
            if cfg.gateway.rate_limit_enabled else 10_000,
            burst_size=cfg.gateway.rate_limit_rpm,
        )
        _audit = AuditLogger()
        _audit.log(
            SecurityEventType.SYSTEM_STARTUP,
            details={"version": "0.2.0", "mock_mode": cfg.is_mock_mode},
        )
        logger.info("AgentFence startup complete")
    except Exception as e:
        logger.error("AgentFence startup failed", extra={"error": str(e)})
        raise

    yield

    logger.info("AgentFence shutting down")
    if _audit:
        _audit.log(SecurityEventType.SYSTEM_SHUTDOWN)
    if _budget_enforcer:
        _budget_enforcer.close()
    if _tracer:
        _tracer.close()
    logger.info("AgentFence shutdown complete")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(
    title="AgentFence",
    version="0.2.0",
    description="AI agent infrastructure: cost control, execution monitoring, failure replay.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Middleware: request logging and timing
# ---------------------------------------------------------------------------
@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    """Log every request with timing and request ID."""
    request_id = str(uuid.uuid4())[:8]
    start = time.time()

    logger.info(
        "Request started",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
        },
    )

    try:
        response = await call_next(request)
        duration_ms = round((time.time() - start) * 1000, 2)

        logger.info(
            "Request completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Duration-Ms"] = str(duration_ms)
        return response
    except Exception as e:
        duration_ms = round((time.time() - start) * 1000, 2)
        logger.error(
            "Request failed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "error": str(e),
                "duration_ms": duration_ms},
        )
        raise


# ---------------------------------------------------------------------------
# Global exception handler
# ---------------------------------------------------------------------------
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Return JSON error responses for all unhandled exceptions."""
    logger.error(
        "Unhandled exception",
        extra={"path": request.url.path, "error": str(exc)},
    )
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "message": "Internal server error",
            "detail": str(exc),
        },
    )


@app.exception_handler(CircuitBreakerException)
async def circuit_breaker_handler(request: Request, exc: CircuitBreakerException):
    """Handle circuit breaker exceptions."""
    logger.warning(
        "Circuit breaker tripped",
        extra={"task_id": exc.task_id, "remaining": exc.remaining},
    )
    return JSONResponse(
        status_code=429,
        content={
            "status": "circuit_breaker",
            "message": str(exc),
            "task_id": exc.task_id,
            "remaining": exc.remaining,
        },
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _truncate(text: str, max_len: int = 500) -> str:
    """Truncate a string for preview display."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _extract_output_text(response_data: dict[str, Any]) -> str:
    """Extract the output text from a provider API response."""
    try:
        choices = response_data.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            content = message.get("content", "")
            if content:
                return str(content)
        return str(response_data)
    except Exception:
        return str(response_data)


def _extract_token_usage(response_data: dict[str, Any]) -> tuple[int, int]:
    """Extract input and output token counts from a provider response."""
    try:
        usage = response_data.get("usage", {})
        return usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
    except Exception:
        return 0, 0


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
async def health_check() -> dict:
    """
    Health check endpoint.

    Returns status of all dependencies.
    """
    cfg = get_config()
    uptime_seconds = round(time.time() - _startup_time, 2) if _startup_time else 0

    budget_health = _budget_enforcer.health_check() if _budget_enforcer else {"status": "not_initialized"}
    tracer_health = _tracer.health_check() if _tracer else {"status": "not_initialized"}
    audit_health = _audit.get_summary() if _audit else {"status": "not_initialized"}

    overall = "healthy"
    if budget_health.get("status") != "healthy" or tracer_health.get("status") != "healthy":
        overall = "degraded"

    return {
        "status": overall,
        "version": "0.2.0",
        "uptime_seconds": uptime_seconds,
        "mock_mode": cfg.is_mock_mode,
        "budget_enforcer": budget_health,
        "tracer": tracer_health,
        "audit": audit_health,
        "sandbox": {
            "policies_loaded": len(_sandbox.list_policies()) if _sandbox else 0,
        },
    }


@app.post("/v1/execute", response_model=ToolResponse)
async def execute_tool(request: ToolRequest) -> ToolResponse:
    """
    Execute a tool call through AgentFence.

    Flow:
    1. Validate request (Pydantic).
    2. Security: Sandbox check (tool policy, params, input).
    3. Security: Rate limit check.
    4. Security: Input validation (prompt injection).
    5. Ensure task has a budget (auto-init with default).
    6. Estimate cost.
    7. Reserve budget.
    8. If budget exceeded → return immediately.
    9. Forward to provider (or return mock response).
    10. Settle actual cost.
    11. Log trace.
    12. Audit log.
    13. Return response.
    """
    global _budget_enforcer, _tracer, _sandbox, _rate_limiter, _audit

    if _budget_enforcer is None or _tracer is None:
        raise HTTPException(status_code=503, detail="Service not initialized")

    cfg = get_config()
    step_id = str(uuid.uuid4())
    start_time = time.monotonic()
    agent_id = request.params.get("_agent_id", "default")

    # Step 2: Security — Sandbox check
    if _sandbox:
        sandbox_result = _sandbox.check(
            tool_name=request.tool_name,
            params=request.params,
            input_text=request.estimated_input,
        )
        if not sandbox_result:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            if _audit:
                _audit.log(
                    SecurityEventType.TOOL_CALL_BLOCKED,
                    agent_id=agent_id,
                    task_id=request.task_id,
                    details={
                        "tool": request.tool_name,
                        "reason": sandbox_result.reason,
                    },
                    risk_level=sandbox_result.risk_level,
                )
            trace_step = TraceStep(
                step_id=step_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                tool_name=request.tool_name,
                model=request.model,
                input_preview=_truncate(request.estimated_input),
                output_preview="",
                input_tokens=0,
                output_tokens=0,
                cost_usd=0.0,
                latency_ms=round(elapsed_ms, 2),
                status="blocked",
                error=f"Security: {sandbox_result.reason}",
            )
            _tracer.log_step(request.task_id, trace_step)
            return ToolResponse(
                status="error",
                output=None,
                cost_usd=0.0,
                execution_time_ms=round(elapsed_ms, 2),
                trace_step_id=step_id,
            )

    # Step 3: Security — Rate limit
    if _rate_limiter and cfg.gateway.rate_limit_enabled:
        if not _rate_limiter.acquire(agent_id):
            elapsed_ms = (time.monotonic() - start_time) * 1000
            if _audit:
                _audit.log(
                    SecurityEventType.TOOL_CALL_RATE_LIMITED,
                    agent_id=agent_id,
                    task_id=request.task_id,
                    details={"tool": request.tool_name},
                    risk_level=RiskLevel.MEDIUM,
                )
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded for agent '{agent_id}'. Try again later.",
            )

    # Step 4: Security — Input validation (prompt injection)
    if _sandbox:
        injection_matches = InputValidator.check_prompt_injection(request.estimated_input)
        if injection_matches:
            if _audit:
                _audit.log(
                    SecurityEventType.INPUT_VALIDATION_FAILED,
                    agent_id=agent_id,
                    task_id=request.task_id,
                    details={
                        "tool": request.tool_name,
                        "injection_patterns_found": len(injection_matches),
                    },
                    risk_level=RiskLevel.HIGH,
                )
            # Log but don't block — just warn
            logger.warning(
                "Potential prompt injection detected",
                extra={
                    "task_id": request.task_id,
                    "agent_id": agent_id,
                    "patterns": len(injection_matches),
                },
            )

    # Step 5: Ensure task budget exists
    existing_budget = _budget_enforcer.get_budget_config(request.task_id)
    if existing_budget is None:
        _budget_enforcer.init_task(request.task_id, cfg.default_budget_usd)

    # Step 6: Estimate cost
    estimated_cost = estimate_cost(
        model=request.model,
        input_text=request.estimated_input,
        expected_output_tokens=request.max_output_tokens,
    )

    # Step 7: Reserve budget
    can_execute = _budget_enforcer.check_and_reserve(request.task_id, estimated_cost)

    if not can_execute:
        elapsed_ms = (time.monotonic() - start_time) * 1000
        if _audit:
            _audit.log(
                SecurityEventType.BUDGET_VIOLATION,
                agent_id=agent_id,
                task_id=request.task_id,
                details={"estimated_cost": estimated_cost},
                risk_level=RiskLevel.MEDIUM,
            )
        trace_step = TraceStep(
            step_id=step_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            tool_name=request.tool_name,
            model=request.model,
            input_preview=_truncate(request.estimated_input),
            output_preview="",
            input_tokens=count_tokens(request.estimated_input, request.model),
            output_tokens=0,
            cost_usd=0.0,
            latency_ms=round(elapsed_ms, 2),
            status="budget_exceeded",
            error="Budget exceeded: insufficient funds for estimated cost.",
        )
        _tracer.log_step(request.task_id, trace_step)
        return ToolResponse(
            status="budget_exceeded",
            output=None,
            cost_usd=0.0,
            execution_time_ms=round(elapsed_ms, 2),
            trace_step_id=step_id,
        )

    # Step 9: Forward to provider (or mock)
    output_text = ""
    input_tokens = 0
    output_tokens = 0
    actual_cost = 0.0
    status = "success"
    error_msg = None

    try:
        if cfg.is_mock_mode:
            mock_output = (
                f"[AgentFence Mock] Tool '{request.tool_name}' "
                f"called with model '{request.model}'. "
                f"Set AF_MOCK_MODE=false and provide AF_API_KEY to use a real provider."
            )
            output_text = mock_output
            input_tokens = count_tokens(request.estimated_input, request.model)
            output_tokens = count_tokens(output_text, request.model)
        elif request.tool_name in ("openai.chat", "llm.chat", "chat", "openrouter.chat"):
            payload = {
                "model": request.model,
                "messages": request.params.get("messages", []),
                "max_tokens": request.params.get("max_tokens", request.max_output_tokens),
            }
            for key, value in request.params.items():
                if key not in payload and not key.startswith("_"):
                    payload[key] = value

            headers = {"Content-Type": "application/json"}
            if cfg.api_key:
                headers["Authorization"] = f"Bearer {cfg.api_key}"

            timeout = cfg.request_timeout_seconds
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{cfg.provider_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                response_data = response.json()
                output_text = _extract_output_text(response_data)
                input_tokens, output_tokens = _extract_token_usage(response_data)
        else:
            output_text = (
                f"[AgentFence Mock] Tool '{request.tool_name}' "
                f"executed with params: {request.params}"
            )
            input_tokens = count_tokens(request.estimated_input, request.model)
            output_tokens = count_tokens(output_text, request.model)

        # Step 10: Calculate actual cost and settle
        actual_cost = calculate_actual_cost(request.model, input_tokens, output_tokens)

        try:
            remaining = _budget_enforcer.settle_actual(request.task_id, step_id, actual_cost)
            if remaining <= 0:
                status = "circuit_breaker"
        except CircuitBreakerException as cb:
            status = "circuit_breaker"
            error_msg = str(cb)

    except httpx.HTTPStatusError as e:
        status = "error"
        error_msg = f"Provider HTTP error {e.response.status_code}: {e.response.text[:500]}"
        output_text = error_msg
        try:
            _budget_enforcer.settle_actual(request.task_id, step_id, 0.0)
        except CircuitBreakerException:
            status = "circuit_breaker"
    except httpx.RequestError as e:
        status = "error"
        error_msg = f"Provider request failed: {str(e)}"
        output_text = error_msg
        try:
            _budget_enforcer.settle_actual(request.task_id, step_id, 0.0)
        except CircuitBreakerException:
            status = "circuit_breaker"
    except Exception as e:
        status = "error"
        error_msg = f"Unexpected error: {str(e)}"
        output_text = error_msg
        logger.error("Execute failed", extra={"task_id": request.task_id, "error": str(e)})
        try:
            _budget_enforcer.settle_actual(request.task_id, step_id, 0.0)
        except CircuitBreakerException:
            status = "circuit_breaker"

    # Step 11: Log trace
    elapsed_ms = (time.monotonic() - start_time) * 1000
    trace_step = TraceStep(
        step_id=step_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        tool_name=request.tool_name,
        model=request.model,
        input_preview=_truncate(request.estimated_input),
        output_preview=_truncate(output_text),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=actual_cost,
        latency_ms=round(elapsed_ms, 2),
        status=status,
        error=error_msg,
    )
    _tracer.log_step(request.task_id, trace_step)

    # Step 12: Audit log
    if _audit:
        if status == "success":
            _audit.log(
                SecurityEventType.TOOL_CALL_ALLOWED,
                agent_id=agent_id,
                task_id=request.task_id,
                details={
                    "tool": request.tool_name,
                    "cost_usd": actual_cost,
                    "latency_ms": round(elapsed_ms, 2),
                },
                risk_level=RiskLevel.LOW,
            )
        elif status == "error":
            _audit.log(
                SecurityEventType.SANDBOX_VIOLATION,
                agent_id=agent_id,
                task_id=request.task_id,
                details={
                    "tool": request.tool_name,
                    "error": error_msg,
                },
                risk_level=RiskLevel.MEDIUM,
            )

    # Step 13: Return response
    return ToolResponse(
        status=status,
        output=output_text,
        cost_usd=actual_cost,
        execution_time_ms=round(elapsed_ms, 2),
        trace_step_id=step_id,
    )


@app.get("/v1/tasks")
async def list_tasks() -> list[dict]:
    """List all tasks with summary statistics."""
    return _tracer.get_recent_tasks(limit=100)


@app.get("/v1/tasks/{task_id}/trace")
async def get_task_trace(task_id: str) -> list[dict]:
    """Get the full execution trace for a task."""
    steps = _tracer.get_trace(task_id)
    if not steps:
        raise HTTPException(status_code=404, detail=f"No trace found for task '{task_id}'.")
    return [step.model_dump() for step in steps]


@app.get("/v1/tasks/{task_id}/budget")
async def get_task_budget(task_id: str) -> dict:
    """Get the current budget state for a task."""
    config = _budget_enforcer.get_budget_config(task_id)
    if config is None:
        raise HTTPException(status_code=404, detail=f"No budget found for task '{task_id}'.")
    return config.model_dump()


@app.get("/v1/tasks/{task_id}/replay/state")
async def get_replay_state(task_id: str) -> dict:
    """Get the current replay state for a task."""
    engine = ReplayEngine(tracer=_tracer)
    try:
        engine.load_trace(task_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No trace found for task '{task_id}'.")
    return engine.get_state()


@app.post("/v1/tasks/{task_id}/replay/next")
async def replay_next(task_id: str) -> dict:
    """Advance the replay cursor by one step."""
    engine = ReplayEngine(tracer=_tracer)
    try:
        engine.load_trace(task_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No trace found for task '{task_id}'.")
    step = engine.next_step()
    state = engine.get_state()
    return {"state": state, "step": step.model_dump() if step else None}


@app.post("/v1/tasks/{task_id}/replay/prev")
async def replay_prev(task_id: str) -> dict:
    """Rewind the replay cursor by one step."""
    engine = ReplayEngine(tracer=_tracer)
    try:
        engine.load_trace(task_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No trace found for task '{task_id}'.")
    step = engine.prev_step()
    state = engine.get_state()
    return {"state": state, "step": step.model_dump() if step else None}


# ---------------------------------------------------------------------------
# Security Endpoints
# ---------------------------------------------------------------------------

@app.get("/v1/security/audit")
async def get_audit_log(
    agent_id: Optional[str] = None,
    risk_level: Optional[str] = None,
    limit: int = 50,
) -> dict:
    """
    Query the security audit log.

    Query params:
        agent_id: Filter by agent ID.
        risk_level: Filter by risk level (low, medium, high, critical).
        limit: Max events to return (default 50, max 500).
    """
    if _audit is None:
        raise HTTPException(status_code=503, detail="Audit system not initialized")

    from agentfence.security import RiskLevel

    risk = None
    if risk_level:
        try:
            risk = RiskLevel(risk_level.lower())
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid risk level: {risk_level}")

    events = _audit.get_events(agent_id=agent_id, risk_level=risk, limit=min(limit, 500))
    return {"events": events, "count": len(events)}


@app.get("/v1/security/audit/summary")
async def get_audit_summary() -> dict:
    """Get a summary of security audit events."""
    if _audit is None:
        raise HTTPException(status_code=503, detail="Audit system not initialized")
    return _audit.get_summary()


@app.post("/v1/security/audit/verify")
async def verify_audit_chain() -> dict:
    """
    Verify the integrity of the audit log hash chain.

    Returns whether the chain is intact and any tampering detected.
    """
    if _audit is None:
        raise HTTPException(status_code=503, detail="Audit system not initialized")
    is_valid, errors = _audit.verify_chain()
    return {
        "chain_intact": is_valid,
        "errors": errors,
        "message": "Audit chain is intact" if is_valid else "AUDIT CHAIN COMPROMISED",
    }


@app.get("/v1/security/sandbox/policies")
async def list_sandbox_policies() -> dict:
    """List all tool sandbox policies."""
    if _sandbox is None:
        raise HTTPException(status_code=503, detail="Sandbox not initialized")
    policies = _sandbox.list_policies()
    return {
        "policies": {
            name: {
                "allowed": p.allowed,
                "max_input_length": p.max_input_length,
                "max_output_tokens": p.max_output_tokens,
                "require_budget": p.require_budget,
                "risk_level": p.risk_level.value,
                "blocked_params": list(p.blocked_params) if p.blocked_params else [],
            }
            for name, p in policies.items()
        },
        "count": len(policies),
    }


@app.get("/v1/security/rate-limits/{agent_id}")
async def get_rate_limit_status(agent_id: str) -> dict:
    """Get rate limit status for an agent."""
    if _rate_limiter is None:
        raise HTTPException(status_code=503, detail="Rate limiter not initialized")
    return {"agent_id": agent_id, "remaining_tokens": _rate_limiter.get_remaining(agent_id)}
