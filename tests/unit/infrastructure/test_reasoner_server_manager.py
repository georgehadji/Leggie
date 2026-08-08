"""Tests for ReasonerServerManager — fakes for health probe + spawner, no real process/network."""

from typing import Any

import pytest

from leggie.application.ports.reasoner import ReasonerUnavailableError
from leggie.config.settings import ReasonerSettings
from leggie.infrastructure.reasoner.server_manager import ReasonerServerManager


class FakeProcess:
    """Duck-typed stand-in for subprocess.Popen."""

    def __init__(self, exit_code: int | None = None) -> None:
        self._exit_code = exit_code
        self.terminated = False
        self.killed = False

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


def _settings(**overrides: Any) -> ReasonerSettings:
    # Annotated as Any-valued: the literal mixes bool/int/str, so mypy would
    # otherwise infer dict[str, object] and reject the ** expansion.
    defaults: dict[str, Any] = {
        "enabled": True,
        "autostart": True,
        "startup_timeout": 1,
        "base_url": "http://localhost:8003",
    }
    defaults.update(overrides)
    return ReasonerSettings(**defaults)


class TestReuseIfUp:
    @pytest.mark.asyncio
    async def test_reuse_when_already_healthy(self):
        spawn_calls = {"n": 0}

        async def healthy() -> bool:
            return True

        def spawn() -> FakeProcess:
            spawn_calls["n"] += 1
            return FakeProcess()

        manager = ReasonerServerManager(
            _settings(), health_prober=healthy, process_spawner=spawn, poll_interval=0.01
        )
        await manager.ensure_running()
        assert spawn_calls["n"] == 0


class TestStartIfDown:
    @pytest.mark.asyncio
    async def test_spawns_and_waits_until_healthy(self):
        probe_calls = {"n": 0}
        spawn_calls = {"n": 0}

        async def probe() -> bool:
            probe_calls["n"] += 1
            return probe_calls["n"] > 2  # unhealthy first two probes, then healthy

        def spawn() -> FakeProcess:
            spawn_calls["n"] += 1
            return FakeProcess()

        manager = ReasonerServerManager(
            _settings(), health_prober=probe, process_spawner=spawn, poll_interval=0.01
        )
        await manager.ensure_running()
        assert spawn_calls["n"] == 1
        assert probe_calls["n"] >= 3

    @pytest.mark.asyncio
    async def test_does_not_double_spawn_on_second_call(self):
        spawn_calls = {"n": 0}
        healthy_flag = {"v": False}

        async def probe() -> bool:
            return healthy_flag["v"]

        def spawn() -> FakeProcess:
            spawn_calls["n"] += 1
            healthy_flag["v"] = True  # becomes healthy right after spawn
            return FakeProcess()

        manager = ReasonerServerManager(
            _settings(), health_prober=probe, process_spawner=spawn, poll_interval=0.01
        )
        await manager.ensure_running()
        await manager.ensure_running()
        assert spawn_calls["n"] == 1


class TestAutostartDisabled:
    @pytest.mark.asyncio
    async def test_raises_when_unhealthy_and_autostart_disabled(self):
        async def unhealthy() -> bool:
            return False

        def spawn() -> FakeProcess:
            raise AssertionError("should not spawn when autostart disabled")

        manager = ReasonerServerManager(
            _settings(autostart=False),
            health_prober=unhealthy,
            process_spawner=spawn,
            poll_interval=0.01,
        )
        with pytest.raises(ReasonerUnavailableError, match="(?i)autostart"):
            await manager.ensure_running()


class TestTimeout:
    @pytest.mark.asyncio
    async def test_raises_after_startup_timeout(self):
        async def never_healthy() -> bool:
            return False

        def spawn() -> FakeProcess:
            return FakeProcess()

        manager = ReasonerServerManager(
            _settings(startup_timeout=1),
            health_prober=never_healthy,
            process_spawner=spawn,
            poll_interval=0.02,
        )
        with pytest.raises(ReasonerUnavailableError, match="did not become healthy"):
            await manager.ensure_running()


class TestProcessExitsEarly:
    @pytest.mark.asyncio
    async def test_raises_when_process_dies_before_healthy(self):
        async def unhealthy() -> bool:
            return False

        def spawn() -> FakeProcess:
            return FakeProcess(exit_code=1)  # already exited

        manager = ReasonerServerManager(
            _settings(), health_prober=unhealthy, process_spawner=spawn, poll_interval=0.01
        )
        with pytest.raises(ReasonerUnavailableError, match="exited early"):
            await manager.ensure_running()


class TestShutdown:
    @pytest.mark.asyncio
    async def test_shutdown_terminates_spawned_process(self):
        probe_calls = {"n": 0}

        async def probe() -> bool:
            probe_calls["n"] += 1
            return probe_calls["n"] > 1

        proc = FakeProcess()

        def spawn() -> FakeProcess:
            return proc

        manager = ReasonerServerManager(
            _settings(), health_prober=probe, process_spawner=spawn, poll_interval=0.01
        )
        await manager.ensure_running()
        await manager.shutdown()
        assert proc.terminated is True

    @pytest.mark.asyncio
    async def test_shutdown_noop_when_no_process_spawned(self):
        async def healthy() -> bool:
            return True

        manager = ReasonerServerManager(
            _settings(),
            health_prober=healthy,
            process_spawner=lambda: FakeProcess(),
            poll_interval=0.01,
        )
        await manager.ensure_running()  # reused, never spawned
        await manager.shutdown()  # should not raise


class TestEphemeralContextManager:
    @pytest.mark.asyncio
    async def test_context_manager_ensures_and_tears_down(self):
        probe_calls = {"n": 0}

        async def probe() -> bool:
            probe_calls["n"] += 1
            return probe_calls["n"] > 1

        proc = FakeProcess()

        async with ReasonerServerManager(
            _settings(autostart=True),
            health_prober=probe,
            process_spawner=lambda: proc,
            poll_interval=0.01,
        ) as manager:
            assert manager is not None

        assert proc.terminated is True


class TestDefaultSpawnValidation:
    def test_raises_when_home_not_set(self):
        manager = ReasonerServerManager(_settings(home=""))
        with pytest.raises(ReasonerUnavailableError, match="LEGGIE_REASONER_HOME is not set"):
            manager._default_spawn()

    def test_raises_when_home_does_not_exist(self):
        manager = ReasonerServerManager(_settings(home="/nonexistent/path/xyz"))
        with pytest.raises(ReasonerUnavailableError, match="does not exist"):
            manager._default_spawn()


class TestFindPython:
    def test_falls_back_to_sys_executable(self, tmp_path):
        import sys

        manager = ReasonerServerManager(_settings())
        result = manager._find_python(tmp_path)
        assert result == sys.executable

    def test_prefers_venv_python_unix(self, tmp_path):
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        venv_python = venv_bin / "python"
        venv_python.touch()

        manager = ReasonerServerManager(_settings())
        result = manager._find_python(tmp_path)
        assert result == str(venv_python)


class TestPortParsing:
    def test_extracts_port_from_base_url(self):
        manager = ReasonerServerManager(_settings(base_url="http://localhost:9999"))
        assert manager._port() == 9999

    def test_defaults_to_8003_when_no_port(self):
        manager = ReasonerServerManager(_settings(base_url="http://localhost"))
        assert manager._port() == 8003
