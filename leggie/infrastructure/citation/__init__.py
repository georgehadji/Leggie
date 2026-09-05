"""Deterministic Citation Parser — Interpreter pattern (U1).

Parses references from Greek legal texts and normalizes to standard IDs:
  - ΦΕΚ: issue/year/number
  - CELEX: EU law identifier
  - ECLI: European Case Law Identifier
  - URL: direct links
  - individual law references ("Ν. 4622/2019") under CitationScheme.UNKNOWN,
    since no resolution index carries law identifiers (DH-28)
"""

from __future__ import annotations

import re
from re import Pattern

from leggie.application.ports.citation_parser import CitationParserPort
from leggie.domain.models import Citation, CitationScheme

# Citation regex patterns
FEK_PATTERN: Pattern[str] = re.compile(
    r"(?:ΦΕΚ|Φ\.?Ε\.?Κ\.?|Εφημερίδα.*?Κυβερνήσεως)\s+"
    r"(?:(?:Τεύχος\s+)?([ΑαΒβΓγΔδΕεΣστΤ]’?)\s+)?"
    r"(\d+)\s*[/\\]\s*(\d{4})",
    re.UNICODE,
)

CELEX_PATTERN: Pattern[str] = re.compile(
    r"(?:CELEX|Celex|celex)[:\s]*(\d{4,5}[A-Z]{1,2}\d+)",
    re.UNICODE,
)

ECLI_PATTERN: Pattern[str] = re.compile(
    r"(?:ECLI|Ecli|ecli)[:\s]*([A-Z]{2}:[A-Z\u0386-\u03CE]+:\d{4}:\d+)",
    re.UNICODE,
)

URL_PATTERN: Pattern[str] = re.compile(
    r"(https?://(?:www\.)?(?:et\.gr|eur-lex\.europa\.eu|nomothesia\.gr|legislation\.gr|"
    r"hellenicparliament\.gr|diavgeia\.gov\.gr)/[^\s)]+)",
    re.UNICODE,
)

# Individual law references: Ν. ΧΧΧΧ/Έτος
# ``\b`` keeps the initial nu from matching mid-word ("ΣΥΝ 12/2020"), and the
# lowercase form is included because that is the one real amending text
# actually uses ("τροποποιείται ο ν. 4270/2014") — patterns.py's own
# cross-reference stop-list already had to spell out "του ν." for the same
# reason.
LAW_REF_PATTERN: Pattern[str] = re.compile(
    r"\b[Νν]\.?\s*(\d+)\s*[/\\]\s*(\d{4})",
    re.UNICODE,
)

# Which ``categories`` key of data/citation_index.json corresponds to which
# scheme this parser emits (DH-36). The index also carries `constitution` and
# `charter` counts, deliberately absent here: those identifiers are shaped
# "Σύνταγμα Άρθρο N" / "Χάρτης Άρθρο N", which parse() never produces, so they
# can never make the index authoritative for anything.
INDEX_CATEGORY_SCHEMES: dict[str, CitationScheme] = {
    "fek": CitationScheme.FEK,
    "celex": CitationScheme.CELEX,
    "ecli": CitationScheme.ECLI,
    "url": CitationScheme.URL,
}


class CitationParseError(Exception):
    """Raised when citation parsing fails."""


