"""Preprocessing — newline repair, normalisation for Greek legal text.

All functions are pure: str -> str. No state, no I/O.
"""

from __future__ import annotations

import re


def normalize_newlines(text: str) -> str:
    """Normalize \\r\\n and \\r to \\n, collapse excessive blank lines."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def repair_pdf_line_breaks(text: str) -> str:
    """Join letters split across PDF line breaks.

    F0.5: A lowercase letter at end of line followed by a lowercase letter
    at start of the next line is a mid-token page/font break in the PDF.
    E.g. "Άρθρο 64\\nθρου" → "Άρθρο 64θρου".
    """
    return re.sub(r"([α-ωa-z])\n([α-ωa-z])", r"\1\2", text)


def preprocess(text: str) -> str:
    """Full preprocessing pipeline for Greek bill text."""
    text = normalize_newlines(text)
    return repair_pdf_line_breaks(text)
