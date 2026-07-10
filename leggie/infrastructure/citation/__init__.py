"""Deterministic Citation Parser — Interpreter pattern (U1).

Parses references from Greek legal texts and normalizes to standard IDs:
  - ΦΕΚ: issue/year/number
  - CELEX: EU law identifier
  - ECLI: European Case Law Identifier
  - URL: direct links
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
LAW_REF_PATTERN: Pattern[str] = re.compile(
    r"Ν\.?\s*(\d+)\s*[/\\]\s*(\d{4})",
    re.UNICODE,
)


class CitationParseError(Exception):
    """Raised when citation parsing fails."""


class GreekCitationParser(CitationParserPort):
    """Deterministic citation parser for Greek legal texts."""

    def __init__(self, resolution_index: set[str] | None = None) -> None:
        self._resolution_index = resolution_index or set()

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

        return citations

    async def resolve(self, citation: Citation) -> Citation:
        """Resolve a citation against the available index.

        Returns the citation with resolved flag set based on index lookup.
        """
        if self._resolution_index:
            resolved = citation.identifier in self._resolution_index
            evidence = "resolved against internal index" if resolved else "not found in index"
        else:
            resolved = True
            evidence = "no resolution index configured — assumed valid"

        return Citation(
            scheme=citation.scheme,
            identifier=citation.identifier,
            original_text=citation.original_text,
            resolved=resolved,
            resolution_evidence=evidence,
        )

    def supported_schemes(self) -> list[CitationScheme]:
        return [CitationScheme.FEK, CitationScheme.CELEX, CitationScheme.ECLI, CitationScheme.URL]

    def build_resolution_index(self, citations: list[Citation]) -> set[str]:
        """Build a resolution index from a known-good list of citations."""
        return {c.identifier for c in citations}
