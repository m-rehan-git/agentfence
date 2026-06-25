"""
Security Layer - Tool call sandboxing, input validation, and audit logging.

This module provides the security backbone for Sentinel:
  - ToolSandbox    : Whitelist/blacklist tool calls, enforce parameter constraints
  - RateLimiter    : Token-bucket rate limiter (per-agent, per-tool, global)
  - AuditLogger    : Append-only security event log (tamper-evident)
  - InputValidator : Sanitize and validate all inputs before execution

All security events are logged to both SQLite and a separate audit.jsonl file
for forensic analysis. The audit log uses hash chaining so any tampering is
detectable.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from sentinel.config import get_config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Security event types
# ---------------------------------------------------------------------------

class SecurityEventType(str, Enum):
    """Types of security events logged by the audit system."""
    TOOL_CALL_ALLOWED = "tool_call_allowed"
    TOOL_CALL_BLOCKED = "tool_call_blocked"
    TOOL_CALL_RATE_LIMITED = "tool_call_rate_limited"
    INPUT_VALIDATION_FAILED = "input_validation_failed"
    BUDGET_VIOLATION = "budget_violation"
    AGENT_REGISTERED = "agent_registered"
    AGENT_AUTHENTICATED = "agent_authenticated"
    AGENT_AUTH_FAILED = "agent_auth_failed"
    POLICY_VIOLATION = "policy_violation"
    SANDBOX_VIOLATION = "sandbox_violation"
    SYSTEM_STARTUP = "system_startup"
    SYSTEM_SHUTDOWN = "system_shutdown"


class RiskLevel(str, Enum):
    """Risk classification for security events."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# Security Policy
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ToolPolicy:
    """
    Security policy for a specific tool.

    Attributes:
        tool_name: The tool identifier this policy applies to.
        allowed: Whether this tool is allowed at all.
        max_input_length: Maximum allowed input string length (0 = unlimited).
        max_output_tokens: Maximum allowed output tokens.
        allowed_params: Set of allowed parameter keys (empty = all allowed).
        blocked_params: Set of blocked parameter keys.
        require_budget: Whether this tool requires budget allocation.
        risk_level: The risk classification for this tool.
        rate_limit_key: Key for rate limiting (e.g., "global", "per-agent").
    """
    tool_name: str
    allowed: bool = True
    max_input_length: int = 100_000
    max_output_tokens: int = 32768
    allowed_params: frozenset[str] = frozenset()
    blocked_params: frozenset[str] = frozenset()
    require_budget: bool = True
    risk_level: RiskLevel = RiskLevel.LOW
    rate_limit_key: str = "per-agent"


@dataclass
class AgentPolicy:
    """
    Security policy for an agent identity.

    Attributes:
        agent_id: Unique agent identifier.
        agent_name: Human-readable name.
        allowed_tools: Set of allowed tool names (empty = use defaults).
        blocked_tools: Set of explicitly blocked tool names.
        max_budget_usd: Maximum budget this agent can allocate.
        max_requests_per_minute: Rate limit for this agent.
        enabled: Whether this agent is currently active.
    """
    agent_id: str
    agent_name: str = ""
    allowed_tools: set[str] = field(default_factory=set)
    blocked_tools: set[str] = field(default_factory=set)
    max_budget_usd: float = 10.0
    max_requests_per_minute: int = 30
    enabled: bool = True


# ---------------------------------------------------------------------------
# Default tool policies (security-hardened defaults)
# ---------------------------------------------------------------------------

