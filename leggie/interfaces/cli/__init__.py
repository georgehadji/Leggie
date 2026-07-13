"""Leggie CLI — command-line interface for bill analysis.

Dispatches all operations through the CQRS mediator, keeping the
interface layer thin per Clean Architecture. No direct infrastructure calls.
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from leggie.application.cqrs.mediator import Mediator


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="leggie",
        description="Leggie — Greek legal bill analysis tool",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Show version and exit",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # analyze
    analyze = subparsers.add_parser("analyze", help="Analyze a legal bill")
    analyze.add_argument("file", type=Path, help="Path to the bill file (PDF/DOCX/HTML/TXT)")
    analyze.add_argument("--output", "-o", type=Path, default=None, help="Output directory for reports")
    analyze.add_argument("--lenses", "-l", nargs="+", default=None, help="Lenses to apply (default: all 5)")
    analyze.add_argument(
        "--articles", "-a", type=str, default=None,
        help="Articles to analyze, e.g. '1-5,7,10' or '1,2,3' (default: all)",
    )
    analyze.add_argument(
        "--verbalized-sampling", action="store_true",
        help="Enable verbalized sampling (experimental, increases cost)",
    )
    analyze.add_argument(
        "--checkpoint", "-c", type=Path, default=None,
        help="Path to persist/restore budget spend across runs (survives a crash mid-run)",
    )
    analyze.add_argument(
        "--pipeline",
        choices=["deterministic", "deliberative"],
        default="deterministic",
        help="Analysis pipeline: deterministic (default, 5-lens) or deliberative (opt-in)",
    )
    analyze.add_argument(
        "--perspective",
        default=None,
        help="Party perspective for the deliberative pipeline (default: neutral)",
    )
    analyze.add_argument(
        "--fallback",
        action="store_true",
        help="If the Reasoner backend is unavailable, fall back to the deterministic "
        "pipeline instead of aborting (deliberative pipeline only)",
    )

    # eval
    eval_cmd = subparsers.add_parser("eval", help="Run evaluation against gold set")
    eval_cmd.add_argument("--gold-set", "-g", type=Path, required=True, help="Path to gold-set JSON")
    eval_cmd.add_argument("--results", "-r", type=Path, default=None, help="Path to save results")

    # parse
    parse_cmd = subparsers.add_parser("parse", help="Parse a bill and output structured JSON")
    parse_cmd.add_argument("file", type=Path, help="Path to the bill file")
    parse_cmd.add_argument("--output", "-o", type=Path, default=None, help="Output path for JSON")

    return parser


def _build_mediator() -> Mediator:
    """Build and configure the CQRS mediator with all handlers.

    W1: Creates the DI container once at startup and injects it into all handlers.
    """
    from leggie.application.cqrs.commands.cli_commands import (
        AnalyzeBillCommand,
        EvalGoldSetCommand,
        ParseDocumentCommand,
    )
    from leggie.application.cqrs.handlers.cli_handlers import (
        AnalyzeBillHandler,
        EvalGoldSetHandler,
        ParseDocumentHandler,
    )
    from leggie.application.cqrs.mediator import Mediator
    from leggie.infrastructure.container import Container

    # Single composition root — one container, one configure_defaults() call (W1)
    container = Container()
    container.configure_defaults()

    mediator = Mediator()
    mediator.register_command_handler(ParseDocumentCommand, ParseDocumentHandler(container=container))
    mediator.register_command_handler(AnalyzeBillCommand, AnalyzeBillHandler(container=container))
    mediator.register_command_handler(EvalGoldSetCommand, EvalGoldSetHandler(container=container))
    return mediator


async def main() -> int:
    """CLI entry point — dispatches through CQRS mediator."""
    # Configure structured logging once at startup (W6)
    from leggie.infrastructure.observability import configure_logging
    configure_logging()

    parser = build_parser()
    args = parser.parse_args()

    if args.version:
        from leggie import __version__
        print(f"Leggie v{__version__}")
        return 0

    if args.command is None:
        parser.print_help()
        return 0

    # Build mediator once for all commands
    mediator = _build_mediator()

    if args.command == "parse":
        return await _handle_parse(args, mediator)
    if args.command == "analyze":
        return await _handle_analyze(args, mediator)
    if args.command == "eval":
        return await _handle_eval(args, mediator)

    return 0


async def _handle_parse(args: argparse.Namespace, mediator: Mediator) -> int:
    """Handle the parse command via CQRS."""
    import json

    from leggie.application.cqrs.commands.cli_commands import ParseDocumentCommand

    cmd = ParseDocumentCommand(
        file_path=str(args.file),
        output_path=str(args.output) if args.output else None,
    )
    result = await mediator.send(cmd)

    if not result.success:
        print(f"Error: {result.error}", file=sys.stderr)
        return 1

    print(json.dumps(result.data, ensure_ascii=False, indent=2))
    if args.output:
        print(f"Parsed document written to {args.output}")
    return 0


async def _handle_analyze(args: argparse.Namespace, mediator: Mediator) -> int:
    """Handle the analyze command via CQRS."""
    from leggie.application.cqrs.commands.cli_commands import AnalyzeBillCommand

    cmd = AnalyzeBillCommand(
        file_path=str(args.file),
        output_path=str(args.output) if args.output else None,
        lenses=args.lenses,
        articles=args.articles,
        use_verbalized_sampling=args.verbalized_sampling,
        checkpoint_path=str(args.checkpoint) if args.checkpoint else None,
        pipeline=args.pipeline,
        perspective=args.perspective,
        fallback=args.fallback,
    )
    result = await mediator.send(cmd)

    if not result.success:
        print(f"{result.error}")
        return 1
    if result.data:
        print(result.data)
    return 0


async def _handle_eval(args: argparse.Namespace, mediator: Mediator) -> int:
    """Handle the eval command via CQRS."""
    import json

    from leggie.application.cqrs.commands.cli_commands import EvalGoldSetCommand

    cmd = EvalGoldSetCommand(
        gold_set_path=str(args.gold_set),
        results_path=str(args.results) if args.results else None,
    )
    result = await mediator.send(cmd)

    if not result.success:
        print(f"Error: {result.error}", file=sys.stderr)
        return 1

    for bill_result in result.data or []:
        print(f"\n{bill_result['bill_id']}:")
        print(f"  Gold labels: {bill_result['total_gold']}")
        print(f"  Findings: {bill_result['total_findings']}")
        print(f"  Matched: {bill_result['matched']}")
        print(f"  Precision: {bill_result['precision']:.4f}")
        print(f"  Recall: {bill_result['recall']:.4f}")
        print(f"  F1: {bill_result['f1']:.4f}")
        print(f"  RDI: {bill_result['risk_direction_index']:.4f}")

    results_path = args.results or Path("eval_results.json")
    print(f"\nResults saved to {results_path}")
    return 0


def _force_utf8_console() -> None:
    """Force UTF-8 stdout/stderr so Greek output does not mojibake on Windows.

    Windows consoles default to a legacy code page (e.g. cp1252) that cannot
    encode Greek. Reconfigure the streams to UTF-8; harmless on POSIX.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):
                reconfigure(encoding="utf-8")


def entry_point() -> int:
    """Synchronous entry point for CLI."""
    import asyncio
    _force_utf8_console()
    return asyncio.run(main())


if __name__ == "__main__":
    sys.exit(entry_point())
