"""Regression guard: the CI workflows must not re-declare a quality gate.

FIX-1 history. The coverage threshold is declared once, in pyproject.toml
``[tool.coverage.report] fail_under``. Both workflows also passed
``--cov-fail-under`` on the pytest line, and a CLI flag silently overrides the
config file — so the number the repo advertised was never the number enforced:

  * ci.yml carried ``--cov-fail-under=80`` while pyproject said 85, so the gate
    was five points looser than documented for every PR.
  * release.yml carried ``--cov-fail-under=85`` against a measured 82.43%, so a
    ``v*`` tag would have failed at publish time on a threshold nothing else
    enforced — and that one survived the first fix, because ci.yml was
    corrected without grepping for the flag repo-wide.

Two workflows, two different overrides, neither matching the config. This test
is the cheap thing that would have caught both at once.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOW_DIR = _REPO_ROOT / ".github" / "workflows"

# Flags that duplicate a threshold owned by pyproject.toml. A workflow may run
# the tool; it may not restate the number the tool reads from config.
_FORBIDDEN_FLAGS = ("--cov-fail-under",)


def _workflow_files() -> list[Path]:
    return sorted(_WORKFLOW_DIR.glob("*.yml")) + sorted(_WORKFLOW_DIR.glob("*.yaml"))


def test_workflow_directory_is_present() -> None:
    """Guard the guard: a moved workflow dir must not make this vacuously pass."""
    assert _workflow_files(), f"no workflow files found under {_WORKFLOW_DIR}"


@pytest.mark.parametrize("flag", _FORBIDDEN_FLAGS)
def test_no_workflow_overrides_a_pyproject_threshold(flag: str) -> None:
    offenders = []
    for path in _workflow_files():
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            # Skip comments — this file's own rationale mentions the flag by
            # name, and so does the comment above each corrected pytest line.
            if line.lstrip().startswith("#"):
                continue
            if flag in line:
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")

    assert not offenders, (
        f"{flag} is set in a workflow, which silently overrides pyproject.toml.\n"
        + "\n".join(offenders)
        + "\nDelete the flag; the threshold belongs in "
        "[tool.coverage.report] fail_under alone."
    )


def test_coverage_gate_is_declared_in_pyproject() -> None:
    """The single source of truth must actually exist and be enforcing."""
    config = tomllib.loads(
        (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    fail_under = config["tool"]["coverage"]["report"]["fail_under"]
    assert isinstance(fail_under, (int, float))
    assert fail_under > 0, "coverage gate is declared but set to zero — not a gate"