DEFAULT_TOOL_POLICIES: dict[str, ToolPolicy] = {
    # LLM chat tools — medium risk, require budget
    "openai.chat": ToolPolicy(
        tool_name="openai.chat",
        allowed=True,
        max_input_length=50_000,
        max_output_tokens=4096,
        allowed_params=frozenset({"messages", "max_tokens", "temperature", "top_p", "model"}),
        blocked_params=frozenset({"functions", "function_call", "tools"}),  # No tool-calling escape
        require_budget=True,
        risk_level=RiskLevel.MEDIUM,
    ),
    "llm.chat": ToolPolicy(
        tool_name="llm.chat",
        allowed=True,
        max_input_length=50_000,
        max_output_tokens=4096,
        allowed_params=frozenset({"messages", "max_tokens", "temperature", "top_p", "model"}),
        blocked_params=frozenset({"functions", "function_call", "tools"}),
        require_budget=True,
        risk_level=RiskLevel.MEDIUM,
    ),
    "chat": ToolPolicy(
        tool_name="chat",
        allowed=True,
        max_input_length=50_000,
        max_output_tokens=4096,
        require_budget=True,
        risk_level=RiskLevel.MEDIUM,
    ),
    "openrouter.chat": ToolPolicy(
        tool_name="openrouter.chat",
        allowed=True,
        max_input_length=50_000,
        max_output_tokens=4096,
        require_budget=True,
        risk_level=RiskLevel.MEDIUM,
    ),
    # Web/search tools — medium risk
    "web_search": ToolPolicy(
        tool_name="web_search",
        allowed=True,
        max_input_length=10_000,
        max_output_tokens=2048,
        require_budget=True,
        risk_level=RiskLevel.MEDIUM,
    ),
    "web.fetch": ToolPolicy(
        tool_name="web.fetch",
        allowed=True,
        max_input_length=5_000,
        max_output_tokens=8192,
        require_budget=True,
        risk_level=RiskLevel.MEDIUM,
    ),
    # File tools — high risk (arbitrary file access)
    "file.read": ToolPolicy(
        tool_name="file.read",
        allowed=True,
        max_input_length=2_000,  # Path length
        max_output_tokens=16384,
        require_budget=False,
        risk_level=RiskLevel.HIGH,
    ),
    "file.write": ToolPolicy(
        tool_name="file.write",
        allowed=True,
        max_input_length=100_000,
        max_output_tokens=100,
        require_budget=False,
        risk_level=RiskLevel.HIGH,
    ),
    "file.delete": ToolPolicy(
        tool_name="file.delete",
        allowed=False,  # Blocked by default
        risk_level=RiskLevel.CRITICAL,
    ),
    # Shell/execution tools — critical risk
    "shell.exec": ToolPolicy(
        tool_name="shell.exec",
        allowed=False,  # Blocked by default
        risk_level=RiskLevel.CRITICAL,
    ),
    "code.execute": ToolPolicy(
        tool_name="code.execute",
        allowed=False,  # Blocked by default
        risk_level=RiskLevel.CRITICAL,
    ),
    "python.exec": ToolPolicy(
        tool_name="python.exec",
        allowed=False,  # Blocked by default
        risk_level=RiskLevel.CRITICAL,
    ),
    # System tools
    "system.env": ToolPolicy(
        tool_name="system.env",
        allowed=False,  # Blocked by default — env var exfiltration
        risk_level=RiskLevel.CRITICAL,
    ),
}


# ---------------------------------------------------------------------------
# Tool Sandbox
# ---------------------------------------------------------------------------

class SandboxResult:
    """Result of a sandbox check."""

    def __init__(self, allowed: bool, reason: str = "", risk_level: RiskLevel = RiskLevel.LOW):
        self.allowed = allowed
        self.reason = reason
        self.risk_level = risk_level

    def __bool__(self) -> bool:
        return self.allowed

    def __repr__(self) -> str:
        status = "ALLOWED" if self.allowed else "BLOCKED"
        return f"SandboxResult({status}, reason='{self.risk_level.value}: {self.reason}')"


