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


class TestLawReferenceExtraction:
    """DH-28: LAW_REF_PATTERN ("Ν. ΧΧΧΧ/Έτος") sat unused from the initial MVP
    commit until now, so individual-law references — the most common
    cross-reference shape in Greek amending text — were never extracted at
    all, not even as "unverified".

    Wiring it in was only safe once DH-36 landed. Emitted under
    CitationScheme.UNKNOWN, which no index ever declares coverage for, so
    these always come back checked=False: visible in reports, never
    disprovable by CoVeVerifier._check_citations. A dedicated
    CitationScheme.LAW member remains a separate Domain change, deliberately
    not made here — it would buy nothing until a law-reference index exists.
    """

    def test_uppercase_law_reference_is_extracted(self, parser):
        citations = parser.parse("Το άρθρο 5 του Ν. 4622/2019 τροποποιείται ως εξής.")
        assert len(citations) == 1
        assert citations[0].scheme == CitationScheme.UNKNOWN
        assert citations[0].identifier == "Ν. 4622/2019"

    def test_lowercase_law_reference_is_extracted(self, parser):
        """The form real amending text actually uses; the original pattern
        matched only the uppercase nu and would have missed every one."""
        citations = parser.parse("τροποποιείται ο ν. 4270/2014 ως προς τα εξής")
        assert [c.identifier for c in citations] == ["Ν. 4270/2014"]

    def test_no_match_mid_word(self, parser):
        """Boundary: the \\b guard keeps a word-final nu from inventing a law
        reference out of unrelated numbering."""
        assert parser.parse("ΣΥΝ 12/2020 και ΑΒΓΝ 7/2019") == []

    def test_fek_and_law_reference_in_one_sentence_do_not_collide(self, parser):
        """Boundary: the two patterns are disjoint — a sentence carrying both
        yields exactly one of each, not a double extraction."""
        citations = parser.parse("Ο ν. 4622/2019 (ΦΕΚ Α 137/2023) προβλέπει ότι…")
        schemes = sorted(c.scheme.value for c in citations)
        assert schemes == ["fek", "unknown"]

    @pytest.mark.asyncio
    async def test_law_reference_is_unverified_never_disproven(self):
        """No-regression for the reason this was escalated: against a
        real-shaped packaged index (Constitution/ΦΕΚ/CELEX/Charter, zero
        law-ref entries) a real, valid law citation must come back
        checked=False — "unverified", not the checked=True/resolved=False pair
        CoVe hard-drops findings for."""
        parser = GreekCitationParser(
            resolution_index={"Σύνταγμα Άρθρο 5", "ΦΕΚ Α 137/2023", "32018L1972"},
            covered_schemes={CitationScheme.FEK, CitationScheme.CELEX},
        )
        (citation,) = parser.parse("Ο Ν. 4622/2019 ορίζει…")
        resolved = await parser.resolve(citation)

        assert resolved.checked is False
        assert resolved.resolved is False


