---
name: leggie-config-and-flags
description: >
  Authoritative catalog of every Leggie configuration axis: pydantic-settings
  fields and their env var names, config/routes.yaml routes, and CLI commands/
  flags. Load when looking up, adding, or changing any setting, env var, model
  route, or CLI flag, or when a setting seems to have no effect. Includes the
  env-prefix subtlety (single vs double underscore), production-vs-experimental
  status, and re-verification commands (flags drift).
---

# Leggie Config and Flags Catalog

All facts verified against working-tree source 2026-07-10. Flags drift — run
the Provenance commands before trusting this after any commit.

## 1. Settings (`leggie/config/settings.py`, pydantic-settings, `.env` file)

**Env-prefix subtlety (trap):** each sub-settings class declares its own
prefix with a SINGLE trailing underscore (e.g. `env_prefix="LEGGIE_LLM_"`),
while the parent `Settings` uses `env_nested_delimiter="__"`. Result: BOTH
`LEGGIE_LLM_OPENROUTER_API_KEY` (direct sub-settings load) and
`LEGGIE_LLM__OPENROUTER_API_KEY` (nested delimiter, used in `.env.example`)
can populate the key. `.env.example` uses the double-underscore form —
prefer that form for consistency.

| Group | Field | Env var (.env.example form) | Default | Status |
|---|---|---|---|---|
| llm | `openrouter_api_key` | `LEGGIE_LLM__OPENROUTER_API_KEY` | `""` (required for analyze) | PROD |
| llm | `openrouter_base_url` | `LEGGIE_LLM__OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | PROD |
| llm | `openrouter_default_model` | `LEGGIE_LLM__OPENROUTER_DEFAULT_MODEL` | `google/gemini-2.5-flash` | PROD (validated against offline allowlist at adapter init) |
| cascade | `rules_path` | `LEGGIE_CASCADE__RULES_PATH` | `config/routes.yaml` | PROD |
| cascade | `free_model` / `budget_model` / `premium_model` | `LEGGIE_CASCADE__*_MODEL` | gemini-2.5-flash-lite / -flash / -pro | PROD |
| cascade | `confidence_floor` | `LEGGIE_CASCADE__CONFIDENCE_FLOOR` | `0.6` | PROD |
| cascade | `premium_fallback_enabled` | `LEGGIE_CASCADE__PREMIUM_FALLBACK_ENABLED` | `True` | PROD |
| budget | `max_tokens_per_run` | `LEGGIE_BUDGET__MAX_TOKENS_PER_RUN` | `20_000_000` (safety ceiling only) | PROD |
| budget | `max_cost_per_run` | `LEGGIE_BUDGET__MAX_COST_PER_RUN` | `5.0` USD — **the real governor** | PROD |
| budget | `degrade_on_budget_warning` | `LEGGIE_BUDGET__DEGRADE_ON_BUDGET_WARNING` | `True` | PROD |
| budget | `degrade_strategy` | `LEGGIE_BUDGET__DEGRADE_STRATEGY` | `fewer_paths` (or `fewer_lenses`, `cheaper_tier`) | PROD |
| retrieval | `embed_model` | `LEGGIE_RETRIEVAL__EMBED_MODEL` | `spyrosbriakos/greek_legal_bert_v2` | EXPERIMENTAL — retrieval largely unwired |
| retrieval | `dense_top_k`/`sparse_top_k`/`hybrid_top_k`/`rrf_constant`/`max_concurrent_cellar`/`cellar_timeout_seconds` | `LEGGIE_RETRIEVAL__*` | 10/10/10/60/4/60 | EXPERIMENTAL |
| ingest | `max_file_size_mb` | `LEGGIE_INGEST__MAX_FILE_SIZE_MB` | `50` | PROD |
| ingest | `temp_dir` | `LEGGIE_INGEST__TEMP_DIR` | `/tmp/leggie_ingest` (**POSIX path on a Windows-only project — suspicious default, verify usage before relying on it**) | check |
| ingest | `ocr_enabled` | `LEGGIE_INGEST__OCR_ENABLED` | `False` | EXPERIMENTAL |
| persistence | `url` | `LEGGIE_DB__URL` | `sqlite:///leggie.db` | PROD |
| persistence | `echo` / `wal_mode` | `LEGGIE_DB__ECHO` / `LEGGIE_DB__WAL_MODE` | `False` / `True` | PROD |
| top-level | `debug` | `LEGGIE_DEBUG` | `False` | PROD |
| top-level | `log_level` | `LEGGIE_LOG_LEVEL` | `INFO` (DEBUG/INFO/WARNING/ERROR/CRITICAL) | PROD |
| top-level | `seed` | `LEGGIE_SEED` | `42` (negative values silently reset to 42) | PROD |

