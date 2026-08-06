"""Tests for CLI — argparse-based command-line interface."""

import sys
from pathlib import Path

import pytest

from leggie.interfaces.cli import build_parser

SAMPLE_BILL = """
ΣΧΕΔΙΟ ΝΟΜΟΥ
Άρθρο 1 – Δοκιμή
1. Κείμενο.
"""


class TestBuildParser:
    def test_version_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--version"])
        assert args.version is True

    def test_preview_command(self):
        parser = build_parser()
        args = parser.parse_args(["preview", "bill.txt"])
        assert args.command == "preview"
        assert args.file == Path("bill.txt")

    def test_preview_with_output(self):
        parser = build_parser()
        args = parser.parse_args(["preview", "bill.txt", "-o", "prev.json"])
        assert args.output == Path("prev.json")


class TestPreviewHandler:
    @pytest.mark.asyncio
    async def test_preview_handler_returns_overview(self, tmp_path):
        from leggie.application.cqrs.commands.cli_commands import PreviewBillCommand
        from leggie.application.cqrs.handlers.cli_handlers import PreviewBillHandler
        from leggie.infrastructure.container import Container

        bill = tmp_path / "bill.txt"
        bill.write_text(SAMPLE_BILL, encoding="utf-8")

        container = Container()
        container.configure_defaults()  # no API key -> llm resolves to None -> fallback

        handler = PreviewBillHandler(container=container)
        result = await handler.handle(PreviewBillCommand(file_path=str(bill)))

        assert result.success is True
        assert "articles" in result.data
        assert result.data["articles"][0]["article_id"] == "1"

    @pytest.mark.asyncio
    async def test_preview_handler_writes_output_file(self, tmp_path):
        import json

        from leggie.application.cqrs.commands.cli_commands import PreviewBillCommand
        from leggie.application.cqrs.handlers.cli_handlers import PreviewBillHandler
        from leggie.infrastructure.container import Container

        bill = tmp_path / "bill.txt"
        bill.write_text(SAMPLE_BILL, encoding="utf-8")
        out = tmp_path / "preview.json"

        container = Container()
        container.configure_defaults()
        handler = PreviewBillHandler(container=container)
        result = await handler.handle(
            PreviewBillCommand(file_path=str(bill), output_path=str(out))
        )

        assert result.success is True
        assert out.exists()
        written = json.loads(out.read_text(encoding="utf-8"))
        assert written["articles"][0]["article_id"] == "1"


class TestCliMainDispatch:
    @pytest.mark.asyncio
    async def test_main_preview_dispatch(self, tmp_path, monkeypatch, capsys):
        """Full CLI path: main() → _handle_preview → mediator → PreviewBillHandler."""
        from leggie.interfaces.cli import main

        bill = tmp_path / "bill.txt"
        bill.write_text(SAMPLE_BILL, encoding="utf-8")
        monkeypatch.setattr(sys, "argv", ["leggie", "preview", str(bill)])

        rc = await main()
        assert rc == 0
        out = capsys.readouterr().out
        assert "articles" in out

    @pytest.mark.asyncio
    async def test_main_preview_writes_file(self, tmp_path, monkeypatch, capsys):
        from leggie.interfaces.cli import main

        bill = tmp_path / "bill.txt"
        bill.write_text(SAMPLE_BILL, encoding="utf-8")
        out_path = tmp_path / "prev.json"
        monkeypatch.setattr(
            sys, "argv", ["leggie", "preview", str(bill), "-o", str(out_path)]
        )

        rc = await main()
        assert rc == 0
        assert out_path.exists()


