"""Tests for Rerank service — scoring and ordering findings."""

import pytest
from leggie.application.services.rerank import CompositeReranker, ScoredFinding
from leggie.domain.models import Finding, IRAC, Confidence, FindingType, Severity


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
        lens="test", model="test",
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
        critical = await reranker.score(
            make_finding(severity="critical", issue="critical"), []
        )
        low = await reranker.score(
            make_finding(severity="low", issue="low"), []
        )
        assert critical.severity_score > low.severity_score

    @pytest.mark.asyncio
    async def test_confidence_affects_score(self):
        reranker = CompositeReranker()
        high_conf = await reranker.score(
            make_finding(confidence=0.9, issue="high"), []
        )
        low_conf = await reranker.score(
            make_finding(confidence=0.3, issue="low"), []
        )
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
