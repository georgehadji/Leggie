"""Tests for citation parser — deterministic Greek legal citation extraction."""

import pytest

from leggie.domain.models import Citation, CitationScheme
from leggie.infrastructure.citation import GreekCitationParser


@pytest.fixture
def parser():
    return GreekCitationParser()


class TestGreekCitationParser:
    def test_parse_fek_simple(self, parser):
        text = "ΦΕΚ Α 137/2023"
        citations = parser.parse(text)
        assert len(citations) == 1
        assert citations[0].scheme == CitationScheme.FEK
        assert "137" in citations[0].identifier
        assert "2023" in citations[0].identifier

    def test_parse_fek_with_teyxos(self, parser):
        text = "ΦΕΚ Τεύχος Β 42/2022"
        citations = parser.parse(text)
        assert len(citations) == 1

    def test_parse_fek_multiple(self, parser):
        text = "ΦΕΚ Α 10/2024 και ΦΕΚ Β 20/2024"
        citations = parser.parse(text)
        assert len(citations) == 2

    def test_parse_celex(self, parser):
        text = "CELEX:32018L1972"
        citations = parser.parse(text)
        assert len(citations) == 1
        assert citations[0].scheme == CitationScheme.CELEX

    def test_parse_ecli(self, parser):
        text = "ECLI:GR:ΣτΕ:2023:1234"
        citations = parser.parse(text)
        assert len(citations) == 1
        assert citations[0].scheme == CitationScheme.ECLI

    def test_parse_url(self, parser):
        text = "https://www.et.gr/ΦΕΚ/Α/2023"
        citations = parser.parse(text)
        assert len(citations) >= 1

    def test_parse_mixed(self, parser):
        text = "ΦΕΚ Α 137/2023, CELEX:32018L1972, ECLI:GR:ΣτΕ:2023:1234"
        citations = parser.parse(text)
        assert len(citations) == 3

    def test_parse_no_citations(self, parser):
        text = "Απλό κείμενο χωρίς παραπομπές σε νομοθεσία."
        citations = parser.parse(text)
        assert len(citations) == 0

    def test_supported_schemes(self, parser):
        schemes = parser.supported_schemes()
        assert CitationScheme.FEK in schemes
        assert CitationScheme.CELEX in schemes
        assert CitationScheme.ECLI in schemes

    @pytest.mark.asyncio
    async def test_resolve_with_index(self):
        index = {"ΦΕΚ Α 137/2023", "ΦΕΚ Β 42/2022"}
        parser = GreekCitationParser(resolution_index=index)

        cite = Citation(
            scheme=CitationScheme.FEK,
            identifier="ΦΕΚ Α 137/2023",
            original_text="ΦΕΚ Α 137/2023",
        )
        resolved = await parser.resolve(cite)
        assert resolved.resolved is True
        assert "resolved against internal index" in (resolved.resolution_evidence or "")

        cite2 = Citation(
            scheme=CitationScheme.FEK,
            identifier="ΦΕΚ Α 999/2099",
            original_text="ΦΕΚ Α 999/2099",
        )
        resolved2 = await parser.resolve(cite2)
        assert resolved2.resolved is False
        assert "not found in index" in (resolved2.resolution_evidence or "")

    @pytest.mark.asyncio
    async def test_resolve_without_index(self, parser):
        cite = Citation(
            scheme=CitationScheme.FEK,
            identifier="ΦΕΚ Α 137/2023",
            original_text="ΦΕΚ Α 137/2023",
        )
        resolved = await parser.resolve(cite)
        # Fail closed: no index means nothing was actually checked, so it must
        # not be reported as resolved.
        assert resolved.resolved is False
        assert "not independently verified" in (resolved.resolution_evidence or "")


class TestLawReferencePatternNotWired:
    """DH-28 (R7): LAW_REF_PATTERN (module-level, "Individual law references:
    Ν. ΧΧΧΧ/Έτος") has existed since the initial MVP commit but is never
    invoked by parse() — individual-law citations like "Ν. 4622/2019", one of
    the most common cross-reference formats in Greek bills (amending "Ν. XXXX
    /YYYY" is routine statutory drafting), are silently never extracted at
    all; they don't even reach "unverified" status.

    [REQUIRES HUMAN REVIEW] — not fixed. Naively wiring the pattern in is not
    a safe small patch: see the second test below, which proves it would be
    actively HARMFUL given the current resolution index, not merely
    incomplete. A correct fix needs either (a) a dedicated CitationScheme
    member for law references (a Domain change — hook-blocked, its own
    class-A change) plus populating the index with real law identifiers, or
    (b) making CoVeVerifier._check_citations scheme-aware about what
    "disproven" means (R4 territory, already re-verified/closed this
    campaign — reopening it here is out of R7's file scope). Both cross
    region/layer boundaries and carry real regression risk if rushed, per
    this campaign's own DH-9 precedent.
    """

    def test_law_reference_text_yields_zero_citations_today(self, parser):
        text = "Το άρθρο 5 του Ν. 4622/2019 τροποποιείται ως εξής."
        citations = parser.parse(text)
        assert citations == []  # documents today's (incomplete) behavior

    @pytest.mark.asyncio
    async def test_naive_wiring_would_falsely_disprove_a_real_law_citation(self):
        """The packaged resolution index (built by tools/build_citation_index.py,
        wired in container.py) has zero "Ν. XXXX/YYYY"-shaped entries — only
        Constitution/ΦΕΚ/CELEX/Charter identifiers. If LAW_REF_PATTERN matches
        were wired into parse() today, mapped to any existing CitationScheme,
        resolve() against a real-shaped index reports checked=True,
        resolved=False for every one of them — which
        CoVeVerifier._check_citations (cove_verifier.py) treats as positively
        DISPROVEN, hard-dropping the finding — even for a citation to a real,
        valid law. That would be worse than today's silent omission.
        """
        # Representative of the real packaged index: non-empty, no
        # law-ref-shaped identifiers (matches build_citation_index.py's
        # constitution/fek/celex/charter categories).
        index = {"Σύνταγμα Άρθρο 5", "ΦΕΚ Α 137/2023", "32018L1972"}
        parser = GreekCitationParser(resolution_index=index)
        would_be_citation = Citation(
            scheme=CitationScheme.UNKNOWN,
            identifier="Ν. 4622/2019",
            original_text="Ν. 4622/2019",
        )
        resolved = await parser.resolve(would_be_citation)
        assert resolved.checked is True
        # -> CoVe would read this pair as DISPROVEN, not "unverified".
        assert resolved.resolved is False


class TestGreekCitationParserBuildIndex:
    def test_build_index(self, parser):
        citations = [
            Citation(
                scheme=CitationScheme.FEK,
                identifier="ΦΕΚ Α 137/2023",
                original_text="ΦΕΚ Α 137/2023",
            ),
            Citation(
                scheme=CitationScheme.FEK, identifier="ΦΕΚ Β 42/2022", original_text="ΦΕΚ Β 42/2022"
            ),
        ]
        index = parser.build_resolution_index(citations)
        assert "ΦΕΚ Α 137/2023" in index
        assert "ΦΕΚ Β 42/2022" in index
        assert len(index) == 2
