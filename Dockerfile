# =============================================================================
# Sentinel — Multi-stage Production Dockerfile
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: Builder — compile and install dependencies
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Install build dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency manifests first (layer caching)
COPY pyproject.toml ./

# Install production dependencies into a virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --upgrade pip setuptools wheel && \
    pip install -e ".[dev]"

# ---------------------------------------------------------------------------
# Stage 2: Runtime — minimal image with only what's needed
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

# Metadata labels
LABEL maintainer="Sentinel Team <team@sentinel.dev>" \
      version="0.3.0" \
      description="Sentinel — guardrails, budget enforcement, and observability for AI agent workloads" \
      org.opencontainers.image.source="https://github.com/m-rehan-git/sentinel" \
      org.opencontainers.image.licenses="MIT"

# Create non-root user
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid appuser --create-home appuser

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Copy application code
COPY . .

# Create directories for traces and data, set ownership
RUN mkdir -p /app/traces /app/data && \
    chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Expose gateway port
EXPOSE 8000

# Health check — verify gateway is responsive
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Default command: start the gateway
CMD ["uvicorn", "sentinel.gateway:app", "--host", "0.0.0.0", "--port", "8000"]
