# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] — 2026-06-25

### Added
- **OpenAI-compatible proxy endpoint** (`POST /v1/chat/completions`) — drop-in replacement for OpenAI's API, zero code changes needed for adoption
- **Multi-provider pricing support** — Anthropic (Claude), Google (Gemini), Mistral, Cohere model pricing added
- **GitHub Actions CI** — automated testing and linting on push/PR
- **Issue templates** — bug report, feature request, security report
- **PR template** — standardized contribution process
- **CODEOWNERS** — @m-rehan-git as default reviewer
- **CONTRIBUTING.md** — development setup, code style, commit messages, architecture overview

### Changed
- **Renamed project from `agentfence` to `sentinel`** — resolves naming conflict with existing GitHub org
- **Version bumped to 0.3.0**
- **Streamlit made optional** — moved to `[dashboard]` extras, install with `pip install sentinel-gateway[dashboard]`
- **Professional README rewrite** — badges, install guide, quick start, API reference, architecture diagram
- **Dockerfile OCI labels** — now point to correct repository URL
- **SECURITY.md** — updated contact email

### Fixed
- Fixed floating-point precision in budget comparisons (consistent use of `round(result, 10)`)
- Updated all internal references from AgentFence to Sentinel

## [0.2.0] — 2026-06-17

### Added
- Security-aware AI agent gateway (initial release)
- Two-phase budget enforcement (Reserve → Settle) with circuit breaker
- Tool sandbox with default-deny policy
- SHA-256 hash-chained tamper-evident audit log
- Token-bucket rate limiting per agent
- Execution tracing with dual-write (SQLite + JSONL)
- Failure replay with persistent cursor positions
- Agent identity with API key authentication
- Streamlit dashboard for monitoring
- Docker Compose deployment with health checks
- Cloudflare Tunnel support
- 119 tests covering budget, tracer, cost engine, security, agent registry

[0.3.0]: https://github.com/m-rehan-git/sentinel/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/m-rehan-git/sentinel/releases/tag/v0.2.0
