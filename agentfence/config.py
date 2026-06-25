"""
Centralized configuration management for AgentFence.

All settings are loaded from environment variables with the AF_ prefix.
A .env file is loaded at startup if present. Sensible defaults are provided
for every setting so the system works out of the box in development.

Usage:
    from agentfence.config import get_config
    cfg = get_config()
    print(cfg.database_url)
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ---------------------------------------------------------------------------
# Helper: locate project root (parent of agentfence/ package)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class DatabaseSettings(BaseSettings):
    """Database connection settings."""

    model_config = SettingsConfigDict(env_prefix="AF_DB_")

    url: str = Field(
        default="sqlite:///agentfence.db",
        description="Database URL. Supports sqlite:///path or postgresql://...",
    )
    pool_size: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum number of database connections in the pool.",
    )
    pool_timeout: float = Field(
        default=10.0,
        ge=1.0,
        le=60.0,
        description="Seconds to wait for a connection from the pool.",
    )
    retry_max_attempts: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum number of retries on database lock contention.",
    )
    retry_base_delay: float = Field(
        default=0.1,
        ge=0.01,
        le=5.0,
        description="Base delay in seconds for exponential backoff on retries.",
    )

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not v:
            raise ValueError("Database URL must not be empty.")
        return v


class GatewaySettings(BaseSettings):
    """FastAPI gateway settings."""

    model_config = SettingsConfigDict(env_prefix="AF_GATEWAY_")

    host: str = Field(
        default="0.0.0.0",
        description="Host address to bind the gateway.",
    )
    port: int = Field(
        default=8080,
        ge=1,
        le=65535,
        description="Port number to bind the gateway.",
    )
    workers: int = Field(
        default=1,
        ge=1,
        le=16,
        description="Number of uvicorn worker processes.",
    )
    cors_origins: list[str] = Field(
        default=["*"],
        description="Allowed CORS origins. Use ['*'] for development.",
    )
    rate_limit_enabled: bool = Field(
        default=False,
        description="Enable rate limiting on API endpoints.",
    )
    rate_limit_rpm: int = Field(
        default=60,
        ge=1,
        le=10000,
        description="Maximum requests per minute when rate limiting is enabled.",
    )
    request_timeout: float = Field(
        default=120.0,
        ge=1.0,
        le=600.0,
        description="Default request timeout in seconds for outbound provider calls.",
    )
    max_request_body_size: int = Field(
        default=10_485_760,  # 10 MB
        ge=1024,
        le=104_857_600,  # 100 MB
        description="Maximum request body size in bytes.",
    )


class ProviderSettings(BaseSettings):
    """AI provider (API) settings."""

    model_config = SettingsConfigDict(env_prefix="AF_PROVIDER_")

    url: str = Field(
        default="https://openrouter.ai/api/v1",
        description="Base URL for the AI provider API.",
    )
    api_key: str = Field(
        default="",
        description="API key for the AI provider. If empty, mock mode is activated.",
    )
    model_default: str = Field(
        default="gpt-4o",
        description="Default model string when none is specified in a request.",
    )
    max_output_tokens_default: int = Field(
        default=500,
        ge=1,
        le=131072,
        description="Default max output tokens for cost estimation.",
    )


class LoggingSettings(BaseSettings):
    """Logging configuration."""

    model_config = SettingsConfigDict(env_prefix="AF_LOG_")

    level: str = Field(
        default="INFO",
        description="Log level: DEBUG, INFO, WARNING, ERROR, CRITICAL.",
    )
    format: str = Field(
        default="json",
        description="Log output format: 'json' for production, 'readable' for development.",
    )
    file: Optional[str] = Field(
        default=None,
        description="Optional log file path. If set, logs are written here in addition to stdout.",
    )
    rotation: str = Field(
        default="daily",
        description="Log file rotation: 'daily', 'hourly', or 'size'.",
    )
    max_bytes: int = Field(
        default=10_485_760,  # 10 MB
        ge=1024,
        description="Max log file size in bytes before rotation (when rotation='size').",
    )
    backup_count: int = Field(
        default=5,
        ge=0,
        le=100,
        description="Number of rotated log files to keep.",
    )

    @field_validator("level")
    @classmethod
    def validate_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(
                f"Invalid log level '{v}'. Must be one of: {', '.join(sorted(allowed))}"
            )
        return upper

    @field_validator("format")
    @classmethod
    def validate_format(cls, v: str) -> str:
        allowed = {"json", "readable"}
        lower = v.lower()
        if lower not in allowed:
            raise ValueError(
                f"Invalid log format '{v}'. Must be one of: {', '.join(sorted(allowed))}"
            )
        return lower


class TracingSettings(BaseSettings):
    """Trace logging settings."""

    model_config = SettingsConfigDict(env_prefix="AF_TRACE_")

    dir: str = Field(
        default=str(_PROJECT_ROOT / "traces"),
        description="Directory for JSONL trace files.",
    )
    batch_size: int = Field(
        default=1,
        ge=1,
        le=100,
        description="Number of trace steps to buffer before flushing to disk.",
    )
    flush_interval_sec: float = Field(
        default=5.0,
        ge=0.1,
        le=300.0,
        description="Interval in seconds between automatic batch flushes.",
    )
    max_file_size_mb: int = Field(
        default=100,
        ge=1,
        le=10000,
        description="Maximum size in MB for a single JSONL trace file.",
    )


class BudgetSettings(BaseSettings):
    """Budget and cost control settings."""

    model_config = SettingsConfigDict(env_prefix="AF_BUDGET_")

    default_budget_usd: float = Field(
        default=1.0,
        ge=0.0,
        le=1_000_000.0,
        description="Default budget in USD for new tasks.",
    )
    pricing_path: str = Field(
        default=str(_PROJECT_ROOT / "pricing.json"),
        description="Path to the pricing JSON file.",
    )
    pricing_cache_ttl_sec: int = Field(
        default=300,
        ge=10,
        le=3600,
        description="Time-to-live in seconds for the in-memory pricing cache.",
    )
    custom_pricing_overrides: dict[str, dict[str, float]] = Field(
        default_factory=dict,
        description="Custom pricing overrides keyed by model name. "
        "Each value should have 'input_per_1k' and 'output_per_1k' keys.",
    )
    circuit_breaker_threshold: float = Field(
        default=0.0,
        ge=-1.0,
        le=1.0,
        description="Remaining budget threshold below which the circuit breaker trips.",
    )


class Config(BaseSettings):
    """Top-level configuration that aggregates all sub-configurations.

    Settings are loaded from environment variables with the AF_ prefix
    and from a .env file if present.
    """

    model_config = SettingsConfigDict(
        env_prefix="AF_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Sub-configurations
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    gateway: GatewaySettings = Field(default_factory=GatewaySettings)
    provider: ProviderSettings = Field(default_factory=ProviderSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    tracing: TracingSettings = Field(default_factory=TracingSettings)
    budget: BudgetSettings = Field(default_factory=BudgetSettings)

    # Global flags
    mock_mode: bool = Field(
        default=False,
        description="Force mock mode. If True, no real API calls are made.",
    )
    debug: bool = Field(
        default=False,
        description="Enable debug mode with verbose output.",
    )

    # -- Convenience properties -----------------------------------------------

    @property
    def database_url(self) -> str:
        """Database connection URL."""
        return self.database.url

    @property
    def api_key(self) -> str:
        """AI provider API key."""
        return self.provider.api_key

    @property
    def provider_url(self) -> str:
        """AI provider base URL."""
        return self.provider.url

    @property
    def log_level(self) -> str:
        """Logging level string."""
        return self.logging.level

    @property
    def is_mock_mode(self) -> bool:
        """Whether the system is in mock mode (no real API key or forced)."""
        return self.mock_mode or not bool(self.provider.api_key.strip())

    @property
    def default_budget_usd(self) -> float:
        """Default budget in USD for new tasks."""
        return self.budget.default_budget_usd

    @property
    def traces_dir(self) -> Path:
        """Directory for trace files as a Path object."""
        return Path(self.tracing.dir)

    @property
    def max_output_tokens_default(self) -> int:
        """Default max output tokens for cost estimation."""
        return self.provider.max_output_tokens_default

    @property
    def pricing_path(self) -> Path:
        """Path to the pricing JSON file."""
        return Path(self.budget.pricing_path)


@lru_cache(maxsize=1)
def get_config() -> Config:
    """
    Return the singleton Config instance.

    The configuration is loaded once and cached. Subsequent calls return
    the same instance. To reload, call ``get_config.cache_clear()``
    followed by ``get_config()`` again.

    Returns:
        Config: The fully resolved configuration object.
    """
    return Config()
