"""LLM Infrastructure — provider adapters + decorators.

Implements the LLM port for Anthropic, OpenAI, and Google providers.
Stacks resilience decorators: retry → circuit-breaker → cache → budget.
"""

from __future__ import annotations

import hashlib
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from functools import wraps
from typing import Any

from leggie.application.ports.llm import LLMPort, LLMRequest, LLMResponse
from leggie.domain.models import ModelTier

# ── Errors ──────────────────────────────────────────────────────────────────────


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


# ── Base Provider ──────────────────────────────────────────────────────────────


class BaseLLMProvider(ABC):
    """Abstract base for provider-specific LLM adapters."""

    @abstractmethod
    async def generate(self, request: LLMRequest) -> LLMResponse: ...

    @abstractmethod
    async def count_tokens(self, text: str, model: str | None = None) -> int: ...


class AnthropicProvider(BaseLLMProvider):
    """Anthropic Claude provider adapter."""

    def __init__(self, api_key: str, default_model: str = "claude-sonnet-4-20250514") -> None:
        if not api_key:
            raise LLMConfigurationError("Anthropic API key not configured")
        self._api_key = api_key
        self._default_model = default_model

    async def generate(self, request: LLMRequest) -> LLMResponse:
        try:
            import httpx
        except ImportError:
            raise LLMError("httpx not installed")

        model = request.model or self._default_model
        start = time.monotonic()

        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": request.max_tokens,
            "messages": [{"role": "user", "content": request.prompt}],
        }
        if request.system_prompt:
            body["system"] = request.system_prompt
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.seed is not None:
            body["seed"] = request.seed

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=body,
            )
            elapsed = (time.monotonic() - start) * 1000

        if resp.status_code == 429:
            raise LLMRateLimitError(f"Anthropic rate limited: {resp.text}")
        if resp.status_code != 200:
            raise LLMError(f"Anthropic API error {resp.status_code}: {resp.text}")

        data = resp.json()
        content = data.get("content", [{}])[0].get("text", "")
        usage = data.get("usage", {})
        return LLMResponse(
            content=content,
            model=model,
            tier_used=ModelTier.BUDGET,
            usage={"prompt_tokens": usage.get("input_tokens", 0), "completion_tokens": usage.get("output_tokens", 0)},
            latency_ms=elapsed,
        )

    async def count_tokens(self, text: str, model: str | None = None) -> int:
        try:
            import httpx
        except ImportError:
            raise LLMError("httpx not installed")

        model = model or self._default_model
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages/count_tokens",
                headers=headers,
                json={"model": model, "messages": [{"role": "user", "content": text}]},
            )
        if resp.status_code != 200:
            raise LLMError(f"Token count failed: {resp.text}")
        return resp.json().get("input_tokens", 0)


class OpenAIProvider(BaseLLMProvider):
    """OpenAI provider adapter."""

    def __init__(self, api_key: str, default_model: str = "gpt-4o") -> None:
        if not api_key:
            raise LLMConfigurationError("OpenAI API key not configured")
        self._api_key = api_key
        self._default_model = default_model

    async def generate(self, request: LLMRequest) -> LLMResponse:
        try:
            import httpx
        except ImportError:
            raise LLMError("httpx not installed")

        model = request.model or self._default_model
        start = time.monotonic()

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "content-type": "application/json",
        }
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": request.max_tokens,
            "messages": [{"role": "user", "content": request.prompt}],
        }
        if request.system_prompt:
            body["messages"].insert(0, {"role": "system", "content": request.system_prompt})
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.seed is not None:
            body["seed"] = request.seed

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json=body,
            )
            elapsed = (time.monotonic() - start) * 1000

        if resp.status_code == 429:
            raise LLMRateLimitError(f"OpenAI rate limited: {resp.text}")
        if resp.status_code != 200:
            raise LLMError(f"OpenAI API error {resp.status_code}: {resp.text}")

        data = resp.json()
        choice = data.get("choices", [{}])[0]
        content = choice.get("message", {}).get("content", "")
        usage = data.get("usage", {})
        return LLMResponse(
            content=content,
            model=model,
            tier_used=ModelTier.BUDGET,
            usage={"prompt_tokens": usage.get("prompt_tokens", 0), "completion_tokens": usage.get("completion_tokens", 0)},
            latency_ms=elapsed,
        )

    async def count_tokens(self, text: str, model: str | None = None) -> int:
        try:
            import httpx
        except ImportError:
            raise LLMError("httpx not installed")

        model = model or self._default_model
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "content-type": "application/json",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json={"model": model, "messages": [{"role": "user", "content": text}], "max_tokens": 1},
            )
        if resp.status_code != 200:
            raise LLMError(f"Token count failed: {resp.text}")
        return resp.json().get("usage", {}).get("prompt_tokens", 0)


