"""Tests for Constitutional Lens — pattern-based legal analysis."""

import pytest

from leggie.application.agents.constitutional_lens import ConstitutionalLens
from leggie.domain.models import Article, FindingType

SAMPLE_ARTICLE = Article(
    id="1",
    title="Σκοπός",
    raw_text=(
        "Άρθρο 1 – Σκοπός\n"
        "1. Σκοπός του παρόντος νόμου είναι η ρύθμιση της ψηφιακής διακυβέρνησης.\n"
        "2. Με προεδρικό διάταγμα που εκδίδεται με πρόταση του Υπουργού, "
        "είναι δυνατή η εξουσιοδότηση για την έκδοση κανονιστικών πράξεων.\n"
        "3. Οι διατάξεις του παρόντος εφαρμόζονται αναδρομικά από 1.1.2025."
    ),
)

SIMPLE_ARTICLE = Article(
    id="2",
    title="Απλό άρθρο",
    raw_text=(
        "Άρθρο 2\n1. Η ισχύς του παρόντος αρχίζει από τη δημοσίευσή του στην Εφημερίδα "
        "της Κυβερνήσεως."
    ),
)


class TestConstitutionalLens:
    @pytest.mark.asyncio
    async def test_name(self):
        lens = ConstitutionalLens()
        assert lens.name() == "constitutional"

    @pytest.mark.asyncio
    async def test_description(self):
        lens = ConstitutionalLens()
        assert "Constitution" in lens.description()

    @pytest.mark.asyncio
    async def test_analyze_returns_findings(self):
        lens = ConstitutionalLens()
        findings = await lens.analyze(SAMPLE_ARTICLE)
        assert len(findings) > 0

    @pytest.mark.asyncio
    async def test_analyze_detects_delegation(self):
        lens = ConstitutionalLens()
        findings = await lens.analyze(SAMPLE_ARTICLE)
        constitutional = [f for f in findings if f.finding_type == FindingType.CONSTITUTIONAL]
        # Should find at least one constitutional finding (delegation patterns)
        assert len(constitutional) >= 1

    @pytest.mark.asyncio
    async def test_analyze_returns_empty_for_simple_article(self):
        """F2: No findings for articles with no constitutional issues."""
        lens = ConstitutionalLens()
        simple = Article(
            id="99",
            raw_text="\u0386\u03c1\u03b8\u03c1\u03bf 99\n1. \u0391\u03c0\u03bb\u03ae \u03b4\u03b9\u03ac\u03c4\u03b1\u03be\u03b7 \u03c7\u03c9\u03c1\u03af\u03c2 \u03b6\u03b7\u03c4\u03ae\u03bc\u03b1\u03c4\u03b1.",
        )
        findings = await lens.analyze(simple)
        assert len(findings) == 0

    @pytest.mark.asyncio
    async def test_finding_has_irac(self):
        lens = ConstitutionalLens()
        findings = await lens.analyze(SAMPLE_ARTICLE)
        for f in findings:
            assert f.irac.issue
            assert f.irac.rule
            assert f.irac.application
            assert f.irac.conclusion

    @pytest.mark.asyncio
    async def test_finding_has_confidence(self):
        lens = ConstitutionalLens()
        findings = await lens.analyze(SAMPLE_ARTICLE)
        for f in findings:
            assert f.confidence.score > 0

    @pytest.mark.asyncio
    async def test_finding_has_lens_and_model(self):
        lens = ConstitutionalLens()
        findings = await lens.analyze(SAMPLE_ARTICLE)
        for f in findings:
            assert f.lens == "constitutional"
            assert f.model in ("regex-fallback", "google/gemini-2.5-flash:free")

    @pytest.mark.asyncio
    async def test_analyze_empty_article(self):
        lens = ConstitutionalLens()
        empty = Article(id="99", raw_text="")
        findings = await lens.analyze(empty)
        assert len(findings) == 0  # No baseline for empty articles
