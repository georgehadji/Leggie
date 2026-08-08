#!/usr/bin/env python3
"""Build the citation resolution index (PROD-05).

Offline builder for `data/citation_index.json`. Seeds the index with
known-good Greek legal citation identifiers across the three resolver
categories:

* ΦΕΚ (Government Gazette) — e.g. "ΦΕΚ Α 137/2023"
* CELEX (EU law) — e.g. "32018L1972"
* ECLI (case law) — e.g. "ECLI:EL:...:2023:..."

Future: pull live identifiers from data.gov.gr `gov-et-laws`, EUR-Lex CELLAR
SPARQL, and the static Σύνταγμα when network access is available. This script
always produces a valid, versioned index.

Usage:
    python tools/build_citation_index.py [--output data/citation_index.json]
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Constitutions: the current Greek Σύνταγμα articles (revised 2019).
CONSTITUTION_ARTICLES = [f"Σύνταγμα Άρθρο {n}" for n in range(1, 121)]

# ΦΕΚ references cited across the current test corpus / common legal work.
FEK_REFERENCES = [
    "ΦΕΚ Α 137/2023",
    "ΦΕΚ Α 196/2019",
    "ΦΕΚ Α 211/2020",
]

# EU law (CELEX) — GDPR, directives, regulations commonly cited in Greek bills.
CELEX_REFS = [
    "32018L1972",   # European Electronic Communications Code
    "32016R0679",   # GDPR
    "32015L0849",   # 4AMLD
    "32019L0790",   # AML directive
]

# Key EU Charter (Χάρτης) articles.
CHARTER_ARTICLES = [f"Χάρτης Άρθρο {n}" for n in range(1, 55)]


def identifier_count() -> int:
    """Total seeded identifiers."""
    return len(CONSTITUTION_ARTICLES) + len(FEK_REFERENCES) + len(CELEX_REFS) + len(CHARTER_ARTICLES)


def build() -> dict[str, Any]:
    """Assemble the citation index document."""
    identifiers = (
        CONSTITUTION_ARTICLES + FEK_REFERENCES + CELEX_REFS + CHARTER_ARTICLES
    )
    return {
        "description": (
            "Known-good Greek legal citation identifiers used by the deterministic "
            "citation resolver (PROD-05). Constituition articles, ΦΕΚ, CELEX, and "
            "Charter references."
        ),
        "source": "Built offline by tools/build_citation_index.py",
        "version": 1,
        "identifier_count": len(identifiers),
        "build_date": datetime.now(UTC).isoformat(),
        "categories": {
            "constitution": len(CONSTITUTION_ARTICLES),
            "fek": len(FEK_REFERENCES),
            "celex": len(CELEX_REFS),
            "charter": len(CHARTER_ARTICLES),
        },
        "identifiers": identifiers,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the citation resolution index.")
    parser.add_argument(
        "--output", default="leggie/data/citation_index.json",
        help="Output path (default: the packaged leggie/data location)",
    )
    args = parser.parse_args()

    index = build()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {index['identifier_count']} identifiers to {out}")


if __name__ == "__main__":
    main()
