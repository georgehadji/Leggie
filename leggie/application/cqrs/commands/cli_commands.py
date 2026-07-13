"""CLI Commands — CQRS commands for CLI operations.

Each command maps to a CLI action, keeping the interface layer thin.
"""

from __future__ import annotations

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
    pipeline: str = "deterministic"
    perspective: str | None = None
    fallback: bool = False


class EvalGoldSetCommand(Command):
    """Run evaluation against a gold set."""

    gold_set_path: str
    results_path: str | None = None
