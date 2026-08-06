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
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default=None,
        help="Override the configured log level for this run",
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress informational output (only errors and results shown)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON output for the active command",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # preview
    preview = subparsers.add_parser(
        "preview",
        help="Preview a bill: intro, summary, per-article purpose/provisions/consequences",
    )
    preview.add_argument("file", type=Path, help="Path to the bill file (PDF/DOCX/HTML/TXT)")
    preview.add_argument(
        "--output", "-o", type=Path, default=None, help="Output path for the preview JSON"
    )

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
    analyze.add_argument(
        "--allow-degraded-parse",
        action="store_true",
        help="Proceed with analysis even if parse integrity check fails "
        "(missing articles, duplicates, rejected headings). "
        "Default: abort on degraded parse.",
    )

    # eval
    eval_cmd = subparsers.add_parser("eval", help="Run evaluation against gold set")
    eval_cmd.add_argument("--gold-set", "-g", type=Path, required=True, help="Path to gold-set JSON")
    eval_cmd.add_argument("--results", "-r", type=Path, default=None, help="Path to save results")

    # parse
    parse_cmd = subparsers.add_parser("parse", help="Parse a bill and output structured JSON")
    parse_cmd.add_argument("file", type=Path, help="Path to the bill file")
    parse_cmd.add_argument("--output", "-o", type=Path, default=None, help="Output path for JSON")

    # replay
    replay_cmd = subparsers.add_parser("replay", help="Replay a run from the event log")
    replay_cmd.add_argument("run_id", type=str, help="Run ID to replay")
    replay_cmd.add_argument("--verify", action="store_true", help="Verify against stored findings")

    return parser


def _build_mediator() -> Mediator:
    """Build and configure the CQRS mediator with all handlers.

    W1: Creates the DI container once at startup and injects it into all handlers.
    """
    from leggie.application.cqrs.commands.cli_commands import (
        AnalyzeBillCommand,
        EvalGoldSetCommand,
        ParseDocumentCommand,
        PreviewBillCommand,
        ReplayRunCommand,
    )
    from leggie.application.cqrs.handlers.cli_handlers import (
        AnalyzeBillHandler,
        EvalGoldSetHandler,
        ParseDocumentHandler,
        PreviewBillHandler,
        ReplayRunHandler,
    )
    from leggie.application.cqrs.mediator import Mediator
    from leggie.infrastructure.container import Container

    # Single composition root — one container, one configure_defaults() call (W1)
    container = Container()
    container.configure_defaults()

    mediator = Mediator()
    mediator.register_command_handler(ParseDocumentCommand, ParseDocumentHandler(container=container))
    mediator.register_command_handler(PreviewBillCommand, PreviewBillHandler(container=container))
    mediator.register_command_handler(AnalyzeBillCommand, AnalyzeBillHandler(container=container))
    mediator.register_command_handler(EvalGoldSetCommand, EvalGoldSetHandler(container=container))
    mediator.register_command_handler(ReplayRunCommand, ReplayRunHandler(container=container))
    return mediator


async def main() -> int:
    """CLI entry point — dispatches through CQRS mediator."""
    parser = build_parser()
    args = parser.parse_args()

    # Configure structured logging once at startup (W6), honouring --log-level
    from leggie.observability import configure_logging
    configure_logging(args.log_level)

    # Configure the output presenter from CLI flags (PROD-33)
    global presenter
    presenter = Presenter(quiet=args.quiet, json_mode=args.json)

    if args.version:
        import json as _json

        from leggie import __version__
        if args.json:
            print(_json.dumps({"version": __version__}))
        else:
            print(f"Leggie v{__version__}")
            _print_disclaimer()
        return 0

    if args.command is None:
        parser.print_help()
        return 0

    # Build mediator once for all commands
    mediator = _build_mediator()

    if args.command == "parse":
        return await _handle_parse(args, mediator)
    if args.command == "preview":
        return await _handle_preview(args, mediator)
    if args.command == "analyze":
        return await _handle_analyze(args, mediator)
    if args.command == "eval":
        return await _handle_eval(args, mediator)
    if args.command == "replay":
        return await _handle_replay(args, mediator)

    return 0


async def _handle_replay(args: argparse.Namespace, mediator: Mediator) -> int:
    """Handle the replay command via CQRS (PROD-06d)."""
    import json as _json

    from leggie.application.cqrs.commands.cli_commands import ReplayRunCommand

    cmd = ReplayRunCommand(run_id=args.run_id, verify=args.verify)
    result = await mediator.send(cmd)

    if not result.success:
        return _fail(result)

    presenter.result(_json.dumps(result.data, indent=2, ensure_ascii=False))
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
        return _fail(result)

    presenter.result(json.dumps(result.data, ensure_ascii=False, indent=2))
    if args.output:
        # info(), not result(): a trailing prose line on stdout after the JSON
        # payload would break any agent parsing this command's output.
        presenter.info(f"Parsed document written to {args.output}")
    return 0


