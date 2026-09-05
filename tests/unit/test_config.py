"""Tests for config/settings — pydantic-settings validation."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from leggie.config.settings import (
    BudgetSettings,
    LLMSettings,
    PersistenceSettings,
    ReasonerSettings,
    Settings,
)


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
    def test_reasoner_disabled_by_default(self):
        s = ReasonerSettings()
        assert s.enabled is False

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

    def test_reasoner_autostart_disabled(self):
        """Autostart defaults to False (manual start is the supported path)."""
        s = ReasonerSettings()
        assert s.autostart is False

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
        assert s.reasoner.enabled is False


class TestPersistenceSettingsUrlValidation:
    """DH-31: PersistenceSettings.url had no scheme validation, so a non-sqlite
    value would silently reach container.py's `.replace("sqlite:///", "")`
    no-op and be used as a raw (wrong) filesystem path instead of failing
    loudly — the same "closed-set validation instead of a silently-accepted
    bare str" fix shape as DH-20's `AnalyzeBillCommand.pipeline`."""

    def test_default_url_is_sqlite(self):
        assert PersistenceSettings().url == "sqlite:///leggie.db"

    def test_non_sqlite_scheme_rejected(self):
        with pytest.raises(ValidationError):
            PersistenceSettings(url="postgres://user@host/db")

    def test_sqlite_path_variants_still_accepted(self):
        """Boundary: a real, different sqlite path, and the bare-prefix form
        sqlite3 itself treats as "private on-disk temp db" — neither is
        malformed, so the validator must not reject them."""
        assert PersistenceSettings(url="sqlite:///data/other.db").url == "sqlite:///data/other.db"
        assert PersistenceSettings(url="sqlite:///").url == "sqlite:///"

    def test_bad_url_via_env_var_also_rejected(self, monkeypatch):
        """The validator fires through the real env-loading path, not just
        direct kwarg construction."""
        monkeypatch.setenv("LEGGIE_DB_URL", "mysql://nope")
        with pytest.raises(ValidationError):
            PersistenceSettings()


class TestPersistenceUrlEnvAlias:
    """DH-31: of Settings' 8 sub-settings models, `persistence` is the only
    one whose Python field name ("persistence") disagrees with its own
    env_prefix stem ("DB") — every sibling (llm/LLM, cascade/CASCADE,
    budget/BUDGET, retrieval/RETRIEVAL, analysis/ANALYSIS, ingest/INGEST,
    reasoner/REASONER) keeps the two in sync. Settings' own nested-delimiter
    mechanism keys off the PYTHON FIELD NAME, not the sub-model's own prefix,
    so `.env.example`'s previously-documented `LEGGIE_DB__URL` (double
    underscore — correct by every sibling's convention) silently never
    applied; only `LEGGIE_DB_URL` (single underscore, matching the
    sub-model's own prefix) and the unintuitive `LEGGIE_PERSISTENCE__URL`
    actually override it. Proven directly against the real Settings class,
    not reasoned about — see the region's own report for the empirical
    A/B/C comparison this was found with."""

    def test_working_single_underscore_form_overrides(self, monkeypatch):
        monkeypatch.setenv("LEGGIE_DB_URL", "sqlite:///alternate.db")
        assert Settings().persistence.url == "sqlite:///alternate.db"

    def test_previously_documented_double_underscore_form_is_silently_ignored(self, monkeypatch):
        """Locks in the surprising-but-real non-functional form so nobody
        "corrects" .env.example back to it without noticing the regression."""
        monkeypatch.setenv("LEGGIE_DB__URL", "sqlite:///alternate.db")
        assert Settings().persistence.url == "sqlite:///leggie.db"  # default, override dropped

    def test_env_example_documents_the_working_form(self):
        env_example = Path(__file__).resolve().parent.parent.parent / ".env.example"
        text = env_example.read_text(encoding="utf-8")
        assert "LEGGIE_DB_URL=" in text
        assert "LEGGIE_DB__URL=" not in text


# ── PROD-11 settings reflection test ─────────────────────────────────────


class TestSettingsReflection:
    """Every Settings field must be referenced in leggie/ source code
    or explicitly documented as consumption-by-env-var-only."""

    # Fields consumed purely via pydantic-settings env-var loading
    # (no leggie/ Python code reads the attribute directly).
    # These are legitimate — the env var is the interface.
    _ENV_ONLY_FIELDS: frozenset[str] = frozenset(
        {
            # Ingest — deferred to Phase 5
            "max_file_size_mb",
            "temp_dir",
            "ocr_enabled",
            # Retrieval — deferred to Phase 3/4
            "embed_model",
            "dense_top_k",
            "sparse_top_k",
            "hybrid_top_k",
            "rrf_constant",
            "max_concurrent_cellar",
            "cellar_timeout_seconds",
            # Persistence — deferred to Phase 4
            "url",
            "echo",
            "wal_mode",
            # Cascade — consumed by routes.yaml; settings hold documented defaults
            "free_model",
            "budget_model",
            "premium_model",
            "confidence_floor",
            "premium_fallback_enabled",
            # Budget degrade — consumed by BudgetGuard internally via Settings()
            "degrade_on_budget_warning",
            "degrade_strategy",
            # Reasoner — consumed by ReasonerServerManager via Settings()
            "enabled",
            "home",
            "base_url",
            "api_key",
            "startup_timeout",
            "request_timeout",
            "stage1_preset",
            "stage2_preset",
            "perspective",
            # Analysis — opt-in features, consumed by BillAnalysisFlow builder
            "use_verbalized_sampling",
            "reranker",
        }
    )

    def test_all_settings_fields_referenced_in_source(self):
        """Every Settings field must appear in leggie/ source code or
        be documented in _ENV_ONLY_FIELDS."""
        from pathlib import Path

        from pydantic_settings import BaseSettings

        # Collect all Settings field names (including nested sub-settings)

        all_fields: dict[str, str] = {}  # field_name → parent class name

        def _collect(cls: type) -> None:
            if not issubclass(cls, BaseSettings):
                return
            for name, field in cls.model_fields.items():
                key = name
                all_fields[key] = cls.__name__
                # Recurse into nested BaseSettings subfields
                annotation = field.annotation
                if (
                    annotation is not None
                    and isinstance(annotation, type)
                    and issubclass(annotation, BaseSettings)
                ):
                    _collect(annotation)

        _collect(Settings)

        # Grep every .py file in leggie/ for all field names
        repo_root = Path(__file__).resolve().parent.parent.parent
        leggie_dir = repo_root / "leggie"
        py_files = list(leggie_dir.rglob("*.py"))

        referenced: set[str] = set()
        for py_file in py_files:
            text = py_file.read_text(encoding="utf-8")
            for field_name in all_fields:
                if field_name not in referenced and field_name in text:
                    referenced.add(field_name)

        unreferenced = set(all_fields) - referenced - self._ENV_ONLY_FIELDS
        if unreferenced:
            msg = (
                "Unreferenced Settings fields found. Either wire them in "
                "leggie/ source or add to _ENV_ONLY_FIELDS with a reason:\n"
                + "\n".join(f"  {f} ({all_fields[f]})" for f in sorted(unreferenced))
            )
            raise AssertionError(msg)
