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

        Returns the citation with resolved=True/False + evidence.
        """
        ...

    @abstractmethod
    def supported_schemes(self) -> list[CitationScheme]:
        """List citation schemes this parser handles."""
        ...
