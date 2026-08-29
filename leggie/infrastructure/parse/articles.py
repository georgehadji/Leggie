"""Article segmentation — heading detection, candidate filtering.

Extracts article candidates from cleaned text, applies cross-reference
rejection, monotonic-sequence guard, and produces `Article` domain objects.
"""

from __future__ import annotations

import re
from typing import Any

from leggie.domain.models import Article
from leggie.infrastructure.parse.patterns import (
    _STOP_PATTERN,
    ARTICLE_HEADING_SINGLE_LINE,
    CROSS_REF_TITLE_PREFIX_MIN,
)
from leggie.infrastructure.parse.structure import extract_paragraphs
from leggie.infrastructure.parse.toc import find_body_start


def is_cross_reference(title: str) -> bool:
    """True when *title* is only a reference to another instrument (F0.3).

    "Άρθρο 552 του ΚΠολΔ" is a cross-reference: the stop phrase *is* the
    title. "Άρθρο 61 Προσθήκη άρθρου 58Α ... του ν. 4999/2022" is a real
    amending heading: the stop phrase trails a substantive title.
    """
    stop = _STOP_PATTERN.search(title)
    if not stop:
        return False
    return len(title[: stop.start()].strip()) < CROSS_REF_TITLE_PREFIX_MIN


def extract_articles(text: str) -> tuple[list[Article], list[dict[str, Any]]]:
    r"""Extract articles using line-anchored heading detection.

    F0.1: Line-anchor headings via ^ with re.MULTILINE.
    F0.2: Number constrained to \d+[Α-Ωα-ω]? (no multi-token garbage).
    F0.3: Cross-ref stop-list rejects in-body references.
    F0.4: Monotonic-sequence guard.
    F0.6: Table-of-contents excision (must precede F0.4 — a TOC running to
          Άρθρο 91 otherwise poisons the guard's last_num and cascades into
          dropping every body article below 41).

    Returns (articles, rejected_candidates) — never silently drops.
    """
    # F0.6: Excise TOC before heading detection
    body_start = find_body_start(text)
    text = text[body_start:]

    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for match in ARTICLE_HEADING_SINGLE_LINE.finditer(text):
        article_num = match.group(1)
        article_title = match.group(2).strip() if match.group(2) else ""
        line_start = match.start()

        # F0.3: Reject headings that are nothing but a cross-reference.
        if is_cross_reference(article_title):
            rejected.append(
                {
                    "num": article_num,
                    "reason": "cross_reference",
                    "offset": line_start,
                }
            )
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
                rejected.append(
                    {
                        "num": num_str,
                        "reason": "monotonic_jump",
                        "offset": cand["start"],
                    }
                )
                continue

        last_num = num_int

        # Content: from this heading start to next heading start (or end)
        content_end = candidates[i + 1]["start"] if i + 1 < len(candidates) else len(text)
        raw = text[cand["start"] : content_end].strip()

        paragraphs = extract_paragraphs(raw)

        articles.append(
            Article(
                id=num_str,
                title=cand["title"],
                paragraphs=paragraphs,
                raw_text=raw,
            )
        )

    return articles, rejected
