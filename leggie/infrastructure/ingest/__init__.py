"""Ingest module — bytes → clean text per format (Factory pattern).

Supports PDF, DOCX, HTML, and plain text formats.
All ingestors offload blocking I/O/parsing off the event loop (PROD-16b) via
``run_off_loop``, which — unlike ``asyncio.to_thread`` — does not let an
abandoned extraction hold the process open past BoundedIngestor's timeout
(DH-10).
"""

from __future__ import annotations

from pathlib import Path

from leggie.config.settings import get_settings
from leggie.domain.models import Document as Document
from leggie.infrastructure.ingest.base import (
    IngestError,
    Ingestor,
    InputNotFoundError,
    UnsupportedFormatError,
    run_off_loop,
)
from leggie.infrastructure.ingest.bounded import BoundedIngestor

# Explicit re-export list. Under [tool.mypy] strict (which implies
# no_implicit_reexport) a plain `from .base import IngestError` is NOT a
# re-export, so `from leggie.infrastructure.ingest import IngestError` — which
# interfaces/cli/__init__.py does when mapping exceptions to exit codes — failed
# type checking even though it works at runtime.
__all__ = [
    "BoundedIngestor",
    "DOCXIngestor",
    "Document",
    "HTMLIngestor",
    "IngestError",
    "Ingestor",
    "IngestorFactory",
    "InputNotFoundError",
    "PDFIngestor",
    "TextIngestor",
    "UnsupportedFormatError",
]


class PDFIngestor(Ingestor):
    """Ingest PDF files using pdfplumber."""

    async def ingest(self, source: Path | str) -> str:
        try:
            import pdfplumber
        except ImportError as exc:
            raise IngestError("pdfplumber not installed; run `pip install pdfplumber`") from exc

        path = Path(source)
        if not path.exists():
            raise InputNotFoundError(f"File not found: {path}")

        def _extract() -> str:
            text_parts: list[str] = []
            with pdfplumber.open(str(path)) as pdf:
                # Page-count guard (PROD-16a): reject before the expensive
                # per-page extract_text() loop, not after. A page-count bomb
                # (many pages, little text) would otherwise sail past the
                # max_elements char cap, which only ever sees the *result*.
                max_pages = IngestorFactory._cap("max_pages")
                if len(pdf.pages) > max_pages:
                    raise IngestError(
                        f"Refusing to ingest {path.name}: {len(pdf.pages)} pages "
                        f"exceeds the {max_pages} page cap."
                    )
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        text_parts.append(text)
            return "\n\n".join(text_parts)

        return await run_off_loop(_extract)


class DOCXIngestor(Ingestor):
    """Ingest DOCX files using python-docx."""

    async def ingest(self, source: Path | str) -> str:
        try:
            from docx import Document as DocxDocument
        except ImportError as exc:
            raise IngestError("python-docx not installed; run `pip install python-docx`") from exc

        path = Path(source)
        if not path.exists():
            raise InputNotFoundError(f"File not found: {path}")

        def _extract() -> str:
            # DEFLATE decompression-bomb guard (PROD-16c): a DOCX is a ZIP;
            # check total uncompressed size and entry count before parsing.
            import zipfile

            try:
                with zipfile.ZipFile(str(path)) as zf:
                    infos = zf.infolist()
                    total_uncompressed = sum(i.file_size for i in infos)
                    if total_uncompressed > 500 * 1024 * 1024:
                        raise IngestError(
                            f"Refusing DOCX {path.name}: uncompressed size "
                            f"{total_uncompressed} exceeds 500MB (decompression-bomb guard)."
                        )
            except zipfile.BadZipFile as e:
                raise IngestError(f"Not a valid DOCX (ZIP) file: {path.name}") from e

            doc = DocxDocument(str(path))
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            return "\n\n".join(paragraphs)

        return await run_off_loop(_extract)


class HTMLIngestor(Ingestor):
    """Ingest HTML files using BeautifulSoup."""

    async def ingest(self, source: Path | str) -> str:
        try:
            from bs4 import BeautifulSoup
        except ImportError as exc:
            raise IngestError(
                "beautifulsoup4 not installed; run `pip install beautifulsoup4`"
            ) from exc

        path = Path(source)
        if not path.exists():
            raise InputNotFoundError(f"File not found: {path}")

        def _extract() -> str:
            content = path.read_text(encoding="utf-8")
            # Use Python's html.parser (safer than lxml) — lxml's HTMLParser
            # can perform external entity resolution / network access on
            # untrusted content (PROD-16c). Pure-python parser avoids that.
            soup = BeautifulSoup(content, "html.parser")
            # Remove script, style, and link elements
            for tag in soup(["script", "style", "nav", "footer", "header", "link"]):
                tag.decompose()
            return soup.get_text(separator="\n", strip=True)

        return await run_off_loop(_extract)


class TextIngestor(Ingestor):
    """Ingest plain text files."""

    async def ingest(self, source: Path | str) -> str:
        path = Path(source)
        if not path.exists():
            raise InputNotFoundError(f"File not found: {path}")

        def _read() -> str:
            return path.read_text(encoding="utf-8")

        return await run_off_loop(_read)


class IngestorFactory:
    """Factory for creating ingestors by format."""

    _ingestors: dict[str, type[Ingestor]] = {
        ".pdf": PDFIngestor,
        ".docx": DOCXIngestor,
        ".html": HTMLIngestor,
        ".htm": HTMLIngestor,
        ".txt": TextIngestor,
    }

    # Runtime OVERRIDES of the PROD-16a bounds, empty by default.
    #
    # SSOT: the values themselves live in IngestSettings and nowhere else, so
    # they are configurable (LEGGIE_INGEST_*) and cannot drift between the
    # three places that used to spell them out. This dict exists only so a
    # caller — in practice a test — can tune one cap without touching the
    # environment. Keys: max_file_size_mb, max_pages, max_elements, timeout_s.
    bounds: dict[str, float | int] = {}

    @classmethod
    def _cap(cls, name: str) -> float | int:
        """Resolve one cap: a runtime override if set, else the setting."""
        if name in cls.bounds:
            return cls.bounds[name]
        caps = get_settings().ingest
        if name == "timeout_s":
            return caps.timeout_seconds
        value: float | int = getattr(caps, name)
        return value

    @classmethod
    def register_format(cls, extension: str, ingestor_cls: type[Ingestor]) -> None:
        """Register a custom ingestor for a file extension."""
        cls._ingestors[extension.lower()] = ingestor_cls

    @classmethod
    def get_ingestor(cls, source: Path | str) -> Ingestor:
        """Get the appropriate ingestor for a file based on its extension.

        Every ingestor is wrapped in ``BoundedIngestor`` so the safety caps
        apply uniformly across all four formats (PROD-16a).
        """
        path = Path(source)
        ext = path.suffix.lower()
        if ext not in cls._ingestors:
            raise UnsupportedFormatError(f"Unsupported format: {ext}")
        base = cls._ingestors[ext]()
        # Page/element caps are counts and must arrive as int.
        return BoundedIngestor(
            base,
            max_file_size_mb=float(cls._cap("max_file_size_mb")),
            max_pages=int(cls._cap("max_pages")),
            max_elements=int(cls._cap("max_elements")),
            timeout_s=float(cls._cap("timeout_s")),
        )

    @classmethod
    async def ingest(cls, source: Path | str) -> str:
        """Convenience: detect format and ingest in one call."""
        ingestor = cls.get_ingestor(source)
        return await ingestor.ingest(source)
