"""
Comprehensive tests for the BudgetEnforcer module.

Tests cover:
- Reservation success/failure
- Settlement and deduction
- Circuit breaker tripping
- BudgetConfig model
- Concurrent access (race conditions)
- Edge cases: zero cost, negative budget, settle without reserve, stress

Run with: pytest tests/test_budget.py -v
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

import pytest

from agentfence.budget_enforcer import BudgetEnforcer
from agentfence.models import BudgetConfig, CircuitBreakerException


# ---------------------------------------------------------------------------
# Existing tests (preserved from original test_budget.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def enforcer(tmp_path: Path) -> BudgetEnforcer:
    """Create a BudgetEnforcer with a temporary database for each test."""
    db_path = tmp_path / "test_budget.db"
    return BudgetEnforcer(db_path=db_path)


class TestReserveSucceedsWhenFundsAvailable:
    """Test that budget reservation succeeds when sufficient funds exist."""

    def test_reserve_succeeds_when_funds_available(self, enforcer: BudgetEnforcer) -> None:
        task_id = "test-task-1"
        enforcer.init_task(task_id, total_budget_usd=1.00)
        result = enforcer.check_and_reserve(task_id, estimated_cost=0.10)
        assert result is True
        config = enforcer.get_budget_config(task_id)
        assert config is not None
        assert config.reserved_budget_usd == pytest.approx(0.10)
        assert config.remaining_budget_usd == pytest.approx(1.00)

    def test_multiple_reservations_succeed(self, enforcer: BudgetEnforcer) -> None:
        task_id = "test-task-multi"
        enforcer.init_task(task_id, total_budget_usd=1.00)
        assert enforcer.check_and_reserve(task_id, 0.30) is True
        assert enforcer.check_and_reserve(task_id, 0.30) is True
        assert enforcer.check_and_reserve(task_id, 0.30) is True
        config = enforcer.get_budget_config(task_id)
        assert config is not None
        assert config.reserved_budget_usd == pytest.approx(0.90)

    def test_reserve_exact_remaining_succeeds(self, enforcer: BudgetEnforcer) -> None:
        task_id = "test-task-exact"
        enforcer.init_task(task_id, total_budget_usd=0.50)
        result = enforcer.check_and_reserve(task_id, estimated_cost=0.50)
        assert result is True


class TestReserveFailsWhenInsufficientFunds:
    """Test that budget reservation fails when funds are insufficient."""

    def test_reserve_fails_when_insufficient_funds(self, enforcer: BudgetEnforcer) -> None:
        task_id = "test-task-2"
        enforcer.init_task(task_id, total_budget_usd=0.10)
        result = enforcer.check_and_reserve(task_id, estimated_cost=0.20)
        assert result is False

    def test_reserve_fails_after_partial_reservations(self, enforcer: BudgetEnforcer) -> None:
        task_id = "test-task-partial"
        enforcer.init_task(task_id, total_budget_usd=1.00)
        assert enforcer.check_and_reserve(task_id, 0.80) is True
        result = enforcer.check_and_reserve(task_id, 0.30)
        assert result is False
        result = enforcer.check_and_reserve(task_id, 0.20)
        assert result is True

    def test_reserve_fails_for_nonexistent_task(self, enforcer: BudgetEnforcer) -> None:
        result = enforcer.check_and_reserve("nonexistent-task", 0.10)
        assert result is False

    def test_reserve_fails_after_circuit_breaker(self, enforcer: BudgetEnforcer) -> None:
        task_id = "test-task-cb"
        enforcer.init_task(task_id, total_budget_usd=0.10)
        enforcer.check_and_reserve(task_id, 0.10)
        with pytest.raises(CircuitBreakerException):
            enforcer.settle_actual(task_id, "step-1", actual_cost=0.15)
        result = enforcer.check_and_reserve(task_id, 0.01)
        assert result is False


class TestSettleDeductsRemaining:
    """Test that settlement correctly deducts from the remaining budget."""

    def test_settle_deducts_remaining(self, enforcer: BudgetEnforcer) -> None:
        task_id = "test-task-3"
        enforcer.init_task(task_id, total_budget_usd=1.00)
        enforcer.check_and_reserve(task_id, estimated_cost=0.10)
        remaining = enforcer.settle_actual(task_id, "step-1", actual_cost=0.08)
        assert remaining == pytest.approx(0.92)
        config = enforcer.get_budget_config(task_id)
        assert config is not None
        assert config.remaining_budget_usd == pytest.approx(0.92)

    def test_settle_with_higher_actual_cost(self, enforcer: BudgetEnforcer) -> None:
        task_id = "test-task-overrun"
        enforcer.init_task(task_id, total_budget_usd=1.00)
        enforcer.check_and_reserve(task_id, estimated_cost=0.05)
        remaining = enforcer.settle_actual(task_id, "step-1", actual_cost=0.12)
        assert remaining == pytest.approx(0.88)

    def test_settle_multiple_times(self, enforcer: BudgetEnforcer) -> None:
        task_id = "test-task-multi-settle"
        enforcer.init_task(task_id, total_budget_usd=1.00)
        enforcer.check_and_reserve(task_id, 0.10)
        remaining = enforcer.settle_actual(task_id, "step-1", 0.08)
        assert remaining == pytest.approx(0.92)
        enforcer.check_and_reserve(task_id, 0.20)
        remaining = enforcer.settle_actual(task_id, "step-2", 0.15)
        assert remaining == pytest.approx(0.77)
        enforcer.check_and_reserve(task_id, 0.50)
        remaining = enforcer.settle_actual(task_id, "step-3", 0.50)
        assert remaining == pytest.approx(0.27)

    def test_settle_zero_cost(self, enforcer: BudgetEnforcer) -> None:
        task_id = "test-task-zero"
        enforcer.init_task(task_id, total_budget_usd=0.50)
        enforcer.check_and_reserve(task_id, 0.10)
        remaining = enforcer.settle_actual(task_id, "step-1", actual_cost=0.0)
        assert remaining == pytest.approx(0.50)


class TestCircuitBreakerOnOverrun:
    """Test that the circuit breaker trips when budget is exceeded."""

    def test_circuit_breaker_on_overrun(self, enforcer: BudgetEnforcer) -> None:
        task_id = "test-task-4"
        enforcer.init_task(task_id, total_budget_usd=0.10)
        enforcer.check_and_reserve(task_id, estimated_cost=0.10)
        with pytest.raises(CircuitBreakerException) as exc_info:
            enforcer.settle_actual(task_id, "step-1", actual_cost=0.15)
        assert exc_info.value.task_id == task_id
        assert exc_info.value.remaining == pytest.approx(-0.05)
        assert exc_info.value.actual_cost == pytest.approx(0.15)

    def test_circuit_breaker_flag_set_in_db(self, enforcer: BudgetEnforcer) -> None:
        task_id = "test-task-cb-flag"
        enforcer.init_task(task_id, total_budget_usd=0.10)
        enforcer.check_and_reserve(task_id, 0.10)
        with pytest.raises(CircuitBreakerException):
            enforcer.settle_actual(task_id, "step-1", actual_cost=0.20)
        assert enforcer.is_circuit_breaker_tripped(task_id) is True

    def test_circuit_breaker_allows_zero_remaining(self, enforcer: BudgetEnforcer) -> None:
        task_id = "test-task-exact-zero"
        enforcer.init_task(task_id, total_budget_usd=0.10)
        enforcer.check_and_reserve(task_id, 0.10)
        remaining = enforcer.settle_actual(task_id, "step-1", actual_cost=0.10)
        assert remaining == pytest.approx(0.0)
        assert enforcer.is_circuit_breaker_tripped(task_id) is False

    def test_circuit_breaker_prevents_further_reservations(self, enforcer: BudgetEnforcer) -> None:
        task_id = "test-task-cb-block"
        enforcer.init_task(task_id, total_budget_usd=0.05)
        enforcer.check_and_reserve(task_id, 0.05)
        with pytest.raises(CircuitBreakerException):
            enforcer.settle_actual(task_id, "step-1", actual_cost=0.10)
        assert enforcer.check_and_reserve(task_id, 0.01) is False
        assert enforcer.check_and_reserve(task_id, 0.001) is False

    def test_circuit_breaker_exception_message(self, enforcer: BudgetEnforcer) -> None:
        task_id = "test-task-msg"
        enforcer.init_task(task_id, total_budget_usd=0.10)
        enforcer.check_and_reserve(task_id, 0.10)
        with pytest.raises(CircuitBreakerException, match="Circuit breaker tripped"):
            enforcer.settle_actual(task_id, "step-1", actual_cost=0.50)


class TestGetRemaining:
    """Test the get_remaining helper."""

    def test_get_remaining_for_existing_task(self, enforcer: BudgetEnforcer) -> None:
        task_id = "test-remaining"
        enforcer.init_task(task_id, total_budget_usd=2.50)
        assert enforcer.get_remaining(task_id) == pytest.approx(2.50)

    def test_get_remaining_for_nonexistent_task(self, enforcer: BudgetEnforcer) -> None:
        assert enforcer.get_remaining("does-not-exist") == pytest.approx(0.0)


class TestBudgetConfig:
    """Test BudgetConfig model initialization."""

    def test_budget_config_model(self) -> None:
        config = BudgetConfig(
            task_id="test",
            total_budget_usd=1.00,
            remaining_budget_usd=0.75,
            reserved_budget_usd=0.25,
        )
        assert config.task_id == "test"
        assert config.total_budget_usd == 1.00
        assert config.remaining_budget_usd == 0.75
        assert config.reserved_budget_usd == 0.25


# ---------------------------------------------------------------------------
# New production tests
# ---------------------------------------------------------------------------


class TestConcurrentReservations:
    """Test thread-safety of budget reservations."""

    def test_concurrent_reservations(self, tmp_path: Path) -> None:
        """Multiple threads competing for the same budget should not over-reserve."""
        db_path = tmp_path / "concurrent_budget.db"
        enforcer = BudgetEnforcer(db_path=db_path)
        task_id = "concurrent-task"
        enforcer.init_task(task_id, total_budget_usd=1.00)

        results: list[bool] = []
        lock = threading.Lock()

        def try_reserve(amount: float) -> None:
            result = enforcer.check_and_reserve(task_id, amount)
            with lock:
                results.append(result)

        # 20 threads each trying to reserve $0.10 from a $1.00 budget
        threads = [threading.Thread(target=try_reserve, args=(0.10,)) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # At most 10 should succeed (1.00 / 0.10 = 10)
        succeeded = sum(1 for r in results if r)
        assert succeeded <= 10, f"Expected at most 10 successes, got {succeeded}"

        config = enforcer.get_budget_config(task_id)
        assert config is not None
        # Reserved should never exceed total budget
        assert config.reserved_budget_usd <= 1.00 + 1e-9

    def test_concurrent_reserve_and_settle(self, tmp_path: Path) -> None:
        """Concurrent reserve+settle cycles should maintain budget consistency."""
        db_path = tmp_path / "concurrent_rs.db"
        enforcer = BudgetEnforcer(db_path=db_path)
        task_id = "concurrent-rs-task"
        enforcer.init_task(task_id, total_budget_usd=10.00)

        errors: list[str] = []
        lock = threading.Lock()

        def reserve_and_settle(step: int) -> None:
            try:
                if enforcer.check_and_reserve(task_id, 0.50):
                    enforcer.settle_actual(task_id, f"step-{step}", 0.40)
            except CircuitBreakerException:
                pass  # Expected if budget runs out
            except Exception as e:
                with lock:
                    errors.append(str(e))

        threads = [threading.Thread(target=reserve_and_settle, args=(i,)) for i in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Unexpected errors: {errors}"

        config = enforcer.get_budget_config(task_id)
        assert config is not None
        # Remaining should never be negative (circuit breaker prevents that)
        assert config.remaining_budget_usd >= -1e-9


class TestSettleWithoutReserve:
    """Test settling a task that was never reserved."""

    def test_settle_without_reserve(self, enforcer: BudgetEnforcer) -> None:
        """Settling without a prior reserve should still deduct from remaining."""
        task_id = "no-reserve-task"
        enforcer.init_task(task_id, total_budget_usd=1.00)
        # Settle without reserving first
        remaining = enforcer.settle_actual(task_id, "step-1", actual_cost=0.10)
        assert remaining == pytest.approx(0.90)
        config = enforcer.get_budget_config(task_id)
        assert config is not None
        assert config.remaining_budget_usd == pytest.approx(0.90)

    def test_settle_without_init_raises(self, enforcer: BudgetEnforcer) -> None:
        """Settling for a task that was never initialized should raise ValueError."""
        with pytest.raises(ValueError, match="not found"):
            enforcer.settle_actual("never-inited", "step-1", 0.10)


class TestNegativeBudgetInit:
    """Test that negative budgets are handled properly."""

    def test_negative_budget_init(self, enforcer: BudgetEnforcer) -> None:
        """Initializing with a negative budget should be rejected or clamped."""
        task_id = "negative-budget"
        # BudgetConfig has ge=0 constraint, so this should raise a validation error
        with pytest.raises(Exception):
            enforcer.init_task(task_id, total_budget_usd=-1.00)


class TestZeroCostCalls:
    """Test that many zero-cost calls don't break anything."""

    def test_zero_cost_calls(self, enforcer: BudgetEnforcer) -> None:
        """Hundreds of zero-cost calls should not affect the budget."""
        task_id = "zero-cost-task"
        enforcer.init_task(task_id, total_budget_usd=1.00)

        for i in range(500):
            assert enforcer.check_and_reserve(task_id, 0.0) is True
            remaining = enforcer.settle_actual(task_id, f"step-{i}", 0.0)
            assert remaining == pytest.approx(1.00)

        config = enforcer.get_budget_config(task_id)
        assert config is not None
        assert config.remaining_budget_usd == pytest.approx(1.00)
        assert config.reserved_budget_usd == pytest.approx(0.0)


