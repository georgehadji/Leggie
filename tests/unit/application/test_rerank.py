"""Tests for Rerank service — scoring and ordering findings."""

import logging

import pytest

from leggie.application.ports.reranker import RerankerPort, RerankResult
from leggie.application.services.rerank import (
    CompositeReranker,
    ModelBasedReranker,
    ScoredFinding,
)
from leggie.domain.models import (
    IRAC,
    Confidence,
    Event,
    EventType,
    Finding,
    FindingType,
    Severity,
)


def make_finding(
    issue: str = "test issue",
    severity: str = "medium",
    confidence: float = 0.5,
    finding_type: FindingType = FindingType.CONSTITUTIONAL,
) -> Finding:
    return Finding(
        finding_type=finding_type,
        irac=IRAC(issue=issue, rule="rule", application="app", conclusion="conc"),
        confidence=Confidence.from_score(confidence),
        severity=Severity(severity),
        lens="test",
        model="test",
    )


class TestCompositeReranker:
    @pytest.mark.asyncio
    async def test_score_returns_scored_finding(self):
        reranker = CompositeReranker()
        finding = make_finding()
        scored = await reranker.score(finding, [finding])
        assert isinstance(scored, ScoredFinding)
        assert scored.composite_score > 0

    @pytest.mark.asyncio
    async def test_severity_weights(self):
        reranker = CompositeReranker()
        critical = await reranker.score(make_finding(severity="critical", issue="critical"), [])
        low = await reranker.score(make_finding(severity="low", issue="low"), [])
        assert critical.severity_score > low.severity_score

    @pytest.mark.asyncio
    async def test_confidence_affects_score(self):
        reranker = CompositeReranker()
        high_conf = await reranker.score(make_finding(confidence=0.9, issue="high"), [])
        low_conf = await reranker.score(make_finding(confidence=0.3, issue="low"), [])
        assert high_conf.composite_score > low_conf.composite_score

    @pytest.mark.asyncio
    async def test_rerank_orders_by_score(self):
        reranker = CompositeReranker()
        findings = [
            make_finding(severity="low", confidence=0.3, issue="A"),
            make_finding(severity="high", confidence=0.9, issue="B"),
            make_finding(severity="medium", confidence=0.5, issue="C"),
        ]
        scored = await reranker.rerank(findings)
        assert len(scored) == 3
        # Highest score first
        assert scored[0].composite_score >= scored[1].composite_score
        assert scored[1].composite_score >= scored[2].composite_score

    @pytest.mark.asyncio
    async def test_rerank_empty(self):
        reranker = CompositeReranker()
        scored = await reranker.rerank([])
        assert scored == []

    @pytest.mark.asyncio
    async def test_novelty_different_findings(self):
        reranker = CompositeReranker()
        finding = make_finding(issue="unique constitutional issue about delegation")
        duplicate = make_finding(issue="unique constitutional issue about delegation")
        novelty = reranker._compute_novelty(finding, [finding, duplicate])
        assert novelty < 1.0  # Not fully novel — text overlaps

    @pytest.mark.asyncio
    async def test_novelty_single_finding(self):
        reranker = CompositeReranker()
        finding = make_finding()
        novelty = reranker._compute_novelty(finding, [finding])
        assert novelty == 1.0  # Only one finding = fully novel


class _CountingRerankerPort(RerankerPort):
    """Fake rerank port: counts calls and records the documents it was sent."""

    def __init__(self, scores: list[float] | None = None, fail: bool = False) -> None:
        self.calls = 0
        self.received_documents: list[list[str]] = []
        self._scores = scores
        self._fail = fail

    async def rerank(self, query, documents, model="", top_k=None):  # type: ignore[no-untyped-def]
        self.calls += 1
        self.received_documents.append(list(documents))
        if self._fail:
            raise RuntimeError("rerank service unavailable")
        scores = self._scores if self._scores is not None else [1.0] * len(documents)
        return [
            RerankResult(index=i, relevance_score=scores[i])
            for i in range(min(len(documents), len(scores)))
        ]


