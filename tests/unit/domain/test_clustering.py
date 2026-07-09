"""Tests for domain/clustering — pure dedup/cluster functions."""

from leggie.domain.clustering import cluster, deduplicate, merge_findings
from leggie.domain.models import (
    Finding, IRAC, Confidence, FindingType, Severity,
)


def make_finding(issue: str, confidence=0.5, severity="medium") -> Finding:
    return Finding(
        finding_type=FindingType.CONSTITUTIONAL,
        irac=IRAC(issue=issue, rule="r", application="a", conclusion="c"),
        confidence=Confidence.from_score(confidence),
        severity=Severity(severity),
        lens="test", model="test",
    )


def keyword_similarity(a: Finding, b: Finding) -> float:
    """Keyword overlap similarity for testing."""
    a_set = set(a.irac.issue.lower().split())
    b_set = set(b.irac.issue.lower().split())
    if not a_set or not b_set:
        return 0.0
    return len(a_set & b_set) / max(len(a_set), len(b_set))


class TestCluster:
    def test_empty(self):
        assert cluster([], keyword_similarity) == []

    def test_single(self):
        f = make_finding("test")
        result = cluster([f], keyword_similarity)
        assert len(result) == 1
        assert len(result[0]) == 1

    def test_all_unique(self):
        findings = [
            make_finding("alpha beta"),
            make_finding("gamma delta"),
            make_finding("epsilon zeta"),
        ]
        result = cluster(findings, keyword_similarity, threshold=0.8)
        assert len(result) == 3  # Each in its own cluster

    def test_groups_similar(self):
        findings = [
            make_finding("alpha beta gamma"),
            make_finding("alpha beta delta"),  # 2/3 overlap
            make_finding("zeta eta theta"),
        ]
        result = cluster(findings, keyword_similarity, threshold=0.6)
        # First two should cluster together
        assert len(result) == 2


class TestDeduplicate:
    def test_empty(self):
        assert deduplicate([], keyword_similarity) == []

    def test_keeps_highest_confidence(self):
        f1 = make_finding("alpha beta gamma", confidence=0.3)
        f2 = make_finding("alpha beta delta", confidence=0.9)  # Similar
        result = deduplicate([f1, f2], keyword_similarity, threshold=0.5, keep="highest_confidence")
        assert len(result) == 1
        assert result[0].confidence.score == 0.9

    def test_keeps_most_severe(self):
        f1 = make_finding("alpha beta gamma", severity="low")
        f2 = make_finding("alpha beta delta", severity="critical")
        result = deduplicate([f1, f2], keyword_similarity, threshold=0.5, keep="most_severe")
        assert len(result) == 1
        assert result[0].severity == Severity.CRITICAL


class TestMergeFindings:
    def test_merge_dedups(self):
        list_a = [make_finding("alpha beta")]
        list_b = [make_finding("alpha gamma")]  # Overlaps
        result = merge_findings(list_a, list_b, keyword_similarity, threshold=0.5)
        assert len(result) == 1

    def test_merge_keeps_unique(self):
        list_a = [make_finding("alpha beta")]
        list_b = [make_finding("zeta eta")]
        result = merge_findings(list_a, list_b, keyword_similarity, threshold=0.8)
        assert len(result) == 2
