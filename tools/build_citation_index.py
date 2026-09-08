#!/usr/bin/env python3
"""Build the citation resolution index (PROD-05).

Offline builder for `leggie/data/citation_index.json`. Seeds the index with
known-good Greek legal citation identifiers across four categories:

* Σύνταγμα (Constitution) — "Σύνταγμα Άρθρο N"
* ΦΕΚ (Government Gazette) — e.g. "ΦΕΚ Α 137/2023"
* CELEX (EU law) — e.g. "32018L1972"
* Χάρτης (EU Charter) — "Χάρτης Άρθρο N"

No ECLI or URL identifiers are seeded, and only the ΦΕΚ/CELEX entries are in a
shape ``GreekCitationParser.parse()`` can ever emit — the Σύνταγμα and Χάρτης
strings exist for a resolver that does not parse them yet.

DH-37 — ``authoritative_schemes`` is the switch that lets the resolver report a
MISS as positively disproven, and it ships EMPTY on purpose. Every seeded
category here is a hand-picked handful, not an exhaustive register: three ΦΕΚ
numbers cannot tell a fabricated gazette reference from a real one nobody
seeded. Add a scheme name to that list ONLY when the index for it is built from
a complete live register (data.gov.gr `gov-et-laws`, EUR-Lex CELLAR SPARQL) —
arming it on a seeded list makes CoVe hard-drop valid findings.

Usage:
    python tools/build_citation_index.py [--output leggie/data/citation_index.json]
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
    "32018L1972",  # European Electronic Communications Code
    "32016R0679",  # GDPR
    "32015L0849",  # 4AMLD
    "32019L0790",  # AML directive
]

# Key EU Charter (Χάρτης) articles.
CHARTER_ARTICLES = [f"Χάρτης Άρθρο {n}" for n in range(1, 55)]


def identifier_count() -> int:
    """Total seeded identifiers."""
    return (
        len(CONSTITUTION_ARTICLES) + len(FEK_REFERENCES) + len(CELEX_REFS) + len(CHARTER_ARTICLES)
    )


def build() -> dict[str, Any]:
    """Assemble the citation index document."""
    identifiers = CONSTITUTION_ARTICLES + FEK_REFERENCES + CELEX_REFS + CHARTER_ARTICLES
    return {
        "description": (
            "Known-good Greek legal citation identifiers used by the deterministic "
            "citation resolver (PROD-05). Constitution articles, ΦΕΚ, CELEX, and "
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
        # DH-37: schemes this index is an EXHAUSTIVE register for, and may
        # therefore disprove a miss in. Empty because none of the above is —
        # see the module docstring before adding to it.
        "authoritative_schemes": [],
        "identifiers": identifiers,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the citation resolution index.")
    parser.add_argument(
        "--output",
        default="leggie/data/citation_index.json",
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
