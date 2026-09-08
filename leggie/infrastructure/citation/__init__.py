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

# Which ``authoritative_schemes`` entry of data/citation_index.json corresponds
# to which scheme this parser emits (DH-36/DH-37). `constitution` and `charter`
# are deliberately absent: those identifiers are shaped "Σύνταγμα Άρθρο N" /
# "Χάρτης Άρθρο N", which parse() never produces, so they can never make the
# index authoritative for anything.
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
        authoritative_schemes: set[CitationScheme] | None = None,
    ) -> None:
        self._resolution_index = resolution_index or set()
        # DH-37: an index may CONFIRM anything it holds, but may DISPROVE only
        # a scheme it is an exhaustive register for. DH-36 gated on *presence*
        # ("does the index hold any entries for this scheme?") — but the
        # packaged file hand-seeds three ΦΕΚ and four CELEX identifiers, so
        # presence was true while exhaustiveness was nowhere near, and every
        # real citation outside those seven still came back disproven.
        #
        # ``None`` = the caller built this index itself and asserts
        # exhaustiveness (build_resolution_index). The default empty set is
        # confirm-only, which is what every index Leggie ships today earns.
        # Stored as-is: ``None`` and ``set()`` mean opposite things here.
        self._authoritative_schemes = authoritative_schemes

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
        # change. UNKNOWN is never in any index's declared authority, so these
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

    def _is_authoritative(self, scheme: CitationScheme) -> bool:
        """May a MISS on *scheme* be reported as positively disproven?

        Only for a scheme the index is an exhaustive register of. Holding a
        few entries is not the same as holding them all (DH-37).
        """
        return self._authoritative_schemes is None or scheme in self._authoritative_schemes

    async def resolve(self, citation: Citation) -> Citation:
        """Resolve a citation against the available index.

        Returns the citation with resolved flag set based on index lookup.
        """
        if citation.identifier in self._resolution_index:
            # A hit is positive evidence whether or not the index is
            # exhaustive: the identifier is literally on the known-good list.
            resolved = True
            checked = True
            evidence = "resolved against internal index"
        elif self._resolution_index and self._is_authoritative(citation.scheme):
            resolved = False
            checked = True
            evidence = f"not found in an index declared exhaustive for '{citation.scheme.value}'"
        else:
            # Fail closed: nothing was conclusively checked, so we must not
            # report the citation as resolved. Structural parsing (parse())
            # already succeeded — this only means "unverified", not "invalid".
            #
            # Two ways to get here. No index at all, or (DH-37) an index that
            # is not an exhaustive register for this citation's scheme — which
            # is every index Leggie ships: the packaged file hand-seeds 3 ΦΕΚ
            # and 4 CELEX identifiers. Reporting those misses checked=True made
            # CoVeVerifier._check_citations read a real, valid ΦΕΚ outside the
            # seeded three as positively DISPROVEN and hard-drop the whole
            # finding. Whether a citation is *wrong* is left to CoVe's LLM
            # cross-check, which is where a judgement call belongs.
            resolved = False
            checked = False
            evidence = (
                "no resolution index configured — not independently verified"
                if not self._resolution_index
                else f"index is not exhaustive for scheme '{citation.scheme.value}' "
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
        # Every scheme parse() can emit — UNKNOWN included, which carries the
        # DH-28 individual law references ("Ν. 4622/2019"). The port contract
        # is "schemes this parser handles", not "schemes the index covers", so
        # index authority is deliberately not consulted here (DH-40).
        return [
            CitationScheme.FEK,
            CitationScheme.CELEX,
            CitationScheme.ECLI,
            CitationScheme.URL,
            CitationScheme.UNKNOWN,
        ]

    def build_resolution_index(self, citations: list[Citation]) -> set[str]:
        """Build a resolution index from a known-good list of citations."""
        return {c.identifier for c in citations}
