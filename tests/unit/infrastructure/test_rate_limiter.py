"""Tests for rate limiter — token-bucket."""

import time

import pytest

from leggie.infrastructure.rate_limiter import RateLimiter


class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_acquire_does_not_block_single(self):
        limiter = RateLimiter(max_rate=100.0)
        start = time.monotonic()
        await limiter.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 0.1

    @pytest.mark.asyncio
    async def test_acquire_zero_rate(self):
        limiter = RateLimiter(max_rate=0.0)
        start = time.monotonic()
        await limiter.acquire()
        assert time.monotonic() - start < 0.05
