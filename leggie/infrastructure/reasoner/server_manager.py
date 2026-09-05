"""ReasonerServerManager — health-check + auto-start lifecycle for the Reasoner backend.

Reuses an already-running Reasoner instance when healthy; otherwise spawns
`uvicorn asgi:app` (backend only — no UI, no SearXNG) from REASONER_HOME and
polls until healthy. Persistent by default; supports an ephemeral mode that
tears the process down on context exit.
"""

from __future__ import annotations

import asyncio
import os
import subprocess  # nosec B404
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

import httpx

from leggie.application.ports.reasoner import ReasonerUnavailableError
from leggie.config.settings import ReasonerSettings
from leggie.observability import get_logger

logger = get_logger(__name__)

_AGENT_RUN_PATH = "/api/agent/run/sync"


class SpawnedProcess(Protocol):
    """Duck-typed process handle — subprocess.Popen satisfies this."""

    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int: ...


HealthProber = Callable[[], Awaitable[bool]]
ProcessSpawner = Callable[[], SpawnedProcess]


class ReasonerServerManager:
    """Ensures a healthy Reasoner backend is available before use.

    Health probes and process spawning are injectable seams so tests never
    spawn a real process or hit the network.
    """

    def __init__(
        self,
        settings: ReasonerSettings,
        health_prober: HealthProber | None = None,
        process_spawner: ProcessSpawner | None = None,
        poll_interval: float = 1.0,
    ) -> None:
        self._settings = settings
        self._health_prober = health_prober or self._default_health_probe
        self._process_spawner = process_spawner or self._default_spawn
        self._poll_interval = poll_interval
        self._process: SpawnedProcess | None = None

    async def ensure_running(self) -> None:
        """Reuse a healthy Reasoner if present; else autostart or fail."""
        if await self._health_prober():
            return

        if not self._settings.autostart:
            raise ReasonerUnavailableError(
                f"Reasoner not reachable at {self._settings.base_url} "
                "and LEGGIE_REASONER_AUTOSTART is disabled"
            )

        if self._process is not None and self._process.poll() is None:
            # We already spawned it; don't double-start — just wait for health.
            await self._wait_until_healthy()
            return

        self._process = self._process_spawner()
        await self._wait_until_healthy()

    async def shutdown(self) -> None:
        """Terminate the process this manager spawned, if any."""
        if self._process is None:
            return
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._process = None

    async def __aenter__(self) -> ReasonerServerManager:
        # DH-25: __aexit__ never runs if __aenter__ itself raises (standard
        # async context manager protocol) — so a process spawned by
        # ensure_running() that then fails to become healthy (or dies early)
        # would otherwise be leaked with no caller ever able to clean it up.
        try:
            await self.ensure_running()
        except Exception:
            # Mirror cli_handlers.py's own established idiom (PR #7): a
            # cleanup failure must never shadow the real startup error.
            try:
                await self.shutdown()
            except Exception:
                logger.warning("reasoner.cleanup_after_failed_start_failed", exc_info=True)
            raise
        return self

    async def __aexit__(self, *_args: object) -> None:
        if self._settings.autostart:
            await self.shutdown()

    # ── internals ──────────────────────────────────────────────────

    async def _wait_until_healthy(self) -> None:
        deadline = time.monotonic() + self._settings.startup_timeout
        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                raise ReasonerUnavailableError(
                    f"Reasoner backend process exited early (code {self._process.poll()})"
                )
            if await self._health_prober():
                return
            await asyncio.sleep(self._poll_interval)
        raise ReasonerUnavailableError(
            f"Reasoner did not become healthy within {self._settings.startup_timeout}s"
        )

    async def _default_health_probe(self) -> bool:
        """GET {base_url}/openapi.json; confirm it's actually Reasoner (R8)."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self._settings.base_url.rstrip('/')}/openapi.json")
        except httpx.RequestError:
            return False
        if resp.status_code != 200:
            return False
        try:
            data = resp.json()
        except ValueError:
            return False
        # DH-26: a wrong service occupying the port can return valid JSON
        # that isn't a dict (bare list/string/number/null) — .get() on that
        # would raise AttributeError instead of degrading to "not healthy".
        if not isinstance(data, dict):
            return False
        paths = data.get("paths", {})
        if _AGENT_RUN_PATH not in paths:
            raise ReasonerUnavailableError(
                f"Port {self._port()} is occupied by a service that is not Reasoner "
                f"(missing {_AGENT_RUN_PATH} in OpenAPI schema)"
            )
        return True

    def _default_spawn(self) -> SpawnedProcess:
        home = self._settings.home
        if not home:
            raise ReasonerUnavailableError(
                "LEGGIE_REASONER_HOME is not set; cannot autostart Reasoner"
            )
        home_path = Path(home)
        if not home_path.is_dir():
            raise ReasonerUnavailableError(f"LEGGIE_REASONER_HOME does not exist: {home}")

        python_exe = self._find_python(home_path)
        cmd = [python_exe, "-m", "uvicorn", "asgi:app", "--port", str(self._port())]
        env = dict(os.environ)
        try:
            return subprocess.Popen(  # nosec B603 - fixed argv, no shell
                cmd,
                cwd=str(home_path),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise ReasonerUnavailableError(f"Failed to spawn Reasoner backend: {exc}", exc) from exc

    def _find_python(self, home_path: Path) -> str:
        venv_python = home_path / ".venv" / "bin" / "python"
        if venv_python.exists():
            return str(venv_python)
        venv_python_win = home_path / ".venv" / "Scripts" / "python.exe"
        if venv_python_win.exists():
            return str(venv_python_win)
        return sys.executable

    def _port(self) -> int:
        parsed = urlparse(self._settings.base_url)
        return parsed.port or 8003
