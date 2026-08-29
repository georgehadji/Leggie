"""Paragraph / subparagraph extraction from article text."""

from __future__ import annotations

from leggie.domain.models import Paragraph, SubParagraph
from leggie.infrastructure.parse.patterns import (
    ARTICLE_HEADING_SINGLE_LINE,
    PARAGRAPH_PATTERN,
    SUB_PARAGRAPH_PATTERN,
)


def _strip_heading(article_text: str) -> str:
    """Return *article_text* without its leading "Άρθρο N <title>" heading.

    `extract_articles` slices each article from its heading offset, so the
    heading line is part of the text handed here. Left in, it becomes the
    opening of the first paragraph.
    """
    heading = ARTICLE_HEADING_SINGLE_LINE.match(article_text)
    body = article_text[heading.end() :] if heading else article_text
    return body.strip()


def extract_paragraphs(article_text: str) -> list[Paragraph]:
    """Extract paragraphs within an article.

    Greek bills write an article body either as a numbered list ("1. ...") or
    as plain prose, optionally lettered ("α) ... β) ..."). Prose bodies carry
    no paragraph number, so they are returned as a single paragraph "1" rather
    than dropped — 24% of the reference bill's articles are prose and
    previously parsed to an empty body, leaving the lenses nothing to analyse.
    """
    body = _strip_heading(article_text)
    if not body:
        return []

    paragraphs: list[Paragraph] = []
    for match in PARAGRAPH_PATTERN.finditer(body):
        para_text = match.group(2).strip()
        paragraphs.append(
            Paragraph(
                number=match.group(1).strip(),
                text=para_text,
                subparagraphs=extract_subparagraphs(para_text),
            )
        )
    if paragraphs:
        return paragraphs

    return [
        Paragraph(
            number="1",
            text=body,
            subparagraphs=extract_subparagraphs(body),
        )
    ]


def extract_subparagraphs(paragraph_text: str) -> list[SubParagraph]:
    """Extract sub-paragraphs (εδάφια) within a paragraph."""
    sub_paras: list[SubParagraph] = []
    matches = list(SUB_PARAGRAPH_PATTERN.finditer(paragraph_text))
    for match in matches:
        letter = match.group(1).strip()
        p_text = match.group(2).strip()
        sub_paras.append(SubParagraph(letter=letter, text=p_text))
    return sub_paras
