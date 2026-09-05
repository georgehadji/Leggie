"""Tests for ReasonerServerManager — fakes for health probe + spawner, no real process/network."""

from typing import Any

import pytest

from leggie.application.ports.reasoner import ReasonerUnavailableError
from leggie.config.settings import ReasonerSettings
from leggie.infrastructure.reasoner.server_manager import _AGENT_RUN_PATH, ReasonerServerManager


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

    @pytest.mark.asyncio
    async def test_leaked_process_cleaned_up_when_aenter_times_out(self):
        """DH-25 proof: __aexit__ never runs if __aenter__ raises (standard
        async context manager protocol) — so a process spawned inside
        ensure_running() that then never becomes healthy must be cleaned up
        by __aenter__ itself, or it leaks with no caller able to reach it.
        """
        proc = FakeProcess()

        async def never_healthy() -> bool:
            return False

        manager = ReasonerServerManager(
            _settings(startup_timeout=1),
            health_prober=never_healthy,
            process_spawner=lambda: proc,
            poll_interval=0.02,
        )
        with pytest.raises(ReasonerUnavailableError, match="did not become healthy"):
            async with manager:
                pytest.fail("must not enter the body")

        assert proc.terminated is True
        assert manager._process is None

    @pytest.mark.asyncio
    async def test_leaked_process_cleaned_up_when_process_exits_early(self):
        """Boundary: same cleanup must fire on the OTHER __aenter__ failure
        path (process died before ever becoming healthy), not just timeout."""
        proc = FakeProcess(exit_code=1)

        async def unhealthy() -> bool:
            return False

        manager = ReasonerServerManager(
            _settings(), health_prober=unhealthy, process_spawner=lambda: proc, poll_interval=0.01
        )
        with pytest.raises(ReasonerUnavailableError, match="exited early"):
            async with manager:
                pytest.fail("must not enter the body")

        # Already dead (poll() != None from the start) — shutdown() correctly
        # skips a redundant terminate(), but must still clear _process so the
        # manager doesn't keep thinking it owns a (dead) process handle.
        assert proc.terminated is False
        assert manager._process is None

    @pytest.mark.asyncio
    async def test_cleanup_failure_does_not_mask_the_real_startup_error(self):
        """Self-review catch: shutdown() itself can raise (e.g. terminate()
        on an already-dead process handle) — that must never replace the
        real ReasonerUnavailableError from ensure_running(), mirroring
        cli_handlers.py's own established try/except-log idiom (PR #7)."""

        class ExplodingProcess(FakeProcess):
            def terminate(self) -> None:
                raise OSError("already dead")

        proc = ExplodingProcess()

        async def never_healthy() -> bool:
            return False

        manager = ReasonerServerManager(
            _settings(startup_timeout=1),
            health_prober=never_healthy,
            process_spawner=lambda: proc,
            poll_interval=0.02,
        )
        with pytest.raises(ReasonerUnavailableError, match="did not become healthy"):
            async with manager:
                pytest.fail("must not enter the body")

    @pytest.mark.asyncio
    async def test_aenter_failure_with_nothing_spawned_does_not_crash(self):
        """Boundary: __aenter__'s except-path shutdown() call must stay a
        safe no-op when ensure_running() raised before ever spawning
        anything (autostart disabled) — no AttributeError/crash masking the
        real ReasonerUnavailableError."""

        async def unhealthy() -> bool:
            return False

        manager = ReasonerServerManager(
            _settings(autostart=False), health_prober=unhealthy, poll_interval=0.01
        )
        with pytest.raises(ReasonerUnavailableError, match="(?i)autostart"):
            async with manager:
                pytest.fail("must not enter the body")

        assert manager._process is None


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


class _FakeProbeResponse:
    def __init__(self, status_code: int = 200, json_value: Any = None) -> None:
        self.status_code = status_code
        self._json_value = json_value

    def json(self) -> Any:
        return self._json_value


class _FakeProbeAsyncClient:
    """Stand-in for httpx.AsyncClient — no transport-injection seam exists on
    _default_health_probe (unlike ReasonerAdapter), so the client class itself
    is monkeypatched at the module reference, matching this file's existing
    fakes-over-mocks convention."""

    def __init__(self, response: _FakeProbeResponse, **_kwargs: Any) -> None:
        self._response = response

    async def __aenter__(self) -> "_FakeProbeAsyncClient":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, _url: str) -> _FakeProbeResponse:
        return self._response


class TestDefaultHealthProbeResponseValidation:
    """DH-26: the real _default_health_probe() HTTP-parsing logic — every
    other test in this file injects a fake health_prober callable and never
    exercises this method's own response handling at all."""

    def _patch_client(self, monkeypatch: pytest.MonkeyPatch, response: _FakeProbeResponse) -> None:
        import leggie.infrastructure.reasoner.server_manager as sm

        monkeypatch.setattr(
            sm.httpx, "AsyncClient", lambda **kw: _FakeProbeAsyncClient(response, **kw)
        )

    @pytest.mark.asyncio
    async def test_non_dict_json_body_degrades_to_unhealthy(self, monkeypatch):
        """Proof: a wrong service occupying the port can return valid JSON
        that isn't a dict (a bare list) — must degrade to False, not raise
        AttributeError out of the probe."""
        self._patch_client(monkeypatch, _FakeProbeResponse(200, ["not", "a", "dict"]))
        manager = ReasonerServerManager(_settings())
        assert await manager._default_health_probe() is False

    @pytest.mark.asyncio
    async def test_null_json_body_degrades_to_unhealthy(self, monkeypatch):
        """Boundary: a JSON `null` body (data=None) hits the same .get() call."""
        self._patch_client(monkeypatch, _FakeProbeResponse(200, None))
        manager = ReasonerServerManager(_settings())
        assert await manager._default_health_probe() is False

    @pytest.mark.asyncio
    async def test_wrong_service_dict_without_agent_path_still_raises(self, monkeypatch):
        """No-regression: a real dict response from a wrong service (missing
        the Reasoner agent-run path) must still raise ReasonerUnavailableError
        exactly as before — the isinstance guard must not swallow this case."""
        self._patch_client(monkeypatch, _FakeProbeResponse(200, {"paths": {"/other": {}}}))
        manager = ReasonerServerManager(_settings())
        with pytest.raises(ReasonerUnavailableError, match="not Reasoner"):
            await manager._default_health_probe()

    @pytest.mark.asyncio
    async def test_real_reasoner_openapi_body_is_healthy(self, monkeypatch):
        """No-regression: a genuine Reasoner OpenAPI document still reports healthy."""
        self._patch_client(monkeypatch, _FakeProbeResponse(200, {"paths": {_AGENT_RUN_PATH: {}}}))
        manager = ReasonerServerManager(_settings())
        assert await manager._default_health_probe() is True