class GreekCitationParser(CitationParserPort):
    """Deterministic citation parser for Greek legal texts."""

    def __init__(
        self,
        resolution_index: set[str] | None = None,
        covered_schemes: set[CitationScheme] | None = None,
    ) -> None:
        self._resolution_index = resolution_index or set()
        # DH-36: an index is authoritative only for the schemes it actually
        # holds entries for. ``None`` means "the caller asserts this index
        # covers every scheme it will be asked about" — true when the caller
        # built it itself (build_resolution_index), false for a packaged data
        # file, which is why container.py passes an explicit set derived from
        # the file's own ``categories``.
        self._covered_schemes = covered_schemes

    def parse(self, text: str) -> list[Citation]:
        """Extract and normalize all citations from text."""
        citations: list[Citation] = []

        # ΦΕΚ citations
        for match in FEK_PATTERN.finditer(text):
            issue = match.group(1) or "Α"
            number = match.group(2)
            year = match.group(3)
            identifier = f"ΦΕΚ {issue} {number}/{year}"
            citations.append(
                Citation(
                    scheme=CitationScheme.FEK,
                    identifier=identifier,
                    original_text=match.group(0),
                )
            )

        # CELEX citations
        for match in CELEX_PATTERN.finditer(text):
            identifier = match.group(1)
            citations.append(
                Citation(
                    scheme=CitationScheme.CELEX,
                    identifier=identifier,
                    original_text=match.group(0),
                )
            )

        # ECLI citations
        for match in ECLI_PATTERN.finditer(text):
            identifier = match.group(1)
            citations.append(
                Citation(
                    scheme=CitationScheme.ECLI,
                    identifier=identifier,
                    original_text=match.group(0),
                )
            )

        # URL citations
        for match in URL_PATTERN.finditer(text):
            url = match.group(1)
            citations.append(
                Citation(
                    scheme=CitationScheme.URL,
                    identifier=url,
                    original_text=url,
                )
            )

        # Individual law references (DH-28). Emitted under UNKNOWN rather than
        # a dedicated scheme: no resolution index carries law identifiers, so a
        # scheme of its own would buy nothing today, and adding one is a Domain
        # change. UNKNOWN is never in any index's declared coverage, so these
        # can only ever come back checked=False — visible as "unverified" in
        # reports, and never disprovable by CoVe (which is what made wiring
        # this pattern in unsafe before DH-36 was fixed).
        for match in LAW_REF_PATTERN.finditer(text):
            citations.append(
                Citation(
                    scheme=CitationScheme.UNKNOWN,
                    identifier=f"Ν. {match.group(1)}/{match.group(2)}",
                    original_text=match.group(0).strip(),
                )
            )

        return citations

    def _covers(self, scheme: CitationScheme) -> bool:
        """Can the configured index say anything at all about *scheme*?"""
        return self._covered_schemes is None or scheme in self._covered_schemes

    async def resolve(self, citation: Citation) -> Citation:
        """Resolve a citation against the available index.

        Returns the citation with resolved flag set based on index lookup.
        """
        if self._resolution_index and self._covers(citation.scheme):
            resolved = citation.identifier in self._resolution_index
            evidence = "resolved against internal index" if resolved else "not found in index"
            checked = True
        else:
            # Fail closed: nothing was actually checked, so we must not report
            # the citation as resolved. Structural parsing (parse()) already
            # succeeded — this only means "unverified", not "invalid".
            #
            # Two ways to get here. No index at all, or (DH-36) an index that
            # holds no entries for this citation's scheme: the packaged index
            # carries 3 ΦΕΚ, 4 CELEX and zero ECLI/URL identifiers, so marking
            # those checked=True made CoVeVerifier._check_citations read every
            # real, valid ECLI/URL — and every ΦΕΚ outside those three — as
            # positively DISPROVEN and hard-drop the whole finding.
            resolved = False
            checked = False
            evidence = (
                "no resolution index configured — not independently verified"
                if not self._resolution_index
                else f"index has no entries for scheme '{citation.scheme.value}' "
                "— not independently verified"
            )

        return Citation(
            scheme=citation.scheme,
            identifier=citation.identifier,
            original_text=citation.original_text,
            resolved=resolved,
            checked=checked,
            resolution_evidence=evidence,
        )

    def supported_schemes(self) -> list[CitationScheme]:
        return [CitationScheme.FEK, CitationScheme.CELEX, CitationScheme.ECLI, CitationScheme.URL]

    def build_resolution_index(self, citations: list[Citation]) -> set[str]:
        """Build a resolution index from a known-good list of citations."""
        return {c.identifier for c in citations}