class TestOtherHandlers:
    """Cover the sibling handlers registered alongside PreviewBillHandler."""

    @pytest.mark.asyncio
    async def test_parse_handler_returns_structure(self, tmp_path):
        from leggie.application.cqrs.commands.cli_commands import ParseDocumentCommand
        from leggie.application.cqrs.handlers.cli_handlers import ParseDocumentHandler
        from leggie.infrastructure.container import Container

        bill = tmp_path / "bill.txt"
        bill.write_text(SAMPLE_BILL, encoding="utf-8")

        container = Container()
        container.configure_defaults()
        handler = ParseDocumentHandler(container=container)
        result = await handler.handle(ParseDocumentCommand(file_path=str(bill)))

        assert result.success is True
        assert result.data["articles"][0]["id"] == "1"

    @pytest.mark.asyncio
    async def test_analyze_handler_completes(self, tmp_path):
        from leggie.application.cqrs.commands.cli_commands import AnalyzeBillCommand
        from leggie.application.cqrs.handlers.cli_handlers import AnalyzeBillHandler
        from leggie.infrastructure.container import Container

        bill = tmp_path / "bill.txt"
        bill.write_text(SAMPLE_BILL, encoding="utf-8")

        container = Container()
        container.configure_defaults()
        handler = AnalyzeBillHandler(container=container)
        result = await handler.handle(
            AnalyzeBillCommand(file_path=str(bill), output_path=str(tmp_path / "out"))
        )

        assert result.success is True
        assert "Analysis complete" in result.data

    def test_parse_command(self):
        parser = build_parser()
        args = parser.parse_args(["parse", "test.txt"])
        assert args.command == "parse"
        assert args.file == Path("test.txt")

    def test_parse_with_output(self):
        parser = build_parser()
        args = parser.parse_args(["parse", "test.txt", "-o", "out.json"])
        assert args.output == Path("out.json")

    def test_analyze_command(self):
        parser = build_parser()
        args = parser.parse_args(["analyze", "bill.txt"])
        assert args.command == "analyze"

    def test_analyze_with_lenses(self):
        parser = build_parser()
        args = parser.parse_args(["analyze", "bill.txt", "-l", "constitutional", "eu_gdpr"])
        assert args.lenses == ["constitutional", "eu_gdpr"]

    def test_analyze_with_articles(self):
        parser = build_parser()
        args = parser.parse_args(["analyze", "bill.txt", "-a", "1-3,5"])
        assert args.articles == "1-3,5"

    def test_analyze_default_pipeline_is_deterministic(self):
        parser = build_parser()
        args = parser.parse_args(["analyze", "bill.txt"])
        assert args.pipeline == "deterministic"

    def test_analyze_deliberative_pipeline_flag(self):
        parser = build_parser()
        args = parser.parse_args(["analyze", "bill.txt", "--pipeline", "deliberative"])
        assert args.pipeline == "deliberative"

    def test_analyze_invalid_pipeline_rejected(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["analyze", "bill.txt", "--pipeline", "not-a-real-pipeline"])

    def test_analyze_default_perspective_is_none(self):
        parser = build_parser()
        args = parser.parse_args(["analyze", "bill.txt"])
        assert args.perspective is None

    def test_analyze_perspective_flag(self):
        parser = build_parser()
        args = parser.parse_args(
            ["analyze", "bill.txt", "--pipeline", "deliberative", "--perspective", "neutral"]
        )
        assert args.perspective == "neutral"

    def test_analyze_default_fallback_is_false(self):
        parser = build_parser()
        args = parser.parse_args(["analyze", "bill.txt"])
        assert args.fallback is False

    def test_analyze_fallback_flag(self):
        parser = build_parser()
        args = parser.parse_args(
            ["analyze", "bill.txt", "--pipeline", "deliberative", "--fallback"]
        )
        assert args.fallback is True

    def test_eval_command(self):
        parser = build_parser()
        args = parser.parse_args(["eval", "-g", "gold.json"])
        assert args.command == "eval"
        assert args.gold_set == Path("gold.json")

    def test_eval_with_results(self):
        parser = build_parser()
        args = parser.parse_args(["eval", "-g", "gold.json", "-r", "results.json"])
        assert args.results == Path("results.json")

    def test_no_command_prints_help(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.command is None
        assert args.version is False

    def test_parse_file_type_is_path(self):
        parser = build_parser()
        args = parser.parse_args(["parse", "test.txt"])
        assert isinstance(args.file, Path)


class TestExitCodes:
    """Every documented exit code is produced by a test (PROD-19)."""

    def test_budget_exceeded_exit_code(self):
        from leggie.infrastructure.llm.base import BudgetExceededError
        from leggie.interfaces.cli import EXIT_BUDGET_EXCEEDED, _exit_code_for
        assert _exit_code_for(BudgetExceededError("over")) == EXIT_BUDGET_EXCEEDED

    def test_config_error_exit_code(self):
        from leggie.infrastructure.ingest import UnsupportedFormatError
        from leggie.infrastructure.llm.base import LLMConfigurationError
        from leggie.interfaces.cli import EXIT_CONFIG_ERROR, _exit_code_for
        assert _exit_code_for(LLMConfigurationError("bad key")) == EXIT_CONFIG_ERROR
        assert _exit_code_for(UnsupportedFormatError("unknown")) == EXIT_CONFIG_ERROR

    def test_degraded_parse_exit_code(self):
        from leggie.application.workflow.bill_analysis_flow import ParseIntegrityError
        from leggie.interfaces.cli import EXIT_DEGRADED_PARSE, _exit_code_for
        assert _exit_code_for(ParseIntegrityError("bad")) == EXIT_DEGRADED_PARSE

    def test_provider_unavailable_exit_code(self):
        from leggie.infrastructure.ingest import IngestError
        from leggie.infrastructure.llm.base import LLMError, LLMTimeoutError
        from leggie.interfaces.cli import EXIT_PROVIDER_UNAVAILABLE, _exit_code_for
        assert _exit_code_for(LLMError("down")) == EXIT_PROVIDER_UNAVAILABLE
        assert _exit_code_for(LLMTimeoutError("down")) == EXIT_PROVIDER_UNAVAILABLE
        assert _exit_code_for(IngestError("down")) == EXIT_PROVIDER_UNAVAILABLE

    def test_interrupted_exit_code(self):
        from leggie.interfaces.cli import EXIT_INTERRUPTED, _exit_code_for
        assert _exit_code_for(KeyboardInterrupt()) == EXIT_INTERRUPTED

    def test_unknown_exit_code_default(self):
        from leggie.interfaces.cli import EXIT_UNKNOWN, _exit_code_for
        assert _exit_code_for(ValueError("blah")) == EXIT_UNKNOWN

    def test_exit_message_has_actionable_text(self):
        from leggie.interfaces.cli import EXIT_BUDGET_EXCEEDED, EXIT_CONFIG_ERROR, _exit_message
        assert "budget" in _exit_message(EXIT_BUDGET_EXCEEDED).lower()
        assert "configuration" in _exit_message(EXIT_CONFIG_ERROR).lower()


class TestNewFlags:
    """PROD-33: --json, --log-level, --quiet flags."""

    def test_log_level_flag(self):
        from leggie.interfaces.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["--log-level", "DEBUG", "preview", "bill.txt"])
        assert args.log_level == "DEBUG"

    def test_quiet_flag(self):
        from leggie.interfaces.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["--quiet", "preview", "bill.txt"])
        assert args.quiet is True

    def test_json_flag(self):
        from leggie.interfaces.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["--json", "preview", "bill.txt"])
        assert args.json is True


class TestPresenter:
    """PROD-33: Presenter routes output and respects quiet/json modes."""

    def _reset_presenter(self):
        import leggie.interfaces.cli as cli
        cli.presenter = cli.Presenter()
        return cli

    def test_info_hidden_when_quiet(self, capsys):
        cli = self._reset_presenter()
        cli.presenter = cli.Presenter(quiet=True)
        cli.presenter.info("important")
        assert capsys.readouterr().out == ""

    def test_info_hidden_when_json(self, capsys):
        cli = self._reset_presenter()
        cli.presenter = cli.Presenter(json_mode=True)
        cli.presenter.info("noise")
        assert capsys.readouterr().out == ""

    def test_result_always_shown_in_quiet(self, capsys):
        cli = self._reset_presenter()
        cli.presenter = cli.Presenter(quiet=True)
        cli.presenter.result("PAYLOAD")
        assert capsys.readouterr().out == "PAYLOAD\n"

    def test_error_to_stderr(self, capsys):
        cli = self._reset_presenter()
        cli.presenter.error("boom")
        assert "boom" in capsys.readouterr().err
