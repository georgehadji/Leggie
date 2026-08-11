"""Tests for AnalyzeBillHandler pipeline routing (WU-7).

Deterministic path must remain unchanged; deliberative path must construct
DeliberativeFlow only when explicitly enabled, and must refuse cleanly when
LEGGIE_REASONER__ENABLED is not set — no accidental network/process activity.
"""

from __future__ import annotations

from typing import Any

import pytest

from leggie.application.cqrs.commands.cli_commands import AnalyzeBillCommand
from leggie.application.cqrs.handlers import cli_handlers
from leggie.application.ports.citation_parser import CitationParserPort
from leggie.application.ports.reasoner import ReasonerPort
from leggie.config.settings import ReasonerSettings, Settings
from leggie.infrastructure.container import Container


class FakeDeliberativeFlow:
    """Records construction args and run() calls; never touches the network."""

    instances: list[FakeDeliberativeFlow] = []

    def __init__(
        self,
        reasoner,
        stage1_preset,
        stage2_preset,
        server_manager=None,
        citation_parser=None,
        max_tokens_per_run=None,
    ):
        self.reasoner = reasoner
        self.stage1_preset = stage1_preset
        self.stage2_preset = stage2_preset
        self.server_manager = server_manager
        self.citation_parser = citation_parser
        self.max_tokens_per_run = max_tokens_per_run
        self.run_calls: list[dict[str, Any]] = []
        FakeDeliberativeFlow.instances.append(self)

    async def run(self, file_path, output_dir="Outputs", perspective="neutral"):
        self.run_calls.append(
            {"file_path": file_path, "output_dir": output_dir, "perspective": perspective}
        )
        return f"{output_dir}/fake_deliberative.md"


class FakeReasonerAdapter:
    def __init__(self, base_url, api_key, request_timeout):  # noqa: ARG002
        self.base_url = base_url


class FakeReasonerServerManager:
    def __init__(self, settings):
        self.settings = settings


class FakeGreekCitationParser:
    def parse(self, text):  # noqa: ARG002
        return []

    async def resolve(self, citation):
        return citation

    def supported_schemes(self):
        return []


@pytest.fixture(autouse=True)
def _reset_fake_instances():
    FakeDeliberativeFlow.instances = []
    yield
    FakeDeliberativeFlow.instances = []


@pytest.fixture
def patch_deliberative_collaborators(monkeypatch) -> Container:
    """Wire a Container with fakes for the deliberative path's collaborators.

    D22 fix: AnalyzeBillHandler._handle_deliberative resolves ReasonerPort and
    CitationParserPort from the container instead of constructing
    ReasonerAdapter/GreekCitationParser directly, so those two are registered
    as bindings here rather than monkeypatched. DeliberativeFlow and
    ReasonerServerManager are still hand-constructed by the handler (Group C,
    not yet container-resolved) so those stay monkeypatched via local import.
    """
    import leggie.application.workflow.deliberative_flow as deliberative_flow_module
    import leggie.infrastructure.reasoner.server_manager as server_manager_module

    monkeypatch.setattr(deliberative_flow_module, "DeliberativeFlow", FakeDeliberativeFlow)
    monkeypatch.setattr(server_manager_module, "ReasonerServerManager", FakeReasonerServerManager)

    container = Container()
    container.register(
        ReasonerPort,
        lambda: FakeReasonerAdapter(base_url="http://fake", api_key="", request_timeout=1.0),
    )
    container.register(CitationParserPort, lambda: FakeGreekCitationParser())
    return container


def _settings_with_reasoner(**reasoner_overrides) -> Settings:
    return Settings(reasoner=ReasonerSettings(**reasoner_overrides))