def _fell_back_per_finding(scored: ScoredFinding) -> bool:
    """True when this finding took the per-finding composite fallback.

    `_apply_score` delegates to CompositeReranker for a finding the model did
    not score, which fills the component scores; a model-scored finding has them
    zeroed. Note this does NOT detect the whole-batch failure path, where
    `_compute_batch_scores` collapses composite results down to bare scores —
    check the composite_score value for that.
    """
    return scored.severity_score != 0.0 or scored.novelty_score != 0.0


class TestModelBasedReranker:
    """The rerank model must be re-queried for every batch.

    Batch scores were once cached in an instance field that was never reset. A
    second `rerank()` on the same instance then skipped the model call: none of
    the new batch's finding IDs were in the stale dict, so every lookup missed
    and every finding silently fell through to composite scoring. Nothing logged
    or raised — the reranker just quietly stopped being model-based.
    """

    @pytest.mark.asyncio
    async def test_second_rerank_queries_the_model_again(self):
        port = _CountingRerankerPort(scores=[0.9, 0.8])
        reranker = ModelBasedReranker(reranker_port=port)

        first = [make_finding(issue="first batch A"), make_finding(issue="first batch B")]
        second = [make_finding(issue="second batch A"), make_finding(issue="second batch B")]

        await reranker.rerank(first)
        second_scored = await reranker.rerank(second)

        assert port.calls == 2, "the model was not re-queried for the second batch"
        # The second batch's own text reached the model, not the first batch's.
        assert any("second batch" in doc for doc in port.received_documents[1])
        # And its findings carry model scores, not silent composite fallbacks.
        assert not any(_fell_back_per_finding(s) for s in second_scored)
        assert [s.composite_score for s in second_scored] == [0.9, 0.8]

    @pytest.mark.asyncio
    async def test_model_scores_drive_the_order(self):
        # Deliberately inverted: the finding listed first gets the lowest score.
        port = _CountingRerankerPort(scores=[0.1, 0.95])
        reranker = ModelBasedReranker(reranker_port=port)
        findings = [make_finding(issue="listed first"), make_finding(issue="listed second")]

        scored = await reranker.rerank(findings)

        assert [s.finding.irac.issue for s in scored] == ["listed second", "listed first"]
        assert [s.composite_score for s in scored] == [0.95, 0.1]

    @pytest.mark.asyncio
    async def test_falls_back_to_composite_when_the_port_raises(self):
        port = _CountingRerankerPort(fail=True)
        reranker = ModelBasedReranker(reranker_port=port)
        findings = [make_finding(severity="critical", confidence=0.9, issue="A")]

        scored = await reranker.rerank(findings)
        expected = await CompositeReranker().score(findings[0], findings)

        assert port.calls == 1
        assert len(scored) == 1
        assert scored[0].composite_score == expected.composite_score

    @pytest.mark.asyncio
    async def test_findings_the_model_skipped_fall_back_to_composite(self):
        # Two findings sent, only one score returned.
        port = _CountingRerankerPort(scores=[0.9])
        reranker = ModelBasedReranker(reranker_port=port)
        findings = [make_finding(issue="scored"), make_finding(issue="unscored")]

        scored = await reranker.rerank(findings)

        assert len(scored) == 2
        by_issue = {s.finding.irac.issue: s for s in scored}
        assert not _fell_back_per_finding(by_issue["scored"])
        assert _fell_back_per_finding(by_issue["unscored"])

    @pytest.mark.asyncio
    async def test_empty_batch_does_not_call_the_model(self):
        port = _CountingRerankerPort()
        reranker = ModelBasedReranker(reranker_port=port)

        assert await reranker.rerank([]) == []
        assert port.calls == 0


