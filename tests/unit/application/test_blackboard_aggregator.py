"""Tests for BlackboardAggregator — event-sourced aggregation pipeline (EN3)."""

import pytest

from leggie.application.services.blackboard_aggregator import (
    BlackboardAggregator,
    _finding_similarity_article_aware,
)
from leggie.domain.models import IRAC, Confidence, Finding, FindingType, Severity


def _make(issue: str, confidence: float = 0.8, finding_type=None,
           lens: str = "test", severity: str = "medium") -> Finding:
    return Finding(
        finding_type=finding_type or FindingType.CONSTITUTIONAL,
        irac=IRAC(issue=issue, rule="r", application="a", conclusion="c"),
        confidence=Confidence.from_score(confidence),
        severity=Severity(severity),
        lens=lens,
        model="test",
    )


class TestBlackboardAggregator:
    @pytest.mark.asyncio
    async def test_empty_findings(self):
        agg = BlackboardAggregator()
        result = await agg.aggregate([])
        assert result == []

    @pytest.mark.asyncio
    async def test_single_finding_passes_through(self):
        agg = BlackboardAggregator()
        findings = [_make("Άρθρο 1: alpha beta gamma")]
        result = await agg.aggregate(findings)
        assert len(result) == 1
        assert result[0].irac.issue == "Άρθρο 1: alpha beta gamma"

    @pytest.mark.asyncio
    async def test_dedup_collapses_near_duplicates(self):
        agg = BlackboardAggregator(dedup_threshold=0.5)
        findings = [
            _make("Άρθρο 1: alpha beta gamma", confidence=0.9),
            _make("Άρθρο 1: alpha beta delta", confidence=0.7),
        ]
        result = await agg.aggregate(findings)
        assert len(result) == 1
        assert result[0].confidence.score == 0.9

    @pytest.mark.asyncio
    async def test_different_articles_preserved(self):
        agg = BlackboardAggregator(dedup_threshold=0.5)
        findings = [
            _make("Άρθρο 1: alpha beta", confidence=0.9),
            _make("Άρθρο 5: alpha beta", confidence=0.8),
        ]
        result = await agg.aggregate(findings)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_events_recorded(self):
        agg = BlackboardAggregator(dedup_threshold=0.5)
        findings = [
            _make("Άρθρο 1: alpha beta", confidence=0.9),
            _make("Άρθρο 1: alpha beta delta", confidence=0.7),
        ]
        await agg.aggregate(findings)
        event_types = [e.event_type for e in agg.events]
        assert "finding_created" in event_types
        assert "aggregation_completed" in event_types


class TestSimilarityFunction:
    def test_same_article_similar(self):
        a = _make("Άρθρο 1: delegation limits exceeded")
        b = _make("Άρθρο 1: delegation limits overreach")
        assert _finding_similarity_article_aware(a, b) > 0.5

    def test_different_article_not_similar(self):
        a = _make("Άρθρο 1: delegation limits exceeded")
        b = _make("Άρθρο 5: delegation limits exceeded")
        assert _finding_similarity_article_aware(a, b) == 0.0

    def test_different_type_not_similar(self):
        a = _make("alpha beta", finding_type=FindingType.CONSTITUTIONAL)
        b = _make("alpha beta", finding_type=FindingType.ECONOMIC)
        assert _finding_similarity_article_aware(a, b) == 0.0
