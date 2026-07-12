"""Tests for domain specs — composable business rules."""

from leggie.domain.models import (
    IRAC,
    Citation,
    CitationScheme,
    Confidence,
    Finding,
    FindingType,
    Severity,
)
from leggie.domain.specs import (
    CitationResolves,
    FindingAdmissible,
    MeetsSeverityThreshold,
)


def make_finding(
    finding_type=FindingType.OTHER, confidence_score=0.5, severity="medium", evidence=None
):
    return Finding(
        finding_type=finding_type,
        irac=IRAC(issue="x", rule="y", application="z", conclusion="w"),
        confidence=Confidence.from_score(confidence_score),
        severity=Severity(severity),
        lens="test",
        model="test",
        evidence=evidence or [],
    )


class TestFindingAdmissible:
    def test_above_default_threshold(self):
        finding = make_finding(confidence_score=0.7)
        spec = FindingAdmissible()
        assert spec.is_satisfied_by(finding) is True

    def test_below_threshold(self):
        finding = make_finding(confidence_score=0.3)
        spec = FindingAdmissible()
        assert spec.is_satisfied_by(finding) is False

    def test_custom_threshold(self):
        finding = make_finding(confidence_score=0.8)
        spec = FindingAdmissible(threshold=0.9)
        assert spec.is_satisfied_by(finding) is False


class TestCitationResolves:
    def test_resolved_citation(self):
        cite = Citation(
            scheme=CitationScheme.FEK,
            identifier="ΦΕΚ Α 137/2023",
            original_text="ΦΕΚ Α 137/2023",
            resolved=True,
        )
        spec = CitationResolves()
        assert spec.is_satisfied_by(cite) is True

    def test_unresolved_citation(self):
        cite = Citation(
            scheme=CitationScheme.FEK,
            identifier="ΦΕΚ Α 999/2099",
            original_text="ΦΕΚ Α 999/2099",
            resolved=False,
        )
        spec = CitationResolves()
        assert spec.is_satisfied_by(cite) is False

    def test_with_index(self):
        cite = Citation(
            scheme=CitationScheme.FEK,
            identifier="ΦΕΚ Α 137/2023",
            original_text="ΦΕΚ Α 137/2023",
        )
        spec = CitationResolves(resolution_index={"ΦΕΚ Α 137/2023", "ΦΕΚ Β 42/2022"})
        assert spec.is_satisfied_by(cite) is True

        cite2 = Citation(
            scheme=CitationScheme.FEK,
            identifier="ΦΕΚ Α 999/2099",
            original_text="ΦΕΚ Α 999/2099",
        )
        assert spec.is_satisfied_by(cite2) is False


class TestMeetsSeverityThreshold:
    def test_critical_passes_low(self):
        finding = make_finding(severity="critical")
        spec = MeetsSeverityThreshold(min_severity="low")
        assert spec.is_satisfied_by(finding) is True

    def test_info_fails_medium(self):
        finding = make_finding(severity="info")
        spec = MeetsSeverityThreshold(min_severity="medium")
        assert spec.is_satisfied_by(finding) is False

    def test_high_passes_high(self):
        finding = make_finding(severity="high")
        spec = MeetsSeverityThreshold(min_severity="high")
        assert spec.is_satisfied_by(finding) is True


class TestCompositeSpecs:
    def test_and_spec(self):
        finding = make_finding(confidence_score=0.8, severity="high")
        spec = FindingAdmissible() & MeetsSeverityThreshold(min_severity="high")
        assert spec.is_satisfied_by(finding) is True

    def test_and_spec_fails(self):
        finding = make_finding(confidence_score=0.8, severity="info")
        spec = FindingAdmissible() & MeetsSeverityThreshold(min_severity="high")
        assert spec.is_satisfied_by(finding) is False

    def test_or_spec(self):
        finding = make_finding(confidence_score=0.3, severity="critical")
        spec = FindingAdmissible() | MeetsSeverityThreshold(min_severity="critical")
        assert spec.is_satisfied_by(finding) is True

    def test_not_spec(self):
        finding = make_finding(confidence_score=0.3)
        spec = ~FindingAdmissible()
        assert spec.is_satisfied_by(finding) is True
