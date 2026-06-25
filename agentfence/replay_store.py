"""
Persistent Replay Store — saves replay cursor positions to SQLite.

Survives server restarts so replay sessions persist across deployments.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from agentfence.config import get_config

logger = logging.getLogger(__name__)


class ReplayStore:
    """
    Persists replay cursor positions to SQLite.

    Usage:
        store = ReplayStore()
        store.save_cursor("task-123", 5)
        pos = store.get_cursor("task-123")  # returns 5
    """

    def __init__(self, db_path: Optional[Path] = None):
        cfg = get_config()
        if db_path:
            self._db_path = db_path
        else:
            db_url = cfg.database_url
            if db_url.startswith("sqlite:///"):
                self._db_path = Path(db_url.replace("sqlite:///", ""))
            else:
                self._db_path = Path("agentfence.db")

        self._lock = threading.Lock()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS replay_cursors (
                        task_id TEXT PRIMARY KEY,
                        current_step INTEGER NOT NULL DEFAULT 0,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                conn.commit()
            finally:
                conn.close()

    def save_cursor(self, task_id: str, step: int) -> None:
        """Save the current cursor position for a task."""
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    """
                    INSERT INTO replay_cursors (task_id, current_step, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(task_id) DO UPDATE SET
                        current_step = excluded.current_step,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (task_id, step),
                )
                conn.commit()
            finally:
                conn.close()

    def get_cursor(self, task_id: str) -> int:
        """Get the saved cursor position. Returns 0 if not found."""
        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.execute(
                    "SELECT current_step FROM replay_cursors WHERE task_id = ?",
                    (task_id,),
                )
                row = cursor.fetchone()
                return row["current_step"] if row else 0
            finally:
                conn.close()

    def delete_cursor(self, task_id: str) -> bool:
        """Delete a saved cursor (e.g., when trace is cleaned up)."""
        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.execute(
                    "DELETE FROM replay_cursors WHERE task_id = ?",
                    (task_id,),
                )
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()
