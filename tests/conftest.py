"""Suite-wide isolation from the developer's real configuration.

Leggie's ``Settings`` read ``.env`` directly, so on a configured developer
machine a container built by ``configure_defaults()`` resolves a LIVE
OpenRouter adapter and the "unit" tests silently make paid API calls:
``tests/unit/test_cli.py`` alone spent $0.23 and took 5m54s against the real
API, and the full suite spent ~$1.39 per run.

The tests were always written for an unconfigured environment — see
``test_cli.py``'s "no API key -> llm resolves to None -> fallback". That held
in CI (no secrets) and quietly stopped holding locally. These fixtures make
the assumption explicit everywhere, so the suite is hermetic, fast, free, and
gives the same result in CI and on a developer machine.

Tests that need LLM behaviour use a fake adapter; nothing here should ever be
relaxed to let the suite reach a real provider.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from typing import Any

import pytest

from leggie.config import settings as settings_module

# Credentials that would flip a component from its fake/None path to a real
# network client. Empty (not absent) — an env var overrides the .env file,
# whereas deleting it would let the .env value through again.
_NEUTRALISED_CREDENTIALS = (
    "LEGGIE_LLM__OPENROUTER_API_KEY",
    "LEGGIE_REASONER__API_KEY",
    "LEGGIE_REASONER_API_KEY",
)


@pytest.fixture(autouse=True)
def hermetic_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Blank provider credentials and reset the cached Settings singleton.

    The singleton is reset on both sides of the test: once so this test cannot
    inherit settings built from the developer's real .env, and once after so it
    cannot leak its blanked settings into a test that builds its own.
    """
    for var in _NEUTRALISED_CREDENTIALS:
        monkeypatch.setenv(var, "")
    monkeypatch.setattr(settings_module, "_settings", None, raising=False)
    yield
    settings_module._settings = None


@pytest.fixture(autouse=True)
def socket_guard(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Prevent any outbound network connections during tests.

    Monkeypatches ``socket.socket.connect`` to raise a ``RuntimeError``
    whenever a test attempts to open a real network connection.  This is a
    belt-and-suspenders guard alongside the credential-blanking fixture above:
    even if a credential slips through, the test will still fail before
    reaching an external host.

    Unix sockets and ``connect_ex`` are similarly trapped.
    """

    _original_connect = socket.socket.connect
    _original_connect_ex = socket.socket.connect_ex

    def _deny_connect(self: socket.socket, address: Any, /) -> None:
        host, port = address[:2]
        if host in ("127.0.0.1", "::1", "localhost"):
            _original_connect(self, address)
            return
        raise RuntimeError(
            f"Outbound network connection blocked by test suite socket guard: "
            f"{host}:{port}.  Tests must not reach external services."
        )

    def _deny_connect_ex(self: socket.socket, address: Any, /) -> int:
        host, port = address[:2]
        if host in ("127.0.0.1", "::1", "localhost"):
            return _original_connect_ex(self, address)
        raise RuntimeError(
            f"Outbound network connection blocked by test suite socket guard: "
            f"{host}:{port}.  Tests must not reach external services."
        )

    monkeypatch.setattr(socket.socket, "connect", _deny_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _deny_connect_ex)
    yield