class TestDeterministicPipelineUnchanged:
    @pytest.mark.asyncio
    async def test_routes_to_deterministic_by_default(self, monkeypatch, tmp_path):
        called = {"deterministic": False, "deliberative": False}

        async def fake_deterministic(_self, _command):
            called["deterministic"] = True
            from leggie.application.cqrs.base import CommandResult
            return CommandResult(success=True, data="ok")

        async def fake_deliberative(_self, _command):
            called["deliberative"] = True
            from leggie.application.cqrs.base import CommandResult
            return CommandResult(success=True, data="ok")

        monkeypatch.setattr(
            cli_handlers.AnalyzeBillHandler, "_handle_deterministic", fake_deterministic
        )
        monkeypatch.setattr(
            cli_handlers.AnalyzeBillHandler, "_handle_deliberative", fake_deliberative
        )

        handler = cli_handlers.AnalyzeBillHandler(container=Container())
        command = AnalyzeBillCommand(file_path=str(tmp_path / "bill.txt"))
        await handler.handle(command)

        assert called["deterministic"] is True
        assert called["deliberative"] is False


class TestDeliberativeRoutingDisabled:
    @pytest.mark.asyncio
    async def test_refuses_when_reasoner_disabled(self, monkeypatch, tmp_path):
        settings = _settings_with_reasoner(enabled=False)
        import leggie.config.settings as settings_module
        monkeypatch.setattr(settings_module, "get_settings", lambda: settings)

        handler = cli_handlers.AnalyzeBillHandler(container=Container())
        command = AnalyzeBillCommand(
            file_path=str(tmp_path / "bill.txt"), pipeline="deliberative"
        )
        result = await handler.handle(command)

        assert result.success is False
        assert result.error is not None
        assert "disabled" in result.error.lower()
        assert len(FakeDeliberativeFlow.instances) == 0


class TestDeliberativeRoutingEnabled:
    @pytest.mark.asyncio
    async def test_constructs_deliberative_flow_with_configured_presets(
        self, monkeypatch, tmp_path, patch_deliberative_collaborators
    ):
        settings = _settings_with_reasoner(
            enabled=True,
            stage1_preset="custom-stage1",
            stage2_preset="custom-stage2",
            perspective="neutral",
        )
        import leggie.config.settings as settings_module
        monkeypatch.setattr(settings_module, "get_settings", lambda: settings)

        handler = cli_handlers.AnalyzeBillHandler(container=patch_deliberative_collaborators)
        bill_path = str(tmp_path / "bill.txt")
        command = AnalyzeBillCommand(
            file_path=bill_path, pipeline="deliberative", perspective="neutral"
        )
        result = await handler.handle(command)

        assert result.success is True
        assert len(FakeDeliberativeFlow.instances) == 1
        flow = FakeDeliberativeFlow.instances[0]
        assert flow.stage1_preset == "custom-stage1"
        assert flow.stage2_preset == "custom-stage2"
        assert flow.run_calls[0]["file_path"] == bill_path
        assert flow.run_calls[0]["perspective"] == "neutral"
        # D22: reasoner/citation_parser must come from the container, not a
        # hand-constructed default.
        assert isinstance(flow.reasoner, FakeReasonerAdapter)
        assert isinstance(flow.citation_parser, FakeGreekCitationParser)

    @pytest.mark.asyncio
    async def test_falls_back_to_settings_perspective_when_unset(
        self, monkeypatch, tmp_path, patch_deliberative_collaborators
    ):
        settings = _settings_with_reasoner(enabled=True, perspective="neutral")
        import leggie.config.settings as settings_module
        monkeypatch.setattr(settings_module, "get_settings", lambda: settings)

        handler = cli_handlers.AnalyzeBillHandler(container=patch_deliberative_collaborators)
        command = AnalyzeBillCommand(
            file_path=str(tmp_path / "bill.txt"), pipeline="deliberative", perspective=None
        )
        await handler.handle(command)

        flow = FakeDeliberativeFlow.instances[0]
        assert flow.run_calls[0]["perspective"] == "neutral"

    @pytest.mark.asyncio
    async def test_reasoner_unavailable_error_reported_cleanly(
        self, monkeypatch, tmp_path, patch_deliberative_collaborators
    ):
        from leggie.application.ports.reasoner import ReasonerUnavailableError

        class RaisingFlow(FakeDeliberativeFlow):
            async def run(self, file_path, output_dir="Outputs", perspective="neutral"):  # noqa: ARG002
                raise ReasonerUnavailableError("backend down")

        import leggie.application.workflow.deliberative_flow as deliberative_flow_module
        monkeypatch.setattr(deliberative_flow_module, "DeliberativeFlow", RaisingFlow)

        settings = _settings_with_reasoner(enabled=True)
        import leggie.config.settings as settings_module
        monkeypatch.setattr(settings_module, "get_settings", lambda: settings)

        handler = cli_handlers.AnalyzeBillHandler(container=patch_deliberative_collaborators)
        command = AnalyzeBillCommand(
            file_path=str(tmp_path / "bill.txt"), pipeline="deliberative"
        )
        result = await handler.handle(command)

        assert result.success is False
        assert result.error is not None
        assert "unavailable" in result.error.lower()

    @pytest.mark.asyncio
    async def test_budget_exceeded_reported_cleanly(
        self, monkeypatch, tmp_path, patch_deliberative_collaborators
    ):
        from leggie.application.workflow.deliberative_flow import (
            DeliberativeBudgetExceededError,
        )

        class BudgetRaisingFlow(FakeDeliberativeFlow):
            async def run(self, file_path, output_dir="Outputs", perspective="neutral"):  # noqa: ARG002
                raise DeliberativeBudgetExceededError("estimated tokens exceed budget")

        import leggie.application.workflow.deliberative_flow as deliberative_flow_module
        monkeypatch.setattr(deliberative_flow_module, "DeliberativeFlow", BudgetRaisingFlow)

        settings = _settings_with_reasoner(enabled=True)
        import leggie.config.settings as settings_module
        monkeypatch.setattr(settings_module, "get_settings", lambda: settings)

        handler = cli_handlers.AnalyzeBillHandler(container=patch_deliberative_collaborators)
        command = AnalyzeBillCommand(
            file_path=str(tmp_path / "bill.txt"), pipeline="deliberative"
        )
        result = await handler.handle(command)

        assert result.success is False
        assert result.error is not None
        assert "budget" in result.error.lower()


