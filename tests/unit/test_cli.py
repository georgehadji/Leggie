"""Tests for CLI — argparse-based command-line interface."""

from pathlib import Path

import pytest

from leggie.interfaces.cli import build_parser


class TestBuildParser:
    def test_version_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--version"])
        assert args.version is True

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
