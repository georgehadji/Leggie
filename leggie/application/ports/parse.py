"""Parse Port — abstract interface for Greek legal document parsing."""

from __future__ import annotations

from abc import ABC, abstractmethod

from leggie.domain.models import Document
from leggie.domain.models.parse_integrity import ParseIntegrityReport


class ParsePort(ABC):
    """Port for parsing Greek legal documents into structured form."""

    @abstractmethod
    def parse(self, text: str, title: str = "", source_format: str = "txt") -> Document:
        """Parse raw text into a structured Document."""
        ...

    def parse_with_integrity(
        self, text: str, title: str = "", source_format: str = "txt"
    ) -> tuple[Document, ParseIntegrityReport]:
        """Parse and return an integrity report alongside the document.

        Default implementation: delegates to `parse()` and returns an empty
        (clean) report. Override in concrete adapters to produce a real report.
        """
        doc = self.parse(text, title=title, source_format=source_format)
        report = ParseIntegrityReport(
            articles_parsed=len(doc.articles),
            distinct_ids=len({a.id for a in doc.articles}),
        )
        return doc, report