async def _handle_preview(args: argparse.Namespace, mediator: Mediator) -> int:
    """Handle the preview command via CQRS — overview before ingest/analyze proper."""
    import json

    from leggie.application.cqrs.commands.cli_commands import PreviewBillCommand

    cmd = PreviewBillCommand(
        file_path=str(args.file),
        output_path=str(args.output) if args.output else None,
    )
    result = await mediator.send(cmd)

    if not result.success:
        return _fail(result)

    presenter.result(json.dumps(result.data, ensure_ascii=False, indent=2))
    if args.output:
        presenter.info(f"Preview written to {args.output}")
    presenter.info("\nRun `leggie analyze <file> --articles <id> ...` to analyze only selected Άρθρα.")
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
        allow_degraded_parse=args.allow_degraded_parse,
    )
    result = await mediator.send(cmd)

    if not result.success:
        return _fail(result)
    if presenter.json_mode:
        import json as _json

        # Always emit an envelope, including the no-findings case: an agent
        # parsing stdout must never receive an empty document on success.
        presenter.result(_json.dumps(
            {"ok": True, "report": result.data, "disclaimer": DISCLAIMER},
            ensure_ascii=False,
            indent=2,
        ))
        return 0

    if result.data:
        presenter.result(result.data)
        presenter.info("")
        presenter.info(DISCLAIMER)
    else:
        presenter.info("Analysis completed — no findings.")
    return 0


async def _handle_eval(args: argparse.Namespace, mediator: Mediator) -> int:
    """Handle the eval command via CQRS."""
    import json as _json

    from leggie.application.cqrs.commands.cli_commands import EvalGoldSetCommand

    cmd = EvalGoldSetCommand(
        gold_set_path=str(args.gold_set),
        results_path=str(args.results) if args.results else None,
    )
    result = await mediator.send(cmd)

    if not result.success:
        return _fail(result)

    results_path = args.results or Path("eval_results.json")

    if presenter.json_mode:
        presenter.result(_json.dumps(
            {"ok": True, "results": result.data or [], "results_path": str(results_path)},
            ensure_ascii=False,
            indent=2,
        ))
        return 0

    for bill_result in result.data or []:
        presenter.result(f"\n{bill_result['bill_id']}:")
        presenter.result(f"  Gold labels: {bill_result['total_gold']}")
        presenter.result(f"  Findings: {bill_result['total_findings']}")
        presenter.result(f"  Matched: {bill_result['matched']}")
        presenter.result(f"  Precision: {bill_result['precision']:.4f}")
        presenter.result(f"  Recall: {bill_result['recall']:.4f}")
        presenter.result(f"  F1: {bill_result['f1']:.4f}")
        presenter.result(f"  RDI: {bill_result['risk_direction_index']:.4f}")

    presenter.info(f"\nResults saved to {results_path}")
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


# ── Exit-code strategy table (PROD-19) ─────────────────────────────
# Maps exception types → documented exit codes.
EXIT_OK = 0
EXIT_UNKNOWN = 1
EXIT_CONFIG_ERROR = 2
EXIT_BUDGET_EXCEEDED = 3
EXIT_DEGRADED_PARSE = 4
EXIT_PROVIDER_UNAVAILABLE = 5
EXIT_INTERRUPTED = 6


def _exit_code_for(exc: BaseException) -> int:
    """Return the documented exit code for an exception type (Template Method)."""
    # Import lazily to avoid circular imports at module load.
    from leggie.application.workflow.bill_analysis_flow import ParseIntegrityError
    from leggie.infrastructure.ingest import IngestError, UnsupportedFormatError
    from leggie.infrastructure.ingest.base import InputNotFoundError
    from leggie.infrastructure.llm.base import BudgetExceededError, LLMConfigurationError, LLMError

    if isinstance(exc, KeyboardInterrupt):
        return EXIT_INTERRUPTED
    if isinstance(exc, (LLMConfigurationError, UnsupportedFormatError, InputNotFoundError)):
        return EXIT_CONFIG_ERROR
    if isinstance(exc, FileNotFoundError):
        return EXIT_CONFIG_ERROR
    if isinstance(exc, BudgetExceededError):
        return EXIT_BUDGET_EXCEEDED
    if isinstance(exc, ParseIntegrityError):
        return EXIT_DEGRADED_PARSE
    if isinstance(exc, (LLMError, IngestError)):
        return EXIT_PROVIDER_UNAVAILABLE
    return EXIT_UNKNOWN


