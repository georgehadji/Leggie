"""Pricing domain — model price table and cost arithmetic.

Pure domain module: no I/O, no imports from other layers.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPrice:
    """Price for a model — per-token rates in USD.

    All rates are per 1M tokens.
    """

    input_per_1m: float
    output_per_1m: float
    cached_input_per_1m: float | None = None  # None → falls back to input rate


# ── Known model prices (USD per 1M tokens) ───────────────────────────
# Verified against https://openrouter.ai/api/v1/models on 2026-08-01.
# This dict is the SINGLE source of truth for model identity: the
# infrastructure allowlist is derived from its keys, so a model priced here
# is a model Leggie will accept, and nothing else is.
# Refresh with: python tools/refresh_model_prices.py
MODEL_PRICES: dict[str, ModelPrice] = {
    # ── Google ───────────────────────────────────────────────────────
    "google/gemini-2.5-flash-lite": ModelPrice(
        input_per_1m=0.10,
        output_per_1m=0.40,
        cached_input_per_1m=0.01,
    ),
    "google/gemini-2.5-flash": ModelPrice(
        input_per_1m=0.30,
        output_per_1m=2.50,
        cached_input_per_1m=0.03,
    ),
    "google/gemini-2.5-pro": ModelPrice(
        input_per_1m=1.25,
        output_per_1m=10.00,
        cached_input_per_1m=0.125,
    ),
    "google/gemini-3-flash-preview": ModelPrice(
        input_per_1m=0.50,
        output_per_1m=3.00,
        cached_input_per_1m=0.05,
    ),
    "google/gemini-3.1-pro-preview": ModelPrice(
        input_per_1m=2.00,
        output_per_1m=12.00,
        cached_input_per_1m=0.20,
    ),
    "google/gemini-3.5-flash-lite": ModelPrice(
        input_per_1m=0.30,
        output_per_1m=2.50,
        cached_input_per_1m=0.03,
    ),
    "google/gemini-3.6-flash": ModelPrice(
        input_per_1m=1.50,
        output_per_1m=7.50,
        cached_input_per_1m=0.15,
    ),
    # ── Anthropic ────────────────────────────────────────────────────
    "anthropic/claude-haiku-4.5": ModelPrice(
        input_per_1m=1.00,
        output_per_1m=5.00,
        cached_input_per_1m=0.10,
    ),
    "anthropic/claude-sonnet-4": ModelPrice(
        input_per_1m=3.00,
        output_per_1m=15.00,
        cached_input_per_1m=0.30,
    ),
    "anthropic/claude-sonnet-4.6": ModelPrice(
        input_per_1m=3.00,
        output_per_1m=15.00,
        cached_input_per_1m=0.30,
    ),
    "anthropic/claude-sonnet-5": ModelPrice(
        input_per_1m=2.00,
        output_per_1m=10.00,
        cached_input_per_1m=0.20,
    ),
    "anthropic/claude-opus-4": ModelPrice(
        input_per_1m=15.00,
        output_per_1m=75.00,
        cached_input_per_1m=1.50,
    ),
    "anthropic/claude-opus-4.8": ModelPrice(
        input_per_1m=5.00,
        output_per_1m=25.00,
        cached_input_per_1m=0.50,
    ),
    "anthropic/claude-opus-5": ModelPrice(
        input_per_1m=5.00,
        output_per_1m=25.00,
        cached_input_per_1m=0.50,
    ),
    # ── OpenAI ───────────────────────────────────────────────────────
    "openai/gpt-4o": ModelPrice(
        input_per_1m=2.50,
        output_per_1m=10.00,
        cached_input_per_1m=1.25,
    ),
    "openai/gpt-4o-mini": ModelPrice(
        input_per_1m=0.15,
        output_per_1m=0.60,
        cached_input_per_1m=0.075,
    ),
    "openai/gpt-5-mini": ModelPrice(
        input_per_1m=0.25,
        output_per_1m=2.00,
        cached_input_per_1m=0.025,
    ),
    "openai/gpt-5.4": ModelPrice(
        input_per_1m=2.50,
        output_per_1m=15.00,
        cached_input_per_1m=0.25,
    ),
    "openai/gpt-5.6-luna": ModelPrice(
        input_per_1m=0.10,
        output_per_1m=0.60,
        cached_input_per_1m=0.01,
    ),
    "openai/gpt-5.6-terra": ModelPrice(
        input_per_1m=1.00,
        output_per_1m=6.00,
        cached_input_per_1m=0.10,
    ),
    # ── DeepSeek ─────────────────────────────────────────────────────
    "deepseek/deepseek-v3.2": ModelPrice(
        input_per_1m=0.269,
        output_per_1m=0.40,
        cached_input_per_1m=0.134,
    ),
    "deepseek/deepseek-v4-flash-0731": ModelPrice(
        input_per_1m=0.14,
        output_per_1m=0.28,
        cached_input_per_1m=0.003,
    ),
    # ── MoonshotAI ───────────────────────────────────────────────────
    "moonshotai/kimi-k3": ModelPrice(
        input_per_1m=3.00,
        output_per_1m=15.00,
        cached_input_per_1m=0.30,
    ),
    # ── xAI ──────────────────────────────────────────────────────────
    "x-ai/grok-4.5": ModelPrice(
        input_per_1m=2.00,
        output_per_1m=6.00,
        cached_input_per_1m=0.30,
    ),
    # ── Meta / Mistral ───────────────────────────────────────────────
    "meta-llama/llama-3.3-70b-instruct": ModelPrice(
        input_per_1m=0.13,
        output_per_1m=0.40,
    ),
    "mistralai/mistral-large-2512": ModelPrice(
        input_per_1m=0.50,
        output_per_1m=1.50,
        cached_input_per_1m=0.05,
    ),
}

# Conservative fallback for unknown models (assume expensive)
_FALLBACK_PRICE = ModelPrice(input_per_1m=5.00, output_per_1m=15.00)


def get_model_price(model: str) -> ModelPrice:
    """Look up model price, falling back to a conservative default.

    Unknown models are assumed expensive to avoid silent under-budgeting.
    """
    return MODEL_PRICES.get(model, _FALLBACK_PRICE)


def estimate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int = 0,
) -> float:
    """Estimate cost for a model call.

    Args:
        model: Model ID string.
        prompt_tokens: Number of prompt tokens.
        completion_tokens: Number of completion tokens.
        cached_tokens: Number of cached input tokens (billed at cache rate).

    Returns:
        Estimated cost in USD.
    """
    price = get_model_price(model)

    # Cached tokens are billed at the cache rate.
    # Prompt tokens may be negative during budget reconcile (refund); allow it.
    uncached_prompt = prompt_tokens - cached_tokens
    if cached_tokens > 0 and uncached_prompt < 0:
        uncached_prompt = 0

    prompt_cost = (
        uncached_prompt * price.input_per_1m
        + cached_tokens
        * (
            price.cached_input_per_1m
            if price.cached_input_per_1m is not None
            else price.input_per_1m
        )
    ) / 1_000_000

    completion_cost = completion_tokens * price.output_per_1m / 1_000_000

    return round(prompt_cost + completion_cost, 6)
