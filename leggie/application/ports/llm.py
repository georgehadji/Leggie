"""LLM Port — abstract interface for language model calls.

Provider-agnostic. Implementations wrap Anthropic, OpenAI, Google APIs.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from leggie.domain.models import ModelTier


@dataclass(frozen=True)
class LLMRequest:
    """Request to an LLM provider."""

    prompt: str
    system_prompt: str | None = None
    model: str | None = None
    tier: ModelTier = ModelTier.BUDGET
    max_tokens: int = 4096
    temperature: float = 0.7
    seed: int | None = None
    stop_sequences: list[str] | None = None
    response_format: dict[str, Any] | None = None  # e.g., {"type": "json_object"}


@dataclass(frozen=True)
class LLMResponse:
    """Response from an LLM provider."""

    content: str
    model: str
    tier_used: ModelTier
    usage: dict[str, int]  # {"prompt_tokens": ..., "completion_tokens": ...}
    finish_reason: str = "stop"
    latency_ms: float = 0.0


class LLMPort(ABC):
    """Port for LLM provider calls."""

    @abstractmethod
    async def generate(self, request: LLMRequest) -> LLMResponse:
        """Generate a response from the LLM."""
        ...

    @abstractmethod
    async def generate_structured(
        self, request: LLMRequest, schema: type
    ) -> tuple[Any, LLMResponse]:
        """Generate a structured (typed) response matching the given schema."""
        ...

    @abstractmethod
    async def count_tokens(self, text: str, model: str | None = None) -> int:
        """Count tokens for the given text and model."""
        ...


# IMPL-1 Group C (2026-08-10): moved from infrastructure/llm/base.py. Callers
# across every layer are expected to catch these (cli_handlers.py did, across
# a layer boundary import-linter had to whitelist) — an exception a caller is
# meant to catch is part of the port's contract, not an implementation detail.
# Mirrors this codebase's own ReasonerUnavailableError precedent
# (application/ports/reasoner.py). BaseLLMProvider stays in infrastructure —
# it has no cross-layer callers, it's a provider-adapter-internal ABC.
class LLMError(Exception):
    """Base LLM error."""


class LLMConfigurationError(LLMError):
    """No API key configured for the requested provider."""


class LLMTimeoutError(LLMError):
    """LLM call timed out."""


class LLMRateLimitError(LLMError):
    """Rate limited by provider."""


class BudgetExceededError(LLMError):
    """Budget guard tripped."""
