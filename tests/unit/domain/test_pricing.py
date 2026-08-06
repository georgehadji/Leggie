"""Tests for domain pricing module — ModelPrice, estimate_cost, get_model_price."""
from __future__ import annotations

from leggie.domain.pricing import MODEL_PRICES, estimate_cost, get_model_price


class TestModelPrice:
    """ModelPrice lookups."""

    def test_known_model(self):
        price = get_model_price("google/gemini-2.5-flash")
        assert price.input_per_1m == 0.30
        assert price.output_per_1m == 2.50
        assert price.cached_input_per_1m == 0.03

    def test_unknown_model_fallback(self):
        price = get_model_price("nonexistent/model")
        assert price.input_per_1m == 5.00  # Conservative fallback
        assert price.output_per_1m == 15.00

    def test_all_known_models_in_map(self):
        models = [
            "google/gemini-2.5-flash-lite",
            "google/gemini-2.5-flash",
            "google/gemini-2.5-pro",
            "anthropic/claude-sonnet-4.6",
            "openai/gpt-4o-mini",
            "deepseek/deepseek-v3.2",
        ]
        for m in models:
            assert m in MODEL_PRICES, f"{m} missing from MODEL_PRICES"
            assert get_model_price(m) is MODEL_PRICES[m]


class TestEstimateCost:
    """Cost estimation."""

    def test_gemini_flash_basic(self):
        cost = estimate_cost("google/gemini-2.5-flash", 1000, 1000, 0)
        expected = 0.30 * 1000 / 1_000_000 + 2.50 * 1000 / 1_000_000
        assert cost == expected

    def test_gemini_flash_output_dominated(self):
        """Output is 8.3× more expensive than input for flash."""
        input_cost = estimate_cost("google/gemini-2.5-flash", 1000, 0)
        output_cost = estimate_cost("google/gemini-2.5-flash", 0, 1000)
        assert output_cost > input_cost * 8

    def test_cached_tokens_cheaper(self):
        cost_no_cache = estimate_cost("google/gemini-2.5-flash", 2000, 1000, 0)
        cost_cached = estimate_cost("google/gemini-2.5-flash", 2000, 1000, 1500)
        assert cost_cached < cost_no_cache, "Cached tokens should be cheaper"

    def test_unknown_model_expensive_fallback(self):
        cost = estimate_cost("unknown/model", 1000, 1000)
        assert cost > 0.01, "Unknown model should use expensive fallback"

    def test_zero_tokens(self):
        cost = estimate_cost("google/gemini-2.5-flash", 0, 0)
        assert cost == 0.0
