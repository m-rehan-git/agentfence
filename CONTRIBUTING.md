# Contributing to Sentinel

Thank you for your interest in contributing! This document provides guidelines for contributing to the Sentinel project.

## Development Setup

### Prerequisites
- Python 3.11+
- pip or uv package manager
- Git

### Getting Started

```bash
# Fork and clone the repository
git clone https://github.com/m-rehan-git/sentinel.git
cd sentinel

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install development dependencies
pip install -e ".[dev]"

# Run tests to verify setup
python -m pytest tests/ -v
```

## Branch Strategy

- `main` — stable, production-ready code
- `feature/*` — new features (e.g., `feature/langchain-adapter`)
- `fix/*` — bug fixes (e.g., `fix/budget-rounding-error`)
- `docs/*` — documentation changes

Create your branch from `main`:
```bash
git checkout main
git pull origin main
git checkout -b feature/your-feature-name
```

## Code Style

We use **ruff** for linting and formatting:

```bash
# Check for lint errors
ruff check sentinel/ tests/

# Auto-format code
ruff format sentinel/ tests/

# Auto-fix lint issues
ruff check --fix sentinel/ tests/
```

### Style Rules
- Type hints are required for all function signatures
- Docstrings required for all public classes and functions
- Maximum line length: 120 characters
- Use f-strings over `.format()` or `%` formatting
- Prefer `Path` over string paths for filesystem operations

## Testing

All code changes must include tests:

```bash
# Run all tests
python -m pytest tests/ -v

# Run with coverage
python -m pytest tests/ --cov=sentinel --cov-report=term-missing

# Run a specific test file
python -m pytest tests/test_budget.py -v
```

We have 119 tests covering:
- Budget enforcement (`test_budget.py`)
- Tracer (`test_tracer.py`)
- Cost engine (`test_cost_engine.py`)
- Security layer (`test_security.py`)
- Agent registry (`test_agent_registry.py`)
- Smoke tests (`test_smoke.py`)

## Commit Messages

We follow conventional commits:

- `feat:` — New feature
- `fix:` — Bug fix
- `docs:` — Documentation changes
- `refactor:` — Code refactoring
- `test:` — Adding or updating tests
- `chore:` — Build process, dependencies, tooling

Example:
```
feat: add LangChain callback integration

Implements a LangChain-compatible callback handler that wraps
Sentinel's budget enforcement and tracing for LangChain agents.

Closes #12
```

## Pull Request Process

1. Ensure all tests pass: `python -m pytest tests/ -v`
2. Ensure no lint errors: `ruff check sentinel/ tests/`
3. Update README.md if your changes affect documented behavior
4. Open a PR against `main` branch
5. Fill out the PR template completely
6. Wait for code review and CI to pass

## Architecture Overview

```
sentinel/
├── gateway.py          # FastAPI application, all API endpoints
├── config.py           # Pydantic Settings (AF_ env prefix)
├── models.py           # Pydantic schemas for all data types
├── budget_enforcer.py  # Two-phase budget (Reserve → Settle)
├── cost_engine.py      # Token estimation + pricing lookup
├── security.py         # ToolSandbox, RateLimiter, AuditLogger
├── tracer.py           # Dual-write trace logging (JSONL + SQLite)
├── replay.py           # Trace replay state machine
├── replay_store.py     # Persistent cursor positions
├── agent_registry.py   # Agent identity + API key auth
├── cli.py              # CLI (sentinel start/audit/agents/deploy)
├── logging.py          # Structured logging (JSON + readable)
└── dashboard/          # Streamlit dashboard (optional dependency)
```

### Key Patterns
- **Two-Phase Budget**: Reserve → Execute → Settle pattern prevents overspend
- **Default-Deny Sandbox**: Unknown tools are blocked, not silently allowed
- **Hash-Chained Audit**: SHA-256 chain detects tampering
- **Persistent Replay**: Cursor positions survive server restarts

## Code of Conduct

Be respectful, constructive, and inclusive. We welcome contributors of all experience levels.
