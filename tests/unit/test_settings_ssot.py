"""SSOT guard — a configuration value is defined in settings.py and nowhere else.

Generalised from the ingest-caps test after the 2026-09-08 audit
(``ssot_audit_report_2026-09-08.md``) found seven duplicated constants, two of
them already drifted.

The recurring anti-pattern this catches is NOT copy-paste for its own sake — it
is a settings field added without a consumer, leaving the code's own literal in
charge. That fails by *appearing* to work: the environment variable is
documented, the operator sets it, and nothing happens. Seven fields were inert
when the audit ran.

Each test names the exact literal and the module it must not reappear in, rather
than scanning for "any number", so a failure says precisely what regressed.
"""

from __future__ import annotations

import pathlib

import pytest

import leggie

PKG = pathlib.Path(leggie.__file__).parent
REPO = PKG.parent


def _code(rel_path: str) -> str:
    """Source of a module with comment lines stripped.

    Comments legitimately quote the old values while explaining the history, so
    scanning them would make every one of these tests fail on its own docs.
    """
    src = (PKG / rel_path).read_text(encoding="utf-8")
    return "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))


class TestNoDuplicatedConfigLiterals:
    """SSOT-1..SSOT-5 — the values live in config/settings.py only."""

    @pytest.mark.parametrize(
        ("module", "literal", "ssot_id"),
        [
            # SSOT-1: the budget guard defaulted to 500_000 tokens — 40x below
            # the configured 20,000,000 and the exact value of the historical
            # budget-block incident, where the token ceiling throttled runs long
            # before the $5 cost cap could govern.
            ("infrastructure/budget_guard/__init__.py", "500_000", "SSOT-1"),
            ("infrastructure/budget_guard/__init__.py", "20_000_000", "SSOT-1"),
            # SSOT-2: the router carried its own tier -> model map, so
            # LEGGIE_CASCADE__{FREE,BUDGET,PREMIUM}_MODEL were read by nothing.
            ("infrastructure/router/__init__.py", "x-ai/grok-4.5", "SSOT-2"),
            ("infrastructure/router/__init__.py", "google/gemini-2.5-flash", "SSOT-2"),
            # SSOT-4: the default model id, repeated down the whole stack.
            ("infrastructure/llm/__init__.py", "google/gemini-2.5-flash", "SSOT-4"),
            ("infrastructure/llm/adapters/openrouter.py", "google/gemini-2.5-flash", "SSOT-4"),
            ("application/agents/orchestrator.py", "google/gemini-2.5-flash", "SSOT-4"),
            ("application/agents/constitutional_lens.py", "google/gemini-2.5-flash", "SSOT-4"),
            # SSOT-4: the API base URL was a fourth copy.
            ("infrastructure/llm/__init__.py", "https://openrouter.ai/api/v1", "SSOT-4"),
            ("infrastructure/llm/adapters/openrouter.py", "https://openrouter.ai/api/v1", "SSOT-4"),
            # SSOT-5: the rate-limiter ceiling.
            ("infrastructure/llm/adapters/openrouter.py", "max_rate=5.0", "SSOT-5"),
        ],
    )
    def test_literal_absent(self, module: str, literal: str, ssot_id: str) -> None:
        assert literal not in _code(module), (
            f"{ssot_id}: {module} re-hardcodes {literal!r}. "
            f"It belongs in leggie/config/settings.py and must be read from there — "
            f"a second copy drifts silently and makes the env var a lie."
        )

    @pytest.mark.parametrize(
        ("module", "literal", "ssot_id"),
        [
            # SSOT-3: the two concurrency ceilings existed as documented
            # settings that nothing read (PROD-38); the callee's literal 10 was
            # the real value.
            ("application/services/cove_verifier.py", "max_concurrency: int = 10", "SSOT-3"),
            ("application/agents/skeptic.py", "max_concurrency: int = 10", "SSOT-3"),
        ],
    )
    def test_concurrency_default_is_not_a_literal(
        self, module: str, literal: str, ssot_id: str
    ) -> None:
        assert literal not in _code(module), (
            f"{ssot_id}: {module} pins the concurrency ceiling as a literal, so "
            f"LLMSettings.max_*_concurrency goes back to being read by nothing."
        )


class TestSettingsAreActuallyReachable:
    """A settings field with no consumer is the defect, not the duplicate.

    These assert the WIRING, which the literal-greps above cannot see: a module
    could drop its literal and still ignore the setting.
    """

    def test_budget_guard_reads_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from leggie.config import settings as settings_module
        from leggie.infrastructure.budget_guard import BudgetGuard

        pinned = settings_module.Settings(
            budget=settings_module.BudgetSettings(max_tokens_per_run=123_456, max_cost_per_run=1.5)
        )
        monkeypatch.setattr(settings_module, "_settings", pinned)

        guard = BudgetGuard()
        assert guard._state.max_tokens == 123_456
        assert guard._state.max_cost == 1.5

    def test_router_tier_defaults_read_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from leggie.config import settings as settings_module
        from leggie.domain.models import ModelTier
        from leggie.infrastructure.router import StaticRouter

        pinned = settings_module.Settings(
            cascade=settings_module.CascadeSettings(
                free_model="vendor/free-x",
                budget_model="vendor/budget-x",
                premium_model="vendor/premium-x",
            )
        )
        monkeypatch.setattr(settings_module, "_settings", pinned)

        router = StaticRouter()
        assert router._default_for_tier(ModelTier.FREE) == "vendor/free-x"
        assert router._default_for_tier(ModelTier.BUDGET) == "vendor/budget-x"
        assert router._default_for_tier(ModelTier.PREMIUM) == "vendor/premium-x"

    @pytest.mark.asyncio
    async def test_skeptic_concurrency_reads_settings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The ceiling must reach the semaphore, not merely the signature."""
        import asyncio

        from leggie.application.agents.skeptic import CalibratedSkeptic
        from leggie.config import settings as settings_module
        from leggie.domain.models import IRAC, Confidence, Finding, FindingType

        pinned = settings_module.Settings(
            llm=settings_module.LLMSettings(max_skeptic_concurrency=3)
        )
        monkeypatch.setattr(settings_module, "_settings", pinned)

        seen: list[int] = []
        real_semaphore = asyncio.Semaphore

        def spy(value: int) -> asyncio.Semaphore:
            seen.append(value)
            return real_semaphore(value)

        monkeypatch.setattr(asyncio, "Semaphore", spy)

        finding = Finding(
            finding_type=FindingType.CONSTITUTIONAL,
            irac=IRAC(issue="i", rule="r", application="a", conclusion="c"),
            confidence=Confidence.from_score(0.5),
            lens="test",
            model="test",
        )
        await CalibratedSkeptic().review([finding])

        assert 3 in seen, f"skeptic built its semaphore from {seen}, not the configured 3"


class TestEnvExampleMatchesCodeDefaults:
    """SSOT-6 — .env.example is copied to .env verbatim (its own line 2 says so),
    so a value there that disagrees with the code default silently changes
    behaviour for every new checkout."""

    def test_reasoner_autostart_agrees(self) -> None:
        from leggie.config.settings import ReasonerSettings

        env_example = (REPO / ".env.example").read_text(encoding="utf-8")
        documented = next(
            line.split("=", 1)[1].strip().lower()
            for line in env_example.splitlines()
            if line.startswith("LEGGIE_REASONER__AUTOSTART=")
        )
        assert (documented == "true") is ReasonerSettings().autostart, (
            "SSOT-6: .env.example and ReasonerSettings.autostart disagree. A leaked "
            "autostarted Reasoner process was a real incident (PR #7, af4e4a8)."
        )
