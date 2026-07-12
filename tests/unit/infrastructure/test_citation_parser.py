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

        cite2 = Citation(
            scheme=CitationScheme.FEK,
            identifier="ΦΕΚ Α 999/2099",
            original_text="ΦΕΚ Α 999/2099",
        )
        resolved2 = await parser.resolve(cite2)
        assert resolved2.resolved is False

    @pytest.mark.asyncio
    async def test_resolve_without_index(self, parser):
        cite = Citation(
            scheme=CitationScheme.FEK,
            identifier="ΦΕΚ Α 137/2023",
            original_text="ΦΕΚ Α 137/2023",
        )
        resolved = await parser.resolve(cite)
        assert resolved.resolved is True  # No index = assumed valid

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