class TestBudgetConfigModelValidation:
    """Test Pydantic validation on BudgetConfig."""

    def test_budget_config_model_validation(self) -> None:
        """BudgetConfig should enforce ge=0 on monetary fields."""
        # Valid config
        config = BudgetConfig(
            task_id="valid",
            total_budget_usd=1.00,
            remaining_budget_usd=0.50,
            reserved_budget_usd=0.25,
        )
        assert config.total_budget_usd == 1.00

    def test_budget_config_negative_total_rejected(self) -> None:
        """Negative total_budget_usd should be rejected by Pydantic."""
        with pytest.raises(Exception):
            BudgetConfig(
                task_id="invalid",
                total_budget_usd=-1.00,
                remaining_budget_usd=0.0,
                reserved_budget_usd=0.0,
            )

    def test_budget_config_negative_remaining_rejected(self) -> None:
        """Negative remaining_budget_usd should be rejected by Pydantic."""
        with pytest.raises(Exception):
            BudgetConfig(
                task_id="invalid",
                total_budget_usd=1.00,
                remaining_budget_usd=-0.50,
                reserved_budget_usd=0.0,
            )

    def test_budget_config_negative_reserved_rejected(self) -> None:
        """Negative reserved_budget_usd should be rejected by Pydantic."""
        with pytest.raises(Exception):
            BudgetConfig(
                task_id="invalid",
                total_budget_usd=1.00,
                remaining_budget_usd=1.00,
                reserved_budget_usd=-0.10,
            )

    def test_budget_config_zero_budget_valid(self) -> None:
        """A zero budget should be valid (edge case)."""
        config = BudgetConfig(
            task_id="zero",
            total_budget_usd=0.0,
            remaining_budget_usd=0.0,
            reserved_budget_usd=0.0,
        )
        assert config.total_budget_usd == 0.0