class OpenRouterProvider(BaseLLMProvider):
    """OpenRouter provider adapter — single API for all models.

    Uses OpenAI-compatible chat completions API with the OpenRouter base URL.
    Models are prefixed: anthropic/claude-sonnet-4, google/gemini-2.5-pro, etc.

    OpenRouter-specific features:
      - Prompt caching via transforms: ["cache"] (O6 cost optimization)
      - Reasoning tokens for :thinking variants
      - Provider fallback handled server-side
      - App attribution via HTTP-Referer + X-Title
    """

    def __init__(self, api_key: str, base_url: str = "https://openrouter.ai/api/v1",
                 default_model: str = "anthropic/claude-sonnet-4-20250514") -> None:
        if not api_key:
            raise LLMConfigurationError("OpenRouter API key not configured")
        self._api_key = api_key
        self._base_url = base_url
        self._default_model = default_model

    async def generate(self, request: LLMRequest) -> LLMResponse:
        try:
            import httpx
        except ImportError:
            raise LLMError("httpx not installed")

        model = request.model or self._default_model
        start = time.monotonic()

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "HTTP-Referer": "https://github.com/georgehadji/Leggie",
            "X-Title": "Leggie",
            "content-type": "application/json",
        }
        messages: list[dict] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})

        body: dict[str, Any] = {
            "model": model,
            "max_tokens": request.max_tokens,
            "messages": messages,
            "temperature": request.temperature if request.temperature is not None else 0.7,
            # OpenRouter prompt caching — caches invariant context across API calls (O6)
            "transforms": ["cache"],
        }
        if request.seed is not None:
            body["seed"] = request.seed

        # Enable reasoning tokens for :thinking models
        if ":thinking" in model:
            body["include_reasoning"] = True

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{self._base_url}/chat/completions",
                headers=headers,
                json=body,
            )
            elapsed = (time.monotonic() - start) * 1000

        if resp.status_code == 429:
            raise LLMRateLimitError(f"OpenRouter rate limited: {resp.text}")
        if resp.status_code != 200:
            raise LLMError(f"OpenRouter API error {resp.status_code}: {resp.text}")

        data = resp.json()
        choice = data.get("choices", [{}])[0]
        content = choice.get("message", {}).get("content", "")
        usage = data.get("usage", {})
        return LLMResponse(
            content=content,
            model=model,
            tier_used=ModelTier.BUDGET,
            usage={
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
            },
            latency_ms=elapsed,
        )

    async def count_tokens(self, text: str, model: str | None = None) -> int:
        # OpenRouter doesn't have a token-count endpoint; estimate
        return len(text) // 4 + 1
    """Google Gemini provider adapter."""

    def __init__(self, api_key: str, default_model: str = "gemini-2.5-pro") -> None:
        if not api_key:
            raise LLMConfigurationError("Google API key not configured")
        self._api_key = api_key
        self._default_model = default_model

    async def generate(self, request: LLMRequest) -> LLMResponse:
        try:
            import httpx
        except ImportError:
            raise LLMError("httpx not installed")

        model = request.model or self._default_model
        start = time.monotonic()

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self._api_key}"
        body: dict[str, Any] = {
            "contents": [{"parts": [{"text": request.prompt}]}],
            "generationConfig": {
                "maxOutputTokens": request.max_tokens,
                "temperature": request.temperature,
            },
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, json=body)
            elapsed = (time.monotonic() - start) * 1000

        if resp.status_code == 429:
            raise LLMRateLimitError(f"Google rate limited: {resp.text}")
        if resp.status_code != 200:
            raise LLMError(f"Google API error {resp.status_code}: {resp.text}")

        data = resp.json()
        candidates = data.get("candidates", [])
        content = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "") if candidates else ""

        usage = data.get("usageMetadata", {})
        return LLMResponse(
            content=content,
            model=model,
            tier_used=ModelTier.BUDGET,
            usage={"prompt_tokens": usage.get("promptTokenCount", 0), "completion_tokens": usage.get("candidatesTokenCount", 0)},
            latency_ms=elapsed,
        )

    async def count_tokens(self, text: str, model: str | None = None) -> int:
        # Google's token counting requires an API call
        # Simplified fallback: estimate (4 chars ≈ 1 token for Greek/English)
        return len(text) // 4 + 1


# ── Decorators ──────────────────────────────────────────────────────────────────


def with_retry(max_retries: int = 3, base_delay: float = 1.0) -> Callable:
    """Decorator: retry LLM calls on transient failures with exponential backoff."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            import asyncio
            last_exc: Exception | None = None
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except (LLMRateLimitError, LLMTimeoutError) as e:
                    last_exc = e
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        await asyncio.sleep(delay)
            raise last_exc  # type: ignore
        return wrapper
    return decorator


def with_cache(max_size: int = 100) -> Callable:
    """Decorator: simple LRU cache for LLM responses keyed by prompt hash."""
    import functools
    return functools.lru_cache(maxsize=max_size)


# ── LLM Adapter (Port implementation) ────────────────────────────────────────────


class LLMAdapter(LLMPort):
    """Concrete LLM adapter — uses OpenRouter as primary provider."""

    def __init__(
        self,
        openrouter_key: str = "",
        openrouter_base_url: str = "https://openrouter.ai/api/v1",
        default_model: str = "anthropic/claude-sonnet-4-20250514",
    ) -> None:
        if not openrouter_key:
            raise LLMConfigurationError("OpenRouter API key not configured")
        self._provider: BaseLLMProvider = OpenRouterProvider(
            api_key=openrouter_key,
            base_url=openrouter_base_url,
            default_model=default_model,
        )

    @with_retry()
    async def generate(self, request: LLMRequest) -> LLMResponse:
        return await self._provider.generate(request)

    async def generate_structured(self, request: LLMRequest, schema: type) -> tuple[Any, LLMResponse]:
        response = await self.generate(request)
        try:
            import json
            data = json.loads(response.content)
            obj = schema(**data)
            return obj, response
        except Exception as e:
            raise LLMError(f"Failed to parse structured response: {e}")

    async def count_tokens(self, text: str, model: str | None = None) -> int:
        return await self._provider.count_tokens(text, model)
