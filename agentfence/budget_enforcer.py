"""
Budget Enforcer - Production-grade SQLite-backed budget management.

Two-phase budget system:
  1. RESERVE: Before a tool call, atomically check and reserve estimated cost.
  2. SETTLE: After the call, adjust budget with actual cost.

The circuit breaker trips when remaining_budget_usd drops below zero,
raising CircuitBreakerException to halt the agent.

Features:
  - Configurable database path via AF_DATABASE_URL
  - Retry with exponential backoff on database lock contention
  - Structured logging
  - Connection context manager with automatic cleanup
  - Health check endpoint
"""

from __future__ import annotations

import logging
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

from agentfence.config import get_config
from agentfence.models import BudgetConfig, CircuitBreakerException

logger = logging.getLogger(__name__)


class BudgetEnforcer:
    """
    Manages per-task budgets with SQLite-backed atomic transactions.

    Usage:
        enforcer = BudgetEnforcer()
        enforcer.init_task("task-123", total_budget_usd=0.50)
        if enforcer.check_and_reserve("task-123", estimated_cost=0.01):
            # ... execute tool call ...
            enforcer.settle_actual("task-123", step_id="...", actual_cost=0.008)
    """

    def __init__(self, db_path: Optional[Path] = None):
        """
        Initialize the BudgetEnforcer.

        Args:
            db_path: Path to the SQLite database. Defaults to config database_url.
        """
        cfg = get_config()
        if db_path is not None:
            self._db_path = db_path
        else:
            # Extract path from sqlite:/// URL
            db_url = cfg.database_url
            if db_url.startswith("sqlite:///"):
                self._db_path = Path(db_url.replace("sqlite:///", ""))
            else:
                self._db_path = Path("agentfence.db")

        self._retry_max = cfg.database.retry_max_attempts
        self._retry_delay = cfg.database.retry_base_delay
        self._pool_timeout = cfg.database.pool_timeout
        self._init_db()
        logger.info("BudgetEnforcer initialized", extra={"db_path": str(self._db_path)})

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        """
        Context manager for database connections with automatic cleanup.
        """
        conn = sqlite3.connect(str(self._db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    def _execute_with_retry(self, operation: str, func, *args, **kwargs):
        """
        Execute a database operation with exponential backoff retry.

        Args:
            operation: Human-readable name for logging.
            func: Callable that performs the operation.
            *args, **kwargs: Arguments passed to func.

        Returns:
            The result of func().

        Raises:
            The last exception if all retries are exhausted.
        """
        last_exception = None
        for attempt in range(1, self._retry_max + 1):
            try:
                return func(*args, **kwargs)
            except sqlite3.OperationalError as e:
                last_exception = e
                if "locked" in str(e).lower() or "busy" in str(e).lower():
                    delay = self._retry_delay * (2 ** (attempt - 1))
                    logger.warning(
                        "Database locked, retrying",
                        extra={"operation": operation, "attempt": attempt, "delay": delay},
                    )
                    time.sleep(delay)
                else:
                    raise
            except Exception:
                raise

        logger.error(
            "Database operation failed after retries",
            extra={"operation": operation, "attempts": self._retry_max},
        )
        raise last_exception

    def _init_db(self) -> None:
        """Create the budgets table if it doesn't exist."""
        with self._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS budgets (
                    task_id TEXT PRIMARY KEY,
                    total_budget_usd REAL NOT NULL DEFAULT 0.0,
                    remaining_budget_usd REAL NOT NULL DEFAULT 0.0,
                    reserved_budget_usd REAL NOT NULL DEFAULT 0.0,
                    circuit_breaker_tripped INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.commit()
        logger.debug("Budgets table initialized")

    def init_task(self, task_id: str, total_budget_usd: float) -> BudgetConfig:
        """
        Initialize a new task with a total budget.

        If the task already exists, this resets its budget state.

        Args:
            task_id: Unique task identifier.
            total_budget_usd: Total budget in USD.

        Returns:
            BudgetConfig with the initialized state.
        """
        if total_budget_usd < 0:
            raise ValueError(f"Budget cannot be negative: {total_budget_usd}")

        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO budgets (task_id, total_budget_usd, remaining_budget_usd,
                                     reserved_budget_usd, circuit_breaker_tripped)
                VALUES (?, ?, ?, 0.0, 0)
                ON CONFLICT(task_id) DO UPDATE SET
                    total_budget_usd = excluded.total_budget_usd,
                    remaining_budget_usd = excluded.total_budget_usd,
                    reserved_budget_usd = 0.0,
                    circuit_breaker_tripped = 0,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (task_id, total_budget_usd, total_budget_usd),
            )
            conn.commit()

        logger.info(
            "Task budget initialized",
            extra={"task_id": task_id, "total_budget_usd": total_budget_usd},
        )

        return BudgetConfig(
            task_id=task_id,
            total_budget_usd=total_budget_usd,
            remaining_budget_usd=total_budget_usd,
            reserved_budget_usd=0.0,
        )

    def check_and_reserve(self, task_id: str, estimated_cost: float) -> bool:
        """
        Atomically check if sufficient budget exists and reserve the estimated cost.

        Uses SQLite BEGIN IMMEDIATE to lock the row and prevent race conditions.

        Args:
            task_id: The task to reserve budget for.
            estimated_cost: The estimated cost to reserve in USD.

        Returns:
            True if reservation succeeded, False if insufficient funds.
        """
        if estimated_cost < 0:
            raise ValueError(f"Estimated cost cannot be negative: {estimated_cost}")

        def _do_reserve():
            with self._connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    cursor = conn.execute(
                        "SELECT remaining_budget_usd, reserved_budget_usd, "
                        "circuit_breaker_tripped FROM budgets WHERE task_id = ?",
                        (task_id,),
                    )
                    row = cursor.fetchone()

                    if row is None:
                        conn.rollback()
                        logger.warning(
                            "Reservation failed: task not found",
                            extra={"task_id": task_id},
                        )
                        return False

                    if row["circuit_breaker_tripped"] == 1:
                        conn.rollback()
                        logger.warning(
                            "Reservation failed: circuit breaker tripped",
                            extra={"task_id": task_id},
                        )
                        return False

                    remaining = row["remaining_budget_usd"]
                    reserved = row["reserved_budget_usd"]
                    available = round(remaining - reserved, 10)

                    if available >= estimated_cost:
                        conn.execute(
                            "UPDATE budgets SET reserved_budget_usd = reserved_budget_usd + ?, "
                            "updated_at = CURRENT_TIMESTAMP WHERE task_id = ?",
                            (estimated_cost, task_id),
                        )
                        conn.commit()
                        logger.debug(
                            "Budget reserved",
                            extra={
                                "task_id": task_id,
                                "estimated_cost": estimated_cost,
                                "available": available,
                            },
                        )
                        return True
                    else:
                        conn.rollback()
                        logger.info(
                            "Reservation denied: insufficient funds",
                            extra={
                                "task_id": task_id,
                                "estimated_cost": estimated_cost,
                                "available": available,
                            },
                        )
                        return False
                except Exception:
                    conn.rollback()
                    raise

        return self._execute_with_retry("check_and_reserve", _do_reserve)

    def settle_actual(self, task_id: str, step_id: str, actual_cost: float) -> float:
        """
        Settle a reservation with the actual cost after tool execution.

        Args:
            task_id: The task to settle.
            step_id: The trace step ID for logging.
            actual_cost: The actual cost in USD.

        Returns:
            The remaining budget after settlement.

        Raises:
            CircuitBreakerException: If remaining budget drops below zero.
            ValueError: If task not found.
        """
        if actual_cost < 0:
            raise ValueError(f"Actual cost cannot be negative: {actual_cost}")

        def _do_settle():
            with self._connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    cursor = conn.execute(
                        "SELECT remaining_budget_usd, reserved_budget_usd "
                        "FROM budgets WHERE task_id = ?",
                        (task_id,),
                    )
                    row = cursor.fetchone()

                    if row is None:
                        conn.rollback()
                        raise ValueError(f"Task '{task_id}' not found in budget database.")

                    remaining = row["remaining_budget_usd"]
                    reserved = row["reserved_budget_usd"]

                    new_remaining = round(remaining - actual_cost, 10)
                    new_reserved = max(0.0, round(reserved - actual_cost, 10))
                    circuit_breaker = 1 if new_remaining < 0 else 0

                    conn.execute(
                        """
                        UPDATE budgets
                        SET remaining_budget_usd = ?,
                            reserved_budget_usd = ?,
                            circuit_breaker_tripped = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE task_id = ?
                        """,
                        (new_remaining, new_reserved, circuit_breaker, task_id),
                    )
                    conn.commit()

                    logger.debug(
                        "Budget settled",
                        extra={
                            "task_id": task_id,
                            "step_id": step_id,
                            "actual_cost": actual_cost,
                            "new_remaining": new_remaining,
                        },
                    )

                    if new_remaining < 0:
                        raise CircuitBreakerException(
                            task_id=task_id,
                            remaining=new_remaining,
                            actual_cost=actual_cost,
                        )

                    return new_remaining
                except CircuitBreakerException:
                    raise
                except Exception:
                    conn.rollback()
                    raise

        return self._execute_with_retry("settle_actual", _do_settle)

    def get_remaining(self, task_id: str) -> float:
        """Get the remaining budget for a task. Returns 0.0 if not found."""
        with self._connection() as conn:
            cursor = conn.execute(
                "SELECT remaining_budget_usd FROM budgets WHERE task_id = ?",
                (task_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return 0.0
            return row["remaining_budget_usd"]

    def get_budget_config(self, task_id: str) -> Optional[BudgetConfig]:
        """Get the full budget configuration for a task."""
        with self._connection() as conn:
            cursor = conn.execute(
                "SELECT task_id, total_budget_usd, remaining_budget_usd, reserved_budget_usd "
                "FROM budgets WHERE task_id = ?",
                (task_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return BudgetConfig(
                task_id=row["task_id"],
                total_budget_usd=row["total_budget_usd"],
                remaining_budget_usd=row["remaining_budget_usd"],
                reserved_budget_usd=row["reserved_budget_usd"],
            )

    def is_circuit_breaker_tripped(self, task_id: str) -> bool:
        """Check if the circuit breaker has been tripped for a task."""
        with self._connection() as conn:
            cursor = conn.execute(
                "SELECT circuit_breaker_tripped FROM budgets WHERE task_id = ?",
                (task_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return False
            return row["circuit_breaker_tripped"] == 1

    def health_check(self) -> dict:
        """
        Check the health of the budget enforcer.

        Returns:
            Dict with status and database information.
        """
        try:
            with self._connection() as conn:
                cursor = conn.execute("SELECT COUNT(*) as count FROM budgets")
                row = cursor.fetchone()
                return {
                    "status": "healthy",
                    "database": str(self._db_path),
                    "task_count": row["count"] if row else 0,
                }
        except Exception as e:
            logger.error("Health check failed", extra={"error": str(e)})
            return {
                "status": "unhealthy",
                "database": str(self._db_path),
                "error": str(e),
            }

    def close(self) -> None:
        """Clean up resources. Called during application shutdown."""
        logger.info("BudgetEnforcer closed")
