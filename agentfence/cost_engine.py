"""
Cost Engine - Token estimation and pricing lookup.

This module provides two main functions:
- estimate_cost()     : Pre-execution cost estimate based on input text + expected output.
- calculate_actual_cost() : Post-execution cost based on actual token counts.

Pricing is loaded from a configurable path (defaults to the project-root
pricing.json). A thread-safe in-memory cache with TTL avoids repeated disk
reads. Custom pricing overrides can be supplied via configuration.

For OpenAI models, tiktoken is used for accurate token counting.
For unknown/local models, a heuristic word-based estimator is used as fallback.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Optional

from agentfence.config import get_config
from agentfence.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Thread-safe pricing cache with TTL
# ---------------------------------------------------------------------------

_pricing_cache: Optional[dict[str, Any]] = None
_pricing_cache_ts: float = 0.0
_pricing_cache_lock = threading.Lock()


def _is_cache_valid(ttl_sec: int) -> bool:
    """Check whether the in-memory pricing cache is still fresh."""
    return (
        _pricing_cache is not None
        and (time.monotonic() - _pricing_cache_ts) < ttl_sec
    )


def _load_pricing(force_reload: bool = False) -> dict[str, Any]:
    """
    Load and cache pricing data from the configured pricing path.

    The cache is thread-safe and respects the TTL from the configuration.
    If the pricing file is missing or unreadable, an empty dict is returned
    and a warning is logged — the system degrades gracefully.

    Args:
        force_reload: Bypass the cache and reload from disk.

    Returns:
        dict: The parsed pricing data keyed by model name.
    """
    global _pricing_cache, _pricing_cache_ts

    cfg = get_config()
    ttl = cfg.budget.pricing_cache_ttl_sec
    pricing_path = Path(cfg.budget.pricing_path)

    with _pricing_cache_lock:
        if not force_reload and _is_cache_valid(ttl):
            return _pricing_cache  # type: ignore[return-value]

        # Start with any custom overrides from config
        data: dict[str, Any] = dict(cfg.budget.custom_pricing_overrides)

        # Load from disk and merge (disk values take precedence over overrides)
        if pricing_path.exists():
            try:
                with open(pricing_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                disk_data = raw.get("models", raw)
                if isinstance(disk_data, dict):
                    data.update(disk_data)
                logger.info(
                    "Pricing data loaded from %s (%d models)",
                    pricing_path,
                    len(disk_data) if isinstance(disk_data, dict) else 0,
                )
            except json.JSONDecodeError as exc:
                logger.warning(
                    "pricing.json at %s is malformed: %s. Using empty pricing.",
                    pricing_path,
                    exc,
                )
            except OSError as exc:
                logger.warning(
                    "Could not read pricing.json at %s: %s. Using empty pricing.",
                    pricing_path,
                    exc,
                )
        else:
            logger.warning(
                "pricing.json not found at %s. Using empty pricing (all models free).",
                pricing_path,
            )

        _pricing_cache = data
        _pricing_cache_ts = time.monotonic()
        return data


def invalidate_pricing_cache() -> None:
    """Force the pricing cache to reload on the next access."""
    global _pricing_cache, _pricing_cache_ts
    with _pricing_cache_lock:
        _pricing_cache = None
        _pricing_cache_ts = 0.0
    logger.debug("Pricing cache invalidated.")


# ---------------------------------------------------------------------------
# Model pricing lookup
# ---------------------------------------------------------------------------

def _get_model_pricing(model: str) -> tuple[float, float]:
    """
    Look up input and output pricing for a given model string.

    Args:
        model: Model identifier (e.g., "gpt-4o").

    Returns:
        Tuple of (input_price_per_1k, output_price_per_1k) in USD.
        Falls back to (0.0, 0.0) for unknown models (free tier).
    """
    pricing = _load_pricing()
    if model in pricing:
        entry = pricing[model]
        return float(entry["input_per_1k"]), float(entry["output_per_1k"])
    # Fallback: treat unknown models as free (local/self-hosted)
    return 0.0, 0.0


# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------

def _is_openai_model(model: str) -> bool:
    """
    Check if a model string corresponds to an OpenAI model that tiktoken supports.

    Args:
        model: Model identifier string.

    Returns:
        True if tiktoken should be used for token counting.
    """
    openai_prefixes = ("gpt-4", "gpt-3.5", "text-davinci", "text-curie", "o1", "o3")
    return model.startswith(openai_prefixes)


def _count_tokens_tiktoken(text: str, model: str) -> int:
    """
    Count tokens using the tiktoken library for OpenAI models.

    Args:
        text: The input text to count tokens for.
        model: The model name (used to select the correct encoding).

    Returns:
        Integer token count.
    """
    try:
        import tiktoken

        if model.startswith("gpt-4o"):
            encoding = tiktoken.encoding_for_model("gpt-4o")
        elif model.startswith("gpt-4"):
            encoding = tiktoken.encoding_for_model("gpt-4")
        elif model.startswith("gpt-3.5"):
            encoding = tiktoken.encoding_for_model("gpt-3.5-turbo")
        else:
            encoding = tiktoken.get_encoding("cl100k_base")

        return len(encoding.encode(text))
    except Exception as exc:
        logger.debug(
            "tiktoken failed for model %s: %s. Falling back to heuristic.",
            model,
            exc,
        )
        return _count_tokens_heuristic(text)


def _count_tokens_heuristic(text: str) -> int:
    """
    Heuristic token counter for non-OpenAI or unknown models.

    Uses the approximation: 1 token ~ 1.3 words (rough average for English text).

    Args:
        text: The input text.

    Returns:
        Estimated integer token count.
    """
    word_count = len(text.split())
    return int(word_count * 1.3)


def count_tokens(text: str, model: str) -> int:
    """
    Count tokens in text, choosing the best strategy for the given model.

    Args:
        text: Input text to count.
        model: Model identifier string.

    Returns:
        Integer token count.
    """
    if not text:
        return 0
    if _is_openai_model(model):
        return _count_tokens_tiktoken(text, model)
    return _count_tokens_heuristic(text)


# ---------------------------------------------------------------------------
# Cost estimation and calculation
# ---------------------------------------------------------------------------

def estimate_cost(model: str, input_text: str, expected_output_tokens: int = 500) -> float:
    """
    Estimate the USD cost of a tool call before execution.

    This is used by the budget enforcer to reserve funds before making
    the actual API call.

    Args:
        model: Model identifier for pricing lookup.
        input_text: The input/prompt text to estimate tokens for.
        expected_output_tokens: Upper-bound estimate of output tokens.

    Returns:
        Estimated cost in USD.
    """
    input_price, output_price = _get_model_pricing(model)
    input_tokens = count_tokens(input_text, model)
    cost = (input_tokens / 1000.0) * input_price + (expected_output_tokens / 1000.0) * output_price
    return round(cost, 8)


def calculate_actual_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """
    Calculate the actual USD cost after a tool call completes.

    Args:
        model: Model identifier for pricing lookup.
        input_tokens: Actual input token count.
        output_tokens: Actual output token count.

    Returns:
        Actual cost in USD.
    """
    input_price, output_price = _get_model_pricing(model)
    cost = (input_tokens / 1000.0) * input_price + (output_tokens / 1000.0) * output_price
    return round(cost, 8)
