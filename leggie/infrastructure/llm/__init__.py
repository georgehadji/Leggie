"""LLM infrastructure — provider adapters and decorators.

Split into sub-modules per BUILD_PLAN §3:
  base.py             — BaseLLMProvider ABC, error hierarchy
  adapters/           — provider-specific adapters (OpenRouter, Anthropic, OpenAI)
  decorators.py       — retry, cache, and resilience decorators
  schema_format.py    — Pydantic → strict JSON Schema conversion
  structured_parser.py — Centralised parse/normalize/repair ladder
"""

from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)

# ── Constants for structured-output retry ───────────────────────────
_MAX_TRUNCATION_RETRY_TOKENS = 16_384  # ceiling for doubled max_tokens
_REPAIR_PROMPT_TEMPLATE = (
    "The following content was not valid JSON matching this schema. "
    "Return ONLY valid JSON that conforms to the schema.\n\n"
    "Schema: {schema_name}\n\n"
    "Malformed content:\n{content}"
)

# Offline allowlist of known-valid OpenRouter model IDs (fallback when API unreachable)
_OFFLINE_MODEL_ALLOWLIST: set[str] = {
    # Google
    "google/gemini-2.5-flash",
    "google/gemini-2.5-flash-lite",
    "google/gemini-2.5-pro",
    "google/gemini-3-flash-preview",
    # Anthropic
    "anthropic/claude-sonnet-4",
    "anthropic/claude-sonnet-4.6",
    "anthropic/claude-opus-4",
    "anthropic/claude-opus-4.8",
    # DeepSeek
    "deepseek/deepseek-v3.2",
    # OpenAI
    "openai/gpt-4o",
    "openai/gpt-4o-mini",
    "openai/gpt-5-mini",
    # Meta
    "meta-llama/llama-3.3-70b",
    # Mistral
    "mistral/mistral-large-2411",
    # Qwen
    "qwen/qwen-2.5-72b",
}


async def validate_model_ids(
    api_key: str,
    model_ids: list[str],
    base_url: str = "https://openrouter.ai/api/v1",
    use_live: bool = True,
) -> list[str]:
    """Validate model IDs against the OpenRouter catalog.

    Args:
        api_key: OpenRouter API key for querying /models.
        model_ids: List of model IDs to validate.
        base_url: OpenRouter base URL.
        use_live: If True, query the live /models endpoint; fall back to offline allowlist.

    Returns:
        List of invalid model IDs (empty means all are valid).
    """
    if not model_ids:
        return []

    if use_live and api_key:
        try:
            import httpx
            headers = {
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "https://github.com/georgehadji/Leggie",
            }
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(f"{base_url}/models", headers=headers)

            if resp.status_code == 200:
                data = resp.json()
                live_ids = {m.get("id", "") for m in data.get("data", [])}
                return [m for m in model_ids if m not in live_ids]
            # Fall back to allowlist
        except Exception:
            pass

    # Fallback: check against offline allowlist
    return [m for m in model_ids if m not in _OFFLINE_MODEL_ALLOWLIST]


