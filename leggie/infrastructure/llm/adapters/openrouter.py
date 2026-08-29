"""OpenRouter provider adapter — single API for all models.

Uses OpenAI-compatible chat completions API with the OpenRouter base URL.
Models are prefixed: anthropic/claude-sonnet-4, google/gemini-2.5-pro, etc.
"""

from __future__ import annotations

import time
from typing import Any

from leggie.application.ports.llm import LLMRequest, LLMResponse
from leggie.domain.models import ModelTier
from leggie.infrastructure.llm.base import BaseLLMProvider
from leggie.infrastructure.llm.decorators import with_retry
from leggie.infrastructure.rate_limiter import RateLimiter


class OpenRouterProvider(BaseLLMProvider):
    """OpenRouter provider adapter — single API for all models.

    Features:
      - Prompt caching via transforms: ["cache"] (O6 cost optimization)
      - Reasoning tokens for :thinking variants
      - Rate limiting via injected RateLimiter
      - Provider fallback handled server-side
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://openrouter.ai/api/v1",
        default_model: str = "google/gemini-2.5-flash",
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        if not api_key:
            from leggie.infrastructure.llm.base import LLMConfigurationError

            raise LLMConfigurationError("OpenRouter API key not configured")
        self._api_key = api_key
        self._base_url = base_url
        self._default_model = default_model
        self._rate_limiter = rate_limiter or RateLimiter(max_rate=5.0)

    @with_retry()
    async def generate(self, request: LLMRequest) -> LLMResponse:
        try:
            import httpx
        except ImportError:
            from leggie.infrastructure.llm.base import LLMError

            raise LLMError("httpx not installed")

        await self._rate_limiter.acquire()
        model = request.model or self._default_model
        start = time.monotonic()

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "HTTP-Referer": "https://github.com/georgehadji/Leggie",
            "X-Title": "Leggie",
            "content-type": "application/json",
        }
        messages: list[dict[str, Any]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})

        body: dict[str, Any] = {
            "model": model,
            "max_tokens": request.max_tokens,
            "messages": messages,
            "temperature": request.temperature if request.temperature is not None else 0.7,
            "transforms": ["cache"],
        }
        if request.seed is not None:
            body["seed"] = request.seed
        if ":thinking" in model:
            body["include_reasoning"] = True
        if request.response_format:
            body["response_format"] = request.response_format

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{self._base_url}/chat/completions",
                headers=headers,
                json=body,
            )
            elapsed = (time.monotonic() - start) * 1000

        if resp.status_code == 429:
            from leggie.infrastructure.llm.base import LLMRateLimitError

            raise LLMRateLimitError(f"OpenRouter rate limited: {resp.text}")
        if resp.status_code != 200:
            from leggie.infrastructure.llm.base import LLMError

            raise LLMError(f"OpenRouter API error {resp.status_code}: {resp.text}")

        data = resp.json()
        choice = data.get("choices", [{}])[0]
        content = choice.get("message", {}).get("content") or ""
        finish_reason = choice.get("finish_reason", "stop")
        usage = data.get("usage", {})
        return LLMResponse(
            content=content,
            model=model,
            tier_used=ModelTier.BUDGET,
            usage={
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
            },
            finish_reason=finish_reason,
            latency_ms=elapsed,
        )

    async def count_tokens(self, text: str, model: str | None = None) -> int:
        return len(text) // 4 + 1
