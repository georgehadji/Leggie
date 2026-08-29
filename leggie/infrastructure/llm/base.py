"""Base LLM types — abstract provider, error hierarchy."""

from __future__ import annotations

from abc import ABC, abstractmethod

from leggie.application.ports.llm import LLMRequest, LLMResponse


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


class BaseLLMProvider(ABC):
    """Abstract base for provider-specific LLM adapters."""

    @abstractmethod
    async def generate(self, request: LLMRequest) -> LLMResponse: ...

    @abstractmethod
    async def count_tokens(self, text: str, model: str | None = None) -> int: ...
