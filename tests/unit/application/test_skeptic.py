"""Tests for Calibrated Skeptic — adversarial critic with typed gates."""

import pytest

from leggie.application.agents.skeptic import (
    CalibratedSkeptic,
    FactualGate,
    LLMAdversarialGate,
    NumericGate,
    SkepticVerdict,
)
from leggie.application.ports.llm import LLMResponse
from leggie.domain.models import IRAC, Confidence, Finding, FindingType, ModelTier, Severity
from leggie.domain.models.structured_output import SkepticVerdictResponse


class FakeLLM:
    """Scripted LLM: returns one canned SkepticVerdictResponse."""

    def __init__(self, response: SkepticVerdictResponse) -> None:
        self._response = response
        self.calls = 0

    async def generate(self, request):  # pragma: no cover - unused
        raise NotImplementedError

    async def generate_structured(self, request, schema):
        self.calls += 1
        resp = LLMResponse(content="", model="fake", tier_used=ModelTier.PREMIUM, usage={})
        return self._response, resp

    async def count_tokens(self, text, model=None):  # pragma: no cover
        return len(text) // 4


def make_finding(
    finding_type=FindingType.CONSTITUTIONAL,
    rule="Article 43 of the Constitution",
    confidence=0.6,
    severity="high",
) -> Finding:
    return Finding(
        finding_type=finding_type,
        irac=IRAC(issue="test issue", rule=rule, application="app", conclusion="conc"),
        confidence=Confidence.from_score(confidence),
        severity=Severity(severity),
        lens="test",
        model="test",
    )


class TestNumericGate:
    @pytest.mark.asyncio
    async def test_neutral_for_non_numeric(self):
        gate = NumericGate()
        f = make_finding(finding_type=FindingType.CONSTITUTIONAL)
        v = await gate.examine(f)
        assert v.verdict == "neutral"

    @pytest.mark.asyncio
    async def test_neutral_for_numeric(self):
        gate = NumericGate()
        f = make_finding(finding_type=FindingType.NUMERIC)
        v = await gate.examine(f)
        assert v.verdict == "neutral"  # Deferred to Phase 4


class TestFactualGate:
    @pytest.mark.asyncio
    async def test_supports_constitutional_rule(self):
        gate = FactualGate()
        f = make_finding(rule="Το Άρθρο 43 του Συντάγματος ορίζει")
        v = await gate.examine(f)
        assert v.verdict == "supports"
        assert v.confidence_adjustment > 0

    @pytest.mark.asyncio
    async def test_neutral_no_rule_ref(self):
        gate = FactualGate()
        f = make_finding(rule="Some generic statement without references")
        v = await gate.examine(f)
        assert v.verdict == "neutral"

    @pytest.mark.asyncio
    async def test_neutral_for_economic_type(self):
        gate = FactualGate()
        f = make_finding(finding_type=FindingType.ECONOMIC)
        v = await gate.examine(f)
        assert v.verdict == "neutral"


class TestCalibratedSkeptic:
    @pytest.mark.asyncio
    async def test_review_returns_all_verdicts(self):
        skeptic = CalibratedSkeptic()
        f = make_finding()
        survivors, verdicts = await skeptic.review([f])
        assert len(verdicts) == 4  # 4 gates

    @pytest.mark.asyncio
    async def test_review_survivors(self):
        skeptic = CalibratedSkeptic()
        f = make_finding()
        survivors, _ = await skeptic.review([f])
        assert len(survivors) == 1  # Not refuted

    @pytest.mark.asyncio
    async def test_review_empty(self):
        skeptic = CalibratedSkeptic()
        survivors, verdicts = await skeptic.review([])
        assert len(survivors) == 0
        assert len(verdicts) == 0

    @pytest.mark.asyncio
    async def test_confidence_adjustment(self):
        skeptic = CalibratedSkeptic()
        f = make_finding(confidence=0.5, rule="Το Άρθρο 43 του Συντάγματος")
        survivors, _ = await skeptic.review([f])
        assert survivors[0].confidence.score > 0.5  # Adjusted up

    @pytest.mark.asyncio
    async def test_examine_returns_typed_verdict(self):
        skeptic = CalibratedSkeptic()
        f = make_finding()
        verdicts = await skeptic.examine(f)
        assert len(verdicts) >= 1
        for v in verdicts:
            assert isinstance(v, SkepticVerdict)
            assert v.gate in ("numeric", "temporal", "factual", "obligation")


class TestLLMAdversarialGate:
    @pytest.mark.asyncio
    async def test_refutes_drops_finding(self):
        llm = FakeLLM(
            SkepticVerdictResponse(
                verdict="refutes", reason="Άρθρο δεν υπάρχει", confidence_adjustment=0.0
            )
        )
        skeptic = CalibratedSkeptic(llm=llm)
        f = make_finding()
        survivors, verdicts = await skeptic.review([f])
        assert len(survivors) == 0
        assert any(v.gate == "adversarial" and v.verdict == "refutes" for v in verdicts)

    @pytest.mark.asyncio
    async def test_supports_keeps_finding(self):
        llm = FakeLLM(
            SkepticVerdictResponse(verdict="supports", reason="ok", confidence_adjustment=0.1)
        )
        skeptic = CalibratedSkeptic(llm=llm)
        f = make_finding(confidence=0.5)
        survivors, _ = await skeptic.review([f])
        assert len(survivors) == 1
        assert survivors[0].confidence.score > 0.5

    @pytest.mark.asyncio
    async def test_gate_added_only_with_llm(self):
        no_llm = CalibratedSkeptic()
        with_llm = CalibratedSkeptic(
            llm=FakeLLM(
                SkepticVerdictResponse(verdict="neutral", reason="", confidence_adjustment=0.0)
            )
        )
        assert len(no_llm._gates) == 4
        assert len(with_llm._gates) == 5

    @pytest.mark.asyncio
    async def test_llm_error_fails_neutral_not_crash(self):
        class CrashingLLM:
            async def generate_structured(self, request, schema):
                raise RuntimeError("boom")

        gate = LLMAdversarialGate(llm=CrashingLLM())
        f = make_finding()
        v = await gate.examine(f)
        assert v.verdict == "neutral"
