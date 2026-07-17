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
from leggie.application.ports.router import RouteResult
from leggie.domain.models import (
    IRAC,
    Confidence,
    Event,
    EventType,
    Finding,
    FindingType,
    ModelTier,
    Severity,
)
from leggie.domain.models.structured_output import SkepticVerdictResponse


class FakeLLM:
    """Scripted LLM: returns one canned SkepticVerdictResponse."""

    def __init__(self, response: SkepticVerdictResponse) -> None:
        self._response = response
        self.calls = 0
        self.last_request = None

    async def generate(self, request):  # pragma: no cover - unused
        raise NotImplementedError

    async def generate_structured(self, request, schema):
        self.calls += 1
        self.last_request = request
        resp = LLMResponse(content="", model="fake", tier_used=ModelTier.PREMIUM, usage={})
        return self._response, resp

    async def count_tokens(self, text, model=None):  # pragma: no cover
        return len(text) // 4


class FakeRouter:
    """Scripted router: returns a fixed RouteResult for any task type."""

    def __init__(self, max_tokens: int, model: str = "routed-model") -> None:
        self._max_tokens = max_tokens
        self._model = model

    async def route(self, task_type, budget_remaining=None):
        return RouteResult(model=self._model, tier=ModelTier.PREMIUM, max_tokens=self._max_tokens)

    async def cascade(self, task_type, current_tier, failure_reason=None):
        return None

    def supported_models(self):
        return [self._model]


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
        lens="test", model="test",
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
        llm = FakeLLM(SkepticVerdictResponse(
            verdict="refutes", reason="Άρθρο δεν υπάρχει", confidence_adjustment=0.0))
        skeptic = CalibratedSkeptic(llm=llm)
        f = make_finding()
        survivors, verdicts = await skeptic.review([f])
        assert len(survivors) == 0
        assert any(v.gate == "adversarial" and v.verdict == "refutes" for v in verdicts)

    @pytest.mark.asyncio
    async def test_supports_keeps_finding(self):
        llm = FakeLLM(SkepticVerdictResponse(
            verdict="supports", reason="ok", confidence_adjustment=0.1))
        skeptic = CalibratedSkeptic(llm=llm)
        f = make_finding(confidence=0.5)
        survivors, _ = await skeptic.review([f])
        assert len(survivors) == 1
        assert survivors[0].confidence.score > 0.5

    @pytest.mark.asyncio
    async def test_gate_added_only_with_llm(self):
        no_llm = CalibratedSkeptic()
        with_llm = CalibratedSkeptic(llm=FakeLLM(
            SkepticVerdictResponse(verdict="neutral", reason="", confidence_adjustment=0.0)))
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

    @pytest.mark.asyncio
    async def test_uses_router_max_tokens(self):
        """D12/D11: the route's max_tokens must reach the request, not a
        hardcoded 2048 — otherwise raising routes.yaml's ceiling is a no-op."""
        llm = FakeLLM(SkepticVerdictResponse(
            verdict="supports", reason="ok", confidence_adjustment=0.0))
        router = FakeRouter(max_tokens=8192)
        gate = LLMAdversarialGate(llm=llm, router=router)
        f = make_finding()
        await gate.examine(f)
        assert llm.last_request.max_tokens == 8192

    @pytest.mark.asyncio
    async def test_no_router_falls_back_to_default_max_tokens(self):
        llm = FakeLLM(SkepticVerdictResponse(
            verdict="supports", reason="ok", confidence_adjustment=0.0))
        gate = LLMAdversarialGate(llm=llm)  # no router
        f = make_finding()
        await gate.examine(f)
        assert llm.last_request.max_tokens == 2048

    @pytest.mark.asyncio
    async def test_route_resolution_logged_at_info_not_debug(self, caplog):
        """D19: route resolution must be observable without DEBUG level --
        a silent fallback to the wrong ceiling was reproduced twice in live
        runs (subset6, subset8) with zero warning logged. INFO has been
        reliable in every live run so far; DEBUG has not."""
        import logging

        llm = FakeLLM(SkepticVerdictResponse(
            verdict="supports", reason="ok", confidence_adjustment=0.0))
        router = FakeRouter(max_tokens=8192, model="routed-model")
        gate = LLMAdversarialGate(llm=llm, router=router)
        with caplog.at_level(logging.INFO, logger="leggie.application.agents.skeptic"):
            await gate.examine(make_finding())
        assert any(
            "skeptic_route_resolved" in r.message and "8192" in r.message
            for r in caplog.records
        )

    @pytest.mark.asyncio
    async def test_no_router_logs_absent_at_info(self, caplog):
        import logging

        llm = FakeLLM(SkepticVerdictResponse(
            verdict="supports", reason="ok", confidence_adjustment=0.0))
        gate = LLMAdversarialGate(llm=llm)  # no router
        with caplog.at_level(logging.INFO, logger="leggie.application.agents.skeptic"):
            await gate.examine(make_finding())
        assert any("skeptic_route_absent" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_llm_error_emits_degradation_event(self):
        """D13: a critic that fails on every call must be countable, not
        just logged — otherwise it's silently indistinguishable from a
        critic that ran and agreed."""
        class CrashingLLM:
            async def generate_structured(self, request, schema):
                raise RuntimeError("boom")

        events: list[Event] = []
        gate = LLMAdversarialGate(llm=CrashingLLM(), on_degradation=events.append)
        f = make_finding()
        await gate.examine(f)
        assert len(events) == 1
        assert events[0].event_type == EventType.DEGRADED
        assert events[0].data["gate"] == "adversarial"
        assert events[0].data["finding_id"] == str(f.id)

    @pytest.mark.asyncio
    async def test_no_degradation_event_on_success(self):
        events: list[Event] = []
        llm = FakeLLM(SkepticVerdictResponse(
            verdict="supports", reason="ok", confidence_adjustment=0.0))
        gate = LLMAdversarialGate(llm=llm, on_degradation=events.append)
        f = make_finding()
        await gate.examine(f)
        assert events == []
