"""Paragraph / subparagraph extraction from article text."""

from __future__ import annotations

from leggie.domain.models import Paragraph, SubParagraph
from leggie.infrastructure.parse.patterns import PARAGRAPH_PATTERN, SUB_PARAGRAPH_PATTERN


def extract_paragraphs(article_text: str) -> list[Paragraph]:
    """Extract paragraphs within an article."""
    paragraphs: list[Paragraph] = []
    matches = list(PARAGRAPH_PATTERN.finditer(article_text))
    for match in matches:
        num = match.group(1).strip()
        para_text = match.group(2).strip()
        sub_paras = extract_subparagraphs(para_text)
        paragraphs.append(
            Paragraph(number=num, text=para_text, subparagraphs=sub_paras)
        )
    return paragraphs


def extract_subparagraphs(paragraph_text: str) -> list[SubParagraph]:
    """Extract sub-paragraphs (εδάφια) within a paragraph."""
    sub_paras: list[SubParagraph] = []
    matches = list(SUB_PARAGRAPH_PATTERN.finditer(paragraph_text))
    for match in matches:
        letter = match.group(1).strip()
        p_text = match.group(2).strip()
        sub_paras.append(SubParagraph(letter=letter, text=p_text))
    return sub_paras