# Legacy adapter — uses OpenRouter as primary provider
class LLMAdapter(LLMPort):
    """Concrete LLM adapter — uses OpenRouter as primary provider."""

    def __init__(
        self,
        openrouter_key: str = "",
        openrouter_base_url: str = "https://openrouter.ai/api/v1",
        default_model: str = "google/gemini-2.5-flash",
        validate_on_init: bool = True,
    ) -> None:
        if not openrouter_key:
            raise LLMConfigurationError("OpenRouter API key not configured")
        # Quick offline allowlist check at init time (FX3)
        if validate_on_init and default_model and default_model not in _OFFLINE_MODEL_ALLOWLIST:
            raise LLMConfigurationError(
                f"Unknown model ID '{default_model}'. "
                f"This model is not in the known allowlist. "
                f"Check config/settings.py or LEGGIE_LLM__OPENROUTER_DEFAULT_MODEL env var."
            )
        from leggie.infrastructure.rate_limiter import RateLimiter
        self._provider: BaseLLMProvider = OpenRouterProvider(
            api_key=openrouter_key,
            base_url=openrouter_base_url,
            default_model=default_model,
            rate_limiter=RateLimiter(max_rate=5.0),
        )
        self._default_model = default_model
        self._openrouter_key = openrouter_key
        self._openrouter_base_url = openrouter_base_url

    async def validate_default_model(self) -> None:
        """Validate that the configured default model exists on OpenRouter.

        Raises LLMConfigurationError with a clear message if invalid.
        """
        invalid = await validate_model_ids(
            api_key=self._openrouter_key,
            model_ids=[self._default_model],
            base_url=self._openrouter_base_url,
        )
        if invalid:
            raise LLMConfigurationError(
                f"Invalid model ID '{self._default_model}'. "
                f"This model was not found in the OpenRouter catalog. "
                f"Check config/settings.py or LEGGIE_LLM__OPENROUTER_DEFAULT_MODEL env var."
            )

    async def generate(self, request: LLMRequest) -> LLMResponse:
        response: LLMResponse = await self._provider.generate(request)
        return response

    async def generate_structured(self, request: LLMRequest, schema: type) -> tuple[Any, LLMResponse]:
        """Generate a structured response using json_schema strict mode.

        Retry ladder:
        1. Try ``json_schema`` strict mode.
        2. On provider 400 (model doesn't support json_schema), fall back to
           ``json_object`` mode.
        3. If parse fails AND ``finish_reason == "length"``, retry with
           ``max_tokens`` doubled (capped at ``_MAX_TRUNCATION_RETRY_TOKENS``).
        4. If parse still fails, attempt a repair round (feed raw content back
           with terse instruction).
        """
        from dataclasses import replace
        from leggie.infrastructure.llm.schema_format import pydantic_to_json_schema
        from leggie.infrastructure.llm.structured_parser import (
            StructuredResponseParser,
        )

        parser = StructuredResponseParser()

        # ── Attempt 1: json_schema strict mode ────────────────────
        try:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "strict": True,
                    "schema": pydantic_to_json_schema(schema),
                },
            }
            req = replace(request, response_format=response_format)
            response = await self.generate(req)
            return parser.parse(response.content, schema), response
        except (LLMError, ValueError) as exc:
            if isinstance(exc, LLMError) and ("400" in str(exc) or "Bad Request" in str(exc)):
                logger.warning(
                    "json_schema rejected, falling back to json_object: %s", exc
                )

        # ── Attempt 2: json_object mode (fallback) ────────────────
        try:
            req = replace(request, response_format={"type": "json_object"})
            response = await self.generate(req)
            return parser.parse(response.content, schema), response
        except (LLMError, ValueError):
            pass

        # ── Attempt 3: truncation retry if finish_reason=length ───
        if response and response.finish_reason == "length":
            logger.info(
                "Response truncated (finish_reason=length, %d tokens). "
                "Retrying with doubled max_tokens.",
                request.max_tokens,
            )
            doubled = min(request.max_tokens * 2, _MAX_TRUNCATION_RETRY_TOKENS)
            retry_req = replace(
                request,
                max_tokens=doubled,
                response_format={"type": "json_object"},
            )
            try:
                response = await self.generate(retry_req)
                return parser.parse(response.content, schema), response
            except (LLMError, ValueError):
                pass

        # ── Attempt 4: repair round as last resort ────────────────
        try:
            content_to_repair = response.content if response else ""
            if content_to_repair:
                repair_prompt = _REPAIR_PROMPT_TEMPLATE.format(
                    schema_name=schema.__name__,
                    content=content_to_repair[:4000],
                )
                repair_req = LLMRequest(
                    prompt=repair_prompt,
                    system_prompt=(
                        "You are a JSON repair assistant. "
                        "Return ONLY valid JSON."
                    ),
                    max_tokens=min(
                        request.max_tokens * 2,
                        _MAX_TRUNCATION_RETRY_TOKENS,
                    ),
                    response_format={"type": "json_object"},
                )
                response = await self.generate(repair_req)
                obj = parser.parse(response.content, schema)
                return obj, response
        except (LLMError, ValueError):
            pass

        # ── All attempts exhausted -> degrade ─────────────────────
        raise LLMError(
            f"Failed to parse structured response after all retries "
            f"for schema {schema.__name__}"
        )

    async def count_tokens(self, text: str, model: str | None = None) -> int:
        count: int = await self._provider.count_tokens(text, model)
        return count

__all__ = [
    "BaseLLMProvider",
    "LLMAdapter",
    "LLMError", "LLMConfigurationError", "LLMTimeoutError", "LLMRateLimitError",
    "LLMRateLimitError", "BudgetExceededError",
    "OpenRouterProvider",
    "StructuredResponseParser",
    "pydantic_to_json_schema",
    "with_retry", "with_cache",
]
