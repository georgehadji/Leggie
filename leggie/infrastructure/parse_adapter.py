"""Parse Adapter — implements ParsePort via DocumentParser."""

from __future__ import annotations

from leggie.application.ports.parse import ParsePort
from leggie.domain.models import Document


class ParseAdapter(ParsePort):
    """Concrete adapter — delegates to DocumentParser."""

    def parse(self, text: str, title: str = "", source_format: str = "txt") -> Document:
        from leggie.infrastructure.parse import DocumentParser
        parser = DocumentParser()
        return parser.parse(text, title=title, source_format=source_format)
