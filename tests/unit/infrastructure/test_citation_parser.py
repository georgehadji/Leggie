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

    def test_supported_schemes_covers_everything_parse_emits(self, parser):
        """DH-40: the port contract is "schemes this parser handles", so the
        list must not fall behind parse(). UNKNOWN was missing from it for the
        whole life of the DH-28 law-reference pattern.

        Deriving the expectation from parse() rather than hard-coding it keeps
        the two in step the next time a pattern is added.
        """
        text = (
            "ΦΕΚ Α 137/2023, CELEX 32018L1972, ECLI:EU:C:2014:317, "
            "https://www.et.gr/api/DownloadFeka/?fek_pdf=20210100123, ο ν. 4622/2019"
        )
        emitted = {c.scheme for c in parser.parse(text)}
        assert emitted, "fixture text stopped matching any pattern"
        assert emitted <= set(parser.supported_schemes())
        assert CitationScheme.UNKNOWN in parser.supported_schemes()

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
        # No authority argument = the caller built this index and asserts it is
        # complete, so a miss here is still a real, checked miss (DH-37).
        assert resolved2.checked is True
        assert "declared exhaustive" in (resolved2.resolution_evidence or "")

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
            authoritative_schemes={CitationScheme.FEK, CitationScheme.CELEX},
        )
        (citation,) = parser.parse("Ο Ν. 4622/2019 ορίζει…")
        resolved = await parser.resolve(citation)

        assert resolved.checked is False
        assert resolved.resolved is False


class TestSchemeAuthority:
    """DH-37: an index may CONFIRM anything it holds, but may DISPROVE only a
    scheme it is an *exhaustive* register for.

    The packaged leggie/data/citation_index.json hand-seeds 3 ΦΕΚ and 4 CELEX
    identifiers, zero ECLI and zero URL, and 174 of its 181 entries are shaped
    "Σύνταγμα Άρθρο N" / "Χάρτης Άρθρο N", which parse() can never emit.

    DH-36 gated on *presence* — "does the index hold any entries for this
    scheme?" — which closed the ECLI/URL half and left the ΦΕΚ/CELEX half
    open: a real, valid gazette reference outside the seeded three still came
    back checked=True, resolved=False, exactly the pair
    CoVeVerifier._check_citations (cove_verifier.py) reads as positively
    DISPROVEN and hard-drops the whole finding for. ΦΕΚ is the most-cited
    scheme in Greek bills, so that was the expensive half.

    Authority is now DECLARED by the index, never inferred from a count, and
    the packaged index declares none.
    """

    @staticmethod
    def _packaged_shape_parser() -> GreekCitationParser:
        """A parser wired the way container.py wires the real packaged index:
        entries it can confirm against, and no declared authority at all."""
        return GreekCitationParser(
            resolution_index={"ΦΕΚ Α 137/2023", "32018L1972", "Σύνταγμα Άρθρο 5"},
            authoritative_schemes=set(),
        )

    @pytest.mark.asyncio
    async def test_unauthoritative_scheme_is_unverified_not_disproven(self):
        """An ECLI against an index that declares no authority over ECLI must
        not be checked at all."""
        parser = self._packaged_shape_parser()
        cite = Citation(
            scheme=CitationScheme.ECLI,
            identifier="EU:C:2014:317",
            original_text="ECLI:EU:C:2014:317",
        )
        resolved = await parser.resolve(cite)

        assert resolved.checked is False  # -> CoVe cannot read this as disproven
        assert resolved.resolved is False
        assert "not exhaustive for scheme 'ecli'" in (resolved.resolution_evidence or "")

    @pytest.mark.asyncio
    async def test_unauthoritative_url_scheme_is_unverified(self):
        """Boundary: the second scheme the packaged index has zero entries for."""
        url = "https://www.et.gr/api/DownloadFeka/?fek_pdf=20210100123"
        parser = self._packaged_shape_parser()
        resolved = await parser.resolve(
            Citation(scheme=CitationScheme.URL, identifier=url, original_text=url)
        )

        assert resolved.checked is False
        assert resolved.resolved is False

    @pytest.mark.asyncio
    async def test_sparse_index_miss_is_unverified_not_disproven(self):
        """THE DH-37 defect. A real, valid gazette reference that simply is not
        one of the three hand-seeded ΦΕΚ numbers must come back unverified.

        This replaces test_covered_scheme_still_disproves_a_genuine_miss, whose
        premise ("a ΦΕΚ miss is a real, checked miss") *was* the defect: with 3
        seeded numbers the index disproved essentially every genuine citation
        it was shown, and CoVe hard-dropped the finding carrying it.
        """
        parser = self._packaged_shape_parser()
        resolved = await parser.resolve(
            Citation(
                scheme=CitationScheme.FEK,
                identifier="ΦΕΚ Α 88/2024",
                original_text="ΦΕΚ Α 88/2024",
            )
        )

        assert resolved.checked is False  # -> CoVe cannot read this as disproven
        assert resolved.resolved is False
        assert "not exhaustive for scheme 'fek'" in (resolved.resolution_evidence or "")

    @pytest.mark.asyncio
    async def test_declared_authoritative_scheme_disproves_a_miss(self):
        """The mechanism stays alive behind the switch: an index that DECLARES
        itself exhaustive for ΦΕΚ may still disprove a miss. Nothing Leggie
        ships declares this today — it is for the day the index is built from
        a live register (ADR-0004, CELLAR)."""
        parser = GreekCitationParser(
            resolution_index={"ΦΕΚ Α 137/2023"},
            authoritative_schemes={CitationScheme.FEK},
        )
        resolved = await parser.resolve(
            Citation(
                scheme=CitationScheme.FEK,
                identifier="ΦΕΚ Α 999/2099",
                original_text="ΦΕΚ Α 999/2099",
            )
        )

        assert resolved.checked is True
        assert resolved.resolved is False
        assert "declared exhaustive" in (resolved.resolution_evidence or "")

    @pytest.mark.asyncio
    async def test_hit_resolves_even_when_scheme_is_not_authoritative(self):
        """No-regression: the packaged ΦΕΚ and CELEX entries keep working.

        A hit is positive evidence whether or not the index is exhaustive —
        the identifier is literally on the known-good list. Only a MISS needs
        authority to mean anything.
        """
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
    async def test_none_means_caller_asserts_exhaustiveness(self):
        """Boundary: ``None`` keeps the old semantics for a caller that built
        the index itself (build_resolution_index) and therefore knows it is
        complete. Only container.py, which loads a packaged file it did not
        build, passes an explicit set."""
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
    async def test_empty_authority_still_confirms_a_hit_but_never_disproves(self):
        """Boundary: the fail-open default container.py uses when the index
        declares no authority (which is every index Leggie ships) must still
        confirm what it holds — declaring no authority costs the resolver its
        power to disprove, not its power to verify."""
        parser = GreekCitationParser(
            resolution_index={"ΦΕΚ Α 137/2023"}, authoritative_schemes=set()
        )

        hit = await parser.resolve(
            Citation(
                scheme=CitationScheme.FEK,
                identifier="ΦΕΚ Α 137/2023",
                original_text="ΦΕΚ Α 137/2023",
            )
        )
        assert hit.checked is True
        assert hit.resolved is True

        miss = await parser.resolve(
            Citation(
                scheme=CitationScheme.FEK,
                identifier="ΦΕΚ Α 88/2024",
                original_text="ΦΕΚ Α 88/2024",
            )
        )
        assert miss.checked is False
        assert miss.resolved is False


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
