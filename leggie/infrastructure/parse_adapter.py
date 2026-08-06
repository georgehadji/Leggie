"""Parse Adapter — implements ParsePort via DocumentParser."""

from __future__ import annotations

from leggie.application.ports.parse import ParsePort
from leggie.domain.models import Document
from leggie.domain.models.parse_integrity import ParseIntegrityReport


class ParseAdapter(ParsePort):
    """Concrete adapter — delegates to DocumentParser."""

    def parse(self, text: str, title: str = "", source_format: str = "txt") -> Document:
        from leggie.infrastructure.parse import DocumentParser
        parser = DocumentParser()
        return parser.parse(text, title=title, source_format=source_format)

    def parse_with_integrity(
        self, text: str, title: str = "", source_format: str = "txt"
    ) -> tuple[Document, ParseIntegrityReport]:
        from leggie.infrastructure.parse import DocumentParser
        parser = DocumentParser()
        return parser.parse_with_integrity(text, title=title, source_format=source_format)