Note: `.env.example` shows `LEGGIE_BUDGET__MAX_TOKENS_PER_RUN=500000` — this
is STALE relative to the code default `20_000_000` (the 500k ceiling was the
historical bug that blocked runs before the cost cap could govern). Do not
copy the stale value into a real `.env`.

Access pattern: `from leggie.config.settings import get_settings` (lazy
singleton; `reload_settings()` for tests).

## 2. Routes (`config/routes.yaml`)

Route = task_type → model, tier, max_tokens, cascade flags. Cascade escalates
FREE → BUDGET → PREMIUM on low confidence/failure.

| task_type | model | tier | max_tokens | cascade |
|---|---|---|---|---|
| `lens_analysis` | google/gemini-2.5-flash | budget | 6144 | yes (lite/flash/pro) |
| `verbalized_sampling` | google/gemini-2.5-flash | budget | 8192 | yes |
| `adversarial_critic` | anthropic/claude-sonnet-4.6 | budget | 2048 | yes (→ claude-opus-4.8) |
| `classification` | google/gemini-2.5-flash-lite | free | 1024 | yes |
| `summarization` | google/gemini-2.5-flash-lite | free | 2048 | no |
| `evidence_verification` | google/gemini-2.5-pro | premium | 2048 | yes |
| `report_generation` | google/gemini-2.5-pro | premium | 8192 | yes |

Consumers (grep task-type strings): `adversarial_critic` →
`agents/skeptic.py` (`_CRITIC_TASK`); `lens_analysis` → lens base;
`evidence_verification` → CoVe. Model IDs must be REAL OpenRouter IDs —
`LLMAdapter` checks the default model against an offline allowlist
(`infrastructure/llm/__init__.py` `_OFFLINE_MODEL_ALLOWLIST`) and
`validate_model_ids()` can query the live catalog. History: fake model IDs
once broke the whole pipeline (commit 39b42ef).

## 3. CLI (`leggie/interfaces/cli/__init__.py`, entry point `leggie` per pyproject `[project.scripts]`)

| Command | Flags | Notes |
|---|---|---|
| `leggie --version` | — | prints `Leggie v0.1.0` |
| `leggie parse <file>` | `--output/-o PATH` | deterministic, no LLM, free; prints JSON (UTF-8 forced on Windows console) |
| `leggie analyze <file>` | `--output/-o DIR`, `--lenses/-l NAME...`, `--checkpoint/-c PATH` | full pipeline, costs money; lens names: `constitutional legal_coherence economic implementation eu_gdpr` |
| `leggie eval --gold-set/-g PATH` | `--results/-r PATH` | scores vs gold set; prints precision/recall/F1/RDI per bill |

There is NO `--verbalized-sampling` flag and NO reranker selector as of
2026-07-10 (REMEDIATION_PLAN Phase 5 items D4/D5 remain unwired).

Thread of a flag (worked example, `--lenses`): CLI arg
(`cli/__init__.py:36`) → `AnalyzeBillCommand.lenses`
(`cqrs/commands/cli_commands.py`) → `AnalyzeBillHandler`
(`cqrs/handlers/cli_handlers.py`) → `BillAnalysisFlow.run(lenses=...)` →
`Orchestrator.analyze_article(article, lenses)`.

## 4. How to add a config axis (checklist)

- [ ] Add field to the right `*Settings` class in `leggie/config/settings.py` (typed, `Field(default=...)`, validator if range-bound)
- [ ] Add the env var to `.env.example` (double-underscore form)
- [ ] If CLI-exposed: argparse flag in `interfaces/cli/__init__.py` → field on the Command dataclass → handler → consumer
- [ ] Consume it somewhere real (a defined-but-unread setting is dead weight — retrieval group is the cautionary example)
- [ ] Test in `tests/unit/test_config.py`
- [ ] Route through change control (class B minimum; class A if it changes pipeline behavior)

## When NOT to use this skill

- WHY the cascade/routing is designed this way → **llm-structured-output-reference**
- Running commands / output locations → **leggie-run-and-operate**
- Environment setup / .env creation → **leggie-build-and-env**
- Whether changing a value needs a smoke test → **leggie-change-control**

## Provenance and maintenance

- Settings fields: `grep -n "= Field\|: str\|: int\|: float\|: bool" leggie/config/settings.py`
- Env example vs code defaults: `diff <(grep LEGGIE .env.example) <(grep -n default leggie/config/settings.py)` (manual compare; watch the 500k stale value)
- Routes: `cat config/routes.yaml`
- CLI flags: `python -m leggie.interfaces.cli --help` won't work directly; use `leggie --help`, `leggie analyze --help`
- Lens names: `grep -A7 "_DEFAULT_LENSES" leggie/application/agents/orchestrator.py`
- Dead settings check: `grep -rn "retrieval\." leggie --include="*.py" | grep -v config`
