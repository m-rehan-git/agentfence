"""
Pydantic v2 models for AgentFence.

All data flowing through the system is validated by these schemas:
- ToolRequest  : What the agent wants to execute
- ToolResponse : What AgentFence returned after execution
- TraceStep    : A single logged step in an execution trace
- BudgetConfig : Current state of a task's budget
- TaskStatus   : Enumeration of task lifecycle states
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator


# ---------------------------------------------------------------------------
# TaskStatus enum
# ---------------------------------------------------------------------------

class TaskStatus(str, Enum):
    """Lifecycle status of an agent task."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    BUDGET_EXCEEDED = "budget_exceeded"
    ERROR = "error"


# ---------------------------------------------------------------------------
# ToolRequest
# ---------------------------------------------------------------------------

class ToolRequest(BaseModel):
    """
    Represents a single tool call that an AI agent wants AgentFence to execute.

    Attributes:
        task_id: Unique identifier for the agent's task/session.
        tool_name: The type of tool to call (e.g., "openai.chat", "web_search").
        params: Arbitrary keyword arguments for the tool (the actual API payload).
        model: The model identifier string (e.g., "gpt-4o", "local/ollama/llama3").
        estimated_input: The raw input text used for token estimation.
        max_output_tokens: Upper bound on expected output tokens for cost estimation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique task/session identifier.",
    )
    tool_name: str = Field(
        ...,
        description="Tool identifier, e.g. 'openai.chat', 'web_search'.",
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary parameters to forward to the tool/API.",
    )
    model: str = Field(
        default="gpt-4o",
        description="Model string for pricing lookup.",
    )
    estimated_input: str = Field(
        default="",
        description="Input text used for pre-execution token estimation.",
    )
    max_output_tokens: int = Field(
        default=500,
        ge=1,
        le=32768,
        description="Expected max output tokens for cost estimation.",
    )

    @field_validator("max_output_tokens")
    @classmethod
    def validate_max_output_tokens(cls, v: int) -> int:
        """Ensure max_output_tokens is within a reasonable range."""
        if v < 1:
            raise ValueError("max_output_tokens must be at least 1.")
        if v > 32768:
            raise ValueError("max_output_tokens must not exceed 32768.")
        return v


# ---------------------------------------------------------------------------
# ToolResponse
# ---------------------------------------------------------------------------

class ToolResponse(BaseModel):
    """
    AgentFence's response after attempting to execute a tool call.

    Attributes:
        status: One of "success", "budget_exceeded", "circuit_breaker", "error".
        output: The actual tool output or error message.
        cost_usd: Actual cost of this call in USD.
        execution_time_ms: Wall-clock time for the call in milliseconds.
        trace_step_id: UUID linking this response to its trace log entry.
    """

    model_config = ConfigDict(frozen=True)

    status: Literal["success", "budget_exceeded", "circuit_breaker", "error"] = Field(
        ...,
        description="Execution status: success | budget_exceeded | circuit_breaker | error.",
    )
    output: Optional[Any] = Field(
        default=None,
        description="Tool output on success, or error details on failure.",
    )
    cost_usd: float = Field(
        default=0.0,
        description="Actual cost of this call in USD.",
    )
    execution_time_ms: float = Field(
        default=0.0,
        description="Execution wall-clock time in milliseconds.",
    )
    trace_step_id: str = Field(
        default="",
        description="UUID referencing the corresponding TraceStep.",
    )


# ---------------------------------------------------------------------------
# TraceStep
# ---------------------------------------------------------------------------

class TraceStep(BaseModel):
    """
    A single logged step in an execution trace.

    Each tool call that passes through AgentFence is recorded as a TraceStep,
    written to both JSONL (append-only) and SQLite (queryable).

    Attributes:
        step_id: Unique identifier for this trace step.
        timestamp: UTC ISO-8601 timestamp of when the step was recorded.
        tool_name: The tool that was called.
        model: The model used.
        input_preview: Truncated input text for display (max 500 chars).
        output_preview: Truncated output text for display (max 500 chars).
        input_tokens: Actual or estimated input token count.
        output_tokens: Actual or estimated output token count.
        cost_usd: Cost of this single step in USD.
        latency_ms: Execution time in milliseconds.
        status: "success" or "error".
        error: Error message if status is "error".
    """

    model_config = ConfigDict(frozen=True)

    step_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique step identifier (UUID).",
    )
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="UTC ISO-8601 timestamp.",
    )
    tool_name: str = Field(..., description="Tool identifier.")
    model: str = Field(..., description="Model string.")
    input_preview: str = Field(
        default="",
        description="Truncated input for display (first 500 chars).",
    )
    output_preview: str = Field(
        default="",
        description="Truncated output for display (first 500 chars).",
    )
    input_tokens: int = Field(default=0, ge=0, description="Input token count.")
    output_tokens: int = Field(default=0, ge=0, description="Output token count.")
    cost_usd: float = Field(default=0.0, description="Cost in USD.")
    latency_ms: float = Field(default=0.0, description="Latency in milliseconds.")
    status: str = Field(default="success", description="success | error.")
    error: Optional[str] = Field(default=None, description="Error message if any.")

    @computed_field(description="Whether this step completed successfully.")
    @property
    def is_success(self) -> bool:
        """True if the step status is 'success'."""
        return self.status == "success"


# ---------------------------------------------------------------------------
# BudgetConfig
# ---------------------------------------------------------------------------

class BudgetConfig(BaseModel):
    """
    Represents the current budget state for a task.

    Attributes:
        task_id: The associated task identifier.
        total_budget_usd: The total budget allocated for this task.
        remaining_budget_usd: Budget left after all settlements so far.
        reserved_budget_usd: Budget currently reserved for in-flight calls.
    """

    model_config = ConfigDict(frozen=True)

    task_id: str = Field(..., description="Associated task ID.")
    total_budget_usd: float = Field(..., ge=0, description="Total allocated budget in USD.")
    remaining_budget_usd: float = Field(..., ge=0, description="Remaining spendable budget in USD.")
    reserved_budget_usd: float = Field(
        default=0.0,
        ge=0,
        description="Budget reserved for in-flight calls.",
    )

    @computed_field(description="Percentage of budget utilized (0-100+).")
    @property
    def utilization_pct(self) -> float:
        """
        Percentage of the total budget that has been consumed.

        Returns 0.0 if total_budget_usd is zero. Can exceed 100.0 if
        the budget has been overspent (circuit breaker scenario).
        """
        if self.total_budget_usd <= 0:
            return 0.0
        spent = self.total_budget_usd - self.remaining_budget_usd
        return round((spent / self.total_budget_usd) * 100.0, 2)

    @computed_field(description="Whether the budget is fully depleted.")
    @property
    def is_depleted(self) -> bool:
        """True when remaining_budget_usd is zero or negative."""
        return self.remaining_budget_usd <= 0

    @computed_field(description="Whether budget utilization is critical (>= 90%).")
    @property
    def is_critical(self) -> bool:
        """True when 90% or more of the budget has been consumed."""
        return self.utilization_pct >= 90.0


# ---------------------------------------------------------------------------
# CircuitBreakerException
# ---------------------------------------------------------------------------

class CircuitBreakerException(Exception):
    """
    Raised when a budget is exceeded after actual cost settlement.

    This is the 'kill switch' -- when remaining budget drops below zero,
    the circuit breaker trips and the agent must stop executing.
    """

    def __init__(
        self,
        task_id: str,
        remaining: float,
        actual_cost: float,
        error_code: str = "CIRCUIT_BREAKER_TRIPPED",
    ):
        self.task_id = task_id
        self.remaining = remaining
        self.actual_cost = actual_cost
        self.error_code = error_code
        super().__init__(
            f"[{error_code}] Circuit breaker tripped for task {task_id}: "
            f"remaining=${remaining:.6f}, actual_cost=${actual_cost:.6f}"
        )
