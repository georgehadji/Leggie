"""findings_stats.py — summarize a Leggie findings JSON.

Usage:
    python findings_stats.py Outputs/<stem>_findings.json [--articles N]

Prints: total findings, histograms by type/severity/lens, confidence
distribution, and (with --articles) findings-per-article ratio.
Exit 1 on missing/invalid file.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")  # Greek-safe on Windows consoles


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", type=Path, help="findings JSON (list of finding dicts)")
    ap.add_argument("--articles", type=int, default=0,
                    help="article count from `leggie parse` for the ratio")
    args = ap.parse_args()

    if not args.path.exists():
        print(f"ERROR: file not found: {args.path}", file=sys.stderr)
        return 1
    try:
        findings = json.loads(args.path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"ERROR: cannot parse JSON: {exc}", file=sys.stderr)
        return 1
    if not isinstance(findings, list):
        print(f"ERROR: expected a JSON list, got {type(findings).__name__}", file=sys.stderr)
        return 1

    total = len(findings)
    print(f"total_findings: {total}")
    if total == 0:
        print("WARNING: zero findings — see leggie-debugging-playbook row 1")
        return 0

    for key in ("type", "severity", "lens"):
        counts = Counter(str(f.get(key, "?")) for f in findings)
        print(f"by_{key}: " + ", ".join(f"{k}={v}" for k, v in counts.most_common()))

    confs = [f.get("confidence") for f in findings if isinstance(f.get("confidence"), (int, float))]
    if confs:
        lo, hi = min(confs), max(confs)
        mean = sum(confs) / len(confs)
        print(f"confidence: n={len(confs)} min={lo:.2f} mean={mean:.2f} max={hi:.2f}")

    info_ratio = sum(1 for f in findings if str(f.get("severity")) == "info") / total
    print(f"info_filler_ratio: {info_ratio:.0%}  (historical pathology: 68%)")

    if args.articles:
        print(f"findings_per_article: {total / args.articles:.2f} "
              f"(healthy: roughly proportional, NOT ~{1/args.articles:.2f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
