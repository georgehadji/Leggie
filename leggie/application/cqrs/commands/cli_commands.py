"""CLI Commands — CQRS commands for CLI operations.

Each command maps to a CLI action, keeping the interface layer thin.
"""

from __future__ import annotations

from typing import Literal

from leggie.application.cqrs.base import Command


class ParseDocumentCommand(Command):
    """Parse a legal bill document into structured format."""

    file_path: str
    output_path: str | None = None


class AnalyzeBillCommand(Command):
    """Analyze a legal bill with configured lenses."""

    file_path: str
    output_path: str | None = None
    lenses: list[str] | None = None
    articles: str | None = None
    use_verbalized_sampling: bool = False
    checkpoint_path: str | None = None
    # Closed set, not a bare str: AnalyzeBillHandler.handle() dispatches on
    # `== "deliberative"` with a silent else-branch to the deterministic
    # pipeline — any other value must fail at construction, not silently run
    # the wrong pipeline. The CLI already restricts this via argparse
    # choices=[...]; this makes the command self-validating too, since a
    # Command is a stable contract other callers may construct directly.
    pipeline: Literal["deterministic", "deliberative"] = "deterministic"
    perspective: str | None = None
    fallback: bool = False
    allow_degraded_parse: bool = False


class PreviewBillCommand(Command):
    """Preview a legal bill: intro, summary, per-article purpose/provisions/consequences.

    Runs before ingest/analyze proper, so the caller can pick which
    Άρθρο IDs to pass into `leggie analyze --articles`.
    """

    file_path: str
    output_path: str | None = None


class EvalGoldSetCommand(Command):
    """Run evaluation against a gold set."""

    gold_set_path: str
    results_path: str | None = None


class ReplayRunCommand(Command):
    """Replay a completed run from the event log (PROD-06d)."""

    run_id: str
    verify: bool = False  # whether to diff against stored findings JSON
