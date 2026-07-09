"""Tests for config/settings — pydantic-settings validation."""

import pytest
from leggie.config.settings import Settings, LLMSettings, BudgetSettings


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
        assert s.max_tokens_per_run == 500_000

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
