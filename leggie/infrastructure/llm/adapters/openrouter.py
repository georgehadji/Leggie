"""OpenRouter provider adapter — single API for all models.

Uses OpenAI-compatible chat completions API with the OpenRouter base URL.
Models are prefixed: anthropic/claude-sonnet-4, google/gemini-2.5-pro, etc.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from leggie.application.ports.llm import (
    LLMConfigurationError,
    LLMError,
    LLMRateLimitError,
    LLMRequest,
    LLMResponse,
    LLMTimeoutError,
)
from leggie.infrastructure.llm.base import BaseLLMProvider
from leggie.infrastructure.llm.decorators import with_retry
from leggie.infrastructure.rate_limiter import RateLimiter
from leggie.observability import get_logger

logger = get_logger(__name__)

# Maximum response body size included in error messages (PROD-14).
_MAX_ERROR_BODY_CHARS = 500


class OpenRouterProvider(BaseLLMProvider):
    """OpenRouter provider adapter — single API for all models.

    Features:
      - Reasoning tokens for :thinking variants
      - Rate limiting via injected RateLimiter
      - Provider fallback handled server-side
      - Per-call structured logging with token/cost breakdown
      - Pooled httpx client (PROD-14)
      - Retry-After honouring on 429 (PROD-14)
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://openrouter.ai/api/v1",
        default_model: str = "google/gemini-2.5-flash",
        rate_limiter: RateLimiter | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise LLMConfigurationError("OpenRouter API key not configured")
        self._api_key = api_key
        self._base_url = base_url
        self._default_model = default_model
        self._rate_limiter = rate_limiter or RateLimiter(max_rate=5.0)
        # Container-scoped client avoids a fresh TLS handshake per call (PROD-14).
        # When None, defaults to the pooled client below.
        self._http_client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=30.0, read=120.0, write=30.0, pool=10.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )

    @with_retry()
    async def generate(self, request: LLMRequest) -> LLMResponse:
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
        }
        if request.seed is not None:
            body["seed"] = request.seed
        if ":thinking" in model:
            body["include_reasoning"] = True
        if request.response_format:
            body["response_format"] = request.response_format

        try:
            resp = await self._http_client.post(
                f"{self._base_url}/chat/completions",
                headers=headers,
                json=body,
            )
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(f"OpenRouter request timed out: {exc}") from exc
        except httpx.RequestError as exc:
            raise LLMError(f"OpenRouter request failed: {exc}") from exc
        elapsed = (time.monotonic() - start) * 1000

        if resp.status_code == 429:
            retry_after = _parse_retry_after(resp)
            if retry_after is not None:
                await asyncio.sleep(retry_after)
            raise LLMRateLimitError(
                "OpenRouter rate limited"
                + (f" (retry after {retry_after}s)" if retry_after else "")
            )
        if resp.status_code != 200:
            body_text = resp.text[:_MAX_ERROR_BODY_CHARS]
            raise LLMError(f"OpenRouter API error {resp.status_code}: {body_text}")

        data = resp.json()
        # `.get("choices", [{}])` only supplies the default when the key is
        # ABSENT; a 200 response that carries "choices": [] (moderation
        # blocks, some provider hiccups) leaves choices=[] and [0] raised a
        # raw, unwrapped IndexError instead of the port's LLMError contract.
        choices = data.get("choices", [{}])
        if not choices:
            raise LLMError(f"OpenRouter returned no choices (status {resp.status_code})")
        choice = choices[0]
        content = choice.get("message", {}).get("content") or ""
        finish_reason = choice.get("finish_reason", "stop")
        usage = data.get("usage", {})

        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

        # Parse cached token details (OpenRouter returns usage.prompt_tokens_details.cached_tokens)
        usage_details = usage.get("prompt_tokens_details", {}) or {}
        cached_tokens = usage_details.get("cached_tokens", 0)

        # Estimate cost
        from leggie.domain.pricing import estimate_cost

        estimated_cost = estimate_cost(model, prompt_tokens, completion_tokens, cached_tokens)

        # Structured log line — structlog keyword form so fields render
        logger.info(
            "llm.call",
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached_tokens,
            estimated_cost=round(estimated_cost, 6),
            finish_reason=finish_reason,
            latency_ms=round(elapsed, 1),
        )

        # D11/Phase B: reasoning models bill reasoning tokens under
        # completion_tokens_details, and those do NOT show up in
        # completion_tokens — so a `finish_reason=length` truncation could be
        # driven by reasoning spend that nothing downstream ever sees. Surface
        # reasoning_tokens additively (only when the provider reports a
        # non-zero value: no phantom key, no zero-fill) so a truncation can be
        # attributed to reasoning burn rather than to prompt size.
        # NOTE: budget accounting (budget_guard) and estimate_cost above
        # intentionally still count prompt+completion only; this key is for
        # attribution/visibility, not billing. The $5 cap behaviour is
        # untouched.
        usage_out = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cached_tokens": cached_tokens,
        }
        completion_details = usage.get("completion_tokens_details") or {}
        reasoning_tokens = completion_details.get("reasoning_tokens")
        if reasoning_tokens:
            usage_out["reasoning_tokens"] = reasoning_tokens

        return LLMResponse(
            content=content,
            model=model,
            tier_used=request.tier,
            usage=usage_out,
            finish_reason=finish_reason,
            latency_ms=elapsed,
        )

    async def count_tokens(self, text: str, model: str | None = None) -> int:
        return len(text) // 4 + 1


def _parse_retry_after(resp: httpx.Response) -> float | None:
    """Extract Retry-After header value (seconds). Handles both integer and
    HTTP-date formats, returning a float of seconds or None."""
    raw = resp.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        # Could be an HTTP-date — fall back to a reasonable default
        return 5.0
