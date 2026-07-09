"""Tests for Calibrated Skeptic — adversarial critic with typed gates."""

import pytest
from leggie.application.agents.skeptic import (
    CalibratedSkeptic, NumericGate, TemporalGate, FactualGate, SkepticVerdict,
)
from leggie.domain.models import Finding, IRAC, Confidence, FindingType, Severity


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
    def test_neutral_for_non_numeric(self):
        gate = NumericGate()
        f = make_finding(finding_type=FindingType.CONSTITUTIONAL)
        v = gate.examine(f)
        assert v.verdict == "neutral"

    def test_neutral_for_numeric(self):
        gate = NumericGate()
        f = make_finding(finding_type=FindingType.NUMERIC)
        v = gate.examine(f)
        assert v.verdict == "neutral"  # Deferred to Phase 4


class TestFactualGate:
    def test_supports_constitutional_rule(self):
        gate = FactualGate()
        f = make_finding(rule="Το Άρθρο 43 του Συντάγματος ορίζει")
        v = gate.examine(f)
        assert v.verdict == "supports"
        assert v.confidence_adjustment > 0

    def test_neutral_no_rule_ref(self):
        gate = FactualGate()
        f = make_finding(rule="Some generic statement without references")
        v = gate.examine(f)
        assert v.verdict == "neutral"

    def test_neutral_for_economic_type(self):
        gate = FactualGate()
        f = make_finding(finding_type=FindingType.ECONOMIC)
        v = gate.examine(f)
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