class TestDeliberativeFallback:
    @pytest.mark.asyncio
    async def test_no_fallback_aborts_on_reasoner_unavailable(
        self, monkeypatch, tmp_path, patch_deliberative_collaborators
    ):
        from leggie.application.ports.reasoner import ReasonerUnavailableError

        class RaisingFlow(FakeDeliberativeFlow):
            async def run(self, file_path, output_dir="Outputs", perspective="neutral"):  # noqa: ARG002
                raise ReasonerUnavailableError("backend down")

        import leggie.application.workflow.deliberative_flow as deliberative_flow_module
        monkeypatch.setattr(deliberative_flow_module, "DeliberativeFlow", RaisingFlow)

        settings = _settings_with_reasoner(enabled=True)
        import leggie.config.settings as settings_module
        monkeypatch.setattr(settings_module, "get_settings", lambda: settings)

        handler = cli_handlers.AnalyzeBillHandler(container=patch_deliberative_collaborators)
        command = AnalyzeBillCommand(
            file_path=str(tmp_path / "bill.txt"), pipeline="deliberative", fallback=False
        )
        result = await handler.handle(command)

        assert result.success is False
        assert result.error is not None
        assert "--fallback" in result.error

    @pytest.mark.asyncio
    async def test_fallback_true_routes_to_deterministic_on_unavailable(
        self, monkeypatch, tmp_path, patch_deliberative_collaborators
    ):
        from leggie.application.ports.reasoner import ReasonerUnavailableError

        class RaisingFlow(FakeDeliberativeFlow):
            async def run(self, file_path, output_dir="Outputs", perspective="neutral"):  # noqa: ARG002
                raise ReasonerUnavailableError("backend down")

        import leggie.application.workflow.deliberative_flow as deliberative_flow_module
        monkeypatch.setattr(deliberative_flow_module, "DeliberativeFlow", RaisingFlow)

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

        handler = cli_handlers.AnalyzeBillHandler(container=patch_deliberative_collaborators)
        command = AnalyzeBillCommand(
            file_path=str(tmp_path / "bill.txt"), pipeline="deliberative", fallback=True
        )
        result = await handler.handle(command)

        assert deterministic_called["v"] is True
        assert result.success is True
