"""PROD-18a — cassette/VCR-style integration tests for the LLM ladder.

These cover the real OpenRouter request/response shape: the 4-attempt
structured-output ladder, json_schema rejection → json_object fallback,
truncation → doubled tokens, repair attempt, and budget block.

Since `vcr`/`respx` are not installed in the offline environment, these use
a manual cassette approach: a fake HTTP layer is driven by recorded
OpenRouter-shaped response bodies per scenario, and the decorator ladder is
exercised end-to-end.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from leggie.application.ports.llm import LLMPort, LLMRequest, LLMResponse
from leggie.domain.models import ModelTier
from leggie.infrastructure.budget_guard import BudgetGuard
from leggie.infrastructure.llm.base import LLMError
from leggie.infrastructure.llm.decorators import BudgetGuardDecorator
from leggie.infrastructure.llm.ladder import StructuredOutputDecorator


class _FakeInner(LLMPort):
    """Emulates the transport layer with a scripted sequence of responses.

    `script` is a list of ("success"|"api_error", payload) tuples. Each
    call pops the next entry, simulating a recorded OpenRouter response.
    """

    _default_model = "test-model"

    def __init__(self, script):
        self._script = list(script)
        self.requests: list[LLMRequest] = []

    def _next(self):
        if not self._script:
            raise AssertionError("Unexpected extra LLM call in ladder test")
        return self._script.pop(0)

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        kind, payload = self._next()
        if kind == "api_error":
            raise LLMError(f"OpenRouter API error 400: {payload}")
        # payload: {"content": ..., "finish_reason": ...}
        return LLMResponse(
            content=payload["content"],
            model="test-model",
            tier_used=ModelTier.BUDGET,
            usage={"prompt_tokens": 100, "completion_tokens": 50},
            finish_reason=payload.get("finish_reason", "stop"),
        )

    async def generate_structured(self, request: LLMRequest, schema: type):
        return await self.generate(request), None

    async def count_tokens(self, text, model=None):
        return len(text) // 4 + 1


# The schema used for structured-output tests
class _VerdictSchema(BaseModel):
    verdict: str = Field(...)
    confidence: float = Field(default=0.5)


class TestLadderScenarios:
    @pytest.mark.asyncio
    async def test_json_schema_rejection_falls_back_to_json_object(self):
        """400 (unsupported json_schema) → falls back to json_object mode."""
        inner = _FakeInner([
            ("api_error", "model does not support json_schema"),
            ("success", {"content": '{"verdict": "supports", "confidence": 0.8}'}),
        ])
        decorator = StructuredOutputDecorator(inner)
        obj, resp = await decorator.generate_structured(
            LLMRequest(prompt="p"), _VerdictSchema
        )
        assert obj.verdict == "supports"
        # Second request (fallback) used json_object
        assert inner.requests[1].response_format == {"type": "json_object"}

    @pytest.mark.asyncio
    async def test_truncation_doubles_max_tokens(self):
        """finish_reason=length → retry with doubled max_tokens."""
        # Attempt 1 (json_schema) truncated; attempt 2 (json_object) truncated
        # too; attempt 3 (truncation retry) uses doubled max_tokens + json_object.
        inner = _FakeInner([
            ("success", {"content": "partia", "finish_reason": "length"}),
            ("success", {"content": "truncated too", "finish_reason": "length"}),
            ("success", {"content": '{"verdict": "refutes", "confidence": 0.7}'}),
        ])
        decorator = StructuredOutputDecorator(inner)
        obj, _ = await decorator.generate_structured(
            LLMRequest(prompt="p", max_tokens=1024), _VerdictSchema
        )
        assert obj.verdict == "refutes"
        # Retry (attempt 3) uses doubled max_tokens 2048
        assert inner.requests[2].max_tokens == 2048

    @pytest.mark.asyncio
    async def test_repair_round_parses(self):
        """Malformed JSON with a skeleton → repair round recovers."""
        # Attempt 1 (json_schema) returns malformed JSON with a `{`.
        # Attempt 2 (json_object) also malformed. Repair round fixes it.
        inner = _FakeInner([
            ("success", {"content": '{"verdict": "supports", "confidence": "not-a-num"}', "finish_reason": "stop"}),
            ("success", {"content": '{"verdict": "supports", "confidence": "still-bad"}', "finish_reason": "stop"}),
            ("success", {"content": '{"verdict": "neutral", "confidence": 0.5}'}),
        ])
        decorator = StructuredOutputDecorator(inner)
        # Attempt 1+2 fail Pydantic validation, repair round recovers.
        obj, _ = await decorator.generate_structured(
            LLMRequest(prompt="p"), _VerdictSchema
        )
        assert obj.verdict == "neutral"
        assert len(inner.requests) >= 3

    @pytest.mark.asyncio
    async def test_all_attempts_fail_raises_llm_error(self):
        """Exhausted ladder → LLMError."""
        # Attempt 1 (json_schema) + attempt 2 (json_object) both return
        # prose-without-JSON-skeleton, so the repair round skips and raises LLMError.
        inner = _FakeInner([
            ("success", {"content": "no json skeleton here"}),
            ("success", {"content": "still no json skeleton"}),
        ])
        decorator = StructuredOutputDecorator(inner)
        with pytest.raises(LLMError):
            await decorator.generate_structured(LLMRequest(prompt="p"), _VerdictSchema)


class TestBudgetBlock:
    """Budget block path through the ladder."""

    @pytest.mark.asyncio
    async def test_over_budget_raises_budget_exceeded(self):
        from leggie.infrastructure.llm.base import BudgetExceededError
        # A guard too small for even one call.
        guard = BudgetGuard(max_tokens=10, max_cost=0.000001)
        inner = _FakeInner([("success", {"content": "{}"})])
        decorated = BudgetGuardDecorator(inner, guard)
        with pytest.raises(BudgetExceededError):
            await decorated.generate(LLMRequest(prompt="x" * 100))