# error_type name (from CommandResult.failure) → exit code. Handlers catch
# their exceptions and return a failed CommandResult, so without this mapping
# every handled failure would exit 1 and an external agent could not tell a
# budget stop from a bad file path.
_ERROR_TYPE_EXITS: dict[str, int] = {
    "LLMConfigurationError": EXIT_CONFIG_ERROR,
    "ConfigurationError": EXIT_CONFIG_ERROR,
    "UnsupportedFormatError": EXIT_CONFIG_ERROR,
    "InputNotFoundError": EXIT_CONFIG_ERROR,
    "FileNotFoundError": EXIT_CONFIG_ERROR,
    "ValidationError": EXIT_CONFIG_ERROR,
    "BudgetExceededError": EXIT_BUDGET_EXCEEDED,
    "DeliberativeBudgetExceededError": EXIT_BUDGET_EXCEEDED,
    "ParseIntegrityError": EXIT_DEGRADED_PARSE,
    "ReasonerUnavailableError": EXIT_PROVIDER_UNAVAILABLE,
    "LLMError": EXIT_PROVIDER_UNAVAILABLE,
    "LLMRateLimitError": EXIT_PROVIDER_UNAVAILABLE,
    "LLMTimeoutError": EXIT_PROVIDER_UNAVAILABLE,
    "IngestError": EXIT_PROVIDER_UNAVAILABLE,
}


def _exit_code_for_result(result: object) -> int:
    """Map a failed CommandResult to its documented exit code."""
    error_type = getattr(result, "error_type", None)
    return _ERROR_TYPE_EXITS.get(error_type or "", EXIT_UNKNOWN)


def _fail(result: object) -> int:
    """Render a failed CommandResult and return its exit code.

    In ``--json`` mode the error envelope goes to stdout so an agent parsing
    stdout gets a structured result on both the success and failure paths.
    """
    code = _exit_code_for_result(result)
    error = getattr(result, "error", None) or "unknown error"
    if presenter.json_mode:
        import json as _json

        presenter.result(_json.dumps(
            {
                "ok": False,
                "error": error,
                "error_type": getattr(result, "error_type", None),
                "exit_code": code,
            },
            ensure_ascii=False,
        ))
    else:
        presenter.error(f"Error: {error}")
    return code


def _exit_message(code: int) -> str:
    """Redacted, actionable one-liner for stderr. Full detail goes to the log."""
    messages = {
        EXIT_CONFIG_ERROR: "Configuration error — check that your API keys and .env are set.",
        EXIT_BUDGET_EXCEEDED: "Run halted: the per-run budget ceiling was reached.",
        EXIT_DEGRADED_PARSE: "Input document failed parse-integrity checks; refusing to analyze.",
        EXIT_PROVIDER_UNAVAILABLE: "LLM/ingest provider unavailable — check network and service status.",
        EXIT_INTERRUPTED: "Interrupted by user (Ctrl-C / SIGINT).",
    }
    return messages.get(code, f"Unexpected error (exit {code}).")


# No-legal-advice disclaimer shown on CLI output and report headers (PROD-27).
DISCLAIMER = (
    "Leggie provides automated legal analysis and is NOT legal advice. "
    "Consult a qualified legal professional before acting on any finding."
)


def _print_disclaimer(file=None) -> None:
    print(DISCLAIMER, file=file)


class Presenter:
    """Routes user-facing output so it is redirectable and quiet/JSON-aware (PROD-33).

    ``info`` lines go to stdout and are suppressed in ``--quiet`` mode and in
    ``--json`` mode (so they never corrupt machine-readable output). ``result``
    lines are the actual payload (JSON or text) and always render.
    """

    def __init__(self, *, quiet: bool = False, json_mode: bool = False) -> None:
        self._quiet = quiet
        self._json = json_mode

    @property
    def json_mode(self) -> bool:
        return self._json

    @property
    def quiet(self) -> bool:
        return self._quiet

    def info(self, message: str, file=None) -> None:
        if self._quiet or self._json:
            return
        print(message, file=file)

    def result(self, message: str, file=None) -> None:
        print(message, file=file)

    def error(self, message: str) -> None:
        print(message, file=sys.stderr)


# Presenter uses CLI-level flags; module-level default for compatibility.
presenter = Presenter()


def entry_point() -> int:
    """Synchronous entry point for CLI.

    Wraps ``main()`` with the exception→exit-code strategy table (PROD-19)
    and installs SIGINT/SIGTERM handlers so a run interrupted mid-way leaves
    the checkpoint consistent and exits with a documented code.
    """
    import asyncio
    import signal

    from leggie.observability import get_logger

    _force_utf8_console()
    _log = get_logger(__name__)

    def _handle_signal(signum: int, _frame: object) -> None:
        _log.warning("cli.signal", signum=signum)
        raise KeyboardInterrupt()

    # Install signal handlers (best-effort)
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(ValueError, OSError, AttributeError):
            signal.signal(sig, _handle_signal)

    try:
        return asyncio.run(main())
    except KeyboardInterrupt:
        code = EXIT_INTERRUPTED
        print(_exit_message(code), file=sys.stderr)
        return code
    except Exception as exc:  # noqa: BLE001 — top-level handler
        code = _exit_code_for(exc)
        _log.exception("cli.fatal", exit_code=code, error=str(exc))
        print(_exit_message(code), file=sys.stderr)
        return code


if __name__ == "__main__":
    sys.exit(entry_point())
