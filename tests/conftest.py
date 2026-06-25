"""
Production pytest conftest for AgentFence.

Provides:
- Project root on sys.path (via pytest_configure)
- Shared fixtures: temp_dir, config, budget_enforcer, tracer, client
- Autouse fixture to set AF_MOCK_MODE=true and AF_LOG_LEVEL=WARNING
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Optional

import pytest

# ---------------------------------------------------------------------------
# pytest_configure – runs once before any test collection
# ---------------------------------------------------------------------------

def pytest_configure(config: pytest.Config) -> None:
    """Set up the test environment before any tests are collected."""
    # Ensure project root is on sys.path so `import agentfence` works
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    # Create a traces directory for tests if it doesn't exist
    traces_dir = project_root / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Autouse fixture – every test runs in mock mode with reduced log noise
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force mock mode and warning-level logging for all tests."""
    monkeypatch.setenv("AGENTFENCE_MOCK_MODE", "1")
    monkeypatch.setenv("AF_MOCK_MODE", "true")
    monkeypatch.setenv("AF_LOG_LEVEL", "WARNING")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    """Provide a temporary directory (alias for tmp_path for clarity)."""
    return tmp_path


@pytest.fixture
def config(temp_dir: Path) -> dict[str, Any]:
    """Provide a test configuration dict with temporary paths."""
    return {
        "db_path": str(temp_dir / "test_agentfence.db"),
        "traces_dir": str(temp_dir / "traces"),
        "budget_db_path": str(temp_dir / "test_budget.db"),
        "default_budget_usd": 1.00,
        "mock_mode": True,
    }


@pytest.fixture
def budget_enforcer(temp_dir: Path) -> "BudgetEnforcer":
    """Create a BudgetEnforcer backed by a temporary SQLite database."""
    from agentfence.budget_enforcer import BudgetEnforcer

    db_path = temp_dir / "test_budget.db"
    return BudgetEnforcer(db_path=db_path)


@pytest.fixture
def tracer(temp_dir: Path) -> "Tracer":
    """Create a Tracer with temporary directories for JSONL and SQLite."""
    from agentfence.tracer import Tracer

    traces_dir = temp_dir / "traces"
    db_path = temp_dir / "test_traces.db"
    traces_dir.mkdir(parents=True, exist_ok=True)
    return Tracer(traces_dir=traces_dir, db_path=db_path)


@pytest.fixture
def client() -> "TestClient":
    """Provide a FastAPI TestClient for the AgentFence gateway."""
    from fastapi.testclient import TestClient

    from agentfence.gateway import app

    return TestClient(app)
