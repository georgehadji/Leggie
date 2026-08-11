"""Tests for OpenRouter LLM adapter — mock HTTP."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leggie.application.ports.llm import LLMError, LLMRateLimitError, LLMRequest
from leggie.infrastructure.llm import (
    LLMAdapter,
    LLMConfigurationError,
    OpenRouterProvider,
)

_OK_BODY: dict[str, Any] = {
    "choices": [{"message": {"content": "OK", "role": "assistant"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    "model": "openai/gpt-5.6-luna",
}


@contextmanager
def _provider_with_response(
    *,
    status: int = 200,
    body: dict[str, Any] | None = None,
    text: str | None = None,
    headers: dict[str, str] | None = None,
) -> Iterator[tuple[OpenRouterProvider, AsyncMock]]:
    """Yield a provider whose HTTP client returns one canned response.

    The mocked `post` is yielded alongside it so a test can assert on the
    request the adapter actually put on the wire. That indirection is the whole
    point: the tests this replaced built their own headers/body dict, applied
    the adapter's rules to it by hand, and asserted on their own copy — they
    passed without `generate()` ever being called, so no adapter change could
    have failed them.
    """
    provider = OpenRouterProvider(api_key="sk-test")
    payload = _OK_BODY if body is None else body
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.text = json.dumps(payload) if text is None else text
    # A bare MagicMock answers any header lookup with a truthy mock, and
    # float(MagicMock()) is 1.0 — so the 429 path parsed a phantom Retry-After
    # and slept for a real second on every run. An explicit dict keeps
    # Retry-After under the test's control.
    resp.headers = {} if headers is None else headers
    with patch.object(provider._http_client, "post", new_callable=AsyncMock) as post, \
         patch.object(provider._rate_limiter, "acquire", new_callable=AsyncMock):
        post.return_value = resp
        yield provider, post


def _sent_body(post: AsyncMock) -> dict[str, Any]:
    """The JSON body the adapter posted."""
    body = post.call_args.kwargs["json"]
    assert isinstance(body, dict)
    return body


def _sent_headers(post: AsyncMock) -> dict[str, str]:
    """The headers the adapter posted."""
    headers = post.call_args.kwargs["headers"]
    assert isinstance(headers, dict)
    return headers


class TestOpenRouterProvider:
    def test_init_requires_api_key(self):
        with pytest.raises(LLMConfigurationError):
            OpenRouterProvider(api_key="")

    def test_init_with_key(self):
        prov = OpenRouterProvider(api_key="sk-test")
        assert prov._api_key == "sk-test"

    @pytest.mark.asyncio
    async def test_count_tokens_estimate(self):
        prov = OpenRouterProvider(api_key="sk-test")
        count = await prov.count_tokens("Hello world")
        assert count > 0


class TestLLMAdapterInit:
    def test_requires_api_key(self):
        with pytest.raises(LLMConfigurationError):
            LLMAdapter(openrouter_key="")

    def test_creates_with_key(self):
        adapter = LLMAdapter(openrouter_key="sk-test")
        assert adapter._provider is not None


class TestOpenRouterAPIMock:
    """Drive the real generate() against a mocked transport."""

    @pytest.mark.asyncio
    async def test_generate_success(self):
        """Provider parses a successful response into an LLMResponse."""
        body = {
            "choices": [{
                "message": {"content": "Legal analysis result", "role": "assistant"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 200, "completion_tokens": 100},
            "model": "openai/gpt-5.6-luna",
        }
        with _provider_with_response(body=body) as (provider, _post):
            response = await provider.generate(
                LLMRequest(
                    prompt="Analyze this bill",
                    system_prompt="You are a legal analyst",
                    seed=42,
                )
            )

        assert response.content == "Legal analysis result"
        assert response.finish_reason == "stop"
        assert response.usage["prompt_tokens"] == 200
        assert response.usage["completion_tokens"] == 100

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [400, 401, 500, 503])
    async def test_non_200_raises_llm_error(self, status: int):
        """Any non-200, non-429 status surfaces as LLMError naming the code."""
        with _provider_with_response(status=status, text="upstream detail") as (provider, _), \
             pytest.raises(LLMError) as exc:
            await provider.generate(LLMRequest(prompt="x"))
        assert str(status) in str(exc.value)

    @pytest.mark.asyncio
    async def test_429_raises_rate_limit_error(self):
        """429 is distinguished from other errors by its own exception type."""
        with _provider_with_response(status=429, text="") as (provider, _), \
             pytest.raises(LLMRateLimitError):
            await provider.generate(LLMRequest(prompt="x"))

    @pytest.mark.asyncio
    async def test_system_prompt_is_first_message(self):
        with _provider_with_response() as (provider, post):
            await provider.generate(
                LLMRequest(prompt="User message", system_prompt="System instruction")
            )

        assert _sent_body(post)["messages"] == [
            {"role": "system", "content": "System instruction"},
            {"role": "user", "content": "User message"},
        ]

    @pytest.mark.asyncio
    async def test_no_system_prompt_sends_user_message_only(self):
        with _provider_with_response() as (provider, post):
            await provider.generate(LLMRequest(prompt="User message"))

        assert _sent_body(post)["messages"] == [{"role": "user", "content": "User message"}]

    @pytest.mark.asyncio
    async def test_seed_passed_in_body(self):
        with _provider_with_response() as (provider, post):
            await provider.generate(LLMRequest(prompt="Test", seed=42))

        assert _sent_body(post)["seed"] == 42

    @pytest.mark.asyncio
    async def test_seed_omitted_when_unset(self):
        """Determinism is opt-in — an unset seed must not reach the wire."""
        with _provider_with_response() as (provider, post):
            await provider.generate(LLMRequest(prompt="Test"))

        assert "seed" not in _sent_body(post)

    @pytest.mark.asyncio
    async def test_response_format_forwarded(self):
        with _provider_with_response() as (provider, post):
            await provider.generate(
                LLMRequest(prompt="Test", response_format={"type": "json_object"})
            )

        assert _sent_body(post)["response_format"] == {"type": "json_object"}

    @pytest.mark.asyncio
    async def test_http_headers_built_correctly(self):
        """OpenRouter attribution headers."""
        with _provider_with_response() as (provider, post):
            await provider.generate(LLMRequest(prompt="Test"))

        headers = _sent_headers(post)
        assert headers["Authorization"] == "Bearer sk-test"
        assert headers["HTTP-Referer"] == "https://github.com/georgehadji/Leggie"
        assert headers["X-Title"] == "Leggie"

    @pytest.mark.asyncio
    async def test_thinking_model_adds_reasoning(self):
        """Thinking models get include_reasoning: true."""
        with _provider_with_response() as (provider, post):
            await provider.generate(
                LLMRequest(prompt="Test", model="openai/gpt-5.6-luna:thinking")
            )

        assert _sent_body(post)["include_reasoning"] is True

    @pytest.mark.asyncio
    async def test_non_thinking_model_no_reasoning(self):
        """Non-thinking models do NOT get include_reasoning."""
        with _provider_with_response() as (provider, post):
            await provider.generate(LLMRequest(prompt="Test", model="openai/gpt-5.6-luna"))

        assert "include_reasoning" not in _sent_body(post)


# ── PROD-14 transport tests ──────────────────────────────────────────────

class TestErrorBodyTruncation:
    """Verify upstream error bodies are truncated before reaching exceptions."""

    @pytest.mark.asyncio
    async def test_500_body_truncated(self):
        """A 500 error with a large body is truncated to _MAX_ERROR_BODY_CHARS."""
        from leggie.infrastructure.llm.adapters.openrouter import _MAX_ERROR_BODY_CHARS

        big_body = "x" * (_MAX_ERROR_BODY_CHARS + 500)
        with _provider_with_response(status=500, text=big_body) as (provider, _), \
             pytest.raises(LLMError) as exc:
            await provider.generate(LLMRequest(prompt="test"))

        body_in_exc = str(exc.value)
        assert len(body_in_exc) < len(big_body), \
            f"Error body was not truncated: {len(body_in_exc)} chars"
        assert big_body not in body_in_exc

    @pytest.mark.asyncio
    async def test_429_empty_body_does_not_include_text(self):
        """A 429 with no sensitive body does not leak."""
        with _provider_with_response(status=429, text="") as (provider, _), \
             pytest.raises(LLMRateLimitError) as exc:
            await provider.generate(LLMRequest(prompt="test"))

        assert "OpenRouter rate limited" in str(exc.value)
        # No Retry-After header was sent, so none may be claimed in the message.
        assert "retry after" not in str(exc.value)


class TestRetryAfter:
    """Verify Retry-After header is parsed and honoured."""

    def test_parse_retry_after_integer(self):
        from leggie.infrastructure.llm.adapters.openrouter import _parse_retry_after
        mock_resp = MagicMock()
        mock_resp.headers = {"retry-after": "42"}
        assert _parse_retry_after(mock_resp) == 42.0

    def test_parse_retry_after_float(self):
        from leggie.infrastructure.llm.adapters.openrouter import _parse_retry_after
        mock_resp = MagicMock()
        mock_resp.headers = {"retry-after": "3.14"}
        assert _parse_retry_after(mock_resp) == 3.14

    def test_parse_retry_after_missing(self):
        from leggie.infrastructure.llm.adapters.openrouter import _parse_retry_after
        mock_resp = MagicMock()
        mock_resp.headers = {}
        assert _parse_retry_after(mock_resp) is None

    def test_parse_retry_after_http_date_fallback(self):
        from leggie.infrastructure.llm.adapters.openrouter import _parse_retry_after
        mock_resp = MagicMock()
        mock_resp.headers = {"retry-after": "Wed, 21 Oct 2015 07:28:00 GMT"}
        assert _parse_retry_after(mock_resp) == 5.0  # fallback default


class TestReasoningTokenVisibility:
    """generate() surfaces completion_tokens_details.reasoning_tokens additively.

    REMEDIATION_PLAN_V3 Phase B / OPEN item: reasoning models bill reasoning
    tokens under completion_tokens_details, which OpenRouter's usage previously
    discarded — making a reasoning-driven truncation invisible downstream.
    """

    async def _generate_with(self, body: dict[str, Any]) -> dict[str, int]:
        """Run generate() against a canned OpenRouter body, return its usage."""
        with _provider_with_response(body=body) as (provider, _):
            response = await provider.generate(LLMRequest(prompt="x", max_tokens=100))
        return response.usage

    @staticmethod
    def _body(finish_reason: str, usage: dict[str, Any]) -> dict[str, Any]:
        return {
            "choices": [{
                "message": {"content": "{}", "role": "assistant"},
                "finish_reason": finish_reason,
            }],
            "usage": usage,
            "model": "google/gemini-2.5-pro",
        }

    @pytest.mark.asyncio
    async def test_reasoning_tokens_surfaced_when_present(self):
        usage = await self._generate_with(self._body("length", {
            "prompt_tokens": 200,
            "completion_tokens": 100,
            "completion_tokens_details": {"reasoning_tokens": 4096},
        }))
        assert usage["prompt_tokens"] == 200
        assert usage["completion_tokens"] == 100
        assert usage["reasoning_tokens"] == 4096

    @pytest.mark.asyncio
    async def test_reasoning_tokens_absent_when_not_reported(self):
        usage = await self._generate_with(self._body("stop", {
            "prompt_tokens": 200, "completion_tokens": 100,
        }))
        # Additive only — no phantom key, no zero-fill.
        assert "reasoning_tokens" not in usage

    @pytest.mark.asyncio
    async def test_zero_reasoning_tokens_not_surfaced(self):
        usage = await self._generate_with(self._body("stop", {
            "prompt_tokens": 200,
            "completion_tokens": 100,
            "completion_tokens_details": {"reasoning_tokens": 0},
        }))
        assert "reasoning_tokens" not in usage
