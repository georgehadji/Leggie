"""Lens base class — Strategy pattern for legal analysis perspectives.

Each lens represents one analytical perspective on a bill:
    Constitutional | Legal-coherence | Economic | Implementation | EU-&-GDPR

Lenses are interchangeable Strategies behind a common interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from leggie.domain.models import Article, Finding


class Lens(ABC):
    """A legal analysis lens — Strategy pattern.

    Each lens analyzes an article from one perspective and returns findings.
    """

    @abstractmethod
    def name(self) -> str:
        """Human-readable lens name, e.g. 'constitutional'."""
        ...

    @abstractmethod
    def description(self) -> str:
        """What this lens analyzes."""
        ...

    @abstractmethod
    async def analyze(self, article: Article) -> list[Finding]:
        """Analyze an article from this lens's perspective.

        Returns a list of findings (may be empty if nothing found).
        """
        ...
