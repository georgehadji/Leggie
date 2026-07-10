"""smoke_log_stats.py — count Leggie diagnostic signatures in a run log.

Usage:
    python smoke_log_stats.py run.log

Works on any captured analyze log (`leggie analyze ... 2>&1 | tee run.log`)
or on historical exhibits like analysis_report.md. Prints a triage table of
known failure signatures. Exit 1 on missing file.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

# signature -> (meaning, playbook pointer)
SIGNATURES: dict[str, tuple[str, str]] = {
    "Field required": ("pydantic schema drift — findings being rejected", "playbook row 1"),
    "Unterminated string": ("truncated JSON (finish_reason=length)", "playbook row 1"),
    "Expecting value: line 1 col 1": ("empty/garbage LLM content", "playbook row 1"),
    "json_schema rejected": ("model 400 on strict mode; json_object fallback", "playbook row 8"),
    "Response truncated": ("truncation retry engaged (attempt 3)", "expected recovery path"),
    "Failed to parse structured response": ("retry ladder exhausted → degrade", "playbook row 1"),
    "cove_quote_fail": ("fabricated verbatim_quote dropped (CORRECT behavior)", "playbook row 5"),
    "skeptic_llm_error": ("adversarial critic call failed → neutral", "playbook row 4"),
    "skeptic_route_failed": ("router failed for adversarial_critic", "playbook row 4"),
    "budget": ("budget guard activity — check cost vs token ceiling", "playbook row 6"),
    "DEGRADED": ("pipeline degradation event", "inspect surrounding lines"),
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", type=Path, help="run log or report file")
    args = ap.parse_args()

    if not args.path.exists():
        print(f"ERROR: file not found: {args.path}", file=sys.stderr)
        return 1
    text = args.path.read_text(encoding="utf-8", errors="replace")
    lines = text.count("\n") + 1

    print(f"file: {args.path}  ({lines} lines)")
    print(f"{'count':>6}  {'signature':<38} meaning")
    any_hit = False
    for sig, (meaning, pointer) in SIGNATURES.items():
        n = text.count(sig)
        if n:
            any_hit = True
            print(f"{n:>6}  {sig:<38} {meaning}  [{pointer}]")
    if not any_hit:
        print("     0  (no known failure signatures found)")

    drift = text.count("Field required")
    trunc = text.count("Unterminated string") + text.count("Expecting value: line 1 col 1")
    if drift + trunc:
        print(f"\nparse_failure_signals: drift={drift} truncation={trunc} "
              f"(REMEDIATION_PLAN §10 target: <5% of LLM calls)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
