"""Greek legal document parser — Builder + Composite pattern.

Parses a Greek bill from its legal structure:
  Document → Articles (Άρθρο) → Paragraphs (παράγραφοι) → SubParagraphs (εδάφια)

FIX_PLAN F0 fixes:
- Line-anchor headings: ΄Αρθρο N only at start of line (not in-body cross-refs)
- Number shape: constrained to digits + optional Greek suffix (58Α)
- Cross-ref stop-list: reject if followed by του ν., του Κώδικα, etc.
- Monotonic-sequence guard: large backward/forward jumps are cross-refs
- PDF newline repair: join mid-token line breaks
"""

from __future__ import annotations

import re
from re import Pattern
from typing import Any

from leggie.domain.models import Article, Document, Paragraph, SubParagraph

# ── Cross-reference stop-list (FIX_PLAN D1.4) ───────────────────────────────
_STOP_PATTERN: Pattern[str] = re.compile(
    r"(?:"
    r"του\s+ν\b|του\s+Κώδικα|ΚΠολΔ|ΚΠΔ|\bΠΚ\b|\bΑΚ\b|"
    r"του\s+Συντάγματος|"
    r"της\s+Οδηγίας|του\s+Κανονισμού|της\s+Συνθήκης|"
    r"του\s+π\.δ\.|του\s+ν\.\s*\d+"
    r")",
    re.UNICODE | re.IGNORECASE,
)


# ── Article heading pattern (FIX_PLAN D1.1, D1.2) ──────────────────────────
# Line-anchored: ^\s*Άρθρο\s+ at start of line (re.MULTILINE)
# Number shape: \d+[Α-Ωα-ω]?  — integer with optional single Greek suffix
# Title: remainder of the heading line
ARTICLE_HEADING: Pattern[str] = re.compile(
    r"^\s*Άρθρο\s+(\d+[Α-Ωα-ω]?)\s*[—–\-]?\s*(.*?)$",
    re.UNICODE | re.MULTILINE,
)

# Paragraph patterns
PARAGRAPH_PATTERN: Pattern[str] = re.compile(r"(\d+)\.\s*(.*?)(?=\n\d+\.|\Z)", re.DOTALL)
SUB_PARAGRAPH_PATTERN: Pattern[str] = re.compile(
    r"([α-ωΑ-Ω])\)\s*(.*?)(?=\n\s*[α-ωΑ-Ω]\)|\Z)", re.DOTALL
)

# Citation patterns
FEK_CITATION: Pattern[str] = re.compile(r"ΦΕΚ\s+(?:[ΑαΒβΓγΔδΕεΣΤστ]’?)\s*(\d+)/(\d{4})", re.UNICODE)
CELEX_CITATION: Pattern[str] = re.compile(r"CELEX[:/\s]*([A-Za-z0-9]+)", re.UNICODE)
ECLI_CITATION: Pattern[str] = re.compile(r"ECLI[:/\s]*([A-Za-z0-9:]+)", re.UNICODE)


class ParseError(Exception):
    """Raised when document parsing fails."""


class DocumentParser:
    """Parses Greek legal documents into structured composite trees.

    Pure — input text, output Document. F0 fixes applied.
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
        """Clean and normalize text before parsing.

        F0.5: PDF newline repair — join mid-token line breaks.
        E.g. "Άρθρο 64\\nθρου" → "Άρθρο 64\\nθρου" becomes "Άρθρο 64θρου"
        after lower-lower join.
        """
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        # Remove excessive blank lines
        text = re.sub(r"\n{3,}", "\n\n", text)
        # F0.5: Join letters split across lines: lowercase-\n-lowercase without punctuation
        text = re.sub(r"([α-ωa-z])\n([α-ωa-z])", r"\1\2", text)
        return text.strip()

    def _extract_articles(self, text: str) -> list[Article]:
        r"""Extract articles using line-anchored heading detection.

        F0.1: Line-anchor headings via ^ with re.MULTILINE.
        F0.2: Number constrained to \d+[Α-Ωα-ω]? (no multi-token garbage).
        F0.3: Cross-ref stop-list rejects in-body references.
        F0.4: Monotonic-sequence guard.
        """
        candidates: list[dict[str, Any]] = []
        for match in ARTICLE_HEADING.finditer(text):
            article_num = match.group(1)
            article_title = match.group(2).strip() if match.group(2) else ""
            line_start = match.start()
            line_end = match.end()

            # F0.3: Check cross-ref stop-list on the HEADING LINE only (not the body)
            heading_text = text[line_start:line_end]
            if _STOP_PATTERN.search(heading_text):
                continue

            # F0.4: Extract the content from this heading to the next heading
            candidates.append(
                {
                    "num": article_num,
                    "title": article_title,
                    "start": line_start,
                }
            )

        # Convert candidates to articles
        articles: list[Article] = []
        last_num = 0

        for i, cand in enumerate(candidates):
            num_str = cand["num"]
            # Extract leading digits for monotonic check
            leading_digits = re.match(r"\d+", num_str)
            num_int = int(leading_digits.group()) if leading_digits else 0

            # F0.4: Monotonic-sequence guard
            # Allow small gaps (1..3, 3..5) but reject extreme jumps
            # Cross-references like 552, 622Γ produce huge jumps then back to 59
            if last_num > 0:
                delta = num_int - last_num
                if delta > 50 or (delta < 0 and abs(delta) > 50):
                    continue  # Phantom cross-reference

            last_num = num_int

            # Content: from this heading start to next heading start (or end)
            content_end = candidates[i + 1]["start"] if i + 1 < len(candidates) else len(text)
            raw = text[cand["start"] : content_end].strip()

            paragraphs = self._extract_paragraphs(raw)

            articles.append(
                Article(
                    id=num_str,
                    title=cand["title"],
                    paragraphs=paragraphs,
                    raw_text=raw,
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
            paragraphs.append(Paragraph(number=num, text=para_text, subparagraphs=sub_paras))
        return paragraphs

    def _extract_subparagraphs(self, paragraph_text: str) -> list[SubParagraph]:
        """Extract sub-paragraphs (εδάφια) within a paragraph."""
        sub_paras: list[SubParagraph] = []
        matches = list(SUB_PARAGRAPH_PATTERN.finditer(paragraph_text))
        for match in matches:
            letter = match.group(1).strip()
            p_text = match.group(2).strip()
            sub_paras.append(SubParagraph(letter=letter, text=p_text))
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
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        for line in lines[:5]:
            if len(line) > 10 and not line.startswith("Άρθρο"):
                return line[:200]
        return "Untitled Document"

    def extract_citations(self, text: str) -> list[dict[str, Any]]:
        """Extract all citation references from text."""
        citations: list[dict[str, Any]] = []
        for match in FEK_CITATION.finditer(text):
            citations.append(
                {
                    "type": "fek",
                    "identifier": f"ΦΕΚ {match.group(1)}/{match.group(2)}",
                    "original_text": match.group(0),
                }
            )
        for match in CELEX_CITATION.finditer(text):
            citations.append(
                {
                    "type": "celex",
                    "identifier": match.group(1),
                    "original_text": match.group(0),
                }
            )
        for match in ECLI_CITATION.finditer(text):
            citations.append(
                {
                    "type": "ecli",
                    "identifier": match.group(1),
                    "original_text": match.group(0),
                }
            )
        return citations