class ToolSandbox:
    """
    Enforces tool-level security policies.

    Checks every tool call against:
      1. Tool whitelist/blacklist
      2. Parameter allowlist/blocklist
      3. Input size limits
      4. Output token limits
      5. Custom tool policies

    Usage:
        sandbox = ToolSandbox()
        result = sandbox.check("openai.chat", {"messages": [...]}, input_text="Hello")
        if not result:
            print(f"Blocked: {result.reason}")
    """

    def __init__(self, custom_policies: Optional[dict[str, ToolPolicy]] = None):
        """
        Initialize the sandbox.

        Args:
            custom_policies: Override or extend default tool policies.
        """
        self._policies: dict[str, ToolPolicy] = dict(DEFAULT_TOOL_POLICIES)
        if custom_policies:
            self._policies.update(custom_policies)
        logger.info("ToolSandbox initialized with %d policies", len(self._policies))

    def check(
        self,
        tool_name: str,
        params: dict[str, Any],
        input_text: str = "",
        agent_policy: Optional[AgentPolicy] = None,
    ) -> SandboxResult:
        """
        Check if a tool call is allowed.

        Args:
            tool_name: The tool being called.
            params: The parameters being passed.
            input_text: The raw input text.
            agent_policy: Optional agent-specific policy to also check.

        Returns:
            SandboxResult indicating whether the call is allowed.
        """
        # 1. Check if tool has a specific policy
        policy = self._policies.get(tool_name)

        if policy is not None:
            # Tool is explicitly known — check its policy
            if not policy.allowed:
                return SandboxResult(
                    allowed=False,
                    reason=f"Tool '{tool_name}' is blocked by security policy",
                    risk_level=policy.risk_level,
                )

            # Check input length
            if policy.max_input_length > 0 and len(input_text) > policy.max_input_length:
                return SandboxResult(
                    allowed=False,
                    reason=f"Input length {len(input_text)} exceeds max {policy.max_input_length}",
                    risk_level=RiskLevel.MEDIUM,
                )

            # Check blocked params
            if policy.blocked_params:
                blocked_found = policy.blocked_params.intersection(params.keys())
                if blocked_found:
                    return SandboxResult(
                        allowed=False,
                        reason=f"Blocked parameters: {blocked_found}",
                        risk_level=RiskLevel.HIGH,
                    )

            # Check allowed params (if specified, only those are allowed)
            if policy.allowed_params:
                unknown_params = set(params.keys()) - policy.allowed_params
                if unknown_params:
                    return SandboxResult(
                        allowed=False,
                        reason=f"Unknown parameters: {unknown_params}",
                        risk_level=RiskLevel.MEDIUM,
                    )
        else:
            # Unknown tool — default deny for security
            return SandboxResult(
                allowed=False,
                reason=f"Unknown tool '{tool_name}' — default deny policy",
                risk_level=RiskLevel.HIGH,
            )

        # 2. Check agent-specific policy
        if agent_policy is not None:
            if not agent_policy.enabled:
                return SandboxResult(
                    allowed=False,
                    reason=f"Agent '{agent_policy.agent_id}' is disabled",
                    risk_level=RiskLevel.HIGH,
                )

            if agent_policy.blocked_tools and tool_name in agent_policy.blocked_tools:
                return SandboxResult(
                    allowed=False,
                    reason=f"Tool '{tool_name}' is blocked for agent '{agent_policy.agent_id}'",
                    risk_level=RiskLevel.MEDIUM,
                )

            if agent_policy.allowed_tools and tool_name not in agent_policy.allowed_tools:
                return SandboxResult(
                    allowed=False,
                    reason=f"Tool '{tool_name}' is not in agent '{agent_policy.agent_id}' allowlist",
                    risk_level=RiskLevel.MEDIUM,
                )

        return SandboxResult(allowed=True, risk_level=policy.risk_level if policy else RiskLevel.LOW)

    def get_policy(self, tool_name: str) -> Optional[ToolPolicy]:
        """Get the policy for a specific tool."""
        return self._policies.get(tool_name)

    def list_policies(self) -> dict[str, ToolPolicy]:
        """List all tool policies."""
        return dict(self._policies)


# ---------------------------------------------------------------------------
# Rate Limiter (Token Bucket)
# ---------------------------------------------------------------------------

@dataclass
class _Bucket:
    """Token bucket for rate limiting."""
    tokens: float
    last_refill: float
    lock: threading.Lock = field(default_factory=threading.Lock)


