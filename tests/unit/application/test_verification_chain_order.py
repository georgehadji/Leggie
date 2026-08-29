"""The verification chain must rank LAST — after skeptic and CoVe.

Contract (spec point 4):

    Finding → Calibrated Skeptic → CoVe → citation verification → reranking

The skeptic (`skeptic.review`) and CoVe (`_apply_revision`) both rewrite
``Finding.confidence``, and both drop findings. ``CompositeReranker`` scores
``severity × confidence × novelty``. So a rerank that runs *before* them
publishes an order computed from confidences the survivors no longer carry and
from a novelty baseline that includes findings which have since been removed.

Every test here is written so that it FAILS under the old
``dedup → rerank → skeptic → CoVe`` order.
"""

from __future__ import annotations

import pytest

from leggie.application.agents.skeptic import CalibratedSkeptic, SkepticGate, SkepticVerdict
from leggie.application.services.blackboard_aggregator import BlackboardAggregator
from leggie.application.services.cove_verifier import CoVeResult, CoVeVerifier
from leggie.application.workflow.bill_analysis_flow import BillAnalysisFlow
from leggie.domain.models import IRAC, Confidence, Finding, FindingType, Severity


def _make(issue: str, confidence: float, severity: str = "medium") -> Finding:
    return Finding(
        finding_type=FindingType.CONSTITUTIONAL,
        irac=IRAC(issue=issue, rule="r", application="a", conclusion="c"),
        confidence=Confidence.from_score(confidence),
        severity=Severity(severity),
        lens="test",
        model="test",
    )


class _DowngradeGate(SkepticGate):
    """Knocks `adjustment` off the confidence of findings whose issue holds `marker`."""

    def __init__(self, marker: str, adjustment: float) -> None:
        self._marker = marker
        self._adjustment = adjustment

    async def examine(self, finding: Finding) -> SkepticVerdict:
        hit = self._marker in finding.irac.issue
        return SkepticVerdict(
            finding_id=str(finding.id),
            gate="downgrade",
            verdict="neutral",
            reason="test gate",
            confidence_adjustment=self._adjustment if hit else 0.0,
        )


class _RefuteGate(SkepticGate):
    """Refutes findings whose issue holds `marker`."""

    def __init__(self, marker: str) -> None:
        self._marker = marker

    async def examine(self, finding: Finding) -> SkepticVerdict:
        hit = self._marker in finding.irac.issue
        return SkepticVerdict(
            finding_id=str(finding.id),
            gate="refute",
            verdict="refutes" if hit else "neutral",
            reason="test gate",
        )


class _DowngradingCoVe(CoVeVerifier):
    """CoVe stand-in that re-scores confidence for findings holding `marker`."""

    def __init__(self, marker: str, new_score: float) -> None:
        super().__init__()
        self._marker = marker
        self._new_score = new_score

    async def verify_batch(self, findings, article_index=None):  # type: ignore[no-untyped-def]
        results = []
        for f in findings:
            if self._marker in f.irac.issue:
                f = f.model_copy(update={
                    "confidence": Confidence.from_score(self._new_score, provenance="cove-verified"),
                    "version": f.version + 1,
                })
            results.append(CoVeResult(finding=f, all_verified=True, consistency="consistent"))
        return results


def _issues(findings: list[Finding]) -> list[str]:
    return [f.irac.issue for f in findings]


class TestRerankSeesPostSkepticConfidence:
    """Rerank must score the confidence the skeptic left behind, not the one it replaced."""

    @pytest.mark.asyncio
    async def test_downgraded_finding_sinks_in_published_order(self):
        # A outranks B on raw confidence (0.9 vs 0.5) — old order published [A, B].
        # The skeptic then knocks A down to 0.1, which must sink it below B.
        findings = [
            _make("Άρθρο 1: alpha", confidence=0.9),
            _make("Άρθρο 2: beta", confidence=0.5),
        ]
        agg = BlackboardAggregator(
            skeptic=CalibratedSkeptic(gates=[_DowngradeGate("alpha", -0.8)]),
        )

        result = await agg.aggregate(findings)

        assert _issues(result) == ["Άρθρο 2: beta", "Άρθρο 1: alpha"]
        # And the order is consistent with the confidences actually carried.
        assert [f.confidence.score for f in result] == sorted(
            [f.confidence.score for f in result], reverse=True
        )

    @pytest.mark.asyncio
    async def test_published_order_is_monotonic_in_final_confidence(self):
        findings = [
            _make("Άρθρο 1: alpha", confidence=0.9),
            _make("Άρθρο 2: beta", confidence=0.8),
            _make("Άρθρο 3: gamma", confidence=0.7),
        ]
        agg = BlackboardAggregator(
            skeptic=CalibratedSkeptic(gates=[_DowngradeGate("alpha", -0.85)]),
        )

        result = await agg.aggregate(findings)

        scores = [f.confidence.score for f in result]
        assert scores == sorted(scores, reverse=True), f"non-monotonic order: {scores}"
        assert _issues(result)[-1] == "Άρθρο 1: alpha"


