"""
Sentinel - Security-aware AI agent infrastructure.

Cost control, execution monitoring, failure replay, and security guardrails
for AI agents. Runs locally for privacy. No cloud dependency.

Usage:
    from sentinel import get_config, BudgetEnforcer, Tracer
    from sentinel.security import ToolSandbox, AuditLogger, RateLimiter

CLI:
    sentinel start          # Start the gateway
    sentinel dashboard      # Start the dashboard
    sentinel status         # Check health
    sentinel audit          # View security log
    sentinel sandbox --list # List tool policies
"""

from sentinel.models import (
    BudgetConfig,
    CircuitBreakerException,
    ToolRequest,
    ToolResponse,
    TraceStep,
)
from sentinel.config import get_config
from sentinel.budget_enforcer import BudgetEnforcer
from sentinel.tracer import Tracer
from sentinel.replay import ReplayEngine
from sentinel.cost_engine import estimate_cost, calculate_actual_cost, count_tokens
from sentinel.security import (
    ToolSandbox,
    RateLimiter,
    AuditLogger,
    InputValidator,
    ToolPolicy,
    AgentPolicy,
    SecurityEventType,
    RiskLevel,
)
from sentinel.agent_registry import AgentRegistry, AgentIdentity

__version__ = "0.2.0"

__all__ = [
    "BudgetConfig",
    "BudgetEnforcer",
    "calculate_actual_cost",
    "CircuitBreakerException",
    "count_tokens",
    "estimate_cost",
    "get_config",
    "ReplayEngine",
    "ToolRequest",
    "ToolResponse",
    "TraceStep",
    "Tracer",
    # Security
    "ToolSandbox",
    "RateLimiter",
    "AuditLogger",
    "InputValidator",
    "ToolPolicy",
    "AgentPolicy",
    "SecurityEventType",
    "RiskLevel",
    # Agent Registry
    "AgentRegistry",
    "AgentIdentity",
]