class TestCircuitBreakerExactZeroBoundary:
    """Test the exact boundary where remaining == 0."""

    def test_circuit_breaker_exact_zero_boundary(self, enforcer: BudgetEnforcer) -> None:
        """When remaining hits exactly 0, circuit breaker should NOT trip."""
        task_id = "exact-zero-boundary"
        enforcer.init_task(task_id, total_budget_usd=0.10)
        enforcer.check_and_reserve(task_id, 0.10)
        remaining = enforcer.settle_actual(task_id, "step-1", actual_cost=0.10)
        assert remaining == pytest.approx(0.0)
        assert enforcer.is_circuit_breaker_tripped(task_id) is False

    def test_circuit_breaker_just_over_zero(self, enforcer: BudgetEnforcer) -> None:
        """Going even slightly below zero should trip the breaker."""
        task_id = "just-over-zero"
        enforcer.init_task(task_id, total_budget_usd=0.10)
        enforcer.check_and_reserve(task_id, 0.10)
        with pytest.raises(CircuitBreakerException):
            enforcer.settle_actual(task_id, "step-1", actual_cost=0.100001)
        assert enforcer.is_circuit_breaker_tripped(task_id) is True


class TestLargeNumberOfReservations:
    """Stress test with many reservations."""

    def test_large_number_of_reservations(self, enforcer: BudgetEnforcer) -> None:
        """1000 small reservations should all succeed and track correctly."""
        task_id = "stress-task"
        enforcer.init_task(task_id, total_budget_usd=100.00)

        for i in range(1000):
            assert enforcer.check_and_reserve(task_id, 0.01) is True

        config = enforcer.get_budget_config(task_id)
        assert config is not None
        assert config.reserved_budget_usd == pytest.approx(10.00)

    def test_large_number_of_settlements(self, enforcer: BudgetEnforcer) -> None:
        """1000 reserve+settle cycles should maintain consistency."""
        task_id = "stress-settle-task"
        enforcer.init_task(task_id, total_budget_usd=1000.00)

        for i in range(1000):
            assert enforcer.check_and_reserve(task_id, 0.001) is True
            enforcer.settle_actual(task_id, f"step-{i}", 0.001)

        config = enforcer.get_budget_config(task_id)
        assert config is not None
        # 1000 cycles × $0.001 = $1.00 total spent
        assert config.remaining_budget_usd == pytest.approx(999.0)
        assert config.reserved_budget_usd == pytest.approx(0.0)
