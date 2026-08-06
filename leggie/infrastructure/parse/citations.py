"""Citation extraction — ΦΕΚ, CELEX, ECLI references."""

from __future__ import annotations

from typing import Any

from leggie.infrastructure.parse.patterns import CELEX_CITATION, ECLI_CITATION, FEK_CITATION


def extract_citations(text: str) -> list[dict[str, Any]]:
    """Extract all citation references from text."""
    citations: list[dict[str, Any]] = []
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
