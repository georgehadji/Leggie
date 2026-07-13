"""LLM infrastructure — provider adapters and decorators.

Split into sub-modules per BUILD_PLAN §3:
  base.py             — BaseLLMProvider ABC, error hierarchy
  adapters/           — provider-specific adapters (OpenRouter, Anthropic, OpenAI)
  decorators.py       — retry, cache, and resilience decorators
"""

from dataclasses import replace
from typing import Any

from leggie.application.ports.llm import LLMPort, LLMRequest, LLMResponse
from leggie.infrastructure.llm.adapters.openrouter import OpenRouterProvider
from leggie.infrastructure.llm.base import (
    BaseLLMProvider,
    BudgetExceededError,
    LLMConfigurationError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from leggie.infrastructure.llm.decorators import with_cache, with_retry


# Legacy adapter — uses OpenRouter as primary provider
class LLMAdapter(LLMPort):
    """Concrete LLM adapter — uses OpenRouter as primary provider."""

    def __init__(
        self,
        openrouter_key: str = "",
        openrouter_base_url: str = "https://openrouter.ai/api/v1",
        default_model: str = "openai/gpt-5.6-luna",
    ) -> None:
        if not openrouter_key:
            raise LLMConfigurationError("OpenRouter API key not configured")
        from leggie.infrastructure.rate_limiter import RateLimiter

        self._provider: BaseLLMProvider = OpenRouterProvider(
            api_key=openrouter_key,
            base_url=openrouter_base_url,
            default_model=default_model,
            rate_limiter=RateLimiter(max_rate=5.0),
        )

    async def generate(self, request: LLMRequest) -> LLMResponse:
        return await self._provider.generate(request)

    async def generate_structured(
        self, request: LLMRequest, schema: type
    ) -> tuple[Any, LLMResponse]:
        import json

        req = replace(request, response_format={"type": "json_object"})
        response = await self.generate(req)
        try:
            data = json.loads(response.content)
            # Handle model returning "issues" instead of "findings"
            if isinstance(data, dict) and "issues" in data and "findings" not in data:
                data["findings"] = data.pop("issues")
            obj = schema(**data)
            return obj, response
        except Exception as e:
            raise LLMError(f"Failed to parse structured response: {e}") from e

    async def count_tokens(self, text: str, model: str | None = None) -> int:
        return await self._provider.count_tokens(text, model)


__all__ = [
    "BaseLLMProvider",
    "LLMAdapter",
    "LLMError",
    "LLMConfigurationError",
    "LLMTimeoutError",
    "LLMRateLimitError",
    "LLMRateLimitError",
    "BudgetExceededError",
    "OpenRouterProvider",
    "with_retry",
    "with_cache",
]
