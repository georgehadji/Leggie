"""Integrity — parse validation, produces the domain integrity report.

After extraction, this module validates the parse result: checks for
missing numbers, duplicates, and produces a structured report.
"""

from __future__ import annotations

from leggie.domain.models import Article


def compute_article_numbers(ids: list[str]) -> tuple[list[int], list[int], list[str]]:
    """From article IDs, compute present, missing, and duplicate numbers.

    Returns:
        (present_numbers, missing_numbers, duplicate_ids)
    """
    seen: dict[str, int] = {}
    for aid in ids:
        seen[aid] = seen.get(aid, 0) + 1

    duplicates = [aid for aid, count in seen.items() if count > 1]

    # Map IDs to their leading numeric part
    import re
    present: set[int] = set()
    for aid in ids:
        m = re.match(r"\d+", aid)
        if m:
            present.add(int(m.group()))

    max_num = max(present) if present else 0
    missing = sorted(set(range(1, max_num + 1)) - present)
    present_sorted = sorted(present)

    return present_sorted, missing, duplicates


def compute_title_only_ids(articles: list[Article]) -> list[str]:
    """Return IDs of articles that have no paragraph content."""
    return [a.id for a in articles if not a.paragraphs]
