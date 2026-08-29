"""Defect-hunt reproducer (V7 D1): does the deliberative pipeline release the
Reasoner backend process it autostarts?

AnalyzeBillHandler._handle_deliberative constructs a ReasonerServerManager and
passes it into DeliberativeFlow, whose only interaction with it is
ensure_running() (via the ServerLifecycle Protocol, which does not declare
shutdown()). No call site anywhere in leggie/ invokes .shutdown() outside the
class's own __aexit__, which nothing here uses. This test drives the real
handler → real DeliberativeFlow → real ReasonerServerManager call path (with
only the process spawner and health probe faked — no real subprocess/network)
and asserts the spawned process is terminated when the command completes.
"""

from __future__ import annotations

import pytest

from leggie.application.cqrs.commands.cli_commands import AnalyzeBillCommand
from leggie.application.cqrs.handlers import cli_handlers
from leggie.application.ports.citation_parser import CitationParserPort
from leggie.application.ports.reasoner import ReasonerPort, ReasonerRequest, ReasonerResult
from leggie.config.settings import ReasonerSettings, Settings
from leggie.infrastructure.container import Container
from leggie.infrastructure.reasoner.server_manager import ReasonerServerManager

SAMPLE_BILL = "ΣΧΕΔΙΟ ΝΟΜΟΥ\n\nΆρθρο 1 – Δοκιμή\n1. Κείμενο.\n"


class FakeProcess:
    """Duck-typed stand-in for subprocess.Popen (mirrors test_reasoner_server_manager.py)."""

    def __init__(self) -> None:
        self.terminated = False
        self.killed = False
        self._exit_code: int | None = None

    def poll(self) -> int | None:
        return self._exit_code

    def terminate(self) -> None:
        self.terminated = True
        self._exit_code = 0

    def kill(self) -> None:
        self.killed = True
        self._exit_code = -9

    def wait(self, timeout: float | None = None) -> int:  # noqa: ARG002
        return self._exit_code if self._exit_code is not None else 0


class RealManagerFakeProcess(ReasonerServerManager):
    """Real ReasonerServerManager logic; only the OS-touching seams are faked.

    Registers itself so the test can inspect the spawned FakeProcess after
    the handler call returns — cli_handlers.py builds this instance
    internally and does not expose it.
    """

    instances: list[RealManagerFakeProcess] = []

    def __init__(self, settings: ReasonerSettings) -> None:
        self.spawned_process: FakeProcess | None = None
        self._health_calls = 0
        super().__init__(
            settings,
            health_prober=self._fake_health_probe,
            process_spawner=self._fake_spawn,
            poll_interval=0.01,
        )
        RealManagerFakeProcess.instances.append(self)

    async def _fake_health_probe(self) -> bool:
        self._health_calls += 1
        # Unhealthy until a process has been spawned; healthy immediately after
        # (simulates a successful autostart without real network I/O).
        return self.spawned_process is not None

    def _fake_spawn(self) -> FakeProcess:
        self.spawned_process = FakeProcess()
        return self.spawned_process


class FakeReasoner(ReasonerPort):
    async def reason(self, request: ReasonerRequest) -> ReasonerResult:  # noqa: ARG002
        return ReasonerResult(
            synthesis="ok",
            critical_insights=[],
            open_questions=[],
            citations=[],
            models_used=["fake"],
            total_tokens={},
            duration_seconds=0.01,
            errors=[],
        )


class FakeReasonerAdapter:
    def __init__(self, base_url, api_key, request_timeout):  # noqa: ARG002
        pass

    async def reason(self, request: ReasonerRequest) -> ReasonerResult:  # noqa: ARG002
        return ReasonerResult(
            synthesis="ok",
            critical_insights=[],
            open_questions=[],
            citations=[],
            models_used=["fake"],
            total_tokens={},
            duration_seconds=0.01,
            errors=[],
        )


class FakeGreekCitationParser:
    def parse(self, text):  # noqa: ARG002
        return []

    async def resolve(self, citation):
        return citation

    def supported_schemes(self):
        return []


@pytest.fixture(autouse=True)
def _reset_instances():
    RealManagerFakeProcess.instances = []
    yield
    RealManagerFakeProcess.instances = []


@pytest.fixture
def patch_collaborators(monkeypatch) -> Container:
    """Real DeliberativeFlow + real ReasonerServerManager logic; fake only the
    OS/network-touching leaves (process spawn, HTTP client, citation parser).

    D22: _handle_deliberative resolves ReasonerPort/CitationParserPort from
    the container now, so those two are registered bindings rather than
    monkeypatched classes (see test_cli_handlers_deliberative.py)."""
    import leggie.infrastructure.reasoner.server_manager as server_manager_module

    monkeypatch.setattr(server_manager_module, "ReasonerServerManager", RealManagerFakeProcess)

    container = Container()
    container.register(
        ReasonerPort,
        lambda: FakeReasonerAdapter(base_url="http://fake", api_key="", request_timeout=1.0),
    )
    container.register(CitationParserPort, lambda: FakeGreekCitationParser())
    return container


def _settings_with_reasoner(**overrides) -> Settings:
    return Settings(reasoner=ReasonerSettings(**overrides))