class TestModelBasedRerankerDegradation:
    """A whole-batch port failure must be surfaced, never silently masked.

    `_compute_batch_scores` used to catch the port's exception and fall back to
    composite scoring with nothing logged or raised — the only guard of its
    kind in the verification chain without a signal. It now logs a warning and,
    when wired with an `on_degradation` callback (as `BillAnalysisFlow` does),
    emits an `EventType.DEGRADED` event — the same mechanism lenses already use.
    """

    @pytest.mark.asyncio
    async def test_port_failure_is_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        port = _CountingRerankerPort(fail=True)
        reranker = ModelBasedReranker(reranker_port=port)
        findings = [make_finding(issue="A")]

        with caplog.at_level(logging.WARNING):
            await reranker.rerank(findings)

        assert any("reranker_port_failed" in r.message for r in caplog.records), (
            "reranker-port failure fell back to composite scoring with no log record"
        )

    @pytest.mark.asyncio
    async def test_port_failure_emits_degraded_event(self) -> None:
        port = _CountingRerankerPort(fail=True)
        events: list[Event] = []
        reranker = ModelBasedReranker(reranker_port=port, on_degradation=events.append)
        findings = [make_finding(issue="A"), make_finding(issue="B")]

        await reranker.rerank(findings)

        assert len(events) == 1
        assert events[0].event_type == EventType.DEGRADED
        assert events[0].data["component"] == "ModelBasedReranker"
        assert events[0].data["batch_size"] == 2

    @pytest.mark.asyncio
    async def test_broken_on_degradation_callback_does_not_crash_rerank(self) -> None:
        port = _CountingRerankerPort(fail=True)

        def broken_callback(_event: Event) -> None:
            raise RuntimeError("observer exploded")

        reranker = ModelBasedReranker(reranker_port=port, on_degradation=broken_callback)
        findings = [make_finding(issue="A")]

        scored = await reranker.rerank(findings)

        assert len(scored) == 1  # still falls back to composite scoring

    @pytest.mark.asyncio
    async def test_no_callback_registered_is_a_noop(self) -> None:
        port = _CountingRerankerPort(fail=True)
        reranker = ModelBasedReranker(reranker_port=port)  # on_degradation defaults to None

        scored = await reranker.rerank([make_finding(issue="A")])

        assert len(scored) == 1


class _ArbitraryIndexRerankerPort(RerankerPort):
    """Fake rerank port that returns exactly the RerankResults it's given,
    including malformed ones — for testing index validation."""

    def __init__(self, results: list[RerankResult]) -> None:
        self._results = results

    async def rerank(self, query, documents, model="", top_k=None):  # type: ignore[no-untyped-def]
        return self._results


class TestModelBasedRerankerIndexValidation:
    """DH-16: `_compute_batch_scores` checked a RerankResult.index only
    against the upper bound (`r.index < len(findings)`). RerankResult comes
    from an external HTTP API response (untrusted-boundary data, same class
    DH-5 hardened for chat completions) — a negative index passes that
    upper-bound check and Python's negative-indexing semantics then map it
    to the LAST finding via `findings[r.index]`, silently attributing that
    result's score to the wrong finding instead of being rejected like an
    out-of-range positive index already was.
    """

    @pytest.mark.asyncio
    async def test_negative_index_does_not_misattribute_score(self):
        port = _ArbitraryIndexRerankerPort(
            [
                RerankResult(index=0, relevance_score=0.1),
                RerankResult(index=-1, relevance_score=0.99),
            ]
        )
        reranker = ModelBasedReranker(reranker_port=port)
        findings = [make_finding(issue="first"), make_finding(issue="second")]

        scored = await reranker.rerank(findings)

        by_issue = {s.finding.irac.issue: s for s in scored}
        # The real model score for index 0 lands on the first finding...
        assert by_issue["first"].composite_score == 0.1
        # ...and the second finding must NOT silently inherit the score meant
        # for index -1 — it falls back to composite scoring instead, same as
        # any other finding the model didn't score (preserved, not dropped).
        assert by_issue["second"].composite_score != 0.99
        assert _fell_back_per_finding(by_issue["second"])

    @pytest.mark.asyncio
    async def test_in_range_indices_unaffected(self):
        """No-regression: legitimate results (0 <= index < len) still map
        directly to their finding, unchanged from before this fix."""
        port = _ArbitraryIndexRerankerPort(
            [
                RerankResult(index=0, relevance_score=0.3),
                RerankResult(index=1, relevance_score=0.7),
            ]
        )
        reranker = ModelBasedReranker(reranker_port=port)
        findings = [make_finding(issue="first"), make_finding(issue="second")]

        scored = await reranker.rerank(findings)

        by_issue = {s.finding.irac.issue: s for s in scored}
        assert by_issue["first"].composite_score == 0.3
        assert by_issue["second"].composite_score == 0.7
