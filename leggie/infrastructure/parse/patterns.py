"""Compiled regex patterns for Greek legal document parsing.

All patterns are module-level constants, imported by the parser modules.
This keeps regex compilation at import time and patterns reusable.
"""

from __future__ import annotations

import re
from re import Pattern

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

# A heading is a cross-reference only when the stop phrase is essentially the
# whole title ("Άρθρο 552 του ΚΠολΔ"). Greek amending titles legitimately cite
# other instruments *after* a substantive title ("Άρθρο 61 Προσθήκη άρθρου 58Α
# και τροποποίηση άρθρου 72 του ν. 4999/2022"), so the stop-list must not be
# applied to the whole heading line — that rejected 22 real headings on the
# reference bill.
CROSS_REF_TITLE_PREFIX_MIN = 12


# ── Table of contents (ΠΙΝΑΚΑΣ ΠΕΡΙΕΧΟΜΕΝΩΝ) ───────────────────────────────
# Greek bills open with a TOC that lists every article heading verbatim. Those
# lines are indistinguishable from real headings, so they must be excised
# before extraction or they become phantom (content-free) articles.
_TOC_MARKER: Pattern[str] = re.compile(
    r"^[ \t]*(?:ΠΙΝΑΚΑΣ\s+ΠΕΡΙΕΧΟΜΕΝΩΝ|ΠΕΡΙΕΧΟΜΕΝΑ)[ \t]*$",
    re.UNICODE | re.MULTILINE,
)

# ── Pre-body sections (DH-9) ───────────────────────────────────────────────
# The TOC is not the only region that walks "Άρθρο 1, 2, 3…" before the
# enacting text. A Greek bill routinely carries an ΑΙΤΙΟΛΟΓΙΚΗ ΕΚΘΕΣΗ
# (explanatory memorandum) and/or an ΑΝΑΛΥΣΗ ΣΥΝΕΠΕΙΩΝ ΡΥΘΜΙΣΗΣ (regulatory
# impact analysis), each with its own per-article commentary headings. Body
# detection anchors on the LAST of these markers, so every pre-body run is
# skipped rather than just the first — otherwise the rationale's own restart
# to "Άρθρο 1" is mistaken for the body and the real body then reappears as
# duplicate IDs.
#
# Same strictness as _TOC_MARKER (the marker must be the whole line): this
# heuristic is the site of the F0 phantom-articles incident, and toc.py's own
# rule is "never excise on a guess".
_PRE_BODY_MARKER: Pattern[str] = re.compile(
    r"^[ \t]*(?:"
    r"ΠΙΝΑΚΑΣ\s+ΠΕΡΙΕΧΟΜΕΝΩΝ|ΠΕΡΙΕΧΟΜΕΝΑ|"
    r"ΑΙΤΙΟΛΟΓΙΚΗ\s+ΕΚΘΕΣΗ|"
    r"ΑΝΑΛΥΣΗ\s+ΣΥΝΕΠΕΙΩΝ\s+ΡΥΘΜΙΣΗΣ"
    r")[ \t]*$",
    re.UNICODE | re.MULTILINE,
)


# ── Article heading pattern (FIX_PLAN D1.1, D1.2) ──────────────────────────
# Line-anchored: ^\s*Άρθρο\s+ at start of line (re.MULTILINE)
# Number shape: \d+[Α-Ωα-ω]?  — integer with optional single Greek suffix
# Title: remainder of the heading line
ARTICLE_HEADING: Pattern[str] = re.compile(
    r"^\s*Άρθρο\s+(\d+[Α-Ωα-ω]?)\s*[—–\-]?\s*(.*?)$",
    re.UNICODE | re.MULTILINE,
)
# FIX_PLAN P-2: The \s* before the optional dash matches \n and absorbs the
# next line. Replace with [ \t]* to prevent newline crossing. The title is
# bounded by [^\n]* to keep it on one line. See docs/PARSER_REMEDIATION_PLAN.md §5.1
ARTICLE_HEADING_SINGLE_LINE: Pattern[str] = re.compile(
    r"^\s*Άρθρο\s+(\d+[Α-Ωα-ω]?)[ \t]*[—–\-]?[ \t]*([^\n]*)",
    re.UNICODE | re.MULTILINE,
)

# Paragraph patterns
# A paragraph marker is "N." at the START of a line, followed by whitespace or
# the line end. Both halves matter. Unanchored, the pattern matched inside dates
# and decimals — on the reference bill "(L της 16.4.2024)" split Άρθρο 1 at "4.",
# inventing a paragraph and silently discarding every character before it. And
# without the trailing-whitespace requirement, a line that merely *opens* with a
# date ("16.4.2024 ...") reintroduces the same false match.
_PARAGRAPH_MARKER = r"^[ \t]*\d+\.(?=[ \t]|$)"
PARAGRAPH_PATTERN: Pattern[str] = re.compile(
    r"^[ \t]*(\d+)\.(?=[ \t]|$)[ \t]*(.*?)(?=" + _PARAGRAPH_MARKER + r"|\Z)",
    re.DOTALL | re.MULTILINE,
)
SUB_PARAGRAPH_PATTERN: Pattern[str] = re.compile(
    r"([α-ωΑ-Ω])\)\s*(.*?)(?=\n\s*[α-ωΑ-Ω]\)|\Z)", re.DOTALL
)

# Citation patterns
FEK_CITATION: Pattern[str] = re.compile(r"ΦΕΚ\s+(?:[ΑαΒβΓγΔδΕεΣΤστ]’?)\s*(\d+)/(\d{4})", re.UNICODE)
CELEX_CITATION: Pattern[str] = re.compile(r"CELEX[:/\s]*([A-Za-z0-9]+)", re.UNICODE)
ECLI_CITATION: Pattern[str] = re.compile(r"ECLI[:/\s]*([A-Za-z0-9:]+)", re.UNICODE)
