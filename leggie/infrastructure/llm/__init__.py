"""LLM infrastructure — provider adapters and decorators.

Split into sub-modules per BUILD_PLAN §3:
  base.py             — BaseLLMProvider ABC, error hierarchy
  adapters/           — provider-specific adapters (OpenRouter, Anthropic, OpenAI)
  decorators.py       — retry, cache, and resilience decorators
"""

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
class LLMAdapter:
    """Concrete LLM adapter — uses OpenRouter as primary provider."""

    def __init__(
        self,
        openrouter_key: str = "",
        openrouter_base_url: str = "https://openrouter.ai/api/v1",
        default_model: str = "google/gemini-2.5-flash",
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

    async def generate(self, request):
        return await self._provider.generate(request)

    async def generate_structured(self, request, schema):
        import json
        import re
        from dataclasses import replace
        req = replace(request, response_format={"type": "json_object"})
        response = await self.generate(req)
        try:
            content = response.content.strip()
            # Strip markdown code fences if present (```json ... ```)
            fence = re.search(r"```(?:json)?\s*(.*?)```", content, re.DOTALL)
            if fence:
                content = fence.group(1).strip()
            data = json.loads(content)
            # Models often return a bare array; wrap it into the schema's
            # single list-typed field (e.g. LensFindings.findings).
            if isinstance(data, list):
                list_field = next(iter(schema.model_fields), None)
                data = {list_field: data} if list_field else {}
            # Handle model returning "issues" instead of "findings"
            if isinstance(data, dict) and "issues" in data and "findings" not in data:
                data["findings"] = data.pop("issues")
            obj = schema(**data)
            return obj, response
        except Exception as e:
            raise LLMError(f"Failed to parse structured response: {e}")

    async def count_tokens(self, text, model=None):
        return await self._provider.count_tokens(text, model)

__all__ = [
    "BaseLLMProvider",
    "LLMAdapter",
    "LLMError", "LLMConfigurationError", "LLMTimeoutError", "LLMRateLimitError",
    "LLMRateLimitError", "BudgetExceededError",
    "OpenRouterProvider",
    "with_retry", "with_cache",
]
