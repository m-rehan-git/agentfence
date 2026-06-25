# 🛡️ AgentFence

**Security-aware, local-first infrastructure for AI agents.**

AgentFence sits between your AI agent and its tool execution layer. It provides **cost control**, **execution monitoring**, **failure replay**, and **security guardrails** — all running locally on your machine for maximum privacy. No cloud dependency. No data leaves your machine unless you explicitly call an external API.

## Why AgentFence?

AI agents are powerful but unpredictable. They can:
- **Run up costs** by making unlimited API calls
- **Execute dangerous tools** (shell, file delete, code execution)
- **Leak sensitive data** through prompt injection
- **Fail silently** with no way to replay what happened

AgentFence solves all of this. It's the **security guardrail layer** that every AI agent needs.

## Architecture

```
┌─────────────┐     ┌──────────────────────────────────────────┐     ┌─────────────────┐
│  AI Agent   │────▶│           AgentFence Gateway             │────▶│  LLM Provider   │
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

## Features

### 🔒 Security
- **Tool Sandbox**: Whitelist/blacklist tools, enforce parameter constraints
- **Default-deny policy**: Unknown tools are blocked automatically
- **Prompt injection detection**: Scans inputs for injection patterns
- **Rate limiting**: Per-agent token-bucket rate limiter
- **Tamper-evident audit log**: Hash-chained security event log (SHA-256)
- **Agent identity**: Register agents with specific tool permissions and budgets

### 💰 Cost Control
- **Pre-execution cost estimation**: Before every call
- **Two-phase budget system**: Reserve → Settle
- **Circuit breaker**: Stops agent when budget is exhausted
- **Per-task budgets**: Each task gets its own budget

### 📊 Execution Monitoring
- **Full trace logging**: Every tool call, input, output, and latency
- **Dual-write**: JSONL (forensic backup) + SQLite (queryable)
- **Structured logging**: JSON format for production, readable for dev

### 🔄 Failure Replay
- **Step-by-step replay**: Load any trace and replay it
- **Search**: Find steps by tool name or status
- **Cursor navigation**: Next, prev, jump to any step

## Quick Start

### Option 1: Local Installation

```powershell
# Clone the repo
git clone https://github.com/yourusername/agentfence.git
cd agentfence

# Install
python -m pip install -e .

# Start the gateway
agentfence start

# In another terminal, run a demo
python -m examples.research_agent

# Check the dashboard
agentfence dashboard
```

### Option 2: Docker (One Command)

```powershell
# Clone and deploy
git clone https://github.com/yourusername/agentfence.git
cd agentfence
python deploy.py --docker -d

# Gateway:   http://localhost:8000
# Dashboard: http://localhost:8501
```

### Option 3: Expose via Cloudflare Tunnel

```powershell
# Start the gateway
agentfence start

# In another terminal, create a tunnel
agentfence deploy tunnel --port 8000
# OR
python deploy.py --tunnel
```

## CLI Reference

```powershell
# Core commands
agentfence start                    # Start gateway
agentfence dashboard                # Start dashboard
agentfence status                   # Check health
agentfence init                     # Initialize new project

# Agent management
agentfence agents list              # List registered agents
agentfence agents add my-agent --budget 5.0 --rpm 30
agentfence agents remove my-agent

# Security
agentfence audit                    # View security audit log
agentfence audit --summary          # Security event summary
agentfence audit --verify           # Verify audit chain integrity
agentfence audit --risk critical    # Filter by risk level
agentfence sandbox --list           # List tool sandbox policies

