"""Regression guard for ARCH-01 / ARCH-02.

ARCH-01: six directories under leggie/ (application/{agents,agents/prompts,
cqrs,services,workflow}, infrastructure/llm/adapters) had no __init__.py.
grimp treats them as namespace packages and silently drops them from its
module graph — the layer contract in pyproject.toml evaluated over 74 of 118
real modules and passed vacuously over every application->infrastructure
violation living in those directories. CI's "Enforce architecture with
import-linter" step was green while checking nothing over 34 source files.

The first test below is the actual regression guard: if a future directory
is added without __init__.py, the module count silently drops again and this
fails loudly instead of the contract quietly checking less code. The second
test runs the real contract end to end, with verbose=True (ARCH-02, revised):
import-linter's own `_build_report()` nests a raw `rich.live.Live()` inside
an active `rich.progress.Progress(disable=verbose)` — when verbose is False
(the default), both bind to the shared default Console and Rich's
`Console.set_live()` unconditionally rejects the second registration
(`rich.errors.LiveError: Only one live display may be active at once`).
`is_debug_mode=True` alone does NOT avoid this — that flag only controls
whether exceptions are swallowed for pretty-printing (see
importlinter.cli.lint_imports docstring), it does not touch the Progress
bar. `verbose=True` sets `disable=True` on that Progress, which is what
actually avoids the nested-Live conflict. Verified against
import-linter==2.12 + rich==13.9.4; upstream bug, not ours — reopen this
comment if a future import-linter release fixes it upstream and the
verbose=True workaround becomes unnecessary.
"""

from __future__ import annotations

from pathlib import Path

import grimp
from importlinter.cli import lint_imports

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LEGGIE_ROOT = _REPO_ROOT / "leggie"


def _source_file_count() -> int:
    return sum(1 for p in _LEGGIE_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def test_grimp_graph_sees_every_source_file() -> None:
    """The regression guard: module census must equal the filesystem, exactly.

    Not a threshold — a namespace-package gap drops whole subtrees, so any
    mismatch here means the layer contract is (again) checking less code
    than exists.
    """
    graph = grimp.build_graph("leggie")
    on_disk = _source_file_count()
    assert len(graph.modules) == on_disk, (
        f"grimp saw {len(graph.modules)} modules but {on_disk} .py files exist "
        f"under leggie/ — a directory is likely missing __init__.py and has "
        f"silently dropped out of the import-linter layer contract (ARCH-01)."
    )


def test_known_leak_sites_are_visible_to_the_graph() -> None:
    """Spot-check specific modules that were invisible before the ARCH-01 fix."""
    graph = grimp.build_graph("leggie")
    for module in (
        "leggie.application.agents.skeptic",
        "leggie.application.cqrs.mediator",
        "leggie.application.services.cove_verifier",
        "leggie.application.workflow.bill_analysis_flow",
        "leggie.infrastructure.llm.adapters.openrouter",
    ):
        assert module in graph.modules, f"{module} missing from grimp graph (ARCH-01 regression)"


def test_layer_contract_passes_in_debug_mode() -> None:
    """End-to-end: the real contract, the real invocation this repo's gates use.

    Uses is_debug_mode=True (see importlinter.cli.lint_imports docstring) and
    verbose=True (ARCH-02: avoids the nested Progress/Live crash — see module
    docstring) rather than shelling out to the `lint-imports` console script.
    """
    exit_code = lint_imports(
        config_filename=str(_REPO_ROOT / "pyproject.toml"),
        is_debug_mode=True,
        verbose=True,
    )
    assert exit_code == 0, (
        "import-linter layer/domain-purity contracts failed — see stdout above "
        "for the specific violation. If this is a newly-fixed import, prune its "
        "matching ignore_imports entry from pyproject.toml (ARCH-03/ARCH-04 "
        "baseline) rather than leaving it stale."
    )
