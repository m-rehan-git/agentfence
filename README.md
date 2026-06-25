<div align="center">

# 🛡️ Sentinel

**Security-aware, local-first infrastructure for AI agents.**

[![PyPI version](https://img.shields.io/pypi/v/sentinel-gateway?color=blue)](https://pypi.org/project/sentinel-gateway/)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-119%20passed-brightgreen)](https://github.com/m-rehan-git/sentinel)
[![Docker](https://img.shields.io/badge/docker-ready-2496ED?logo=docker)](https://github.com/m-rehan-git/sentinel)

Cost control, execution monitoring, failure replay, and security guardrails for AI agents.
Runs locally for privacy. No cloud dependency. No data leaves your machine unless you explicitly call an external API.

[Installation](#installation) · [Quick Start](#quick-start) · [Features](#features) · [Documentation](#documentation) · [Contributing](#contributing)

</div>

---

## Why Sentinel?

AI agents are powerful but unpredictable. They can:

- 💸 **Run up costs** by making unlimited API calls
- ⚠️ **Execute dangerous tools** (shell, file delete, code execution)
- 🔓 **Leak sensitive data** through prompt injection
- 🤫 **Fail silently** with no way to replay what happened

Sentinel solves all of this. It's the **security guardrail layer** that every AI agent needs.

---

## Quick Start

### Install

```bash
pip install sentinel-gateway
```

### Run the Gateway

```bash
sentinel start
# Gateway running on http://localhost:8000
```

### Register an Agent

```bash
sentinel agents add my-agent --budget 5.0 --rpm 30
# Agent 'my-agent' registered. API key: af_xxxxxxxxxxxxxxxx
```

### Execute a Tool Call

```bash
curl -X POST http://localhost:8000/v1/execute \
  -H "Content-Type: application/json" \
  -d '{
    "task_id": "my-session-001",
    "tool_name": "llm.chat",
    "model": "gpt-4o",
    "params": {"messages": [{"role": "user", "content": "Hello!"}]},
    "estimated_input": "Hello!",
    "_agent_key": "af_xxxxxxxxxxxxxxxx"
  }'
```

### Start the Dashboard

```bash
sentinel dashboard
# Open http://localhost:8501
```

### Docker Deploy

```bash
docker compose up -d
# Gateway:   http://localhost:8000
# Dashboard: http://localhost:8501
```

---

## Installation

### Requirements

- Python 3.11+
- pip or uv package manager

### Setup

```bash
# Clone the repository
git clone https://github.com/m-rehan-git/sentinel.git
cd sentinel

# Install with production dependencies
pip install -e .

# Or install with dashboard support
pip install -e ".[dashboard]"

# Or install with development dependencies
pip install -e ".[dev]"
```

### Configuration

Create a `.env` file or set environment variables:

```env
# Required: AI provider API key (empty = mock mode)
AF_PROVIDER_API_KEY=sk-your-key-here

# Optional: Provider base URL (default: OpenRouter)
AF_PROVIDER_URL=https://openrouter.ai/api/v1

# Optional: Default model
AF_PROVIDER_MODEL_DEFAULT=gpt-4o

# Optional: Gateway port
AF_GATEWAY_PORT=8000

# Optional: Default budget per task (USD)
AF_BUDGET_DEFAULT_USD=1.0

# Optional: Enable mock mode (no real API calls)
AF_MOCK_MODE=false
```

---

## Features

### 🔒 Security
- **Tool Sandbox**: Whitelist/blacklist tools, enforce parameter constraints
- **Default-deny policy**: Unknown tools are blocked automatically
- **Prompt injection detection**: Scans inputs for injection patterns
- **Rate limiting**: Token-bucket algorithm, per-agent tracking
- **Tamper-evident audit log**: SHA-256 hash chain, verified via `/v1/security/audit/verify`

### 💰 Cost Control
- **Two-phase budget**: Reserve → Settle pattern prevents overspend
- **Circuit breaker**: Automatically halts agent when budget is exhausted
- **Pre-call estimation**: Token-based cost estimation before every call
- **Per-agent policies**: Individual budget caps and rate limits

### 🔍 Observability
- **Execution tracing**: Every tool call logged with full inputs/outputs, latency, and cost
- **Dual-write storage**: SQLite (queryable) + JSONL (forensic backup)
- **Structured logging**: JSON format for production, readable for development
- **Live dashboard**: Streamlit UI for monitoring tasks, traces, and budgets

### 🎬 Failure Replay *(Killer Feature)*
- **Step-by-step replay**: Navigate through any execution trace forward and backward
- **Persistent cursors**: Replay position survives server restarts
- **Search & filter**: Find specific steps by tool name or status
- **Jump to any step**: Instant navigation within large traces

### 🔌 Multi-Provider Support
- OpenAI (GPT-4o, GPT-4o-mini, GPT-3.5-turbo)
- OpenRouter (100+ models)
- Local models (Ollama, llama.cpp)
- Extensible pricing config (`pricing.json`)

---

## Architecture

```
┌─────────────┐     ┌──────────────────────────────────────────┐     ┌─────────────────┐
│  AI Agent   │────▶│           Sentinel Gateway               │────▶│  LLM Provider   │
│  (your code) │     │              (FastAPI)                   │     │  (OpenAI/       │
│             │◀────│                                          │◀────│   OpenRouter/   │
└─────────────┘     │  ┌─────────────┐  ┌──────────────────┐  │     │   Local)        │
                    │  │  Security   │  │  Budget Enforcer │  │     └─────────────────┘
                    │  │  Sandbox    │  │  (SQLite)        │  │
                    │  │  + Audit    │  │  + Circuit Break │  │
                    │  │  + Rate Lim │  └──────────────────┘  │
                    │  └─────────────┘                         │
                    │  ┌─────────────┐  ┌──────────────────┐  │
                    │  │  Tracer     │  │  Replay Engine   │  │
                    │  │  (JSONL +   │  │  (step-by-step)  │  │
                    │  │   SQLite)   │  └──────────────────┘  │
                    │  └─────────────┘                         │
                    └──────────────────────────────────────────┘
                           │
                    ┌──────▼───────┐
                    │  Dashboard   │──── Streamlit (port 8501)
                    │  (visual)    │
                    └──────────────┘
```

---

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/execute` | Execute tool call (requires `_agent_key`) |
| GET | `/v1/tasks` | List all tasks |
| GET | `/v1/tasks/{id}/trace` | Get execution trace |
| GET | `/v1/tasks/{id}/budget` | Get budget state |
| POST | `/v1/tasks/{id}/replay/next` | Advance replay |
| POST | `/v1/tasks/{id}/replay/prev` | Rewind replay |
| GET | `/v1/security/audit` | Query audit log |
| POST | `/v1/security/audit/verify` | Verify chain integrity |
| GET | `/v1/security/sandbox/policies` | List tool policies |
| GET | `/v1/agents` | List registered agents |
| POST | `/v1/agents/{id}/disable` | Disable agent |
| GET | `/health` | Health check |

Full API docs available at `/docs` when gateway is running (Swagger UI).

---

## Testing

```bash
# Run all tests
python -m pytest tests/ -v

# Run with coverage
python -m pytest tests/ --cov=sentinel --cov-report=term-missing

# Run specific test file
python -m pytest tests/test_budget.py -v
```

119 tests covering: budget enforcement, tracer, cost engine, security, agent registry, and smoke tests.

---

## Contributing

We welcome contributions! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Run tests (`python -m pytest tests/ -v`)
5. Run linter (`ruff check sentinel/ tests/`)
6. Submit a pull request

---

## Security

See [SECURITY.md](SECURITY.md) for our security policy and how to report vulnerabilities.

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for version history.

---

<div align="center">

Built with ❤️ by the Sentinel team.

[⬆ Back to Top](#-sentinel)

</div>
