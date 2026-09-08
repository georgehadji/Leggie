"""DH-37 end-to-end on a real document, through the real production wiring.

The 2026-09-08 live smoke on the reference bill could not prove DH-37: that
bill's findings carried no citations at all, so ``resolve()`` never ran and the
fixed path went unexercised (docs/POST_DH36_REVIEW_PLAN.md §11.2). The same wall
stopped the earlier DH-36 attempt.

``Inputs/DH37_citation_probe.txt`` exists to remove that excuse. It is a small
Greek bill whose text forces every citation shape the parser emits, including
the case DH-37 is about: a real, well-formed ΦΕΚ that is simply not one of the
three hand-seeded identifiers in the packaged index.

This runs the container-built parser against the packaged index — the same
objects the CLI uses — so it is a genuine wiring proof, not a stub. No LLM and
no network, so it costs nothing and runs in CI.
"""

from __future__ import annotations

import pathlib

import pytest

from leggie.application.ports.citation_parser import CitationParserPort
from leggie.domain.models import CitationScheme
from leggie.infrastructure.container import Container
from leggie.infrastructure.parse import DocumentParser

PROBE = pathlib.Path(__file__).resolve().parents[2] / "Inputs" / "DH37_citation_probe.txt"

# Not seeded in leggie/data/citation_index.json — the DH-37 case.
UNSEEDED_FEK = "ΦΕΚ Α 88/2024"
# Seeded, so it must still resolve positively.
SEEDED_FEK = "ΦΕΚ Α 137/2023"


@pytest.fixture(scope="module")
def probe_text() -> str:
    if not PROBE.exists():  # pragma: no cover - fixture is committed
        pytest.skip(f"citation probe missing: {PROBE}")
    return PROBE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def parser() -> CitationParserPort:
    container = Container()
    container.configure_defaults()
    return container.get(CitationParserPort)


class TestProbeParsesCleanly:
    """If the probe stops parsing, the citation assertions below prove nothing."""

    def test_three_articles_no_integrity_damage(self, probe_text: str) -> None:
        doc, report = DocumentParser().parse_with_integrity(probe_text)
        assert [a.id for a in doc.articles] == ["1", "2", "3"]
        assert report.is_clean is True
        assert report.duplicate_ids == ()


class TestCitationResolutionOnRealText:
    @pytest.fixture
    def resolved(self, parser: CitationParserPort, probe_text: str):
        doc, _ = DocumentParser().parse_with_integrity(probe_text)
        body = " ".join(p.text for a in doc.articles for p in a.paragraphs)
        return parser, parser.parse(body)

    def test_every_scheme_the_probe_targets_is_emitted(self, resolved) -> None:
        _, citations = resolved
        schemes = {c.scheme for c in citations}
        assert CitationScheme.FEK in schemes
        assert CitationScheme.CELEX in schemes
        assert CitationScheme.UNKNOWN in schemes, "law references (DH-28) went missing"

    @pytest.mark.asyncio
    async def test_unseeded_fek_is_unverified_not_disproven(self, resolved) -> None:
        """THE DH-37 proof, on real document text rather than a constructed
        Citation. Before the fix this returned checked=True/resolved=False —
        the exact pair CoVeVerifier._check_citations reads as positively
        disproven, hard-dropping the entire finding that cited it.
        """
        parser, citations = resolved
        cite = next(c for c in citations if c.identifier == UNSEEDED_FEK)

        result = await parser.resolve(cite)

        assert result.checked is False, "a real ΦΕΚ was reported as conclusively checked"
        assert result.resolved is False
        assert "not exhaustive" in (result.resolution_evidence or "")

    @pytest.mark.asyncio
    async def test_seeded_identifiers_still_confirm(self, resolved) -> None:
        """The other half: dropping the power to disprove must not cost the
        power to verify."""
        parser, citations = resolved
        cite = next(c for c in citations if c.identifier == SEEDED_FEK)

        result = await parser.resolve(cite)

        assert result.resolved is True
        assert result.checked is True

    @pytest.mark.asyncio
    async def test_nothing_in_the_probe_is_ever_disproven(self, resolved) -> None:
        """Whole-document sweep: with the packaged index declaring no authority,
        no citation anywhere in a real bill may come back disproven. This is the
        assertion that would have caught DH-36's surviving half.
        """
        parser, citations = resolved
        assert citations, "probe produced no citations — it has stopped testing anything"

        for cite in citations:
            result = await parser.resolve(cite)
            disproven = result.checked and not result.resolved
            assert not disproven, (
                f"{cite.identifier} ({cite.scheme.value}) came back disproven; "
                f"CoVe would hard-drop the finding carrying it"
            )
