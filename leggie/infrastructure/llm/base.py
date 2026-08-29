"""Base LLM types — abstract provider.

The error hierarchy (LLMError and subclasses) moved to
leggie.application.ports.llm (IMPL-1 Group C, 2026-08-10): callers across
layer boundaries are expected to catch these, which makes them part of the
port's contract, not an infrastructure implementation detail.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from leggie.application.ports.llm import LLMRequest, LLMResponse


class BaseLLMProvider(ABC):
    """Abstract base for provider-specific LLM adapters."""

    @abstractmethod
    async def generate(self, request: LLMRequest) -> LLMResponse: ...

    @abstractmethod
    async def count_tokens(self, text: str, model: str | None = None) -> int: ...
