"""LLM decorators — retry, cache, budget-guard, and other cross-cutting concerns."""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Callable
from functools import wraps
from typing import Any

from leggie.application.ports.llm import LLMPort
from leggie.infrastructure.llm.base import BudgetExceededError, LLMRateLimitError, LLMTimeoutError


def with_retry(max_retries: int = 3, base_delay: float = 1.0) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator: retry LLM calls on transient failures with exponential backoff."""
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
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


def with_cache(max_size: int = 100) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator: simple LRU cache for LLM responses keyed by prompt hash."""
    return functools.lru_cache(maxsize=max_size)


class BudgetGuardDecorator(LLMPort):
    """Decorator wrapping an LLMPort with pre-call budget check and post-call recording.

    Implements the Decorator pattern (rule I). The wrapped port is unaware of
    budget enforcement. On DEGRADE the degrade callback is invoked; on BLOCK
    BudgetExceededError is raised.
    """

    def __init__(
        self,
        llm_port: Any,  # LLMPort-compatible object
        budget_guard: Any,  # BudgetGuard instance
        on_degrade: Callable[[str, float], None] | None = None,
    ) -> None:
        self._wrapped = llm_port
        self._guard = budget_guard
        self._on_degrade = on_degrade or (lambda _action, _ratio: None)

    async def generate(self, request: Any) -> Any:
        """Pre-call budget check → generate → post-call record."""
        from leggie.application.ports.llm import LLMResponse
        model = request.model or getattr(self._wrapped, "_default_model", "")

        # Estimate tokens (use approximate count if not available)
        prompt_tokens = len(request.prompt) // 4 + 1
        completion_estimate = min(request.max_tokens, 2048)

        action = self._guard.check(prompt_tokens, completion_estimate, model)
        if action.value == "block":
            raise BudgetExceededError(
                f"Budget exceeded: tokens={self._guard.remaining_tokens}, "
                f"cost=${self._guard.remaining_cost:.2f}"
            )
        if action.value == "degrade":
            self._guard.apply_degrade()
            self._on_degrade(action.value, self._guard.usage_ratio)

        response = await self._wrapped.generate(request)

        # Record actual usage from response
        actual_prompt = response.usage.get("prompt_tokens", prompt_tokens)
        actual_completion = response.usage.get("completion_tokens", completion_estimate)
        self._guard.record_usage(actual_prompt, actual_completion, model)

        return response

    async def generate_structured(self, request: Any, schema: type) -> tuple[Any, Any]:
        """Check budget, then delegate, then record usage."""
        from leggie.application.ports.llm import LLMResponse
        model = request.model or getattr(self._wrapped, "_default_model", "")

        prompt_tokens = len(request.prompt) // 4 + 1
        completion_estimate = min(request.max_tokens, 2048)

        action = self._guard.check(prompt_tokens, completion_estimate, model)
        if action.value == "block":
            raise BudgetExceededError(
                f"Budget exceeded: tokens={self._guard.remaining_tokens}, "
                f"cost=${self._guard.remaining_cost:.2f}"
            )
        if action.value == "degrade":
            self._guard.apply_degrade()
            self._on_degrade(action.value, self._guard.usage_ratio)

        obj, response = await self._wrapped.generate_structured(request, schema)

        actual_prompt = response.usage.get("prompt_tokens", prompt_tokens)
        actual_completion = response.usage.get("completion_tokens", completion_estimate)
        self._guard.record_usage(actual_prompt, actual_completion, model)

        return obj, response

    async def count_tokens(self, text: str, model: str | None = None) -> int:
        count: int = await self._wrapped.count_tokens(text, model)
        return count

    @property
    def budget_guard(self) -> Any:
        return self._guard
