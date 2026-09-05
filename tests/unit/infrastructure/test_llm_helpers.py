"""Tests for LLM infrastructure helpers — validate_model_ids + with_retry (PROD-18b)."""

from __future__ import annotations

from typing import NoReturn
from unittest.mock import MagicMock, patch

import pytest

from leggie.application.ports.llm import LLMRateLimitError, LLMTimeoutError
from leggie.infrastructure import llm
from leggie.infrastructure.llm.decorators import with_retry


class TestValidateModelIds:
    @pytest.mark.asyncio
    async def test_empty_list_returns_empty(self):
        assert await llm.validate_model_ids("key", []) == []

    @pytest.mark.asyncio
    async def test_offline_allowlist_validates(self):
        # use_live=False → offline allowlist path
        invalid = await llm.validate_model_ids("", ["google/gemini-2.5-flash"], use_live=False)
        assert invalid == []

    @pytest.mark.asyncio
    async def test_offline_allowlist_rejects_unknown(self):
        invalid = await llm.validate_model_ids("", ["fake/model-does-not-exist"], use_live=False)
        assert invalid == ["fake/model-does-not-exist"]

    @pytest.mark.asyncio
    async def test_live_fallback_to_allowlist_on_error(self):
        # live call raises → contextlib.suppress swallows → offline allowlist
        import httpx as _httpx

        with patch.object(_httpx, "AsyncClient", side_effect=RuntimeError("network down")):
            invalid = await llm.validate_model_ids(
                "key", ["google/gemini-2.5-flash"], use_live=True
            )
        # network fails → allowlist fallback → gemini is valid → empty
        assert invalid == []

    @pytest.mark.asyncio
    async def test_live_returns_invalid_from_catalog(self):
        # `resp` must be a MagicMock, not AsyncMock: httpx.Response.json() is
        # synchronous (matches production code, which does not await it). An
        # AsyncMock here makes `resp.json` an AsyncMock child too, so calling
        # it (unawaited, correctly) hands back a never-awaited coroutine
        # instead of the configured dict. `validate_model_ids` wraps this
        # whole block in `contextlib.suppress(Exception)`, so the resulting
        # AttributeError on the coroutine was silently swallowed and this
        # test fell through to the offline-allowlist fallback — passing by
        # coincidence while exercising zero of the live-catalog code path,
        # and leaking a "coroutine was never awaited" RuntimeWarning.
        import httpx as _httpx

        with patch.object(_httpx, "AsyncClient") as mock_client:
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"data": [{"id": "google/gemini-2.5-flash"}]}
            mock_client.return_value.__aenter__.return_value.get.return_value = resp
            # anthropic/claude-sonnet-5 is in the OFFLINE allowlist but absent
            # from this mocked LIVE catalog, so the two code paths disagree on
            # it — only the live-catalog path reports it invalid. A test that
            # can't fail this way can't prove the live path ran at all.
            invalid = await llm.validate_model_ids(
                "key",
                ["google/gemini-2.5-flash", "anthropic/claude-sonnet-5"],
                use_live=True,
            )
        assert "anthropic/claude-sonnet-5" in invalid
        assert "google/gemini-2.5-flash" not in invalid


class TestWithRetry:
    @pytest.mark.asyncio
    async def test_retries_then_succeeds(self):
        """Transient timeout → retry → success."""
        calls = {"n": 0}

        # with_retry now preserves the wrapped signature, so these helpers must
        # be annotated for the call through `wrapped` to type-check.
        async def flaky() -> str:
            calls["n"] += 1
            if calls["n"] < 3:
                raise LLMTimeoutError("timeout")
            return "ok"

        wrapped = with_retry(max_retries=3, base_delay=0)(flaky)
        result = await wrapped()
        assert result == "ok"
        assert calls["n"] == 3

    @pytest.mark.asyncio
    async def test_exhausts_retries_raises(self):
        async def always_fails() -> NoReturn:
            raise LLMRateLimitError("rate limited")

        wrapped = with_retry(max_retries=2, base_delay=0)(always_fails)
        with pytest.raises(LLMRateLimitError):
            await wrapped()

    @pytest.mark.asyncio
    async def test_no_retry_on_success(self):
        calls = {"n": 0}

        async def ok() -> str:
            calls["n"] += 1
            return "ok"

        wrapped = with_retry(max_retries=3, base_delay=0)(ok)
        assert await wrapped() == "ok"
        assert calls["n"] == 1
