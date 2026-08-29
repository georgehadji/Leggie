#!/usr/bin/env python3
"""Run the CI gate sequence locally, exactly as `.github/workflows/ci.yml` does.

Why this exists: CI is the only place the gate sequence was written down, so when
GitHub Actions stopped executing jobs (2026-07-15, see
`docs/CI_OUTAGE_2026-07.md`) there was no single command left that reproduced it.
Commits landed on master unverified. This script is that command, and it works on
Windows and Linux alike — Leggie is developed on Windows, CI runs ubuntu.

Keep this file in lockstep with `.github/workflows/ci.yml`. If a gate changes
there, change it here in the same commit.

Usage:
    python scripts/run_gates.py              # all gates, in CI order
    python scripts/run_gates.py ruff mypy    # only the named gates
    python scripts/run_gates.py --list       # show gate names

Exit code is 0 only when every gate that ran passed.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _console_script(name: str) -> str:
    """Resolve a console script next to the running interpreter.

    Falls back to a bare name so PATH still gets a chance. On Windows the
    entry point is `<name>.exe` inside `Scripts/`.
    """
    bindir = Path(sys.executable).parent
    for candidate in (bindir / name, bindir / f"{name}.exe"):
        if candidate.exists():
            return str(candidate)
    return name


PY = sys.executable

# Mirrors the step order of .github/workflows/ci.yml.
GATES: dict[str, list[str]] = {
    "ruff": [PY, "-m", "ruff", "check", "leggie/", "tests/"],
    "mypy": [PY, "-m", "mypy", "leggie/", "--ignore-missing-imports"],
    "import-linter": [_console_script("lint-imports")],
    "bandit": [PY, "-m", "bandit", "-c", "pyproject.toml", "-r", "leggie/"],
    "pytest": [
        PY, "-m", "pytest", "tests/",
        "--tb=short", "--cov=leggie", "--cov-fail-under=80",
    ],
}


def run_gate(name: str, command: list[str]) -> tuple[bool, float]:
    """Run one gate, streaming its output. Returns (passed, seconds)."""
    print(f"\n{'=' * 70}\n== {name}\n== $ {' '.join(command)}\n{'=' * 70}", flush=True)
    started = time.monotonic()
    try:
        completed = subprocess.run(command, cwd=REPO_ROOT, check=False)
        returncode = completed.returncode
    except FileNotFoundError:
        print(
            f"!! {name}: '{command[0]}' not found. "
            'Install dev + lint extras:  pip install -e ".[dev,lint]"',
            flush=True,
        )
        returncode = 127
    return returncode == 0, time.monotonic() - started


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the CI gates locally, in CI order.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "gates", nargs="*", metavar="GATE",
        help=f"gates to run (default: all). Choices: {', '.join(GATES)}",
    )
    parser.add_argument("--list", action="store_true", help="list gate names and exit")
    args = parser.parse_args(argv)

    if args.list:
        for name, command in GATES.items():
            print(f"{name:<15} {' '.join(command)}")
        return 0

    selected = args.gates or list(GATES)
    unknown = [g for g in selected if g not in GATES]
    if unknown:
        parser.error(f"unknown gate(s): {', '.join(unknown)}. Choices: {', '.join(GATES)}")

    results: list[tuple[str, bool, float]] = []
    for name in selected:
        passed, seconds = run_gate(name, GATES[name])
        results.append((name, passed, seconds))

    print(f"\n{'=' * 70}\n== SUMMARY\n{'=' * 70}")
    for name, passed, seconds in results:
        print(f"{'PASS' if passed else 'FAIL':<6} {name:<15} {seconds:6.1f}s")

    failed = [name for name, passed, _ in results if not passed]
    if failed:
        print(f"\n{len(failed)} gate(s) FAILED: {', '.join(failed)}")
        return 1
    print(f"\nAll {len(results)} gate(s) passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
