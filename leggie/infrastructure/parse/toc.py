"""Table-of-contents detection — identify the TOC structural region.

Greek bills open with a ΠΙΝΑΚΑΣ ΠΕΡΙΕΧΟΜΕΝΩΝ that lists every article
heading verbatim. Those lines are indistinguishable from real headings,
so they must be detected and excised before article extraction.

The TOC is identified as a structural region: a maximal run of consecutive
heading matches with no substantive body text between them.
"""

from __future__ import annotations

import re

from leggie.infrastructure.parse.patterns import _PRE_BODY_MARKER, ARTICLE_HEADING_SINGLE_LINE


def find_toc_span(text: str) -> tuple[int, int] | None:
    """Find the [start, end) span of the pre-body region, or None.

    Uses marker + structural detection:
    1. Find the LAST pre-body marker — ΠΙΝΑΚΑΣ ΠΕΡΙΕΧΟΜΕΝΩΝ, or an
       ΑΙΤΙΟΛΟΓΙΚΗ ΕΚΘΕΣΗ / ΑΝΑΛΥΣΗ ΣΥΝΕΠΕΙΩΝ ΡΥΘΜΙΣΗΣ that follows it.
    2. After that marker, scan headings. The body starts at the first heading
       whose number drops below the maximum seen so far (each pre-body region
       is monotonic ascending, then the next region restarts at 1).

    Anchoring on the LAST marker rather than the first is DH-9: a bill whose
    TOC is followed by a per-article rationale has *two* ascending runs before
    the body, and stopping at the first break lands inside the rationale — the
    commentary then parses as the articles and the real body follows as
    duplicate IDs. Anchoring on the last marker keeps the excision
    marker-driven; scanning for the last descent *anywhere* would instead
    re-create F0, since a single line-anchored in-body cross-reference would
    truncate the whole body.

    Returns (region_start, body_start) or None if no marker is present —
    never excise on a guess.
    """
    # Last marker first, falling back to earlier ones: a rationale section
    # that is pure prose (no per-article headings of its own) yields no
    # descent, and anchoring there regardless would abandon the TOC excision
    # entirely and hand every TOC line back as a phantom article — the exact
    # F0 failure this module exists to prevent.
    for marker in reversed(list(_PRE_BODY_MARKER.finditer(text))):
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

    A Greek bill's ΠΙΝΑΚΑΣ ΠΕΡΙΕΧΟΜΕΝΩΝ repeats every article heading, and an
    ΑΙΤΙΟΛΟΓΙΚΗ ΕΚΘΕΣΗ may walk the same numbers again — so the body is the
    last of several ascending runs of the same numbers. It therefore begins
    at the first heading that breaks the ascent of the last pre-body region
    that has one. Returns 0 when there is no marker, or when no restart
    follows any of them — never excise on a guess.
    """
    span = find_toc_span(text)
    if span is None:
        return 0
    return span[1]
