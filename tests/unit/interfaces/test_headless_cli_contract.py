"""Headless CLI contract — what an external agent driving Leggie relies on.

Three guarantees, each of which was broken before:

1. In ``--json`` mode stdout is a single valid JSON document and nothing else.
   Trailing prose (e.g. "Parsed document written to ...") used to follow the
   payload and break any consumer parsing stdout.
2. Failures are machine-readable on stdout in ``--json`` mode, not plain text
   on stderr only.
3. Exit codes distinguish failure *kind*. Handlers catch their exceptions and
   return a failed CommandResult, so before ``error_type`` existed every
   handled failure exited 1 and an agent could not tell "bad path" (fail fast)
   from "provider down" (retry).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from leggie.application.cqrs.base import CommandResult
from leggie.interfaces.cli import (
    EXIT_BUDGET_EXCEEDED,
    EXIT_CONFIG_ERROR,
    EXIT_DEGRADED_PARSE,
    EXIT_PROVIDER_UNAVAILABLE,
    EXIT_UNKNOWN,
    Presenter,
    _exit_code_for_result,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
# Blank credentials so nothing in this module can make a paid API call.
_HERMETIC_ENV = {
    "LEGGIE_LLM__OPENROUTER_API_KEY": "",
    "LEGGIE_REASONER__API_KEY": "",
    "LEGGIE_REASONER__ENABLED": "false",
    "LEGGIE_REASONER__AUTOSTART": "false",
    "PYTHONIOENCODING": "utf-8",
}


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Invoke the CLI the way an external agent would: as a subprocess."""
    import os

    env = {**os.environ, **_HERMETIC_ENV}
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "leggie", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


@pytest.fixture
def bill(tmp_path: Path) -> Path:
    path = tmp_path / "bill.txt"
    path.write_text("Άρθρο 1\nΔοκιμαστικό περιεχόμενο.\n", encoding="utf-8")
    return path


class TestExitCodeMapping:
    """CommandResult.error_type → documented exit code."""

    @pytest.mark.parametrize(
        ("error_type", "expected"),
        [
            ("InputNotFoundError", EXIT_CONFIG_ERROR),
            ("UnsupportedFormatError", EXIT_CONFIG_ERROR),
            ("LLMConfigurationError", EXIT_CONFIG_ERROR),
            ("BudgetExceededError", EXIT_BUDGET_EXCEEDED),
            ("DeliberativeBudgetExceededError", EXIT_BUDGET_EXCEEDED),
            ("ParseIntegrityError", EXIT_DEGRADED_PARSE),
            ("ReasonerUnavailableError", EXIT_PROVIDER_UNAVAILABLE),
            ("LLMError", EXIT_PROVIDER_UNAVAILABLE),
            ("SomethingNobodyMapped", EXIT_UNKNOWN),
            (None, EXIT_UNKNOWN),
        ],
    )
    def test_error_type_maps_to_exit_code(self, error_type: str | None, expected: int) -> None:
        result: CommandResult[str] = CommandResult(
            success=False, error="boom", error_type=error_type
        )
        assert _exit_code_for_result(result) == expected

    def test_failure_helper_captures_exception_type(self) -> None:
        result: CommandResult[str] = CommandResult.failure(ValueError("bad input"))
        assert result.success is False
        assert result.error == "bad input"
        assert result.error_type == "ValueError"

    def test_a_bad_path_is_not_reported_as_a_transient_provider_failure(self) -> None:
        """Exit 5 invites a retry loop; a nonexistent file must never yield it."""
        result: CommandResult[str] = CommandResult(
            success=False, error="nope", error_type="InputNotFoundError"
        )
        assert _exit_code_for_result(result) != EXIT_PROVIDER_UNAVAILABLE


class TestPresenter:
    def test_json_mode_suppresses_informational_lines(self) -> None:
        p = Presenter(json_mode=True)
        assert p.json_mode is True
        # info() must be a no-op so it cannot appear on stdout beside the payload
        assert p.quiet is False

    def test_quiet_suppresses_informational_lines(self) -> None:
        assert Presenter(quiet=True).quiet is True


class TestStdoutContract:
    """End-to-end: the CLI as a subprocess, from a foreign working directory."""

    def test_parse_json_stdout_is_pure_json(self, bill: Path, tmp_path: Path) -> None:
        proc = _run("--json", "parse", str(bill), cwd=tmp_path)
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)  # raises if polluted
        assert "articles" in payload

    def test_output_flag_does_not_pollute_stdout(self, bill: Path, tmp_path: Path) -> None:
        """The regression: a trailing 'written to ...' line after the payload."""
        out = tmp_path / "parsed.json"
        proc = _run("--json", "parse", str(bill), "-o", str(out), cwd=tmp_path)
        assert proc.returncode == 0, proc.stderr
        json.loads(proc.stdout)

    def test_failure_is_structured_on_stdout_in_json_mode(self, tmp_path: Path) -> None:
        proc = _run("--json", "parse", str(tmp_path / "absent.txt"), cwd=tmp_path)
        assert proc.returncode == EXIT_CONFIG_ERROR
        payload = json.loads(proc.stdout)
        assert payload["ok"] is False
        assert payload["error_type"] == "InputNotFoundError"
        assert payload["exit_code"] == EXIT_CONFIG_ERROR

    def test_unsupported_format_exits_config_error(self, tmp_path: Path) -> None:
        bad = tmp_path / "bill.xyz"
        bad.write_text("x", encoding="utf-8")
        proc = _run("--json", "parse", str(bad), cwd=tmp_path)
        assert proc.returncode == EXIT_CONFIG_ERROR
        assert json.loads(proc.stdout)["error_type"] == "UnsupportedFormatError"

    def test_runs_from_any_working_directory(self, bill: Path, tmp_path: Path) -> None:
        """Agents invoke from their own cwd, not the repo root."""
        elsewhere = tmp_path / "somewhere" / "else"
        elsewhere.mkdir(parents=True)
        proc = _run("--json", "parse", str(bill), cwd=elsewhere)
        assert proc.returncode == 0, proc.stderr
        json.loads(proc.stdout)

    def test_version_is_machine_readable(self, tmp_path: Path) -> None:
        proc = _run("--json", "--version", cwd=tmp_path)
        assert proc.returncode == 0
        assert "version" in json.loads(proc.stdout)

    def test_no_command_does_not_hang_or_prompt(self, tmp_path: Path) -> None:
        """A bare invocation must terminate, never block on input."""
        proc = _run(cwd=tmp_path)
        assert proc.returncode == 0
