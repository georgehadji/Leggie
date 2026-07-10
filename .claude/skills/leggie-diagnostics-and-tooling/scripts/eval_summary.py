"""eval_summary.py — print Leggie eval results cleanly.

Usage:
    python eval_summary.py eval_results.json

Handles both the list-of-bill-results shape produced by `leggie eval` and
empty/stub files. Exit 1 on missing/invalid file.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", type=Path, help="eval results JSON")
    args = ap.parse_args()

    if not args.path.exists():
        print(f"ERROR: file not found: {args.path}", file=sys.stderr)
        return 1
    try:
        data = json.loads(args.path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"ERROR: cannot parse JSON: {exc}", file=sys.stderr)
        return 1

    results = data if isinstance(data, list) else data.get("results", [])
    if not results:
        print("No bill results in file — eval may be stub/empty. "
              "Run: leggie eval --gold-set tests/eval/gold_set_sample.json")
        return 0

    print(f"{'bill_id':<22}{'gold':>6}{'found':>7}{'match':>7}"
          f"{'prec':>8}{'rec':>8}{'F1':>8}{'RDI':>8}")
    for r in results:
        if not isinstance(r, dict):
            continue
        print(f"{str(r.get('bill_id', '?')):<22}"
              f"{r.get('total_gold', 0):>6}"
              f"{r.get('total_findings', 0):>7}"
              f"{r.get('matched', 0):>7}"
              f"{float(r.get('precision', 0)):>8.3f}"
              f"{float(r.get('recall', 0)):>8.3f}"
              f"{float(r.get('f1', 0)):>8.3f}"
              f"{float(r.get('risk_direction_index', 0)):>8.3f}")
    print("\nRDI (Risk Direction Index): >0 = invention bias (finds things that "
          "aren't there), <0 = omission bias (misses real issues), 0 = balanced.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
