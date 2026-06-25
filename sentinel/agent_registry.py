"""
Agent Registry — manages agent identities and enforces access control.

Provides:
  - AgentRegistry: Register, lookup, authenticate agents
  - AgentCredentials: API key-based authentication
  - Per-agent policy enforcement at the gateway level
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from sentinel.config import get_config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Agent identity
# ---------------------------------------------------------------------------

@dataclass
class AgentIdentity:
    """
    Represents a registered agent with credentials and policy.

    Attributes:
        agent_id: Unique agent identifier.
        agent_name: Human-readable name.
        api_key: Hashed API key (SHA-256).
        allowed_tools: Set of allowed tool names (empty = use defaults).
        blocked_tools: Set of explicitly blocked tool names.
        max_budget_usd: Maximum budget this agent can allocate.
        max_requests_per_minute: Rate limit for this agent.
        enabled: Whether this agent is currently active.
        created_at: ISO timestamp of registration.
    """
    agent_id: str
    api_key_hash: str
    agent_name: str = ""
    allowed_tools: set[str] = field(default_factory=set)
    blocked_tools: set[str] = field(default_factory=set)
    max_budget_usd: float = 10.0
    max_requests_per_minute: int = 30
    enabled: bool = True
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "allowed_tools": list(self.allowed_tools),
            "blocked_tools": list(self.blocked_tools),
            "max_budget_usd": self.max_budget_usd,
            "max_requests_per_minute": self.max_requests_per_minute,
            "enabled": self.enabled,
            "created_at": self.created_at,
        }


def _hash_key(key: str) -> str:
    """Hash an API key for storage."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Agent Registry
# ---------------------------------------------------------------------------

