"""Citation Parser Port — abstract interface for deterministic citation parsing.

Parses references from source text and normalizes to standard IDs (U1).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from leggie.domain.models import Citation, CitationScheme


class CitationParserPort(ABC):
    """Port for deterministic citation parsing and resolution."""

    @abstractmethod
    def parse(self, text: str) -> list[Citation]:
        """Extract and normalize all citations from text."""
        ...

    @abstractmethod
    async def resolve(self, citation: Citation) -> Citation:
        """Resolve a citation against the available index.

        Returns the citation with resolved=True/False, checked=True/False, and
        evidence. `checked` MUST be False whenever there was no index to check
        against. Callers (CoVeVerifier) treat resolved=False+checked=True as
        "disproven" and resolved=False+checked=False as merely "unverified" —
        get `checked` wrong and a citation that was never independently
        checkable looks fabricated.
        """
        ...

    @abstractmethod
    def supported_schemes(self) -> list[CitationScheme]:
        """List citation schemes this parser handles."""
        ...
