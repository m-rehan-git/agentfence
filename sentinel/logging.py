"""
Structured logging setup for Sentinel.

Provides a single ``get_logger(name)`` entry point that returns a fully
configured ``logging.Logger`` instance. In production mode (default),
log lines are emitted as JSON with timestamp, level, module, and message.
In development / readable mode, a human-readable format is used.

Usage:
    from sentinel.logging import get_logger
    logger = get_logger(__name__)
    logger.info("Tool executed", extra={"cost_usd": 0.01, "model": "gpt-4o"})
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from sentinel.config import get_config


# ---------------------------------------------------------------------------
# Custom JSON formatter
# ---------------------------------------------------------------------------

class _JsonFormatter(logging.Formatter):
    """
    Formats log records as single-line JSON objects.

    Every line includes:
        - ts:    ISO-8601 UTC timestamp
        - level: Log level name
        - logger: Logger name (module)
        - msg:   The formatted message
    Any additional fields set via ``extra={...}`` are included at the top level.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # Include any extra fields passed via logger.info(..., extra={...})
        standard_attrs = {
            "name", "msg", "args", "created", "relativeCreated", "exc_info",
            "exc_text", "stack_info", "lineno", "funcName", "pathname",
            "filename", "module", "thread", "threadName", "process",
            "processName", "levelname", "levelno", "message", "msecs",
            "taskName",
        }
        for key, value in record.__dict__.items():
            if key not in standard_attrs and not key.startswith("_"):
                log_entry[key] = value

        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, default=str, ensure_ascii=False)


class _ReadableFormatter(logging.Formatter):
    """
    Human-readable log formatter for development.

    Format: 2026-06-10T12:00:00Z [INFO] my.module: message key=value ...
    """

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        level = record.levelname.ljust(8)
        base = f"{ts} [{level}] {record.name}: {record.getMessage()}"

        # Append extra fields
        standard_attrs = {
            "name", "msg", "args", "created", "relativeCreated", "exc_info",
            "exc_text", "stack_info", "lineno", "funcName", "pathname",
            "filename", "module", "thread", "threadName", "process",
            "processName", "levelname", "levelno", "message", "msecs",
            "taskName",
        }
        extras = {
            k: v for k, v in record.__dict__.items()
            if k not in standard_attrs and not k.startswith("_")
        }
        if extras:
            extra_str = " ".join(f"{k}={v}" for k, v in extras.items())
            base = f"{base} {extra_str}"

        if record.exc_info and record.exc_info[1]:
            base = f"{base}\n{self.formatException(record.exc_info)}"

        return base


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_loggers_configured = False


def _configure_root_logger() -> None:
    """
    Configure the root ``sentinel`` logger based on current config settings.

    This is called once on first use of ``get_logger()``. It is safe to
    call multiple times — subsequent calls are no-ops.
    """
    global _loggers_configured
    if _loggers_configured:
        return

    cfg = get_config()
    level = getattr(logging, cfg.log_level, logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)

    if cfg.logging.format == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(_ReadableFormatter())

    root = logging.getLogger("sentinel")
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)
    root.propagate = False

    # Optional file handler
    if cfg.logging.file:
        try:
            from logging.handlers import RotatingFileHandler

            file_handler = RotatingFileHandler(
                cfg.logging.file,
                maxBytes=cfg.logging.max_bytes,
                backupCount=cfg.logging.backup_count,
                encoding="utf-8",
            )
            file_handler.setLevel(level)
            file_handler.setFormatter(_JsonFormatter())
            root.addHandler(file_handler)
        except (OSError, PermissionError) as exc:
            # If we can't open the log file, log to stderr via the stream handler
            root.warning("Could not open log file %s: %s", cfg.logging.file, exc)

    _loggers_configured = True


def get_logger(name: str) -> logging.Logger:
    """
    Return a structured logger for the given module name.

    The logger is automatically configured on first call based on the
    current ``sentinel.config`` settings (log level, format, file).

    Args:
        name: Typically ``__name__`` of the calling module.

    Returns:
        A configured ``logging.Logger`` instance.
    """
    _configure_root_logger()
    return logging.getLogger(f"sentinel.{name}")