class TestReasonerProcessLifecycle:
    @pytest.mark.asyncio
    async def test_autostarted_process_is_terminated_after_command_completes(
        self, monkeypatch, tmp_path, patch_collaborators
    ):
        settings = _settings_with_reasoner(
            enabled=True,
            autostart=True,
            startup_timeout=1,
            base_url="http://localhost:8003",
        )
        import leggie.config.settings as settings_module

        monkeypatch.setattr(settings_module, "get_settings", lambda: settings)

        bill = tmp_path / "bill.txt"
        bill.write_text(SAMPLE_BILL, encoding="utf-8")

        handler = cli_handlers.AnalyzeBillHandler(container=patch_collaborators)
        command = AnalyzeBillCommand(
            file_path=str(bill),
            pipeline="deliberative",
            output_path=str(tmp_path / "out"),
        )
        result = await handler.handle(command)

        assert result.success is True, result.error
        assert len(RealManagerFakeProcess.instances) == 1
        manager = RealManagerFakeProcess.instances[0]
        assert manager.spawned_process is not None, "process was never spawned — test setup invalid"

        # PROOF OF DEFECT: the process this call path autostarted must be
        # released when the command finishes. It is not.
        assert manager.spawned_process.terminated or manager.spawned_process.killed, (
            "Reasoner backend process spawned by ensure_running() was never "
            "terminated — resource leak. No call site releases it: "
            "DeliberativeFlow's ServerLifecycle Protocol does not declare "
            "shutdown(), and _handle_deliberative never calls it directly."
        )

    @pytest.mark.asyncio
    async def test_process_is_released_even_when_flow_raises(
        self, monkeypatch, tmp_path, patch_collaborators
    ):
        """Boundary: cleanup must run on the exception exit path too, not just success."""
        import leggie.application.workflow.deliberative_flow as deliberative_flow_module

        class RaisingFlow:
            def __init__(self, *_args, server_manager=None, **_kwargs):
                self._server_manager = server_manager

            async def run(self, *_args, **_kwargs):
                if self._server_manager is not None:
                    await self._server_manager.ensure_running()
                raise RuntimeError("simulated stage failure after autostart")

        monkeypatch.setattr(deliberative_flow_module, "DeliberativeFlow", RaisingFlow)

        settings = _settings_with_reasoner(
            enabled=True,
            autostart=True,
            startup_timeout=1,
            base_url="http://localhost:8003",
        )
        import leggie.config.settings as settings_module

        monkeypatch.setattr(settings_module, "get_settings", lambda: settings)

        bill = tmp_path / "bill.txt"
        bill.write_text(SAMPLE_BILL, encoding="utf-8")
        handler = cli_handlers.AnalyzeBillHandler(container=patch_collaborators)
        command = AnalyzeBillCommand(file_path=str(bill), pipeline="deliberative")
        result = await handler.handle(command)

        assert result.success is False
        manager = RealManagerFakeProcess.instances[0]
        assert manager.spawned_process is not None
        assert manager.spawned_process.terminated or manager.spawned_process.killed

    @pytest.mark.asyncio
    async def test_shutdown_failure_does_not_shadow_original_error(
        self, monkeypatch, tmp_path, patch_collaborators
    ):
        """Regression guard for the fix-revision-1 break: a broken shutdown() must
        not replace the flow's real exception (e.g. the one --fallback depends on)."""
        import leggie.application.workflow.deliberative_flow as deliberative_flow_module
        from leggie.application.ports.reasoner import ReasonerUnavailableError

        class RaisingFlow:
            def __init__(self, *_args, **_kwargs):
                pass

            async def run(self, *_args, **_kwargs):
                raise ReasonerUnavailableError("backend down")

        monkeypatch.setattr(deliberative_flow_module, "DeliberativeFlow", RaisingFlow)

        class BrokenShutdownManager(RealManagerFakeProcess):
            async def shutdown(self) -> None:
                raise OSError("simulated: process already reaped")

        import leggie.infrastructure.reasoner.server_manager as server_manager_module

        monkeypatch.setattr(server_manager_module, "ReasonerServerManager", BrokenShutdownManager)

        settings = _settings_with_reasoner(enabled=True)
        import leggie.config.settings as settings_module

        monkeypatch.setattr(settings_module, "get_settings", lambda: settings)

        deterministic_called = {"v": False}

        async def fake_deterministic(_self, _command):
            deterministic_called["v"] = True
            from leggie.application.cqrs.base import CommandResult

            return CommandResult(success=True, data="deterministic ran")

        monkeypatch.setattr(
            cli_handlers.AnalyzeBillHandler, "_handle_deterministic", fake_deterministic
        )

        bill = tmp_path / "bill.txt"
        bill.write_text(SAMPLE_BILL, encoding="utf-8")
        handler = cli_handlers.AnalyzeBillHandler(container=patch_collaborators)
        command = AnalyzeBillCommand(file_path=str(bill), pipeline="deliberative", fallback=True)
        result = await handler.handle(command)

        # --fallback must still trigger despite the shutdown() failure — proves
        # the cleanup error was logged, not allowed to shadow ReasonerUnavailableError.
        assert deterministic_called["v"] is True
        assert result.success is True
