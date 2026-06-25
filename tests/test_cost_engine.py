"""
Tests for the Cost Engine module.

Tests cover:
- Cost estimation for OpenAI models (gpt-4o, gpt-3.5-turbo, gpt-4o-mini)
- Cost estimation for local models (all return 0)
- Unknown model handling
- Empty and large input handling
- Actual cost calculation accuracy
- Token counting: tiktoken vs heuristic
- Pricing cache behavior
- Missing pricing file handling
- Custom pricing overrides

Run with: pytest tests/test_cost_engine.py -v
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from agentfence.cost_engine import (
    _count_tokens_heuristic,
    _is_openai_model,
    _load_pricing,
    calculate_actual_cost,
    count_tokens,
    estimate_cost,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def reset_pricing_cache() -> None:
    """Reset the in-memory pricing cache before a test."""
    import agentfence.cost_engine as ce
    ce._pricing_cache = None
    ce._pricing_cache_ts = 0.0
    yield
    ce._pricing_cache = None
    ce._pricing_cache_ts = 0.0


@pytest.fixture
def pricing_file(tmp_path: Path) -> Path:
    """Create a temporary pricing.json with test data."""
    pricing_data = {
        "models": {
            "gpt-4o": {"input_per_1k": 0.005, "output_per_1k": 0.015, "provider": "openai"},
            "gpt-4o-mini": {"input_per_1k": 0.00015, "output_per_1k": 0.0006, "provider": "openai"},
            "gpt-3.5-turbo": {"input_per_1k": 0.0005, "output_per_1k": 0.0015, "provider": "openai"},
            "local/ollama/llama3": {"input_per_1k": 0.0, "output_per_1k": 0.0, "provider": "local"},
            "local/ollama/mistral": {"input_per_1k": 0.0, "output_per_1k": 0.0, "provider": "local"},
            "local/llama-cpp": {"input_per_1k": 0.0, "output_per_1k": 0.0, "provider": "local"},
        }
    }
    f = tmp_path / "pricing.json"
    f.write_text(json.dumps(pricing_data), encoding="utf-8")
    return f


# ---------------------------------------------------------------------------
# OpenAI model estimation
# ---------------------------------------------------------------------------


class TestEstimateCostOpenaiModels:
    """Test cost estimation for OpenAI models."""

    def test_estimate_cost_gpt4o(self, tmp_path: Path, reset_pricing_cache: None) -> None:
        pricing_data = {
            "models": {
                "gpt-4o": {"input_per_1k": 0.005, "output_per_1k": 0.015, "provider": "openai"},
            }
        }
        f = tmp_path / "pricing.json"
        f.write_text(json.dumps(pricing_data), encoding="utf-8")
        with patch("agentfence.cost_engine.get_config") as mock_cfg:
            mock_cfg.return_value.budget.pricing_path = f
            mock_cfg.return_value.budget.pricing_cache_ttl_sec = 300
            mock_cfg.return_value.budget.custom_pricing_overrides = {}
            cost = estimate_cost("gpt-4o", "Hello world, this is a test prompt.", 100)
            assert cost > 0.0

    def test_estimate_cost_gpt35_turbo(self, tmp_path: Path, reset_pricing_cache: None) -> None:
        pricing_data = {
            "models": {
                "gpt-3.5-turbo": {"input_per_1k": 0.0005, "output_per_1k": 0.0015, "provider": "openai"},
            }
        }
        f = tmp_path / "pricing.json"
        f.write_text(json.dumps(pricing_data), encoding="utf-8")
        with patch("agentfence.cost_engine.get_config") as mock_cfg:
            mock_cfg.return_value.budget.pricing_path = f
            mock_cfg.return_value.budget.pricing_cache_ttl_sec = 300
            mock_cfg.return_value.budget.custom_pricing_overrides = {}
            cost = estimate_cost("gpt-3.5-turbo", "Short prompt", 50)
            assert cost > 0.0

    def test_estimate_cost_gpt4o_mini(self, tmp_path: Path, reset_pricing_cache: None) -> None:
        pricing_data = {
            "models": {
                "gpt-4o-mini": {"input_per_1k": 0.00015, "output_per_1k": 0.0006, "provider": "openai"},
            }
        }
        f = tmp_path / "pricing.json"
        f.write_text(json.dumps(pricing_data), encoding="utf-8")
        with patch("agentfence.cost_engine.get_config") as mock_cfg:
            mock_cfg.return_value.budget.pricing_path = f
            mock_cfg.return_value.budget.pricing_cache_ttl_sec = 300
            mock_cfg.return_value.budget.custom_pricing_overrides = {}
            cost = estimate_cost("gpt-4o-mini", "Test", 100)
            assert cost > 0.0

    def test_estimate_cost_scales_with_output_tokens(
        self, tmp_path: Path, reset_pricing_cache: None
    ) -> None:
        pricing_data = {
            "models": {
                "gpt-4o": {"input_per_1k": 0.005, "output_per_1k": 0.015, "provider": "openai"},
            }
        }
        f = tmp_path / "pricing.json"
        f.write_text(json.dumps(pricing_data), encoding="utf-8")
        with patch("agentfence.cost_engine.get_config") as mock_cfg:
            mock_cfg.return_value.budget.pricing_path = f
            mock_cfg.return_value.budget.pricing_cache_ttl_sec = 300
            mock_cfg.return_value.budget.custom_pricing_overrides = {}
            cost_100 = estimate_cost("gpt-4o", "Hello", 100)
            cost_500 = estimate_cost("gpt-4o", "Hello", 500)
            assert cost_500 > cost_100


# ---------------------------------------------------------------------------
# Local model estimation
# ---------------------------------------------------------------------------


class TestEstimateCostLocalModels:
    """Test that local models always return $0 cost."""

    def test_estimate_cost_local_models(self, reset_pricing_cache: None) -> None:
        for model in ["local/ollama/llama3", "local/ollama/mistral", "local/llama-cpp"]:
            cost = estimate_cost(model, "Any input text here", 500)
            assert cost == 0.0, f"Expected 0.0 for {model}, got {cost}"


# ---------------------------------------------------------------------------
# Unknown model handling
# ---------------------------------------------------------------------------


class TestEstimateCostUnknownModel:
    """Test graceful handling of unknown models."""

    def test_estimate_cost_unknown_model(self, reset_pricing_cache: None) -> None:
        cost = estimate_cost("totally-unknown-model-v99", "Hello", 100)
        assert cost == 0.0

    def test_estimate_cost_unknown_model_no_crash(self, reset_pricing_cache: None) -> None:
        # Should never raise, even with empty string
        cost = estimate_cost("", "Hello", 100)
        assert cost == 0.0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEstimateCostEdgeCases:
    """Test edge cases for cost estimation."""

    def test_estimate_cost_empty_input(self, reset_pricing_cache: None) -> None:
        cost = estimate_cost("gpt-4o", "", 100)
        # Empty input = 0 input tokens, but output tokens still estimated
        assert cost >= 0.0

    def test_estimate_cost_large_input(self, reset_pricing_cache: None) -> None:
        large_text = "This is a test sentence. " * 10000
        cost = estimate_cost("gpt-4o", large_text, 500)
        assert cost > 0.0


# ---------------------------------------------------------------------------
# Actual cost calculation
# ---------------------------------------------------------------------------


class TestCalculateActualCost:
    """Test actual cost calculation with real token counts."""

    def test_calculate_actual_cost_accuracy(self, reset_pricing_cache: None) -> None:
        # gpt-4o: $0.005/1K input, $0.015/1K output
        # 1000 input tokens = $0.005, 500 output tokens = $0.0075
        # Total = $0.0125
        with patch("agentfence.cost_engine.get_config") as mock_cfg:
            from pathlib import Path
            pricing_data = {
                "models": {
                    "gpt-4o": {"input_per_1k": 0.005, "output_per_1k": 0.015, "provider": "openai"},
                }
            }
            import json
            f = Path("test_pricing_temp.json")
            f.write_text(json.dumps(pricing_data))
            mock_cfg.return_value.budget.pricing_path = f
            mock_cfg.return_value.budget.pricing_cache_ttl_sec = 300
            mock_cfg.return_value.budget.custom_pricing_overrides = {}
            cost = calculate_actual_cost("gpt-4o", 1000, 500)
            f.unlink(missing_ok=True)
        expected = (1000 / 1000) * 0.005 + (500 / 1000) * 0.015
        assert abs(cost - expected) < 0.0001

    def test_calculate_actual_cost_zero_tokens(self, reset_pricing_cache: None) -> None:
        cost = calculate_actual_cost("gpt-4o", 0, 0)
        assert cost == 0.0

    def test_calculate_actual_cost_local_model(self, reset_pricing_cache: None) -> None:
        cost = calculate_actual_cost("local/ollama/llama3", 1000, 500)
        assert cost == 0.0


# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------


class TestTokenCounting:
    """Test token counting functions."""

    def test_token_counting_tiktoken_vs_heuristic(self) -> None:
        text = "Hello world, this is a test of token counting."
        # Both should return positive integers
        openai_tokens = count_tokens(text, "gpt-4o")
        local_tokens = count_tokens(text, "local/ollama/llama3")
        assert openai_tokens > 0
        assert local_tokens > 0

    def test_token_counting_empty_text(self) -> None:
        assert count_tokens("", "gpt-4o") == 0
        assert count_tokens("", "local/ollama/llama3") == 0

    def test_token_counting_openai_model(self) -> None:
        tokens = count_tokens("Hello world", "gpt-4o")
        assert tokens > 0

    def test_token_counting_local_model(self) -> None:
        tokens = count_tokens("Hello world", "local/ollama/llama3")
        assert tokens > 0

    def test_is_openai_model(self) -> None:
        assert _is_openai_model("gpt-4o") is True
        assert _is_openai_model("gpt-3.5-turbo") is True
        assert _is_openai_model("local/ollama/llama3") is False
        assert _is_openai_model("unknown-model") is False


# ---------------------------------------------------------------------------
# Pricing cache
# ---------------------------------------------------------------------------


class TestPricingCache:
    """Test pricing cache behavior."""

    def test_pricing_cache_ttl(self, tmp_path: Path, reset_pricing_cache: None) -> None:
        """Cache should be used within TTL."""
        pricing_data = {
            "models": {
                "test-model": {"input_per_1k": 0.001, "output_per_1k": 0.002, "provider": "test"},
            }
        }
        f = tmp_path / "pricing.json"
        f.write_text(json.dumps(pricing_data), encoding="utf-8")
        with patch("agentfence.cost_engine.get_config") as mock_cfg:
            mock_cfg.return_value.budget.pricing_path = f
            mock_cfg.return_value.budget.pricing_cache_ttl_sec = 300
            mock_cfg.return_value.budget.custom_pricing_overrides = {}
            # First call loads from disk
            result1 = _load_pricing()
            assert "test-model" in result1
            # Second call should use cache
            result2 = _load_pricing()
            assert result1 is result2  # Same object reference

    def test_pricing_cache_reset(self, tmp_path: Path, reset_pricing_cache: None) -> None:
        """Force reload should bypass cache."""
        pricing_data = {
            "models": {
                "test-model": {"input_per_1k": 0.001, "output_per_1k": 0.002, "provider": "test"},
            }
        }
        f = tmp_path / "pricing.json"
        f.write_text(json.dumps(pricing_data), encoding="utf-8")
        with patch("agentfence.cost_engine.get_config") as mock_cfg:
            mock_cfg.return_value.budget.pricing_path = f
            mock_cfg.return_value.budget.pricing_cache_ttl_sec = 300
            mock_cfg.return_value.budget.custom_pricing_overrides = {}
            result1 = _load_pricing()
            result2 = _load_pricing(force_reload=True)
            assert result1 is not result2  # Different objects
            assert "test-model" in result2


# ---------------------------------------------------------------------------
# Missing pricing file
# ---------------------------------------------------------------------------


class TestMissingPricingFile:
    """Test graceful handling of missing pricing file."""

    def test_missing_pricing_file_graceful(self, tmp_path: Path, reset_pricing_cache: None) -> None:
        """Missing pricing file should not crash — returns empty pricing."""
        fake_path = tmp_path / "nonexistent_pricing.json"
        with patch("agentfence.cost_engine.get_config") as mock_cfg:
            mock_cfg.return_value.budget.pricing_path = fake_path
            mock_cfg.return_value.budget.pricing_cache_ttl_sec = 300
            mock_cfg.return_value.budget.custom_pricing_overrides = {}
            result = _load_pricing(force_reload=True)
            assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Custom pricing overrides
# ---------------------------------------------------------------------------


class TestCustomPricingOverride:
    """Test custom pricing overrides from config."""

    def test_custom_pricing_override(self, tmp_path: Path, reset_pricing_cache: None) -> None:
        """Custom overrides should appear in loaded pricing."""
        pricing_data = {"models": {}}
        f = tmp_path / "pricing.json"
        f.write_text(json.dumps(pricing_data), encoding="utf-8")
        custom = {
            "my-custom-model": {"input_per_1k": 0.001, "output_per_1k": 0.003}
        }
        with patch("agentfence.cost_engine.get_config") as mock_cfg:
            mock_cfg.return_value.budget.pricing_path = f
            mock_cfg.return_value.budget.pricing_cache_ttl_sec = 300
            mock_cfg.return_value.budget.custom_pricing_overrides = custom
            result = _load_pricing(force_reload=True)
            assert "my-custom-model" in result

    def test_custom_pricing_unknown_model_fallback(
        self, tmp_path: Path, reset_pricing_cache: None
    ) -> None:
        """Unknown models should return $0 cost even with custom overrides."""
        pricing_data = {"models": {}}
        f = tmp_path / "pricing.json"
        f.write_text(json.dumps(pricing_data), encoding="utf-8")
        with patch("agentfence.cost_engine.get_config") as mock_cfg:
            mock_cfg.return_value.budget.pricing_path = f
            mock_cfg.return_value.budget.pricing_cache_ttl_sec = 300
            mock_cfg.return_value.budget.custom_pricing_overrides = {}
            cost = estimate_cost("unknown-model-xyz", "Hello", 100)
            assert cost == 0.0