class TestSchemeCoverage:
    """DH-36: a resolution index is authoritative only for the schemes it
    actually holds entries for.

    The packaged data/citation_index.json declares
    ``{"constitution": 120, "fek": 3, "celex": 4, "charter": 54}`` — zero ECLI
    and zero URL identifiers, and 174 of its 181 entries are shaped
    "Σύνταγμα Άρθρο N" / "Χάρτης Άρθρο N", which parse() can never emit. Before
    this fix resolve() reported checked=True for every scheme as long as the
    index was merely non-empty, so a real, valid ECLI or et.gr URL — and every
    ΦΕΚ outside the packaged three — came back checked=True, resolved=False:
    exactly the pair CoVeVerifier._check_citations (cove_verifier.py:334) reads
    as positively DISPROVEN and hard-drops the finding for.
    """

    @staticmethod
    def _packaged_shape_parser() -> GreekCitationParser:
        """A parser wired the way container.py wires the real packaged index."""
        return GreekCitationParser(
            resolution_index={"ΦΕΚ Α 137/2023", "32018L1972", "Σύνταγμα Άρθρο 5"},
            covered_schemes={CitationScheme.FEK, CitationScheme.CELEX},
        )

    @pytest.mark.asyncio
    async def test_uncovered_scheme_is_unverified_not_disproven(self):
        """Proof-of-defect: an ECLI against an index declaring no ECLI entries
        must not be checked at all."""
        parser = self._packaged_shape_parser()
        cite = Citation(
            scheme=CitationScheme.ECLI,
            identifier="EU:C:2014:317",
            original_text="ECLI:EU:C:2014:317",
        )
        resolved = await parser.resolve(cite)

        assert resolved.checked is False  # -> CoVe cannot read this as disproven
        assert resolved.resolved is False
        assert "no entries for scheme 'ecli'" in (resolved.resolution_evidence or "")

    @pytest.mark.asyncio
    async def test_uncovered_url_scheme_is_unverified(self):
        """Boundary: the second scheme the packaged index has zero entries for."""
        url = "https://www.et.gr/api/DownloadFeka/?fek_pdf=20210100123"
        parser = self._packaged_shape_parser()
        resolved = await parser.resolve(
            Citation(scheme=CitationScheme.URL, identifier=url, original_text=url)
        )

        assert resolved.checked is False
        assert resolved.resolved is False

    @pytest.mark.asyncio
    async def test_covered_scheme_still_disproves_a_genuine_miss(self):
        """No-regression: coverage must not turn the gate off entirely. A ΦΕΚ
        is a scheme the index *does* cover, so a miss there is still a real,
        checked miss — that is the whole point of having an index."""
        parser = self._packaged_shape_parser()
        resolved = await parser.resolve(
            Citation(
                scheme=CitationScheme.FEK,
                identifier="ΦΕΚ Α 999/2099",
                original_text="ΦΕΚ Α 999/2099",
            )
        )

        assert resolved.checked is True
        assert resolved.resolved is False
        assert "not found in index" in (resolved.resolution_evidence or "")

    @pytest.mark.asyncio
    async def test_covered_scheme_hit_still_resolves(self):
        """No-regression: the packaged ΦΕΚ and CELEX entries keep working."""
        parser = self._packaged_shape_parser()
        for scheme, identifier in (
            (CitationScheme.FEK, "ΦΕΚ Α 137/2023"),
            (CitationScheme.CELEX, "32018L1972"),
        ):
            resolved = await parser.resolve(
                Citation(scheme=scheme, identifier=identifier, original_text=identifier)
            )
            assert resolved.checked is True, scheme
            assert resolved.resolved is True, scheme

    @pytest.mark.asyncio
    async def test_covered_schemes_none_means_caller_asserts_full_coverage(self):
        """Boundary: the default keeps the old semantics for a caller that
        built the index itself (build_resolution_index) and therefore knows
        what is in it. Only container.py, which loads a packaged file it did
        not build, passes an explicit set."""
        parser = GreekCitationParser(resolution_index={"ΦΕΚ Α 137/2023"})
        resolved = await parser.resolve(
            Citation(
                scheme=CitationScheme.ECLI,
                identifier="EU:C:2014:317",
                original_text="ECLI:EU:C:2014:317",
            )
        )

        assert resolved.checked is True
        assert resolved.resolved is False

    @pytest.mark.asyncio
    async def test_empty_covered_set_checks_nothing(self):
        """Boundary: an index whose categories declare no parser-emitted
        scheme (the fail-open default container.py uses when ``categories`` is
        missing or unrecognisable) must verify nothing rather than disprove
        everything."""
        parser = GreekCitationParser(resolution_index={"ΦΕΚ Α 137/2023"}, covered_schemes=set())
        resolved = await parser.resolve(
            Citation(
                scheme=CitationScheme.FEK,
                identifier="ΦΕΚ Α 137/2023",
                original_text="ΦΕΚ Α 137/2023",
            )
        )

        assert resolved.checked is False
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
