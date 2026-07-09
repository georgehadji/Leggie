"""Tests for CLI — argparse-based command-line interface."""

import pytest
from pathlib import Path
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
