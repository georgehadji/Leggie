"""Greek legal document parser — Builder + Composite pattern.

Parses a Greek bill from its legal structure:
  Document → Articles (Άρθρο) → Paragraphs (παράγραφοι) → SubParagraphs (εδάφια)
"""

from __future__ import annotations

import re
from re import Pattern

from leggie.domain.models import Article, Document, Paragraph, SubParagraph

# ── Constants / Patterns ────────────────────────────────────────────────────────

# Greek ordinal patterns
ARTICLE_PATTERN: Pattern = re.compile(
    r"Άρθρο\s+([Α-Ωα-ω0-9]+(?:\s*[Α-Ωα-ω0-9]*)*)\s*[—–\-]?\s*(.*?)(?=\n|$)",
    re.UNICODE,
)
PARAGRAPH_PATTERN: Pattern = re.compile(r"(\d+)\.\s*(.*?)(?=\n\d+\.|\Z)", re.DOTALL)
SUB_PARAGRAPH_PATTERN: Pattern = re.compile(
    r"([α-ωΑ-Ω])\)\s*(.*?)(?=\n\s*[α-ωΑ-Ω]\)|\Z)", re.DOTALL
)

# Citation patterns
FEK_CITATION: Pattern = re.compile(
    r"ΦΕΚ\s+(?:[ΑαΒβΓγΔδΕεΣΤστ]’?)\s*(\d+)/(\d{4})", re.UNICODE
)
CELEX_CITATION: Pattern = re.compile(r"CELEX[:/\s]*([A-Za-z0-9]+)", re.UNICODE)
ECLI_CITATION: Pattern = re.compile(r"ECLI[:/\s]*([A-Za-z0-9:]+)", re.UNICODE)


class ParseError(Exception):
    """Raised when document parsing fails."""


class DocumentParser:
    """Parses Greek legal documents into structured composite trees.

    Reusable across preprocessing steps. Pure — input text, output Document.
    """

    def parse(self, text: str, title: str = "", source_format: str = "txt") -> Document:
        """Parse full document text into a structured Document object."""
        cleaned = self._preprocess(text)
        articles = self._extract_articles(cleaned)
        preamble = self._extract_preamble(cleaned, articles)
        return Document(
            title=title or self._infer_title(text),
            source_format=source_format,
            articles=articles,
            preamble=preamble,
            raw_text=text,
        )

    def _preprocess(self, text: str) -> str:
        """Clean and normalize text before parsing."""
        # Normalize line endings
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        # Remove excessive blank lines
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _extract_articles(self, text: str) -> list[Article]:
        """Extract articles using the ARTICLE_PATTERN."""
        articles: list[Article] = []
        parts = list(ARTICLE_PATTERN.finditer(text))
        if not parts:
            return articles

        for i, match in enumerate(parts):
            article_num = match.group(1).strip()
            article_title = match.group(2).strip() if match.group(2) else ""

            # Determine article text: from match start to next article start (or end)
            start = match.start()
            end = parts[i + 1].start() if i + 1 < len(parts) else len(text)
            article_text = text[start:end].strip()

            paragraphs = self._extract_paragraphs(article_text)

            articles.append(
                Article(
                    id=article_num,
                    title=article_title,
                    paragraphs=paragraphs,
                    raw_text=article_text,
                )
            )
        return articles

    def _extract_paragraphs(self, article_text: str) -> list[Paragraph]:
        """Extract paragraphs within an article."""
        paragraphs: list[Paragraph] = []
        matches = list(PARAGRAPH_PATTERN.finditer(article_text))
        for match in matches:
            num = match.group(1).strip()
            para_text = match.group(2).strip()
            sub_paras = self._extract_subparagraphs(para_text)
            paragraphs.append(
                Paragraph(number=num, text=para_text, subparagraphs=sub_paras)
            )
        return paragraphs

    def _extract_subparagraphs(self, paragraph_text: str) -> list[SubParagraph]:
        """Extract sub-paragraphs (εδάφια) within a paragraph."""
        sub_paras: list[SubParagraph] = []
        matches = list(SUB_PARAGRAPH_PATTERN.finditer(paragraph_text))
        for match in matches:
            letter = match.group(1).strip()
            text = match.group(2).strip()
            sub_paras.append(SubParagraph(letter=letter, text=text))
        return sub_paras

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
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        for line in lines[:5]:
            if len(line) > 10 and not line.startswith("Άρθρο"):
                return line[:200]
        return "Untitled Document"

    def extract_citations(self, text: str) -> list[dict]:
        """Extract all citation references from text.

        Returns list of {type, identifier, original_text} dicts.
        """
        citations: list[dict] = []
        for match in FEK_CITATION.finditer(text):
            citations.append({
                "type": "fek",
                "identifier": f"ΦΕΚ {match.group(1)}/{match.group(2)}",
                "original_text": match.group(0),
            })
        for match in CELEX_CITATION.finditer(text):
            citations.append({
                "type": "celex",
                "identifier": match.group(1),
                "original_text": match.group(0),
            })
        for match in ECLI_CITATION.finditer(text):
            citations.append({
                "type": "ecli",
                "identifier": match.group(1),
                "original_text": match.group(0),
            })
        return citations
