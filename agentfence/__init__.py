"""
AgentFence - Security-aware AI agent infrastructure.

Cost control, execution monitoring, failure replay, and security guardrails
for AI agents. Runs locally for privacy. No cloud dependency.

Usage:
    from agentfence import get_config, BudgetEnforcer, Tracer
    from agentfence.security import ToolSandbox, AuditLogger, RateLimiter

CLI:
    agentfence start          # Start the gateway
    agentfence dashboard      # Start the dashboard
    agentfence status         # Check health
    agentfence audit          # View security log
    agentfence sandbox --list # List tool policies
"""

from agentfence.models import (
    BudgetConfig,
    CircuitBreakerException,
    ToolRequest,
    ToolResponse,
    TraceStep,
)
from agentfence.config import get_config
from agentfence.budget_enforcer import BudgetEnforcer
from agentfence.tracer import Tracer
from agentfence.replay import ReplayEngine
from agentfence.cost_engine import estimate_cost, calculate_actual_cost, count_tokens
from agentfence.security import (
    ToolSandbox,
    RateLimiter,
    AuditLogger,
    InputValidator,
    ToolPolicy,
    AgentPolicy,
    SecurityEventType,
    RiskLevel,
)
from agentfence.agent_registry import AgentRegistry, AgentIdentity

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
