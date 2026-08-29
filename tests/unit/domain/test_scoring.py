"""Tests for domain/scoring — pure severity/novelty/confidence functions."""

from leggie.domain.models import (
    IRAC,
    Confidence,
    Finding,
    FindingType,
    Severity,
)
from leggie.domain.scoring import (
    combine_confidence,
    confidence_from_verification,
    score_novelty,
    score_severity,
)


def make_finding(
    finding_type: FindingType = FindingType.CONSTITUTIONAL,
    severity: str = "medium",
    confidence: float = 0.6,
) -> Finding:
    return Finding(
        finding_type=finding_type,
        irac=IRAC(issue="test", rule="r", application="a", conclusion="c"),
        confidence=Confidence.from_score(confidence),
        severity=Severity(severity),
        lens="test",
        model="test",
    )


def exact_similarity(a: Finding, b: Finding) -> float:
    """Exact match similarity for testing."""
    return 1.0 if a.irac.issue == b.irac.issue else 0.0


class TestScoreSeverity:
    def test_constitutional_is_high(self):
        f = make_finding(FindingType.CONSTITUTIONAL)
        assert score_severity(f) == Severity.HIGH

    def test_eu_compliance_is_high(self):
        f = make_finding(FindingType.EU_COMPLIANCE)
        assert score_severity(f) == Severity.HIGH

    def test_obligation_is_high(self):
        f = make_finding(FindingType.OBLIGATION_ENTITLEMENT)
        assert score_severity(f) == Severity.HIGH

    def test_procedural_is_low(self):
        f = make_finding(FindingType.PROCEDURAL)
        assert score_severity(f) == Severity.LOW

    def test_other_is_low(self):
        f = make_finding(FindingType.OTHER)
        assert score_severity(f) == Severity.LOW


class TestScoreNovelty:
    def test_no_existing_findings(self):
        f = make_finding()
        assert score_novelty(f, [], exact_similarity) == 1.0

    def test_duplicate_is_low_novelty(self):
        f = make_finding()
        dup = make_finding()  # Same issue text
        novelty = score_novelty(dup, [f], exact_similarity)
        assert novelty == 0.0

    def test_unique_is_high_novelty(self):
        f1 = make_finding()
        f2 = Finding(
            finding_type=FindingType.ECONOMIC,
            irac=IRAC(issue="different", rule="r", application="a", conclusion="c"),
            confidence=Confidence.from_score(0.5),
            lens="test",
            model="test",
        )
        novelty = score_novelty(f2, [f1], exact_similarity)
        assert novelty == 1.0


class TestCombineConfidence:
    def test_equal_weights(self):
        c = combine_confidence(0.8, 0.6, weight_evidence=0.5)
        assert c.score == 0.7

    def test_evidence_weighted(self):
        c = combine_confidence(0.8, 0.6, weight_evidence=0.4)
        assert abs(c.score - 0.68) < 0.01

    def test_returns_confidence_type(self):
        c = combine_confidence(0.5, 0.5)
        assert isinstance(c, Confidence)


class TestConfidenceFromVerification:
    def test_all_verified(self):
        c = confidence_from_verification(5, 5, 0)
        assert c.score > 0.7

    def test_half_verified(self):
        c = confidence_from_verification(2, 4, 0)
        assert 0.35 < c.score < 0.55

    def test_all_refuted(self):
        c = confidence_from_verification(0, 3, 3)
        assert c.score <= 0.2

    def test_no_citations(self):
        c = confidence_from_verification(0, 0, 0)
        assert c.score == 0.5
