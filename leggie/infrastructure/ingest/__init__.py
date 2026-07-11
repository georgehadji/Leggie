"""Ingest module — bytes → clean text per format (Factory pattern).

Supports PDF, DOCX, HTML, and plain text formats.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class IngestError(Exception):
    """Base exception for ingest failures."""


class UnsupportedFormatError(IngestError):
    """Raised when the file format is not supported."""


class Ingestor(ABC):
    """Base ingestor — converts bytes/Path to cleaned text."""

    @abstractmethod
    async def ingest(self, source: Path | str) -> str:
        """Ingest a document and return cleaned text."""
        ...


class PDFIngestor(Ingestor):
    """Ingest PDF files using pdfplumber."""

    async def ingest(self, source: Path | str) -> str:
        try:
            import pdfplumber
        except ImportError as e:
            raise IngestError("pdfplumber not installed; run `pip install pdfplumber`") from e

        path = Path(source)
        if not path.exists():
            raise IngestError(f"File not found: {path}")

        text_parts: list[str] = []
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    text_parts.append(text)
        return "\n\n".join(text_parts)


class DOCXIngestor(Ingestor):
    """Ingest DOCX files using python-docx."""

    async def ingest(self, source: Path | str) -> str:
        try:
            from docx import Document as DocxDocument
        except ImportError as e:
            raise IngestError("python-docx not installed; run `pip install python-docx`") from e

        path = Path(source)
        if not path.exists():
            raise IngestError(f"File not found: {path}")

        doc = DocxDocument(str(path))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n\n".join(paragraphs)


class HTMLIngestor(Ingestor):
    """Ingest HTML files using BeautifulSoup."""

    async def ingest(self, source: Path | str) -> str:
        try:
            from bs4 import BeautifulSoup
        except ImportError as e:
            raise IngestError(
                "beautifulsoup4 not installed; run `pip install beautifulsoup4`"
            ) from e

        path = Path(source)
        if not path.exists():
            raise IngestError(f"File not found: {path}")

        content = path.read_text(encoding="utf-8")
        soup = BeautifulSoup(content, "lxml")
        # Remove script, style elements
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)


class TextIngestor(Ingestor):
    """Ingest plain text files."""

    async def ingest(self, source: Path | str) -> str:
        path = Path(source)
        if not path.exists():
            raise IngestError(f"File not found: {path}")
        return path.read_text(encoding="utf-8")


class IngestorFactory:
    """Factory for creating ingestors by format."""

    _ingestors: dict[str, type[Ingestor]] = {
        ".pdf": PDFIngestor,
        ".docx": DOCXIngestor,
        ".html": HTMLIngestor,
        ".htm": HTMLIngestor,
        ".txt": TextIngestor,
    }

    @classmethod
    def register_format(cls, extension: str, ingestor_cls: type[Ingestor]) -> None:
        """Register a custom ingestor for a file extension."""
        cls._ingestors[extension.lower()] = ingestor_cls

    @classmethod
    def get_ingestor(cls, source: Path | str) -> Ingestor:
        """Get the appropriate ingestor for a file based on its extension."""
        path = Path(source)
        ext = path.suffix.lower()
        if ext not in cls._ingestors:
            raise UnsupportedFormatError(f"Unsupported format: {ext}")
        return cls._ingestors[ext]()

    @classmethod
    async def ingest(cls, source: Path | str) -> str:
        """Convenience: detect format and ingest in one call."""
        ingestor = cls.get_ingestor(source)
        return await ingestor.ingest(source)
