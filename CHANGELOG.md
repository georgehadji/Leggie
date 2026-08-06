# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Production-readiness hardening across all phases (see `docs/PRODUCTION_READINESS_PLAN.md`):
  - Hermetic test suite (blanked credentials + socket guard)
  - Budget reservation (reserve→settle) so `$5` cap is enforceable
  - Pooled HTTP transport with `Retry-After` + error redaction
  - CoVe / skeptic bounded fan-out for throughput
  - `ResourceLocator` for packaged/writable path resolution
  - RunManifest + `--json` / `--log-level` / `--quiet` CLI flags
  - SQLite durable event + state stores and `leggie replay <run_id>`
  - `BoundedIngestor`, `PromptHardeningDecorator`, injection regression corpus
  - Citation index builder (181 identifiers) and ladder cassette tests

### Fixed
- `asyncio` (PyPI backport) removed from dependencies (stdlib used instead)
- Model identity unified to `x-ai/grok-4.5` across settings/routes/allowlist
- `with_cache` (lru_cache over a coroutine) deleted
- structlog fields now render (the `extra={}` telemetry defect)

## [0.1.0] - Initial release

### Added
- Deterministic 5-lens legal analysis pipeline
- Calibrated Skeptic + CoVe citation verification
- Deliberative (opt-in, Reasoner-backed) pipeline
- Clean Architecture spine with import-linter enforced layers
