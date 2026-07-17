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
def hermetic_settings(monkeypatch: pytest.MonkeyPatch) -> None:
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