class TestRerankSeesPostCoVeConfidence:
    """CoVe's revision is the last word on confidence, so rerank must run after it."""

    @pytest.mark.asyncio
    async def test_cove_revision_reorders_findings(self):
        findings = [
            _make("Άρθρο 1: alpha", confidence=0.9),
            _make("Άρθρο 2: beta", confidence=0.5),
        ]
        agg = BlackboardAggregator(cove=_DowngradingCoVe("alpha", new_score=0.1))

        result = await agg.aggregate(findings)

        assert _issues(result) == ["Άρθρο 2: beta", "Άρθρο 1: alpha"]


class TestNoveltyMeasuredAgainstSurvivors:
    """Novelty is relative to the population, so it must be computed on survivors."""

    @pytest.mark.asyncio
    async def test_refuted_twin_stops_suppressing_novelty(self):
        # `beta` is near-identical to `beta_twin`, so pre-filter novelty punishes
        # it. The skeptic refutes the twin; with rerank last, `beta` is measured
        # against the survivors only and is no longer penalised for a finding
        # that is no longer in the report.
        findings = [
            _make("Άρθρο 1: shared wording here", confidence=0.62),
            _make("Άρθρο 2: shared wording here", confidence=0.6),
            _make("Άρθρο 3: entirely different subject", confidence=0.6),
        ]
        agg = BlackboardAggregator(
            skeptic=CalibratedSkeptic(gates=[_RefuteGate("Άρθρο 2")]),
        )

        result = await agg.aggregate(findings)

        assert len(result) == 2
        # Its twin is gone, so the survivor's novelty is full and its higher
        # confidence puts it first. Under the old order it was ranked while the
        # twin still suppressed its novelty, and it came second.
        assert _issues(result)[0] == "Άρθρο 1: shared wording here"


class TestInlinePathMatchesBlackboardPath:
    """The non-blackboard path runs the same chain in the same order."""

    @pytest.mark.asyncio
    async def test_inline_chain_ranks_after_verification(self):
        findings = [
            _make("Άρθρο 1: alpha", confidence=0.9),
            _make("Άρθρο 2: beta", confidence=0.5),
        ]
        flow = BillAnalysisFlow(
            skeptic=CalibratedSkeptic(gates=[_DowngradeGate("alpha", -0.8)]),
            use_blackboard=False,
        )

        deduped = await flow._aggregate_inline_dedup(findings)
        result = await flow._aggregate_inline_verify(deduped, {})

        assert _issues(result) == ["Άρθρο 2: beta", "Άρθρο 1: alpha"]

    @pytest.mark.asyncio
    async def test_both_paths_agree_on_published_order(self):
        def _fresh() -> list[Finding]:
            return [
                _make("Άρθρο 1: alpha", confidence=0.9),
                _make("Άρθρο 2: beta", confidence=0.8),
                _make("Άρθρο 3: gamma", confidence=0.4),
            ]

        blackboard = await BlackboardAggregator(
            skeptic=CalibratedSkeptic(gates=[_DowngradeGate("alpha", -0.7)]),
        ).aggregate(_fresh())

        flow = BillAnalysisFlow(
            skeptic=CalibratedSkeptic(gates=[_DowngradeGate("alpha", -0.7)]),
            use_blackboard=False,
        )
        deduped = await flow._aggregate_inline_dedup(_fresh())
        inline = await flow._aggregate_inline_verify(deduped, {})

        assert _issues(blackboard) == _issues(inline)


class TestAggregationAlwaysCompletes:
    """Every terminal path records AGGREGATION_COMPLETED (event-spine invariant)."""

    @pytest.mark.asyncio
    async def test_completion_event_recorded_when_skeptic_refutes_everything(self):
        agg = BlackboardAggregator(
            skeptic=CalibratedSkeptic(gates=[_RefuteGate("Άρθρο")]),
        )

        result = await agg.aggregate([_make("Άρθρο 1: alpha", confidence=0.9)])

        assert result == []
        assert "aggregation_completed" in [e.event_type for e in agg.events]