class AgentRegistry:
    """
    Manages agent identities, authentication, and policy lookup.

    Usage:
        registry = AgentRegistry()
        identity, raw_key = registry.create_agent("my-agent", budget=5.0)
        # Store raw_key, give to agent

        # On each request:
        agent = registry.authenticate(raw_key)
        if agent is None:
            # reject
        # Use agent.allowed_tools, agent.max_budget_usd, etc.
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
                self._db_path = Path("sentinel.db")

        self._lock = threading.Lock()
        self._init_db()
        logger.info("AgentRegistry initialized", extra={"db": str(self._db_path)})

    @contextmanager
    def _db_conn(self):
        conn = sqlite3.connect(str(self._db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._db_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agents (
                    agent_id TEXT PRIMARY KEY,
                    api_key_hash TEXT NOT NULL,
                    agent_name TEXT DEFAULT '',
                    allowed_tools TEXT DEFAULT '[]',
                    blocked_tools TEXT DEFAULT '[]',
                    max_budget_usd REAL DEFAULT 10.0,
                    max_requests_per_minute INTEGER DEFAULT 30,
                    enabled INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def create_agent(
        self,
        agent_id: str,
        agent_name: str = "",
        allowed_tools: Optional[set[str]] = None,
        blocked_tools: Optional[set[str]] = None,
        max_budget_usd: float = 10.0,
        max_requests_per_minute: int = 30,
    ) -> tuple[AgentIdentity, str]:
        """
        Register a new agent and return its identity + raw API key.

        The raw API key is shown once — it cannot be retrieved later.

        Returns:
            (AgentIdentity, raw_api_key)
        """
        raw_key = f"af_{secrets.token_urlsafe(32)}"
        api_key_hash = _hash_key(raw_key)
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        allowed = allowed_tools or set()
        blocked = blocked_tools or set()

        with self._db_conn() as conn:
            conn.execute(
                """
                INSERT INTO agents
                    (agent_id, api_key_hash, agent_name, allowed_tools, blocked_tools,
                     max_budget_usd, max_requests_per_minute, enabled, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    agent_id,
                    api_key_hash,
                    agent_name,
                    json.dumps(list(allowed)),
                    json.dumps(list(blocked)),
                    max_budget_usd,
                    max_requests_per_minute,
                    now,
                ),
            )
            conn.commit()

        identity = AgentIdentity(
            agent_id=agent_id,
            api_key_hash=api_key_hash,
            agent_name=agent_name,
            allowed_tools=allowed,
            blocked_tools=blocked,
            max_budget_usd=max_budget_usd,
            max_requests_per_minute=max_requests_per_minute,
            enabled=True,
            created_at=now,
        )

        logger.info("Agent registered", extra={"agent_id": agent_id})
        return identity, raw_key

    def authenticate(self, raw_key: str) -> Optional[AgentIdentity]:
        """
        Authenticate an agent by its raw API key.

        Returns:
            AgentIdentity if valid, None if invalid/expired.
        """
        if not raw_key or not raw_key.startswith("af_"):
            return None

        key_hash = _hash_key(raw_key)

        with self._db_conn() as conn:
            cursor = conn.execute(
                "SELECT * FROM agents WHERE api_key_hash = ?",
                (key_hash,),
            )
            row = cursor.fetchone()

        if row is None:
            logger.warning("Authentication failed: invalid key")
            return None

        if not row["enabled"]:
            logger.warning(
                "Authentication failed: agent disabled",
                extra={"agent_id": row["agent_id"]},
            )
            return None

        return AgentIdentity(
            agent_id=row["agent_id"],
            api_key_hash=row["api_key_hash"],
            agent_name=row["agent_name"],
            allowed_tools=set(json.loads(row["allowed_tools"])),
            blocked_tools=set(json.loads(row["blocked_tools"])),
            max_budget_usd=row["max_budget_usd"],
            max_requests_per_minute=row["max_requests_per_minute"],
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
        )

    def get_agent(self, agent_id: str) -> Optional[AgentIdentity]:
        """Look up an agent by ID (without authentication)."""
        with self._db_conn() as conn:
            cursor = conn.execute(
                "SELECT * FROM agents WHERE agent_id = ?",
                (agent_id,),
            )
            row = cursor.fetchone()

        if row is None:
            return None

        return AgentIdentity(
            agent_id=row["agent_id"],
            api_key_hash=row["api_key_hash"],
            agent_name=row["agent_name"],
            allowed_tools=set(json.loads(row["allowed_tools"])),
            blocked_tools=set(json.loads(row["blocked_tools"])),
            max_budget_usd=row["max_budget_usd"],
            max_requests_per_minute=row["max_requests_per_minute"],
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
        )

    def disable_agent(self, agent_id: str) -> bool:
        """Disable an agent. Returns True if found and disabled."""
        with self._db_conn() as conn:
            cursor = conn.execute(
                "UPDATE agents SET enabled = 0 WHERE agent_id = ?",
                (agent_id,),
            )
            conn.commit()
            return cursor.rowcount > 0

    def enable_agent(self, agent_id: str) -> bool:
        """Enable an agent. Returns True if found and enabled."""
        with self._db_conn() as conn:
            cursor = conn.execute(
                "UPDATE agents SET enabled = 1 WHERE agent_id = ?",
                (agent_id,),
            )
            conn.commit()
            return cursor.rowcount > 0

    def delete_agent(self, agent_id: str) -> bool:
        """Remove an agent permanently."""
        with self._db_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM agents WHERE agent_id = ?",
                (agent_id,),
            )
            conn.commit()
            return cursor.rowcount > 0

    def list_agents(self) -> list[AgentIdentity]:
        """List all registered agents."""
        with self._db_conn() as conn:
            cursor = conn.execute("SELECT * FROM agents ORDER BY created_at DESC")
            rows = cursor.fetchall()

        return [
            AgentIdentity(
                agent_id=row["agent_id"],
                api_key_hash=row["api_key_hash"],
                agent_name=row["agent_name"],
                allowed_tools=set(json.loads(row["allowed_tools"])),
                blocked_tools=set(json.loads(row["blocked_tools"])),
                max_budget_usd=row["max_budget_usd"],
                max_requests_per_minute=row["max_requests_per_minute"],
                enabled=bool(row["enabled"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]
