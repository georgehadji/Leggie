"""LLM decorators — retry, cache, and other cross-cutting concerns."""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Callable
from functools import wraps
from typing import Any

from leggie.infrastructure.llm.base import LLMRateLimitError, LLMTimeoutError


def with_retry(max_retries: int = 3, base_delay: float = 1.0) -> Callable:
    """Decorator: retry LLM calls on transient failures with exponential backoff."""

    def decorator(func: Callable) -> Callable:
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
            raise last_exc  # type: ignore

        return wrapper

    return decorator


def with_cache(max_size: int = 100) -> Callable:
    """Decorator: simple LRU cache for LLM responses keyed by prompt hash."""
    return functools.lru_cache(maxsize=max_size)
