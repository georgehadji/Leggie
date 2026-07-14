"""Configuration module — pydantic-settings, 12-factor, validated at startup.

All settings are loaded from environment variables / .env files at startup
and validated. Secrets from env/secret-manager only.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMSettings(BaseSettings):
    """LLM provider configuration via OpenRouter."""

    model_config = SettingsConfigDict(env_prefix="LEGGIE_LLM_", env_file=".env", extra="ignore")

    # OpenRouter — single API key for all providers
    openrouter_api_key: str = Field(default="", description="OpenRouter API key")
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_default_model: str = "google/gemini-2.5-flash"
    max_concurrency: int = Field(
        default=5, ge=1, le=100,
        description="Max concurrent article analyses per document",
    )


class CascadeSettings(BaseSettings):
    """Model cascade / router configuration."""

    model_config = SettingsConfigDict(env_prefix="LEGGIE_CASCADE_", env_file=".env", extra="ignore")

    rules_path: str = Field(default="config/routes.yaml", description="Path to routing rules YAML")
    free_model: str = "google/gemini-2.5-flash-lite"
    budget_model: str = "google/gemini-2.5-flash"
    premium_model: str = "google/gemini-2.5-pro"
    confidence_floor: float = Field(default=0.6, ge=0.0, le=1.0)
    premium_fallback_enabled: bool = True


class BudgetSettings(BaseSettings):
    """Budget guard configuration — token/$ ceiling per run."""

    model_config = SettingsConfigDict(env_prefix="LEGGIE_BUDGET_", env_file=".env", extra="ignore")

    # Token cap is a hard safety ceiling only; cost cap is the intended governor.
    # At the Greek models (~$0.30-1.25/1M) the $5 cost cap allows ~4-16M tokens, so
    # the token ceiling must sit well above that or it throttles a full-bill run
    # (5 lenses x N articles) long before the money budget is used.
    max_tokens_per_run: int = Field(default=20_000_000, ge=1_000)
    max_cost_per_run: float = Field(default=5.0, ge=0.0)  # USD — primary governor
    degrade_on_budget_warning: bool = True
    degrade_strategy: Literal["fewer_paths", "fewer_lenses", "cheaper_tier"] = "fewer_paths"


class RetrievalSettings(BaseSettings):
    """Retrieval configuration — corpora, embeddings, hybrid parameters."""

    model_config = SettingsConfigDict(env_prefix="LEGGIE_RETRIEVAL_", env_file=".env", extra="ignore")

    embed_model: str = "spyrosbriakos/greek_legal_bert_v2"
    dense_top_k: int = 10
    sparse_top_k: int = 10
    hybrid_top_k: int = 10
    rrf_constant: int = Field(default=60, ge=1)
    max_concurrent_cellar: int = Field(default=4, ge=1, le=10)
    cellar_timeout_seconds: int = 60


class AnalysisSettings(BaseSettings):
    """Analysis pipeline configuration — opt-in experimental features."""

    model_config = SettingsConfigDict(env_prefix="LEGGIE_ANALYSIS_", env_file=".env", extra="ignore")

    use_verbalized_sampling: bool = False
    reranker: Literal["composite", "model"] = "composite"


class IngestSettings(BaseSettings):
    """Ingest configuration."""

    model_config = SettingsConfigDict(env_prefix="LEGGIE_INGEST_", env_file=".env", extra="ignore")

    max_file_size_mb: int = Field(default=50, ge=1)
    temp_dir: str = Field(default="/tmp/leggie_ingest")  # nosec B108
    ocr_enabled: bool = False


class PersistenceSettings(BaseSettings):
    """Persistence configuration — SQLite/WAL."""

    model_config = SettingsConfigDict(env_prefix="LEGGIE_DB_", env_file=".env", extra="ignore")

    url: str = Field(default="sqlite:///leggie.db")
    echo: bool = False
    wal_mode: bool = True


class ReasonerSettings(BaseSettings):
    """Reasoner service configuration — multi-model deliberative analysis."""

    model_config = SettingsConfigDict(
        env_prefix="LEGGIE_REASONER_", env_file=".env", extra="ignore"
    )

    enabled: bool = Field(
        default=False, description="Master switch to enable deliberative pipeline"
    )
    home: str = Field(default="", description="Path to Reasoner repository for auto-start")
    base_url: str = Field(default="http://localhost:8003", description="Reasoner backend URL")
    api_key: str = Field(default="", description="Reasoner ADMIN_API_KEY (secret)")
    autostart: bool = Field(
        default=True, description="Auto-start Reasoner backend if not running"
    )
    startup_timeout: int = Field(
        default=60, ge=1, description="Seconds to wait for Reasoner to become healthy"
    )
    request_timeout: int = Field(
        default=300, ge=1, description="Timeout for individual Reasoner requests"
    )
    stage1_preset: str = Field(
        default="multi-perspective-premium", description="Reasoner preset for Stage 1 generation"
    )
    stage2_preset: str = Field(
        default="subagent-premium", description="Reasoner preset for Stage 2 audit"
    )
    perspective: str = Field(
        default="neutral", description="Default party perspective (neutral, niki, etc.)"
    )


class Settings(BaseSettings):
    """Top-level configuration — loads all sub-settings and top-level vars."""

    model_config = SettingsConfigDict(
        env_prefix="LEGGIE_",
        env_file=".env",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # Application
    app_name: str = "Leggie"
    app_version: str = "0.1.0"
    debug: bool = False
    log_level: str = Field(default="INFO", pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")
    seed: int = Field(default=42, description="Global random seed for reproducibility")

    # Sub-settings
    llm: LLMSettings = Field(default_factory=LLMSettings)
    cascade: CascadeSettings = Field(default_factory=CascadeSettings)
    budget: BudgetSettings = Field(default_factory=BudgetSettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    analysis: AnalysisSettings = Field(default_factory=AnalysisSettings)
    ingest: IngestSettings = Field(default_factory=IngestSettings)
    persistence: PersistenceSettings = Field(default_factory=PersistenceSettings)
    reasoner: ReasonerSettings = Field(default_factory=ReasonerSettings)

    @field_validator("seed")
    @classmethod
    def seed_non_negative(cls, v: int) -> int:
        if v < 0:
            return 42
        return v


# Global singleton — lazy-loaded
_settings: Settings | None = None


def get_settings() -> Settings:
    """Get the global Settings singleton, loading on first call."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reload_settings() -> Settings:
    """Force-reload settings (useful for testing)."""
    global _settings
    _settings = Settings()
    return _settings
