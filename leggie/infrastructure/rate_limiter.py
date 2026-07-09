"""Rate limiter — token-bucket for LLM API calls."""

from __future__ import annotations

import asyncio
import time


class RateLimiter:
    """Simple token-bucket rate limiter for LLM API calls.

    Ensures at most max_rate requests per second.
    """

    def __init__(self, max_rate: float = 5.0) -> None:
        self._max_rate = max_rate
        self._interval = 1.0 / max_rate if max_rate > 0 else 0.0
        self._last_call: float = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a slot is available, then proceed."""
        if self._interval <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call
            if elapsed < self._interval:
                await asyncio.sleep(self._interval - elapsed)
            self._last_call = time.monotonic()
