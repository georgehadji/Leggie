"""Parse Port — abstract interface for Greek legal document parsing."""

from __future__ import annotations

from abc import ABC, abstractmethod

from leggie.domain.models import Document


class ParsePort(ABC):
    """Port for parsing Greek legal documents into structured form."""

    @abstractmethod
    def parse(self, text: str, title: str = "", source_format: str = "txt") -> Document:
        """Parse raw text into a structured Document."""
        ...
