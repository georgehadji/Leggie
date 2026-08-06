"""Table-of-contents detection — identify the TOC structural region.

Greek bills open with a ΠΙΝΑΚΑΣ ΠΕΡΙΕΧΟΜΕΝΩΝ that lists every article
heading verbatim. Those lines are indistinguishable from real headings,
so they must be detected and excised before article extraction.

The TOC is identified as a structural region: a maximal run of consecutive
heading matches with no substantive body text between them.
"""

from __future__ import annotations

import re

from leggie.infrastructure.parse.patterns import _TOC_MARKER, ARTICLE_HEADING_SINGLE_LINE


def find_toc_span(text: str) -> tuple[int, int] | None:
    """Find the [start, end) span of the TOC region, or None.

    Uses TOC marker + structural detection:
    1. Find the ΠΙΝΑΚΑΣ ΠΕΡΙΕΧΟΜΕΝΩΝ marker.
    2. After the marker, scan headings. The body starts at the first heading
       whose number drops below the maximum seen so far (the TOC is monotonic
       ascending, then the body restarts at 1).

    Returns (toc_start, body_start) or None if no TOC detected.
    """
    marker = _TOC_MARKER.search(text)
    if not marker:
        return None

    max_seen = 0
    for match in ARTICLE_HEADING_SINGLE_LINE.finditer(text, marker.end()):
        leading_digits = re.match(r"\d+", match.group(1))
        num_int = int(leading_digits.group()) if leading_digits else 0
        if max_seen > 0 and num_int < max_seen:
            return (marker.start(), match.start())
        max_seen = max(max_seen, num_int)

    return None


def find_body_start(text: str) -> int:
    """Return the offset where the enacting body begins (F0.6).

    A Greek bill's ΠΙΝΑΚΑΣ ΠΕΡΙΕΧΟΜΕΝΩΝ repeats every article heading, so
    the TOC and the body are two ascending runs of the same numbers. The
    body therefore begins at the first heading that breaks the TOC's
    ascent. Returns 0 when there is no TOC marker, or when no restart
    follows it — never excise on a guess.
    """
    span = find_toc_span(text)
    if span is None:
        return 0
    return span[1]
