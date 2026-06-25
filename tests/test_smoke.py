"""Quick smoke test to verify the full AgentFence stack works end-to-end."""
from __future__ import annotations

import pytest
from agentfence.models import ToolRequest, TraceStep, CircuitBreakerException
from agentfence.cost_engine import estimate_cost, calculate_actual_cost, count_tokens
from agentfence.budget_enforcer import BudgetEnforcer
from agentfence.tracer import Tracer
from agentfence.replay import ReplayEngine
from agentfence.gateway import app


def test_all_modules_import() -> None:
    """All agentfence modules should be importable."""
    from agentfence import __version__
    assert app is not None
    assert __version__ == "0.2.0"


def test_cost_engine_estimates() -> None:
    """Cost engine should return non-negative estimates."""
    cost = estimate_cost("gpt-4o", "Hello world, this is a test prompt.", 100)
    assert cost >= 0.0

    cost_local = estimate_cost("local/ollama/llama3", "Hello world", 500)
    assert cost_local == 0.0  # local models are free


def test_cost_engine_actual() -> None:
    """Actual cost calculation should work."""
    cost = calculate_actual_cost("gpt-4o", 100, 200)
    assert cost > 0.0

    cost_free = calculate_actual_cost("local/ollama/llama3", 100, 200)
    assert cost_free == 0.0


def test_budget_enforcer_full_cycle(tmp_path) -> None:
    """Test a complete reserve -> settle cycle with the budget enforcer."""
    db_path = tmp_path / "smoke_test.db"
    be = BudgetEnforcer(db_path=db_path)
    task = be.init_task("smoke-task", 1.00)
    assert task.remaining_budget_usd == 1.00

    # Reserve
    assert be.check_and_reserve("smoke-task", 0.30) is True
    config = be.get_budget_config("smoke-task")
    assert config.reserved_budget_usd == pytest.approx(0.30)

    # Settle
    remaining = be.settle_actual("smoke-task", "step-1", 0.25)
    assert remaining == pytest.approx(0.75)

    # Verify
    config = be.get_budget_config("smoke-task")
    assert config.remaining_budget_usd == pytest.approx(0.75)


def test_tracer_roundtrip(tmp_path, monkeypatch) -> None:
    """Tracer should write and read back trace steps."""
    import os
    traces_dir = tmp_path / "traces"
    db_path = tmp_path / "traces.db"
    tracer = Tracer(traces_dir=traces_dir, db_path=db_path)

    step = TraceStep(
        tool_name="test_tool",
        model="gpt-4o",
        input_preview="test input",
        output_preview="test output",
        input_tokens=10,
        output_tokens=20,
        cost_usd=0.001,
        latency_ms=100.0,
        status="success",
    )

    tracer.log_step("smoke-task", step)
    steps = tracer.get_trace("smoke-task")
    assert len(steps) == 1
    assert steps[0].tool_name == "test_tool"


def test_replay_engine(tmp_path, monkeypatch) -> None:
    """ReplayEngine should step through a trace."""
    import os
    traces_dir = tmp_path / "traces"
    db_path = tmp_path / "traces.db"
    tracer = Tracer(traces_dir=traces_dir, db_path=db_path)

    for i in range(3):
        step = TraceStep(
            tool_name=f"tool_{i}",
            model="gpt-4o",
            input_preview=f"input_{i}",
            output_preview=f"output_{i}",
            status="success",
        )
        tracer.log_step("replay-task", step)

    engine = ReplayEngine(tracer=tracer)
    engine.load_trace("replay-task")
    assert engine.get_total_steps() == 3

    state = engine.get_state()
    assert state["at_start"] is True

    step = engine.next_step()
    assert step is not None
    assert step.tool_name == "tool_1"

    step = engine.prev_step()
    assert step is not None
    assert step.tool_name == "tool_0"

    step = engine.jump_to(2)
    assert step.tool_name == "tool_2"


def test_gateway_routes() -> None:
    """FastAPI app should have all expected routes."""
    paths = [r.path for r in app.routes if hasattr(r, "path")]
    expected = [
        "/v1/execute",
        "/v1/tasks",
        "/v1/tasks/{task_id}/trace",
        "/v1/tasks/{task_id}/budget",
        "/v1/tasks/{task_id}/replay/state",
        "/v1/tasks/{task_id}/replay/next",
        "/v1/tasks/{task_id}/replay/prev",
        "/v1/security/audit",
        "/v1/security/audit/summary",
        "/v1/security/audit/verify",
        "/v1/security/sandbox/policies",
        "/v1/security/rate-limits/{agent_id}",
        "/v1/agents",
        "/v1/agents/{agent_id}",
        "/v1/agents/{agent_id}/disable",
        "/v1/agents/{agent_id}/enable",
        "/health",
        "/openapi.json",
        "/docs",
    ]
    for exp in expected:
        assert exp in paths, f"Route {exp} not found. Available: {paths}"
