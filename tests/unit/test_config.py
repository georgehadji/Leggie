"""Tests for config/settings — pydantic-settings validation."""

from leggie.config.settings import BudgetSettings, LLMSettings, ReasonerSettings, Settings


class TestSettings:
    def test_default_settings_load(self):
        s = Settings()
        assert s.app_name == "Leggie"
        assert s.app_version == "0.1.0"
        assert s.seed == 42

    def test_log_level_validation(self):
        s = Settings()
        assert s.log_level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

    def test_debug_default(self):
        s = Settings()
        assert s.debug is False

    def test_seed_non_negative(self):
        s = Settings(seed=-5)
        assert s.seed == 42  # Falls back to default


class TestLLMSettings:
    def test_openrouter_key_default_empty(self):
        s = LLMSettings()
        assert s.openrouter_api_key == ""

    def test_default_model(self):
        s = LLMSettings()
        # Must be a real, working OpenRouter id (no :free suffix, no invalid date-stamped id)
        assert s.openrouter_default_model == "google/gemini-2.5-flash"

    def test_base_url(self):
        s = LLMSettings()
        assert "openrouter.ai" in s.openrouter_base_url


class TestBudgetSettings:
    def test_max_tokens_default(self):
        s = BudgetSettings()
        # Token cap is a hard safety ceiling only; the $ cost cap is the real
        # governor. Kept well above what $5 buys so it never throttles a run first.
        assert s.max_tokens_per_run == 20_000_000

    def test_max_cost_default(self):
        s = BudgetSettings()
        assert s.max_cost_per_run == 5.0

    def test_degrade_strategy_valid(self):
        s = BudgetSettings()
        assert s.degrade_strategy in ("fewer_paths", "fewer_lenses", "cheaper_tier")


class TestSettingsEnvOverride:
    def test_settings_loads_without_crash(self):
        """Settings load without errors regardless of env state."""
        s = Settings()
        assert s.app_name == "Leggie"
        # LLM key may or may not be set depending on environment


class TestReasonerSettings:
    def test_reasoner_enabled_by_default(self):
        s = ReasonerSettings()
        assert s.enabled is True

    def test_reasoner_default_base_url(self):
        s = ReasonerSettings()
        assert s.base_url == "http://localhost:8003"

    def test_reasoner_default_presets(self):
        s = ReasonerSettings()
        assert s.stage1_preset == "multi-perspective-premium"
        assert s.stage2_preset == "subagent-premium"

    def test_reasoner_default_perspective(self):
        s = ReasonerSettings()
        assert s.perspective == "neutral"

    def test_reasoner_autostart_enabled(self):
        s = ReasonerSettings()
        assert s.autostart is True

    def test_reasoner_startup_timeout_positive(self):
        s = ReasonerSettings()
        assert s.startup_timeout == 60
        assert s.startup_timeout > 0

    def test_reasoner_api_key_empty_by_default(self):
        s = ReasonerSettings()
        assert s.api_key == ""

    def test_reasoner_home_empty_by_default(self):
        s = ReasonerSettings()
        assert s.home == ""

    def test_reasoner_settings_in_main_settings(self):
        s = Settings()
        assert hasattr(s, "reasoner")
        assert isinstance(s.reasoner, ReasonerSettings)
        assert s.reasoner.enabled is True