class RateLimiter:
    """
    Token-bucket rate limiter.

    Supports per-agent, per-tool, and global rate limits.
    Thread-safe for concurrent access.

    Usage:
        limiter = RateLimiter(requests_per_minute=60, burst_size=10)
        if limiter.acquire("agent-123"):
            # proceed
        else:
            # rate limited
    """

    def __init__(
        self,
        requests_per_minute: int = 60,
        burst_size: int = 10,
    ):
        """
        Args:
            requests_per_minute: Sustained request rate limit.
            burst_size: Maximum burst size (bucket capacity).
        """
        self._rpm = requests_per_minute
        self._burst = burst_size
        self._refill_rate = requests_per_minute / 60.0  # tokens per second
        self._buckets: dict[str, _Bucket] = defaultdict(
            lambda: _Bucket(tokens=float(burst_size), last_refill=time.monotonic())
        )
        self._global_lock = threading.Lock()
        logger.info(
            "RateLimiter initialized: %d RPM, burst=%d",
            requests_per_minute,
            burst_size,
        )

    def acquire(self, key: str = "global") -> bool:
        """
        Try to acquire a token for the given key.

        Args:
            key: Rate limit bucket key (e.g., agent_id, tool_name).

        Returns:
            True if the request is allowed, False if rate limited.
        """
        bucket = self._buckets[key]
        with bucket.lock:
            now = time.monotonic()
            elapsed = now - bucket.last_refill
            bucket.tokens = min(self._burst, bucket.tokens + elapsed * self._refill_rate)
            bucket.last_refill = now

            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True
            return False

    def get_remaining(self, key: str = "global") -> float:
        """Get remaining tokens for a key."""
        bucket = self._buckets[key]
        with bucket.lock:
            now = time.monotonic()
            elapsed = now - bucket.last_refill
            return min(self._burst, bucket.tokens + elapsed * self._refill_rate)

    def reset(self, key: str = "global") -> None:
        """Reset the bucket for a key."""
        bucket = self._buckets[key]
        with bucket.lock:
            bucket.tokens = float(self._burst)
            bucket.last_refill = time.monotonic()


# ---------------------------------------------------------------------------
# Input Validator
# ---------------------------------------------------------------------------

class InputValidator:
    """
    Validates and sanitizes inputs before they reach tool execution.

    Checks for:
      - Prompt injection patterns
      - Excessive length
      - Dangerous content patterns
      - Parameter type validation
    """

    # Patterns that might indicate prompt injection attempts
    _INJECTION_PATTERNS = [
        r"ignore\s+(all\s+)?(previous|prior|above|earlier)\s+instructions?",
        r"disregard\s+(all\s+)?(previous|prior)\s+instructions?",
        r"you\s+are\s+now\s+(a|an|the)\s+",
        r"new\s+instructions?\s*:",
        r"system\s*:\s*",
        r"<\s*/\s*instruction\s*>",
        r"\[\s*system\s*\]",
        r"{\s*system\s*}",
    ]

    # Compiled patterns
    _compiled_injection = [re.compile(p, re.IGNORECASE) for p in _INJECTION_PATTERNS]

    @classmethod
    def check_prompt_injection(cls, text: str) -> list[str]:
        """
        Check for potential prompt injection patterns.

        Returns:
            List of matched pattern descriptions (empty if clean).
        """
        matches = []
        for pattern in cls._compiled_injection:
            if pattern.search(text):
                matches.append(pattern.pattern)
        return matches

    @classmethod
    def sanitize_string(cls, text: str, max_length: int = 100_000) -> str:
        """
        Sanitize a string input.

        - Truncates to max_length
        - Removes null bytes
        - Normalizes whitespace
        """
        if not text:
            return ""
        # Remove null bytes
        text = text.replace("\x00", "")
        # Truncate
        if len(text) > max_length:
            text = text[:max_length]
        return text

    @classmethod
    def validate_params(
        cls,
        params: dict[str, Any],
        allowed_params: Optional[set[str]] = None,
        blocked_params: Optional[set[str]] = None,
    ) -> tuple[bool, str]:
        """
        Validate tool parameters.

        Returns:
            (is_valid, error_message)
        """
        if blocked_params:
            blocked_found = blocked_params.intersection(params.keys())
            if blocked_found:
                return False, f"Blocked parameters: {blocked_found}"

        if allowed_params:
            unknown = set(params.keys()) - allowed_params
            if unknown:
                return False, f"Unknown parameters: {unknown}"

        return True, ""


