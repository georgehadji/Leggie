"""LLM infrastructure — provider adapters and decorators.

Split into sub-modules per BUILD_PLAN §3:
  base.py             — BaseLLMProvider ABC, error hierarchy
  adapters/           — provider-specific adapters (OpenRouter, Anthropic, OpenAI)
  decorators.py       — retry, cache, and resilience decorators
"""

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
                model_fields = getattr(schema, "model_fields", {})
                list_field = next(iter(model_fields), None)
                data = {list_field: data} if list_field else {}
            # Handle model returning "issues" instead of "findings"
            if isinstance(data, dict) and "issues" in data and "findings" not in data:
                data["findings"] = data.pop("issues")
            # Models frequently invent their own field names for IRAC items
            # (e.g. "constitutional_concern" instead of "issue") even though
            # the values are substantively correct. Rather than reject the
            # whole response — which silently discards a real finding — map
            # known aliases onto the schema's required keys before validating.
            if isinstance(data, dict) and isinstance(data.get("findings"), list):
                data["findings"] = [self._normalize_irac_item(item) for item in data["findings"]]
            obj = schema(**data)
            return obj, response
        except Exception as e:
            raise LLMError(f"Failed to parse structured response: {e}")

    _IRAC_ALIASES: dict[str, list[str]] = {
        "issue": ["issue", "title", "finding", "summary", "concern", "constitutional_concern", "analysis"],
        "rule": ["rule", "constitutional_provision", "rule_id", "legal_basis", "provision", "article"],
        "application": ["application", "analysis", "reasoning", "constitutional_concern"],
        "conclusion": ["conclusion", "verdict", "constitutional_concern", "analysis"],
        "verbatim_quote": ["verbatim_quote", "excerpt", "quote", "text_excerpt"],
    }

    @classmethod
    def _normalize_irac_item(cls, item: object) -> object:
        if not isinstance(item, dict):
            return item
        normalized = dict(item)
        for target, aliases in cls._IRAC_ALIASES.items():
            if normalized.get(target):
                continue
            for alias in aliases:
                if item.get(alias):
                    normalized[target] = item[alias]
                    break
        return normalized

    async def count_tokens(self, text: str, model: str | None = None) -> int:
        count: int = await self._provider.count_tokens(text, model)
        return count

__all__ = [
    "BaseLLMProvider",
    "LLMAdapter",
    "LLMError", "LLMConfigurationError", "LLMTimeoutError", "LLMRateLimitError",
    "LLMRateLimitError", "BudgetExceededError",
    "OpenRouterProvider",
    "with_retry", "with_cache",
]
