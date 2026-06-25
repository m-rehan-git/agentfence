"""
Tracer - Production-grade dual-write trace logging.

Writes every execution trace step to:
  1. JSONL file (traces/{task_id}.jsonl) — append-only, survives DB corruption.
  2. SQLite traces table — queryable by task_id, tool_name, status.

Features:
  - Configurable traces directory from AF_TRACES_DIR
  - Structured logging
  - Graceful handling of disk full / permission errors
  - Health check endpoint
  - Proper cleanup on shutdown
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

from sentinel.config import get_config
from sentinel.models import TraceStep

logger = logging.getLogger(__name__)


class Tracer:
    """Logs execution traces to both JSONL files and SQLite."""

    def __init__(self, traces_dir: Optional[Path] = None, db_path: Optional[Path] = None):
        """
        Initialize the Tracer.

        Args:
            traces_dir: Directory for JSONL trace files. Defaults to config.
            db_path: Path to SQLite database. Defaults to config.
        """
        cfg = get_config()
        self._traces_dir = traces_dir or Path(cfg.traces_dir)
        self._traces_dir.mkdir(parents=True, exist_ok=True)

        if db_path is not None:
            self._db_path = db_path
        else:
            db_url = cfg.database_url
            if db_url.startswith("sqlite:///"):
                self._db_path = Path(db_url.replace("sqlite:///", ""))
            else:
                self._db_path = Path("sentinel.db")

        self._retry_max = cfg.database.retry_max_attempts
        self._retry_delay = cfg.database.retry_base_delay
        self._init_db()
        logger.info(
            "Tracer initialized",
            extra={"traces_dir": str(self._traces_dir), "db_path": str(self._db_path)},
        )

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for database connections."""
        conn = sqlite3.connect(str(self._db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        try:
            yield conn
        finally:
            conn.close()

    def _execute_with_retry(self, operation: str, func, *args, **kwargs):
        """Execute a database operation with exponential backoff retry."""
        import time
        last_exception = None
        for attempt in range(1, self._retry_max + 1):
            try:
                return func(*args, **kwargs)
            except sqlite3.OperationalError as e:
                last_exception = e
                if "locked" in str(e).lower() or "busy" in str(e).lower():
                    delay = self._retry_delay * (2 ** (attempt - 1))
                    logger.warning(
                        "Database locked in tracer, retrying",
                        extra={"operation": operation, "attempt": attempt},
                    )
                    time.sleep(delay)
                else:
                    raise
            except Exception:
                raise
        raise last_exception

    def _init_db(self) -> None:
        """Create the traces table in SQLite if it doesn't exist."""
        with self._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS traces (
                    step_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    model TEXT NOT NULL,
                    input_preview TEXT DEFAULT '',
                    output_preview TEXT DEFAULT '',
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    cost_usd REAL DEFAULT 0.0,
                    latency_ms REAL DEFAULT 0.0,
                    status TEXT DEFAULT 'success',
                    error TEXT DEFAULT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_traces_task_id ON traces(task_id)"
            )
            conn.commit()
        logger.debug("Traces table initialized")

    def _get_jsonl_path(self, task_id: str) -> Path:
        """Get the JSONL file path for a given task, sanitized."""
        safe_task_id = task_id.replace("/", "_").replace("\\", "_").replace("..", "_")
        return self._traces_dir / f"{safe_task_id}.jsonl"

    def log_step(self, task_id: str, trace_step: TraceStep) -> None:
        """
        Log a trace step to both JSONL and SQLite.

        Args:
            task_id: The task this step belongs to.
            trace_step: The TraceStep to log.
        """
        # Write to JSONL (append-only)
        jsonl_path = self._get_jsonl_path(task_id)
        try:
            with open(jsonl_path, "a", encoding="utf-8") as f:
                f.write(trace_step.model_dump_json() + "\n")
        except (IOError, OSError) as e:
            logger.error(
                "Failed to write JSONL trace",
                extra={"task_id": task_id, "path": str(jsonl_path), "error": str(e)},
            )

        # Write to SQLite (upsert)
        def _do_insert():
            with self._connection() as conn:
                conn.execute(
                    """
                    INSERT INTO traces (
                        step_id, task_id, timestamp, tool_name, model,
                        input_preview, output_preview, input_tokens, output_tokens,
                        cost_usd, latency_ms, status, error
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(step_id) DO UPDATE SET
                        output_preview = excluded.output_preview,
                        output_tokens = excluded.output_tokens,
                        cost_usd = excluded.cost_usd,
                        latency_ms = excluded.latency_ms,
                        status = excluded.status,
                        error = excluded.error
                    """,
                    (
                        trace_step.step_id,
                        task_id,
                        trace_step.timestamp,
                        trace_step.tool_name,
                        trace_step.model,
                        trace_step.input_preview,
                        trace_step.output_preview,
                        trace_step.input_tokens,
                        trace_step.output_tokens,
                        trace_step.cost_usd,
                        trace_step.latency_ms,
                        trace_step.status,
                        trace_step.error,
                    ),
                )
                conn.commit()

        try:
            self._execute_with_retry("log_step", _do_insert)
        except Exception as e:
            logger.error(
                "Failed to write trace to SQLite",
                extra={"task_id": task_id, "step_id": trace_step.step_id, "error": str(e)},
            )

    def get_trace(self, task_id: str) -> list[TraceStep]:
        """
        Retrieve all trace steps for a task from JSONL (source of truth).

        Args:
            task_id: The task to retrieve traces for.

        Returns:
            List of TraceStep objects, ordered chronologically.
        """
        jsonl_path = self._get_jsonl_path(task_id)
        steps: list[TraceStep] = []

        if not jsonl_path.exists():
            return steps

        try:
            with open(jsonl_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        steps.append(TraceStep(**data))
                    except (json.JSONDecodeError, Exception) as e:
                        logger.warning(
                            "Skipping malformed trace line",
                            extra={"task_id": task_id, "error": str(e)},
                        )
        except (IOError, OSError) as e:
            logger.error(
                "Failed to read JSONL trace",
                extra={"task_id": task_id, "error": str(e)},
            )

        return steps

    def get_recent_tasks(self, limit: int = 50) -> list[dict]:
        """
        Get a list of recent tasks with summary statistics.

        Args:
            limit: Maximum number of tasks to return.

        Returns:
            List of dicts with task_id, step_count, total_cost, last_activity.
        """
        try:
            with self._connection() as conn:
                cursor = conn.execute(
                    """
                    SELECT
                        task_id,
                        COUNT(*) as step_count,
                        SUM(cost_usd) as total_cost,
                        MAX(timestamp) as last_activity
                    FROM traces
                    GROUP BY task_id
                    ORDER BY last_activity DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
                rows = cursor.fetchall()
                return [
                    {
                        "task_id": row["task_id"],
                        "step_count": row["step_count"],
                        "total_cost": row["total_cost"] or 0.0,
                        "last_activity": row["last_activity"],
                    }
                    for row in rows
                ]
        except Exception as e:
            logger.error("Failed to get recent tasks", extra={"error": str(e)})
            return []

    def get_task_ids(self) -> list[str]:
        """Get all unique task IDs from the traces table."""
        try:
            with self._connection() as conn:
                cursor = conn.execute(
                    "SELECT DISTINCT task_id FROM traces ORDER BY task_id"
                )
                return [row["task_id"] for row in cursor.fetchall()]
        except Exception as e:
            logger.error("Failed to get task IDs", extra={"error": str(e)})
            return []

    def health_check(self) -> dict:
        """
        Check the health of the tracer.

        Returns:
            Dict with status and storage information.
        """
        try:
            with self._connection() as conn:
                cursor = conn.execute("SELECT COUNT(*) as count FROM traces")
                row = cursor.fetchone()
                return {
                    "status": "healthy",
                    "traces_dir": str(self._traces_dir),
                    "database": str(self._db_path),
                    "step_count": row["count"] if row else 0,
                }
        except Exception as e:
            logger.error("Tracer health check failed", extra={"error": str(e)})
            return {
                "status": "unhealthy",
                "traces_dir": str(self._traces_dir),
                "error": str(e),
            }

    def close(self) -> None:
        """Clean up resources."""
        logger.info("Tracer closed")
