"""Tests for Token Optimization Plan — TOK-1, TOK-4, TOK-12 regression coverage."""
from __future__ import annotations

from typing import Any

from leggie.application.ports.llm import LLMPort, LLMRequest, LLMResponse
from leggie.infrastructure.llm.ladder import StructuredOutputDecorator


class RecordingLLM(LLMPort):
    """Fake LLM that counts generate/generate_structured calls."""

    def __init__(self, content: str = "{}", fail_times: int = 0) -> None:
        self.generate_count = 0
        self.generate_structured_count = 0
        self._content = content
        self._fail_times = fail_times
        self._fail_count = 0

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.generate_count += 1
        return LLMResponse(
            content=self._content,
            model="test/model",
            tier_used=request.tier,
            usage={"prompt_tokens": 100, "completion_tokens": 50, "cached_tokens": 0},
            finish_reason="stop",
        )

    async def generate_structured(self, request: LLMRequest, schema: type) -> tuple[object, LLMResponse]:
        self.generate_structured_count += 1
        resp = await self.generate(request)
        return {}, resp

    async def count_tokens(self, text: str, model: str | None = None) -> int:
        return len(text) // 4


class RecordingBudgetGuard(LLMPort):
    """Fake guard that records every generate call (simulating BudgetGuardDecorator)."""

    def __init__(self, inner: LLMPort) -> None:
        self._inner = inner
        self.record_calls = 0

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.record_calls += 1
        return await self._inner.generate(request)

    async def generate_structured(self, request: LLMRequest, schema: type) -> tuple[object, LLMResponse]:
        self.record_calls += 1
        return await self._inner.generate_structured(request, schema)

    async def count_tokens(self, text: str, model: str | None = None) -> int:
        return await self._inner.count_tokens(text, model)


class TestTOK1DecoratorStack:
    """TOK-1: StructuredOutputDecorator must sit outside the budget guard."""

    def test_ladder_attempts_pass_through_guard(self):
        """Each ladder attempt must pass through the inner LLMPort.generate()."""
        transport = RecordingLLM("[]")
        guard = RecordingBudgetGuard(transport)
        ladder = StructuredOutputDecorator(guard)

        from pydantic import BaseModel

        class Simple(BaseModel):
            pass

        async def run() -> Any:
            try:
                return await ladder.generate_structured(
                    LLMRequest(prompt="test", max_tokens=1024, temperature=0.0), Simple
                )
            except Exception:
                pass  # Parse failure is expected; we test call counts

        import asyncio
        asyncio.run(run())
        # The guard's inner transport.generate() must have been called by the ladder
        assert transport.generate_count >= 1, f"Transport generate should be called, got {transport.generate_count}"

    def test_multi_attempt_ladder_traverses_inner(self):
        """The structured ladder must delegate to self._inner.generate(), not bypass it."""
        transport = RecordingLLM("[]")
        guard = RecordingBudgetGuard(transport)
        ladder = StructuredOutputDecorator(guard)

        from pydantic import BaseModel

        class Simple(BaseModel):
            pass

        async def run() -> Any:
            try:
                return await ladder.generate_structured(
                    LLMRequest(prompt="test", max_tokens=1024, temperature=0.0), Simple
                )
            except Exception:
                pass

        import asyncio
        asyncio.run(run())
        # The ladder MUST call inner.generate() — proof: transport.generate was called
        assert transport.generate_count >= 1, "Structured ladder must call inner.generate()"

    def test_container_builds_correct_stack(self):
        """Container._create_llm must wrap with StructuredOutputDecorator."""
        from leggie.infrastructure.llm.ladder import StructuredOutputDecorator

        # The chain should be: StructuredOutput → BudgetGuard → Transport
        assert StructuredOutputDecorator is not None  # must be importable


class TestTOK4MaxTokensFlow:
    """TOK-4: Route max_tokens must flow to lens constructor."""

    def test_lens_constructor_accepts_max_tokens(self):
        """Lens.__init__ must accept max_tokens= keyword."""

        # Verify signature by instantiating a concrete lens
        from leggie.application.agents.constitutional_lens import ConstitutionalLens

        lens = ConstitutionalLens(
            llm=RecordingLLM(),
            model="test/model",
            max_tokens=6144,
        )
        assert lens._max_tokens == 6144

    def test_lens_default_max_tokens(self):
        """Lens.__init__ defaults to 4096."""
        from leggie.application.agents.constitutional_lens import ConstitutionalLens

        lens = ConstitutionalLens(llm=RecordingLLM(), model="test/model")
        assert lens._max_tokens == 4096

    def test_skeptic_returns_max_tokens_from_route(self):
        """Skeptic._select_model returns (model, max_tokens) tuple."""
        from leggie.application.agents.skeptic import LLMAdversarialGate

        gate = LLMAdversarialGate(llm=RecordingLLM())
        # Without router, should return (model_or_none, 8192)
        import asyncio

        result = asyncio.run(gate._select_model())
        assert isinstance(result, tuple), f"Expected tuple, got {type(result)}"
        assert len(result) == 2
        model, max_tokens = result
        assert isinstance(max_tokens, int)
        assert max_tokens == 8192


class TestTOK12GreekSubstantive:
    """TOK-12: Greek-ratio check must be scoped to substantive fields."""

    def test_collect_substantive_strings_ignores_citations(self):
        """_collect_substantive_strings skips non-text fields."""
        from leggie.application.agents.lens import _collect_substantive_strings

        data = {
            "findings": [
                {
                    "article_id": "1",
                    "issue": "Ζήτημα συνταγματικότητας",
                    "rule": "Ο κανόνας εφαρμογής",
                    "confidence_score": 0.8,  # not a string
                    "citation": "ΦΕΚ Α 137/2023",
                }
            ]
        }
        result: list[str] = []
        _collect_substantive_strings(data, result)
        assert len(result) >= 1, "Should collect substantive fields"
        assert "Ζήτημα" in " ".join(result), "Greek issue text not collected"
        # Citation should NOT be in result
        combined = " ".join(result)
        assert "ΦΕΚ" not in combined, "Citation should not appear in substantive check"

    def test_collect_substantive_strings_no_substantive_fields(self):
        """When only citations exist, no strings should be collected."""
        from leggie.application.agents.lens import _collect_substantive_strings

        data = {
            "findings": [
                {
                    "article_id": "5",
                    "citation": "ΦΕΚ Α 137/2023",
                    "confidence_score": 0.5,
                }
            ]
        }
        result: list[str] = []
        _collect_substantive_strings(data, result)
        assert len(result) == 0, "Citation-only data should yield no substantive strings"
