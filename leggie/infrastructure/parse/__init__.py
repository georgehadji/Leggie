"""Greek legal document parser — Builder + Composite pattern.

Parses a Greek bill from its legal structure:
  Document → Articles (Άρθρο) → Paragraphs (παράγραφοι) → SubParagraphs (εδάφια)

Public API (unchanged):
    DocumentParser      — main parser class
    ARTICLE_HEADING      — compiled heading regex
    ParseError           — exception class

All re-exported from the sub-modules for backward compatibility.
"""

from __future__ import annotations

from typing import Any

from leggie.domain.models import Article
from leggie.domain.models import Document as Document
from leggie.domain.models import Paragraph as Paragraph
from leggie.domain.models import SubParagraph as SubParagraph
from leggie.domain.models.parse_integrity import (
    ParseIntegrityReport as ParseIntegrityReport,
)
from leggie.domain.models.parse_integrity import (
    RejectedCandidate as RejectedCandidate,
)

# Re-export patterns for backward compat
from leggie.infrastructure.parse.articles import extract_articles
from leggie.infrastructure.parse.articles import is_cross_reference as is_cross_reference
from leggie.infrastructure.parse.citations import extract_citations
from leggie.infrastructure.parse.patterns import (
    _STOP_PATTERN as _STOP_PATTERN,
)
from leggie.infrastructure.parse.patterns import (
    _TOC_MARKER as _TOC_MARKER,
)
from leggie.infrastructure.parse.patterns import (
    ARTICLE_HEADING as ARTICLE_HEADING,
)
from leggie.infrastructure.parse.patterns import (
    CELEX_CITATION as CELEX_CITATION,
)
from leggie.infrastructure.parse.patterns import (
    ECLI_CITATION as ECLI_CITATION,
)
from leggie.infrastructure.parse.patterns import (
    FEK_CITATION as FEK_CITATION,
)
from leggie.infrastructure.parse.patterns import (
    PARAGRAPH_PATTERN as PARAGRAPH_PATTERN,
)
from leggie.infrastructure.parse.patterns import (
    SUB_PARAGRAPH_PATTERN as SUB_PARAGRAPH_PATTERN,
)
from leggie.infrastructure.parse.preprocess import preprocess
from leggie.infrastructure.parse.structure import extract_paragraphs as extract_paragraphs
from leggie.infrastructure.parse.structure import extract_subparagraphs as extract_subparagraphs
from leggie.infrastructure.parse.toc import find_body_start


class ParseError(Exception):
    """Raised when document parsing fails."""


class DocumentParser:
    """Parses Greek legal documents into structured composite trees.

    Pure — input text, output Document. F0 fixes applied.
    """

    def parse(self, text: str, title: str = "", source_format: str = "txt") -> Document:
        """Parse full document text into a structured Document object."""
        cleaned = preprocess(text)
        articles, _rejected = extract_articles(cleaned)
        preamble = self._extract_preamble(cleaned, articles)
        return Document(
            title=title or self._infer_title(text),
            source_format=source_format,
            articles=articles,
            preamble=preamble,
            raw_text=text,
        )

    def parse_with_integrity(
        self, text: str, title: str = "", source_format: str = "txt"
    ) -> tuple[Document, ParseIntegrityReport]:
        """Parse and return an integrity report alongside the document.

        The report records every candidate that was rejected and why,
        making invisible drops impossible.
        """
        cleaned = preprocess(text)
        articles, rejected_raw = extract_articles(cleaned)
        preamble = self._extract_preamble(cleaned, articles)
        doc = Document(
            title=title or self._infer_title(text),
            source_format=source_format,
            articles=articles,
            preamble=preamble,
            raw_text=text,
        )

        # Build integrity report using shared validation functions
        from leggie.infrastructure.parse.integrity import (
            compute_article_numbers,
            compute_title_only_ids,
        )

        ids = [a.id for a in articles]
        _, missing_list, dup_list = compute_article_numbers(ids)
        duplicates = tuple(dup_list)
        missing = tuple(missing_list)
        empty_ids = tuple(compute_title_only_ids(articles))

        toc_span = find_body_start(cleaned)
        toc = (0, toc_span) if toc_span > 0 else None

        rejected = tuple(
            RejectedCandidate(
                number=r["num"],
                reason=r.get("reason", "unknown"),
                offset=r.get("offset", 0),
            )
            for r in rejected_raw
        )

        report = ParseIntegrityReport(
            articles_parsed=len(articles),
            distinct_ids=len({a.id for a in articles}),
            duplicate_ids=duplicates,
            missing_numbers=missing,
            empty_or_heading_only=empty_ids,
            toc_span=toc,
            rejected=rejected,
        )
        return doc, report

    def _extract_preamble(self, text: str, articles: list[Article]) -> str:
        """Extract the preamble (text before the first article)."""
        if not articles:
            return text[:2000] if text else ""
        first_article_start = text.find(articles[0].raw_text)
        if first_article_start <= 0:
            return ""
        return text[:first_article_start].strip()[:2000]

    def _infer_title(self, text: str) -> str:
        """Infer document title from first meaningful line."""
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        for line in lines[:5]:
            if len(line) > 10 and not line.startswith("Άρθρο"):
                return line[:200]
        return "Untitled Document"

    def extract_citations(self, text: str) -> list[dict[str, Any]]:
        """Extract all citation references from text."""
        return extract_citations(text)