# Deployment
agentfence deploy docker -d         # Deploy with Docker
agentfence deploy tunnel            # Cloudflare Tunnel
```

## API Reference

### POST `/v1/execute`
Execute a tool call through AgentFence.

```json
{
  "task_id": "my-task-123",
  "tool_name": "llm.chat",
  "params": {
    "messages": [{"role": "user", "content": "Hello!"}],
    "max_tokens": 100
  },
  "model": "gpt-4o",
  "estimated_input": "Hello!",
  "max_output_tokens": 100
}
```

### Security Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/security/audit` | Query audit log |
| GET | `/v1/security/audit/summary` | Audit summary |
| POST | `/v1/security/audit/verify` | Verify chain integrity |
| GET | `/v1/security/sandbox/policies` | List tool policies |
| GET | `/v1/security/rate-limits/{agent_id}` | Rate limit status |

### Task Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/tasks` | List all tasks |
| GET | `/v1/tasks/{id}/trace` | Get execution trace |
| GET | `/v1/tasks/{id}/budget` | Get budget state |
| GET | `/v1/tasks/{id}/replay/state` | Get replay state |
| POST | `/v1/tasks/{id}/replay/next` | Advance replay |
| POST | `/v1/tasks/{id}/replay/prev` | Rewind replay |

## Security Model

### Tool Policies (Default)

| Tool | Allowed | Risk | Notes |
|------|---------|------|-------|
| `openai.chat` | ✅ | Medium | Blocks `functions`/`tools` params |
| `llm.chat` | ✅ | Medium | Blocks `functions`/`tools` params |
| `web_search` | ✅ | Medium | |
| `file.read` | ✅ | High | |
| `file.write` | ✅ | High | |
| `file.delete` | ❌ | Critical | Blocked by default |
| `shell.exec` | ❌ | Critical | Blocked by default |
| `code.execute` | ❌ | Critical | Blocked by default |
| `python.exec` | ❌ | Critical | Blocked by default |
| `system.env` | ❌ | Critical | Blocked by default |

### Audit Log

Every security-relevant event is logged with:
- Timestamp (UTC)
- Event type (tool blocked, rate limited, etc.)
- Agent ID
- Risk level (low/medium/high/critical)
- SHA-256 hash chain for tamper detection

### Rate Limiting

Token-bucket rate limiter with configurable:
- Requests per minute (sustained)
- Burst size (bucket capacity)
- Per-agent tracking

## Configuration

Environment variables (`.env` file):

```env
# Provider
AF_API_KEY=your-api-key
AF_PROVIDER_URL=https://openrouter.ai/api/v1
AF_MOCK_MODE=true

# Gateway
AF_GATEWAY_PORT=8000
AF_GATEWAY_RATE_LIMIT_ENABLED=false
AF_GATEWAY_RATE_LIMIT_RPM=60

# Budget
AF_BUDGET_DEFAULT_USD=1.0

# Logging
AF_LOG_LEVEL=INFO
AF_LOG_FORMAT=readable  # or "json" for production

# Database
AF_DB_URL=sqlite:///agentfence.db
```

## Project Structure

```
agentfence/
├── agentfence/              # Core library
│   ├── __init__.py          # Package exports
│   ├── cli.py               # CLI (agentfence command)
│   ├── config.py            # Pydantic Settings
│   ├── models.py            # Pydantic schemas
│   ├── cost_engine.py       # Token estimation + pricing
│   ├── budget_enforcer.py   # SQLite-backed budget management
│   ├── tracer.py            # JSONL + SQLite trace logging
│   ├── replay.py            # Trace replay state machine
│   ├── security.py          # Security sandbox, audit, rate limiter
│   ├── logging.py           # Structured logging
│   └── gateway.py           # FastAPI app
├── dashboard/
│   └── app.py               # Streamlit dashboard
├── examples/
│   ├── research_agent.py    # Demo agent
│   └── runaway_agent.py     # Circuit breaker demo
├── tests/                   # pytest test suite
├── deploy.py                # One-click deploy script
├── cloudflared.yml          # Cloudflare Tunnel config
├── docker-compose.yml       # Full stack Docker
├── Dockerfile               # Multi-stage build
├── Makefile                 # Dev commands
├── pyproject.toml           # Project config
└── README.md                # This file
```

## Running Tests

```powershell
python -m pytest tests/ -v
```

## License

MIT
