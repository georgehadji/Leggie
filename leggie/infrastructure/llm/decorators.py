"""LLM decorators — retry and other cross-cutting concerns."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar, cast

from leggie.application.ports.llm import (
    BudgetExceededError,
    LLMPort,
    LLMRateLimitError,
    LLMTimeoutError,
)

# Preserves the decorated function's own signature. Typed as
# Callable[..., Any] -> Callable[..., Any], this decorator erased the return
# type of everything it wrapped: `await provider.generate(req)` handed back Any,
# so no caller of a retried method was checked against LLMResponse at all.
AsyncFn = TypeVar("AsyncFn", bound=Callable[..., Any])


def with_retry(max_retries: int = 3, base_delay: float = 1.0) -> Callable[[AsyncFn], AsyncFn]:
    """Decorator: retry LLM calls on transient failures with exponential backoff."""

    def decorator(func: AsyncFn) -> AsyncFn:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Exception | None = None
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except (LLMRateLimitError, LLMTimeoutError) as e:
                    last_exc = e
                    if attempt < max_retries - 1:
                        delay = base_delay * (2**attempt)
                        await asyncio.sleep(delay)
            raise last_exc  # type: ignore[misc]

        return cast(AsyncFn, wrapper)

    return decorator


class BudgetGuardDecorator(LLMPort):
    """Decorator wrapping an LLMPort with reserve→await→settle budget enforcement.

    Implements the Decorator pattern (rule I). Under an ``asyncio.Lock``, reserves
    the estimated cost against the ceiling before the call and returns a reservation
    handle. After the call, settles to actuals (releases the delta) in a ``finally``.
    This fixes the PROD-08 race where N concurrent calls could all clear the
    pre-call check before any recorded usage.

    On DEGRADE the degrade callback is invoked; on BLOCK BudgetExceededError is raised.
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
        self._reserve_lock = asyncio.Lock()

    async def _reserve(self, model: str, prompt_tokens: int, completion_estimate: int) -> None:
        """Reserve estimated cost under lock. Raises BudgetExceededError if over budget."""
        async with self._reserve_lock:
            action = self._guard.check(prompt_tokens, completion_estimate, model)
            if action.value == "block":
                raise BudgetExceededError(
                    f"Budget exceeded: tokens={self._guard.remaining_tokens}, "
                    f"cost=${self._guard.remaining_cost:.2f}"
                )
            if action.value == "degrade":
                self._guard.apply_degrade()
                self._on_degrade(action.value, self._guard.usage_ratio)
            # Tentatively record the *estimated* cost under the lock so the
            # ceiling is held for the next caller.  The settle step will
            # reconcile to actuals.
            self._guard.record_usage(prompt_tokens, completion_estimate, model)

    async def _settle(
        self,
        model: str,
        actual_prompt: int,
        actual_completion: int,
        actual_cached: int,
        reserved_prompt: int,
        reserved_completion: int,
    ) -> None:
        """Reconcile reserved estimate to actuals.

        Reverses the estimated charge, then applies the actual charge.
        This avoids arithmetic on negative token counts through estimate_cost.
        """
        # Reverse the estimate (refund)
        self._guard.record_usage(-reserved_prompt, -reserved_completion, model, 0)
        # Apply actual usage
        self._guard.record_usage(actual_prompt, actual_completion, model, actual_cached)

    async def generate(self, request: Any) -> Any:
        """Reserve → await → settle."""
        model = request.model or getattr(self._wrapped, "_default_model", "")

        prompt_estimate = len(request.prompt) // 4 + 1
        completion_estimate = min(request.max_tokens, 2048)

        await self._reserve(model, prompt_estimate, completion_estimate)

        try:
            response = await self._wrapped.generate(request)
        except BaseException:
            # Release the full estimate on failure — it was never spent
            self._guard.record_usage(-prompt_estimate, -completion_estimate, model, 0)
            raise

        # Settle to actuals
        actual_prompt = response.usage.get("prompt_tokens", prompt_estimate)
        actual_completion = response.usage.get("completion_tokens", completion_estimate)
        actual_cached = response.usage.get("cached_tokens", 0)
        await self._settle(
            model,
            actual_prompt,
            actual_completion,
            actual_cached,
            prompt_estimate,
            completion_estimate,
        )

        return response

    async def generate_structured(self, request: Any, schema: type) -> tuple[Any, Any]:
        """Reserve → await → settle for structured generation."""
        model = request.model or getattr(self._wrapped, "_default_model", "")

        prompt_estimate = len(request.prompt) // 4 + 1
        completion_estimate = min(request.max_tokens, 2048)

        await self._reserve(model, prompt_estimate, completion_estimate)

        try:
            obj, response = await self._wrapped.generate_structured(request, schema)
        except BaseException:
            self._guard.record_usage(-prompt_estimate, -completion_estimate, model, 0)
            raise

        actual_prompt = response.usage.get("prompt_tokens", prompt_estimate)
        actual_completion = response.usage.get("completion_tokens", completion_estimate)
        actual_cached = response.usage.get("cached_tokens", 0)
        await self._settle(
            model,
            actual_prompt,
            actual_completion,
            actual_cached,
            prompt_estimate,
            completion_estimate,
        )

        return obj, response

    async def count_tokens(self, text: str, model: str | None = None) -> int:
        count: int = await self._wrapped.count_tokens(text, model)
        return count

    @property
    def budget_guard(self) -> Any:
        return self._guard
