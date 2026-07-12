"""Tests for domain models — frozen Pydantic entities and value objects."""

from uuid import UUID

import pytest
from pydantic import ValidationError

from leggie.domain.models import (
    IRAC,
    Article,
    Citation,
    CitationScheme,
    Confidence,
    ConfidenceGrade,
    Document,
    Event,
    EventType,
    Evidence,
    Finding,
    FindingType,
    Paragraph,
    Severity,
    SubParagraph,
)


class TestConfidence:
    def test_from_score_certain(self):
        c = Confidence.from_score(0.98)
        assert c.grade == ConfidenceGrade.CERTAIN
        assert c.score == 0.98

    def test_from_score_high(self):
        c = Confidence.from_score(0.88)
        assert c.grade == ConfidenceGrade.HIGH

    def test_from_score_medium(self):
        c = Confidence.from_score(0.75)
        assert c.grade == ConfidenceGrade.MEDIUM

    def test_from_score_low(self):
        c = Confidence.from_score(0.60)
        assert c.grade == ConfidenceGrade.LOW

    def test_from_score_very_low(self):
        c = Confidence.from_score(0.30)
        assert c.grade == ConfidenceGrade.VERY_LOW

    def test_from_score_abstain(self):
        c = Confidence.from_score(0.10)
        assert c.grade == ConfidenceGrade.ABSTAIN

    def test_above_threshold(self):
        c = Confidence.from_score(0.70)
        assert c.above_threshold(0.5) is True
        assert c.above_threshold(0.8) is False

    def test_frozen(self):
        c = Confidence.from_score(0.8)
        with pytest.raises(ValidationError):
            c.score = 0.5


class TestCitation:
    def test_create_fek(self):
        cite = Citation(
            scheme=CitationScheme.FEK,
            identifier="ΦΕΚ Α 137/2023",
            original_text="ΦΕΚ Α 137/2023",
        )
        assert cite.scheme == CitationScheme.FEK
        assert cite.identifier == "ΦΕΚ Α 137/2023"
        assert cite.resolved is False

    def test_create_celex(self):
        cite = Citation(
            scheme=CitationScheme.CELEX,
            identifier="32018L1972",
            original_text="CELEX:32018L1972",
        )
        assert cite.scheme == CitationScheme.CELEX

    def test_resolved_citation(self):
        cite = Citation(
            scheme=CitationScheme.FEK,
            identifier="ΦΕΚ Α 137/2023",
            original_text="ΦΕΚ Α 137/2023",
            resolved=True,
            resolution_evidence="verified against gov-et-laws index",
        )
        assert cite.resolved is True

    def test_identifier_not_empty(self):
        with pytest.raises(ValidationError):
            Citation(
                scheme=CitationScheme.FEK,
                identifier="   ",
                original_text="ΦΕΚ",
            )


class TestIRAC:
    def test_create_irac(self):
        irac = IRAC(
            issue="Does Article 3 exceed constitutional delegation limits?",
            rule="Article 43 of the Constitution limits delegation of legislative power",
            application="Article 3 grants broad rule-making authority without defined criteria",
            conclusion="Article 3 likely violates Article 43",
        )
        assert irac.issue.startswith("Does")
        assert irac.conclusion.startswith("Article 3")


class TestFinding:
    def test_create_finding(self):
        finding = Finding(
            finding_type=FindingType.CONSTITUTIONAL,
            irac=IRAC(
                issue="Test issue",
                rule="Test rule",
                application="Test application",
                conclusion="Test conclusion",
            ),
            confidence=Confidence.from_score(0.85),
            lens="constitutional",
            model="claude-sonnet-4",
        )
        assert isinstance(finding.id, UUID)
        assert finding.finding_type == FindingType.CONSTITUTIONAL
        assert finding.severity == Severity.MEDIUM
        assert finding.version == 1
        assert finding.is_admissible() is True

    def test_finding_is_admissible_below_threshold(self):
        finding = Finding(
            finding_type=FindingType.FACTUAL,
            irac=IRAC(issue="x", rule="y", application="z", conclusion="w"),
            confidence=Confidence.from_score(0.3),
            lens="test",
            model="test-model",
        )
        assert finding.is_admissible() is False

    def test_finding_with_evidence(self):
        finding = Finding(
            finding_type=FindingType.EU_COMPLIANCE,
            irac=IRAC(issue="x", rule="y", application="z", conclusion="w"),
            confidence=Confidence.from_score(0.9),
            lens="eu",
            model="test-model",
            evidence=[
                Evidence(
                    citation=Citation(
                        scheme=CitationScheme.CELEX,
                        identifier="32018L1972",
                        original_text="CELEX:32018L1972",
                        resolved=True,
                    ),
                    text_excerpt="Directive 2018/1972 defines...",
                    verdict="supports",
                )
            ],
        )
        assert len(finding.evidence) == 1
        assert finding.evidence[0].citation.resolved is True

    def test_finding_frozen(self):
        finding = Finding(
            finding_type=FindingType.OTHER,
            irac=IRAC(issue="x", rule="y", application="z", conclusion="w"),
            confidence=Confidence.from_score(0.5),
            lens="test",
            model="test",
        )
        with pytest.raises(ValidationError):
            finding.lens = "changed"


class TestArticle:
    def test_create_article(self):
        article = Article(
            id="1",
            title="Test Article",
            raw_text="Άρθρο 1 test content",
            paragraphs=[
                Paragraph(number="1", text="Paragraph 1 text"),
            ],
        )
        assert article.id == "1"
        assert len(article.paragraphs) == 1
        assert article.paragraph_by_number("1") is not None
        assert article.paragraph_by_number("2") is None

    def test_article_with_subparagraphs(self):
        article = Article(
            id="2",
            raw_text="Άρθρο 2 test",
            paragraphs=[
                Paragraph(
                    number="1",
                    text="Main paragraph",
                    subparagraphs=[
                        SubParagraph(letter="α", text="Sub alpha"),
                        SubParagraph(letter="β", text="Sub beta"),
                    ],
                ),
            ],
        )
        assert len(article.paragraphs[0].subparagraphs) == 2
        assert article.paragraphs[0].subparagraphs[0].letter == "α"


class TestDocument:
    def test_create_document(self):
        doc = Document(
            title="Test Bill",
            source_format="pdf",
            articles=[
                Article(id="1", raw_text="Άρθρο 1"),
                Article(id="2", raw_text="Άρθρο 2"),
            ],
            preamble="Preamble text",
            raw_text="Full bill text",
        )
        assert len(doc.articles) == 2
        assert doc.preamble == "Preamble text"

    def test_document_auto_id(self):
        doc = Document(title="Test", source_format="txt", raw_text="text")
        assert doc.document_id is not None


class TestEvent:
    def test_create_event(self):
        event = Event(
            event_type=EventType.ANALYSIS_STARTED,
            aggregate_id="run-001",
            data={"bill_id": "bill-001"},
        )
        assert event.event_type == EventType.ANALYSIS_STARTED
        assert isinstance(event.id, UUID)

    def test_event_frozen(self):
        event = Event(
            event_type=EventType.WORKFLOW_COMPLETED,
            aggregate_id="run-001",
        )
        with pytest.raises(ValidationError):
            event.event_type = EventType.ANALYSIS_STARTED
