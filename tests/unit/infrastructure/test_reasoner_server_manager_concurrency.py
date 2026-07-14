"""Regression guard: ReasonerServerManager.ensure_running() must not double-spawn
under concurrent invocation.

Investigated as a suspected check-then-act race (defect-hunt candidate D2,
2026-07-14): the null-check on self._process and the process_spawner() call
have no `await` between them, so under asyncio's cooperative scheduling the
sequence is atomic — a concurrent caller resuming from its own health-probe
await always observes the already-assigned self._process. Confirmed FALSE
(innocent) empirically below; kept as a permanent guard against a future
regression (e.g. an await introduced between the check and the assignment).
"""

import asyncio

import pytest

from leggie.config.settings import ReasonerSettings
from leggie.infrastructure.reasoner.server_manager import ReasonerServerManager


class FakeProcess:
    def __init__(self) -> None:
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return None

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:  # noqa: ARG002
        return 0


def _settings(**overrides) -> ReasonerSettings:
    defaults = {
        "enabled": True,
        "autostart": True,
        "startup_timeout": 1,
        "base_url": "http://localhost:8003",
    }
    defaults.update(overrides)
    return ReasonerSettings(**defaults)


class TestConcurrentEnsureRunning:
    @pytest.mark.asyncio
    async def test_two_concurrent_calls_spawn_at_most_one_process(self):
        spawned: list[FakeProcess] = []

        async def never_healthy() -> bool:
            # Forced suspension point so the event loop can interleave the two
            # concurrent ensure_running() calls at this exact await.
            await asyncio.sleep(0)
            return False

        def spawn() -> FakeProcess:
            p = FakeProcess()
            spawned.append(p)
            return p

        manager = ReasonerServerManager(
            _settings(), health_prober=never_healthy, process_spawner=spawn, poll_interval=0.01
        )

        await asyncio.gather(
            manager.ensure_running(), manager.ensure_running(), return_exceptions=True
        )

        assert len(spawned) == 1