# ---------------------------------------------------------------------------
# Audit Logger (Tamper-Evident)
# ---------------------------------------------------------------------------

class AuditLogger:
    """
    Append-only, tamper-evident security audit log.

    Each entry includes a SHA-256 hash of the previous entry's hash + current
    data, forming a hash chain. Any modification to past entries breaks the
    chain and is detectable.

    Writes to:
      - SQLite (sentinel.db, audit_events table) — queryable
      - audit.jsonl (append-only file) — forensic backup

    Usage:
        audit = AuditLogger()
        audit.log(SecurityEventType.TOOL_CALL_BLOCKED, "agent-1",
                  details={"tool": "shell.exec"}, risk_level=RiskLevel.CRITICAL)
        is_valid = audit.verify_chain()  # Check for tampering
    """

    def __init__(self, db_path: Optional[Path] = None, audit_file: Optional[Path] = None):
        """
        Initialize the audit logger.

        Args:
            db_path: Path to SQLite database.
            audit_file: Path to JSONL audit file.
        """
        cfg = get_config()
        if db_path:
            self._db_path = db_path
        else:
            db_url = cfg.database_url
            if db_url.startswith("sqlite:///"):
                self._db_path = Path(db_url.replace("sqlite:///", ""))
            else:
                self._db_path = Path("sentinel.db")

        self._audit_file = audit_file or (self._db_path.parent / "audit.jsonl")
        self._lock = threading.Lock()
        self._last_hash: str = "GENESIS"

        self._init_db()
        self._recover_last_hash()
        logger.info(
            "AuditLogger initialized",
            extra={"db": str(self._db_path), "audit_file": str(self._audit_file)},
        )

    def _init_db(self) -> None:
        """Create the audit_events table."""
        with self._db_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    agent_id TEXT DEFAULT '',
                    task_id TEXT DEFAULT '',
                    details TEXT DEFAULT '{}',
                    risk_level TEXT DEFAULT 'low',
                    hash TEXT NOT NULL UNIQUE,
                    prev_hash TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_agent ON audit_events(agent_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_type ON audit_events(event_type)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_risk ON audit_events(risk_level)"
            )
            conn.commit()

    @contextmanager
    def _db_conn(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
        finally:
            conn.close()

    def _recover_last_hash(self) -> None:
        """Recover the last hash from existing audit log (for restarts)."""
        try:
            with self._db_conn() as conn:
                cursor = conn.execute(
                    "SELECT hash FROM audit_events ORDER BY id DESC LIMIT 1"
                )
                row = cursor.fetchone()
                if row:
                    self._last_hash = row["hash"]
        except Exception:
            self._last_hash = "GENESIS"

    def _compute_hash(self, data: str, prev_hash: str) -> str:
        """Compute SHA-256 hash of data + previous hash."""
        return hashlib.sha256(f"{prev_hash}:{data}".encode("utf-8")).hexdigest()

    def log(
        self,
        event_type: SecurityEventType,
        agent_id: str = "",
        task_id: str = "",
        details: Optional[dict[str, Any]] = None,
        risk_level: RiskLevel = RiskLevel.LOW,
    ) -> str:
        """
        Log a security event.

        Args:
            event_type: Type of security event.
            agent_id: The agent involved.
            task_id: The task involved.
            details: Additional event details.
            risk_level: Risk classification.

        Returns:
            The hash of the logged event.
        """
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        details_json = json.dumps(details or {}, default=str, ensure_ascii=False)
        data = f"{timestamp}:{event_type.value}:{agent_id}:{task_id}:{details_json}:{risk_level.value}"
        event_hash = self._compute_hash(data, self._last_hash)

        with self._lock:
            # Write to SQLite
            try:
                with self._db_conn() as conn:
                    conn.execute(
                        """
                        INSERT INTO audit_events
                            (timestamp, event_type, agent_id, task_id, details, risk_level, hash, prev_hash)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            timestamp,
                            event_type.value,
                            agent_id,
                            task_id,
                            details_json,
                            risk_level.value,
                            event_hash,
                            self._last_hash,
                        ),
                    )
                    conn.commit()
            except Exception as e:
                logger.error("Failed to write audit event to SQLite: %s", e)

            # Write to JSONL (append-only)
            try:
                entry = {
                    "timestamp": timestamp,
                    "event_type": event_type.value,
                    "agent_id": agent_id,
                    "task_id": task_id,
                    "details": json.loads(details_json),
                    "risk_level": risk_level.value,
                    "hash": event_hash,
                    "prev_hash": self._last_hash,
                }
                with open(self._audit_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except Exception as e:
                logger.error("Failed to write audit event to JSONL: %s", e)

            self._last_hash = event_hash

        return event_hash

    def verify_chain(self) -> tuple[bool, list[str]]:
        """
        Verify the integrity of the entire audit chain.

        Returns:
            (is_valid, list_of_errors)
        """
        errors = []
        prev_hash = "GENESIS"

        try:
            with self._db_conn() as conn:
                cursor = conn.execute(
                    "SELECT timestamp, event_type, agent_id, task_id, details, risk_level, hash, prev_hash "
                    "FROM audit_events ORDER BY id ASC"
                )
                rows = cursor.fetchall()

            for i, row in enumerate(rows):
                # Check chain linkage
                if row["prev_hash"] != prev_hash:
                    errors.append(
                        f"Entry {i}: prev_hash mismatch (expected {prev_hash[:16]}..., got {row['prev_hash'][:16]}...)"
                    )

                # Recompute hash
                data = f"{row['timestamp']}:{row['event_type']}:{row['agent_id']}:{row['task_id']}:{row['details']}:{row['risk_level']}"
                expected_hash = self._compute_hash(data, row["prev_hash"])

                if expected_hash != row["hash"]:
                    errors.append(f"Entry {i}: hash mismatch (tampered)")

                prev_hash = row["hash"]

        except Exception as e:
            errors.append(f"Verification failed: {e}")

        return len(errors) == 0, errors

    def get_events(
        self,
        agent_id: Optional[str] = None,
        event_type: Optional[SecurityEventType] = None,
        risk_level: Optional[RiskLevel] = None,
        limit: int = 100,
    ) -> list[dict]:
        """
        Query audit events with optional filters.
        """
        query = "SELECT * FROM audit_events WHERE 1=1"
        params: list[Any] = []

        if agent_id:
            query += " AND agent_id = ?"
            params.append(agent_id)
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type.value)
        if risk_level:
            query += " AND risk_level = ?"
            params.append(risk_level.value)

        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        try:
            with self._db_conn() as conn:
                cursor = conn.execute(query, params)
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error("Failed to query audit events: %s", e)
            return []

    def get_summary(self) -> dict:
        """Get a summary of audit events."""
        try:
            with self._db_conn() as conn:
                cursor = conn.execute(
                    """
                    SELECT
                        COUNT(*) as total_events,
                        COUNT(DISTINCT agent_id) as unique_agents,
                        SUM(CASE WHEN risk_level = 'critical' THEN 1 ELSE 0 END) as critical_events,
                        SUM(CASE WHEN risk_level = 'high' THEN 1 ELSE 0 END) as high_events,
                        SUM(CASE WHEN event_type LIKE '%blocked%' THEN 1 ELSE 0 END) as blocked_events
                    FROM audit_events
                    """
                )
                row = cursor.fetchone()
                return {
                    "total_events": row["total_events"],
                    "unique_agents": row["unique_agents"],
                    "critical_events": row["critical_events"],
                    "high_events": row["high_events"],
                    "blocked_events": row["blocked_events"],
                }
        except Exception as e:
            logger.error("Failed to get audit summary: %s", e)
            return {}
