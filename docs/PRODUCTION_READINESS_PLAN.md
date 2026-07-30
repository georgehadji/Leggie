# Production Readiness Plan

**Date:** 2026-07-29
**Branch:** master @ `83fd14b` + uncommitted token-optimization working tree
**Author:** production-readiness research pass (companion to `docs/PRODUCTION_READINESS.md`)
**ID namespace:** `PROD-01 … PROD-40` — permanent handles, never renumbered. Cross-referenced to the assessment's `P0-/P1-/P2-` findings. `PROD-35…40` added 2026-07-29 on a throughput review (§4b).
**Scope:** target **A** (distributable CLI/library). Target **B** (hosted service) is out of scope and deferred to §12.

---

## 0. Current state — what already works, do not re-touch

Fencing this first, per house convention. Everything below is verified working as of 2026-07-29 and is **not** in scope for this plan except where a phase explicitly extends it.

| Area | Evidence | Status |
|---|---|---|
| Layer contract | `lint-imports` passes; `[tool.importlinter]` layers contract intact | SETTLED |
| Static analysis | `ruff check leggie/ tests/` clean; `bandit -r leggie/` 0 issues across 8,161 LOC | SETTLED |
| Type discipline | `mypy --strict` clean on tracked sources (the 1 error is in untracked `parse/integrity.py:37`) | SETTLED |
| Test suite | 578 tests pass (measured with credentials blanked) | SETTLED |
| Parallel article fan-out (D3/D6) | `bill_analysis_flow.py:264` → `Orchestrator.analyze_document()`, semaphore + `return_exceptions=True` | CLOSED |
| Structured-output ladder (TOK-1…TOK-7) | `llm/ladder.py`, decorator stack `StructuredOutput → BudgetGuard → Transport` in `container.py:105-131` | CLOSED |
| Skeptic gate chain | `agents/skeptic.py` — Numeric/Temporal/Factual/Obligation + LLM adversarial gate | SETTLED |
| CoVe 4-step loop | `services/cove_verifier.py` | SETTLED |
| Parse module decomposition | `infrastructure/parse/{articles,citations,integrity,patterns,preprocess,structure,toc}.py` | SETTLED |
| Deliberative server lifecycle | PR #7 closed the orphaned-process leak; shutdown in `finally` | CLOSED |
| RerankerPort binding | `container.py:176-183` binds `OpenRouterReranker` — the architecture-contract skill's "no binding" note is **stale** | CLOSED |
| CI gates | `.github/workflows/ci.yml` **does** run `lint-imports` and `--cov-fail-under=80` — the change-control skill's "CI does not" note is **stale** | CLOSED |

Two skills need a provenance refresh as a side effect of this plan: `leggie-architecture-contract` §6 (reranker) and `leggie-change-control` §2 (CI gates).

---

## 1. Defect inventory — ranked by release-blocking impact

Severity uses the repo's convention. `Class` is the change-control risk class (A = pipeline-behavior-changing, B = wiring/refactor, C = docs/tests).

### Blocking (must close before any release)

| # | Defect | Layer | Evidence | Severity | Class |
|---|---|---|---|---|---|
| PROD-01 | `tests/conftest.py` absent from master; `pytest` resolves a live OpenRouter adapter from `.env` and bills ~$1.39/run | Tests | `git merge-base --is-ancestor cdc3abc master` → false; stale `tests/__pycache__/conftest…pyc` retains `hermetic_settings`; `tests/unit/test_cli.py:46` comment assumes no key | CRITICAL | C |
| PROD-02 | Full 5-lens pipeline yield never demonstrated | — | `docs/SMOKE_AUDIT.md` — passing gate is `--lenses constitutional` only; three full-run attempts died to stale route / 402 / parse degradation | CRITICAL | A |
| PROD-03 | Deliberative pipeline has zero recorded live runs despite README "Phase 5 ✅ Complete" | — | no run exhibit in repo | CRITICAL | A |
| PROD-04 | Quality is unmeasured: gold set is 2 synthetic bills / 6 labels; `eval_results.json` shows `f1=0.0, total_findings=0` | Tests | `tests/eval/gold_set_sample.json`; `eval_results.json` | CRITICAL | A |
| PROD-05 | Citation resolution index holds **2** identifiers, so CoVe validates against an empty corpus while README claims "verifies every citation" | Infra | `data/citation_index.json`; loaded `container.py:140-145`; = architecture-contract weak point **D7** | CRITICAL | A |
| PROD-06 | Event bus and state store are in-memory only; `PersistenceSettings` unread; no `sqlite3`/`aiosqlite`/SQLAlchemy import exists. "Event-sourced audit spine" does not survive process exit | Infra | `container.py:102,153`; `state_store.py:26-29` docstring admits it; `settings.py:91-98` | CRITICAL | B |
| PROD-07 | Three CWD-relative hardcoded paths break any run outside the repo root, despite a declared console script | Infra | `container.py:134,140,166`; `router/__init__.py:23` | CRITICAL | B |

### High (must close before a 1.0 tag)

| # | Defect | Layer | Evidence | Severity | Class |
|---|---|---|---|---|---|
| PROD-08 | Budget `check()` → `await` → `record_usage()` is not atomic; N concurrent calls all clear the ceiling before any records usage, so the $5 cap can be overshot by up to `max_concurrency` calls | Infra | `llm/decorators.py:66-82` and `:94-109`; concurrency from `settings.llm.max_concurrency=5` | HIGH | A |
| PROD-09 | Per-call cost/token telemetry is discarded: stdlib `logger.info("llm.call", extra={...})` under `basicConfig(format="%(message)s")` prints only `llm.call` | Infra | `openrouter.py:113-124` vs `observability/__init__.py:52` | HIGH | B |
| PROD-10 | 20 modules use stdlib `logging` directly; only 2 call sites use structlog, so trace-id correlation covers ~2% of the code. `configure_logging()` is CLI-only, so library use is unconfigured | App+Infra | `grep "^import logging" leggie/` → 20 hits; structlog use only at `bill_analysis_flow.py:211`, `reasoner/adapter.py:48`; `configure_logging` called only at `interfaces/cli/__init__.py:134` | HIGH | B |
| PROD-11 | ~14 settings are silent no-ops, including `ingest.max_file_size_mb`, all of `retrieval.*`, all of `persistence.*`, `cascade.rules_path`, and the global `seed` | Config | see assessment P1-6 table; `lens.py:132` seeds with `getattr(self,"_seed",0)` | HIGH | B |
| PROD-12 | Model identity conflicts across three sources: `settings.premium_model="moonshotai/kimi-k3"`, `routes.yaml` premium `x-ai/grok-4.5`, offline allowlist contains the former and **not** the latter | Config+Infra | `settings.py:38`; `config/routes.yaml`; `llm/__init__.py:42-67` | HIGH | A |
| PROD-13 | No prompt-injection defense at any of 8 prompt-build sites; untrusted bill text is concatenated into lens/skeptic/CoVe prompts with no delimiting, quarantine, or instruction-stripping | App | `lens.py:104,129`; `skeptic.py:114`; `cove_verifier.py:438`; `bill_overview.py:59,95`; `lens_vs.py:72`; grep for sanitiz/untrusted/delimit → 0 hits | HIGH | A |
| PROD-14 | Transport constructs a fresh `httpx.AsyncClient` per request — full TLS handshake on every one of ~300 calls/bill; timeout hardcoded 120 s; 429 ignores `Retry-After`; raw `resp.text` interpolated into exceptions | Infra | `openrouter.py:80-93` | HIGH | A |
| PROD-15 | `count_tokens` is `len(text)//4+1`, so the pre-call budget check is an estimate and `tier_used` is hardcoded `ModelTier.BUDGET` on every response, making cascade telemetry wrong by construction | Infra | `openrouter.py:127,138`; `decorators.py:63,91` | HIGH | A |
| PROD-16 | Ingest enforces no size, page, or time limit, and all four "async" ingestors perform fully blocking I/O and parsing on the event loop | Infra | `ingest/__init__.py:34-99`; `max_file_size_mb` unreferenced | HIGH | B |
| PROD-17 | Supply chain: every dep is an open lower bound, no lockfile, no hashes, no SBOM, no `pip-audit`/Dependabot; **`asyncio` is declared as a runtime dependency** (the abandoned 2015 PyPI backport, not stdlib) | Build | `pyproject.toml` dependencies | HIGH | B |
| PROD-18 | Coverage 83% overall but the resilience layer is untested: `reranker` 15%, `llm/decorators` 33%, `ingest` 46%, `cli` 48%, `llm/__init__` 52%, `lens.py` 55%, `router` 62%. No offline integration test exercises real OpenRouter request/response shapes | Tests | `coverage report` 2026-07-29 | HIGH | C |
| PROD-19 | No top-level exception handler in the CLI; exit codes are only 0/1; no signal handling, so `SIGINT` leaves checkpoint and partial `Outputs/` undefined | Interfaces | `interfaces/cli/__init__.py:130-160, 278-283` | HIGH | B |
| PROD-20 | Docker image is a development image: installs `.[dev]`, copies `tests/`, runs as **root**, editable install, floating base tag, no `HEALTHCHECK`, no `.dockerignore`, no volume for `Outputs/` | Build | `Dockerfile` | HIGH | B |
| PROD-21 | CI runs ubuntu-only although the product's real target is Windows and the code carries Windows-specific handling; no caching, job timeout, concurrency group, or pre-commit verification | Build | `.github/workflows/ci.yml`; `interfaces/cli/__init__.py:265-275` | HIGH | B |
| PROD-22 | Runs are unattributable: nothing stamps model IDs, route-table hash, prompt versions, index version, Leggie version, or seed onto output | App | no manifest emitted anywhere | HIGH | B |
| PROD-23 | Ruff ignore list disables `F821` (undefined name), `F401`, `I001` — real bug detectors switched off with a stale "out of scope for this PR" justification | Build | `pyproject.toml [tool.ruff.lint] ignore` | HIGH | C |
| PROD-24 | No `LICENSE` file despite MIT declared in `pyproject.toml` and a README badge linking to it; the code is effectively unlicensed. Version declared in 4 places | Build | `ls LICENSE*` → absent; `pyproject.toml`, `setup.py`, `leggie/__init__.py:3`, `settings.py:146` | HIGH | C |
| PROD-25 | README materially overstates status (199 tests vs 578; 5,195 lines vs 10,392; "verifies every citation"; "durable event spine"; "Phases 0–5 ✅ Complete") | Docs | measured 2026-07-29 | HIGH | C |
| PROD-26 | `reasoner.autostart` defaults to **true** — a CLI that silently spawns an external service, with a history of orphaned processes | Config | `settings.py:114-116` | HIGH | B |
| PROD-27 | No data-handling posture: no provider/retention statement, no zero-retention routing configured, no no-legal-advice disclaimer on output | Docs+Infra | absent | HIGH | C |

### Medium

| # | Defect | Layer | Evidence | Severity | Class |
|---|---|---|---|---|---|
| PROD-28 | `with_cache` is `functools.lru_cache` applied to a coroutine function — it would cache coroutine objects and raise "cannot reuse already awaited coroutine" on the second hit. Currently **exported but never applied**, so it is a latent trap, not an active bug | Infra | `llm/decorators.py:34-36`; only usage is the re-export at `llm/__init__.py:28,183` | MEDIUM | B |
| PROD-29 | Shared `RateLimiter` registered at `container.py:163` is consumed by nothing; `LLMAdapter` constructs its own at `llm/__init__.py:135`, so the limit is not process-global (benign today because `LLMAdapter` is itself a container singleton, but it is a trap the moment a second adapter exists). **Correction, 2026-07-29:** an earlier draft claimed the limiter serialises all calls to 1 in flight. That was wrong — `acquire()` releases its lock before the HTTP call is issued (`openrouter.py:52` then `:80`), so it only paces *admission* at 5/s and requests do overlap. The real defect is that 5 req/s is hardcoded and unconfigurable — tracked as PROD-38 | Infra | `container.py:163`; `llm/__init__.py:130-136`; `rate_limiter.py:21-30` | MEDIUM | A |
| PROD-30 | `BudgetGuard.COST_PER_1M_TOKENS` + `_estimate_cost` are a dead duplicate price table superseded by `domain/pricing.py`; two price sources will diverge | Infra | `budget_guard/__init__.py:44-57,117-120` | MEDIUM | B |
| PROD-31 | `OpenRouterProvider` docstring advertises "Prompt caching via OpenRouter server-side caching" as a feature; the body only *reads* `cached_tokens` and never requests caching | Infra | `openrouter.py:25` vs `:67-78` | MEDIUM | C |
| PROD-32 | `ReasonerServerManager` is constructed eagerly at `container.py:207` via `register_instance(factory())`, contradicting the adjacent comment claiming laziness | Infra | `container.py:203-207` | MEDIUM | B |
| PROD-33 | No machine-readable output: `analyze` has no `--json`; no `--log-level`/`--quiet`; 21 `print()` calls bypass logging | Interfaces | `interfaces/cli/__init__.py` | MEDIUM | B |
| PROD-34 | Repo hygiene: no `SECURITY.md`/`CONTRIBUTING.md`/`CHANGELOG.md`/`CODEOWNERS`/templates; 17 overlapping plan docs with no status headers; generated artifacts (`parsed.json`, `eval_results.json`, `e2e_test_results.json`, `analysis_report.md`) committed | Docs | `git ls-files` | MEDIUM | C |

### Throughput and latency (added 2026-07-29)

Context for the whole group: concurrency primitives appear in exactly **three** places in 101 modules — `orchestrator.py:117` (TaskGroup over lenses), `orchestrator.py:240` (gather over articles), `bill_overview.py:81` (TaskGroup over articles). `asyncio.to_thread` appears **zero** times. The lens stage is parallel; everything downstream of it is not. Smoke v2/v3 both died after 35–45 minutes *in the skeptic/CoVe stage* (`docs/SMOKE_AUDIT.md`), which is precisely where the serialisation lives.

| # | Defect | Layer | Evidence | Severity | Class |
|---|---|---|---|---|---|
| PROD-35 | `CoVeVerifier.verify_batch` is a strictly sequential `for` loop — each finding's full 4-step CoVe loop (several LLM calls) runs to completion before the next begins. Findings are independent; nothing justifies the serialisation | App | `services/cove_verifier.py:144-146` | HIGH | A |
| PROD-36 | `CalibratedSkeptic.review` is a strictly sequential `for` loop — each finding runs the whole gate chain including `LLMAdversarialGate` on the premium `adversarial_critic` route (`gemini-2.5-pro`, 8192 max_tokens of which ~2000 are reasoning tokens) before the next starts | App | `agents/skeptic.py:181-182`; route in `config/routes.yaml` | HIGH | A |
| PROD-37 | `BlackboardAggregator.aggregate` imposes four full barriers (post→dedup, dedup→rerank, rerank→skeptic, skeptic→CoVe). Article 1's findings sit idle until article 121's last lens returns. Only **dedup** genuinely needs a barrier — it collapses duplicates *across* articles; skeptic and CoVe are per-finding independent and should pipeline | App | `services/blackboard_aggregator.py:89-145` | HIGH | A |
| PROD-38 | Concurrency ceilings are half-hardcoded and mostly unreachable from config: `article_sem` uses `settings.llm.max_concurrency` (5) but the inner lens semaphore stays at `_DEFAULT_MAX_CONCURRENT = 10` because `bill_analysis_flow.py:94-100` never passes `max_concurrent`; the rate limiter is fixed at 5 req/s with no setting. Effective in-flight ≤ 10, then capped again at 5/s | App+Infra+Config | `orchestrator.py:40-41,66-67,216`; `bill_analysis_flow.py:94-100`; `rate_limiter.py:15-17`; `llm/__init__.py:135` | HIGH | B |
| PROD-39 | Nothing guarantees output is invariant under completion order. `deduplicate(..., keep="highest_confidence")` has no stated tie-breaking rule, so raising concurrency could silently change which of two equal-confidence findings survives — a determinism regression disguised as an optimisation | Domain+App | `domain/clustering/`; called `blackboard_aggregator.py:205-210` | HIGH | A |
| PROD-40 | No throughput measurement exists. `pytest-benchmark` is a declared dev dependency and `.benchmarks/` exists, but nothing runs or gates on timing, and no stage-level wall-clock is recorded, so no speed claim in this plan can be verified after the fact | Tests+App | `pyproject.toml` dev extras; `Timer` in `observability/__init__.py:90-107` is unused | MEDIUM | C |

---

## 2. Paradigm and pattern assignment per module

The plan is constrained by the repo's existing pattern map (`leggie-architecture-contract` §5) and by non-negotiable #3: **no new methods on existing ports — new behavior rides on new adapters, decorators, or new ports.** Every item below obeys that.

| Module | Paradigm | Pattern applied | Why this and not something else |
|---|---|---|---|
| `domain/` | Pure functional; frozen Pydantic value objects | Value Object, Specification | Already correct. Hooks deny edits to `domain/models/`; this plan touches domain only via **new** modules (`domain/manifest.py`), never by editing existing models |
| `domain/pricing.py` | Pure functions over a frozen table | Value Object (`ModelPrice`) | Single source of truth; PROD-30 deletes the duplicate imperative table in `budget_guard` |
| `infrastructure/llm/transport` (new) | Resource-owning object with explicit lifecycle | **Adapter** + pooled-client **Singleton scoped to the DI container** (not a module global) | A module-global client is untestable and leaks across event loops. Container-scoped keeps the composition root authoritative (PROD-14) |
| `infrastructure/llm/decorators.py` | Async-safe imperative shell | **Decorator** chain (unchanged) + **credit-reservation Token Bucket** guarded by `asyncio.Lock` | Fixes PROD-08 without changing `LLMPort`. Reserve an estimate before the await, reconcile to actuals after — the classic reserve/settle pattern |
| `infrastructure/llm/prompt_safety.py` (new) | Declarative policy over text | **Decorator** on `LLMPort` (`PromptHardeningDecorator`) + **Strategy** for the quarantine policy | PROD-13. Slots into the existing decorator stack between StructuredOutput and BudgetGuard; zero changes to lenses or ports |
| `infrastructure/resources.py` (new) | — | **Facade** over `importlib.resources` + **Strategy** (packaged vs user-writable vs explicit override) | PROD-07. One resolver, injected via the container; no caller ever writes a relative literal again |
| `infrastructure/persistence/sqlite_*.py` (new) | Append-only log; imperative shell over pure domain events | **Repository** + **Event Sourcing**; new adapters for the **existing** `EventBusPort` / `StatePort` | PROD-06. SQLite + WAL is the documented intent and is stdlib-only. Ports keep their current signatures |
| `infrastructure/ingest/` | — | **Factory** (existing, keep) + **Decorator** (`BoundedIngestor` wrapping any `Ingestor`) + `asyncio.to_thread` offload | PROD-16. Limits are cross-cutting; a decorator applies them uniformly to all four formats without touching any ingestor |
| `infrastructure/observability/` | — | **Facade** (`get_logger`) + **Null Object** for unconfigured library use | PROD-09/10. `configure_logging()` becomes idempotent and container-invoked so the Python API is covered |
| `application/services/cove_verifier.py`, `agents/skeptic.py` | Structured concurrency over independent units | **Bounded fan-out** (`asyncio.gather` + `Semaphore`) mirroring the orchestrator's existing idiom, not a new mechanism | PROD-35/36. Both batch methods iterate independent findings. Reusing the orchestrator's TaskGroup/semaphore shape keeps one concurrency idiom in the codebase rather than two |
| `application/services/blackboard_aggregator.py` | Dataflow over barriers | **Pipeline** for per-finding stages + a single retained **barrier** for the genuinely cross-cutting one (dedup) | PROD-37. The Blackboard/Observer substrate is kept — rounds still post to the board; what changes is that skeptic→CoVe becomes one per-finding chain instead of two synchronised sweeps |
| `application/services/run_manifest.py` (new) | Immutable assembly | **Builder** (assemble step-by-step, freeze at the end) + **Observer** on `EventBusPort` | PROD-22. Observer means the manifest accumulates without any stage knowing it exists — no pipeline coupling. Also the natural home for stage wall-clock (PROD-40) |
| `infrastructure/rate_limiter.py` | — | **Token Bucket** (existing, keep) made settings-driven | PROD-38. The pacing logic is correct; only its hardcoded rate is not |
| `application/ports/manifest_sink.py` (new) | ABC port | **Port** (new port is permitted; adding a method to an existing port is not) | Keeps manifest persistence in infrastructure |
| `interfaces/cli/` | — | **Command + Mediator** (existing, keep) + **Strategy table** mapping exception type → exit code + **Template Method** for redacted error rendering | PROD-19/33. Keeps the interface layer thin per the contract |
| `config/` | Declarative data | Settings-as-data + a **reflection test** asserting every field is referenced in `leggie/` | PROD-11. Mechanical prevention beats review discipline |
| `tests/` | — | **Fixture-as-guard** (autouse hermetic fixture) + **cassette/VCR** for transport contract tests | PROD-01/18 |

---

## 3. Phase 0 — Safety net (no behavior change)

**Goal:** make it impossible for the test suite to spend money, and stop shipping legally/technically broken packaging metadata. Nothing here changes pipeline behavior, so it needs offline gates only.

**Class:** C (tests/docs/build) except PROD-17 which is B.
**Estimated effort:** ~4 days.

| Item | Change | Layer |
|---|---|---|
| PROD-01 | Restore `tests/conftest.py` from `cdc3abc` as an **autouse** fixture blanking `llm.openrouter_api_key` and `reasoner.api_key` and resetting the `_settings` singleton per test. Add `pytest-socket` (or an autouse `httpx` monkeypatch) so any outbound connection raises. Add one test that asserts the guard is armed | Tests |
| PROD-17a | Delete `asyncio` from `[project].dependencies` — it is the abandoned PyPI backport, not stdlib | Build |
| PROD-24 | Add `LICENSE` (MIT, matching `pyproject.toml`). Delete `setup.py` (fully redundant). Single-source the version: `leggie/__init__.py` reads `importlib.metadata.version("leggie")`; `Settings.app_version` derives from it | Build |
| PROD-23a | Remove `F821`, `F401`, `I001` from the ruff ignore list and fix any fallout. Narrowing the list is explicitly permitted; the guardrail hook only denies **widening** it | Build |
| — | Fix `mypy` error at `infrastructure/parse/integrity.py:37` (`list` missing type params) before that file is committed | Infra |
| PROD-25 | README truth pass: correct test count, LOC, port count, roadmap status; replace "verifies every citation" and "durable spine" with accurate statements pointing at this plan | Docs |
| PROD-34a | `.gitignore` the generated artifacts and `git rm --cached` them; add status headers to superseded docs | Docs |

**Tests:** `pytest` with a populated `.env` present must make **zero** outbound connections — asserted by the socket guard, not by inspection. Full suite still 578+ green. `ruff`, `mypy`, `lint-imports`, `bandit` clean.

---

## 4. Phase 1 — The money path

**Goal:** the $5 cap becomes a real ceiling rather than an advisory one, and every call is cheap and attributable. Non-negotiable #4 stands: **the cap is never raised to make anything pass.**

**Class:** A (touches the LLM adapter) — requires live smoke at the end of the phase.
**Estimated effort:** ~1 week.

| Item | Change | Layer | Pattern |
|---|---|---|---|
| PROD-08 | Convert `BudgetGuardDecorator` to **reserve → await → settle**: under an `asyncio.Lock`, reserve the estimated cost against the ceiling and return a reservation handle; after the call, settle to actuals (release the delta) in a `finally`. Concurrent callers can no longer all pass an unreserved check | Infra | Decorator + credit-reservation Token Bucket |
| PROD-14 | Extract transport into a container-owned pooled `httpx.AsyncClient` with explicit connect/read/write/pool timeouts from settings; add `Retry-After` honoring on 429; truncate and redact upstream bodies before they enter exception messages or logs | Infra | Adapter; container-scoped resource |
| PROD-15 | Replace `len//4` token counting with a real tokenizer (or reconcile strictly against reported usage before the next reservation); set `tier_used` from the route/cascade decision that actually selected the model | Infra | — |
| PROD-30 | Delete `BudgetGuard.COST_PER_1M_TOKENS` and `_estimate_cost`; `domain/pricing.py` becomes the sole price source | Infra+Domain | Value Object |
| PROD-29 | Consume the container's shared `RateLimiter` in `LLMAdapter` instead of constructing a private one; move the `sleep` outside the lock so the limiter throttles without serializing | Infra | — |
| PROD-28 | Delete `with_cache` (unused, and `lru_cache` over a coroutine function is a trap), or reimplement as an async-correct cache decorator if caching is actually wanted | Infra | Decorator |
| PROD-31 | Either request OpenRouter prompt caching for real or delete the claim from the `OpenRouterProvider` docstring — do not leave a documented feature that does not exist | Infra | — |
| PROD-12 | Resolve the premium-model conflict to one value across `settings.py`, `config/routes.yaml`, and the offline allowlist; add a startup validation that fails fast when a route names a model absent from the allowlist | Config+Infra | — |

**Tests:** unit test proving that N concurrent calls against a ceiling of 1 call's worth admit exactly one and raise `BudgetExceededError` for the rest; test that `Retry-After` is honored; test that an upstream 500 body never reaches the exception message verbatim; test that `tier_used` reflects the cascade decision. Then a **cheap single-lens live smoke** confirming spend is recorded accurately and the run completes.

**Guardrail:** this phase must not change findings output. Compare findings JSON before/after on the same bill — differences are a regression, not an improvement.

---

## 4b. Phase 1b — Throughput and latency

**Goal:** make the pipeline as parallel as its data dependencies allow. Today the lens stage fans out and everything after it is a single-file queue.

**Class:** A. `PROD-35/36/37/39` change execution order and therefore risk changing output; `PROD-38/40` are B/C but ride along.
**Estimated effort:** ~1 week.

### Why this must come *after* Phase 1, not before

Two hard sequencing constraints, both load-bearing:

1. **Cost.** PROD-08 (budget `check()`→`await`→`record_usage()` race) overshoots the ceiling by up to `max_concurrency` calls. Parallelising CoVe and the skeptic multiplies in-flight calls on the *premium* tier. Landing throughput work first converts a bounded cost bug into a proportionally larger one, against a non-negotiable ($5 cap).
2. **Rate limits.** Higher in-flight counts will draw 429s from OpenRouter, and `openrouter.py:88-90` currently raises without honouring `Retry-After`. Without PROD-14 the speedup manifests as a failure rate.

Phase 1 closes both. This phase is worthless — actively harmful — before it.

### Work items

| Item | Change | Layer | Pattern |
|---|---|---|---|
| PROD-35 | Replace the `for` loop in `verify_batch` with a bounded `asyncio.gather` over per-finding `verify()` calls, concurrency from a new `settings.llm.max_verification_concurrency`. Preserve input ordering in the returned `list[CoVeResult]` — `gather` already does, but assert it | App | Bounded fan-out |
| PROD-36 | Same treatment for `CalibratedSkeptic.review`: fan out `examine(finding)` under a semaphore, then fold verdicts in **input order** so the `model_copy` confidence adjustments and the survivor list are order-stable | App | Bounded fan-out |
| PROD-37 | Restructure `aggregate()`: keep the post→dedup barrier (cross-article, cheap, no LLM) and the dedup→rerank step (`CompositeReranker` is pure scoring — instant), then run **skeptic→CoVe as one per-finding pipeline** so a finding enters CoVe as soon as its own skeptic verdict lands, instead of waiting for the whole skeptic sweep. Blackboard rounds and events are preserved; only the scheduling changes | App | Pipeline + retained barrier |
| PROD-38 | Pass `max_concurrent` from settings in `bill_analysis_flow.py:94-100` instead of leaving it at the hardcoded default; add `settings.llm.max_rate_per_second` and feed it to the container's `RateLimiter`; document the interaction between the three ceilings (article × lens × rate) in `.env.example` so tuning is not guesswork | App+Infra+Config | Token Bucket, settings-driven |
| PROD-16b **(promoted from Phase 5)** | Move blocking ingest work (`pdfplumber`, `read_text`, `python-docx`, `BeautifulSoup`) onto `asyncio.to_thread`. Promoted here because it is a throughput fix, not a safety fix — the safety half of PROD-16 (`BoundedIngestor`, lxml hardening) stays in Phase 5 | Infra | — |
| PROD-39 | Give `deduplicate` an explicit, total tie-break (e.g. confidence desc, then `finding.id` asc) so equal-confidence duplicates resolve identically regardless of arrival order. Add the determinism regression test described below | Domain+App | Pure function, total ordering |
| PROD-40 | Record per-stage wall-clock through the existing unused `observability.Timer` and surface it in the run manifest (PROD-22): ingest, parse, lens, dedup, rerank, skeptic, CoVe, report. Add one `pytest-benchmark` case over a fixture-backed offline pipeline and gate CI on a regression threshold | App+Tests | Timer + benchmark gate |

### Expected payoff

Order-of-magnitude, from the smoke-run shape (299 LLM calls single-lens; ~600–900 projected for 5 lenses):

| Change | Effect |
|---|---|
| PROD-35 + PROD-36 | Skeptic and CoVe go from Σ(latency) to ≈Σ/C. At 50 survivors × ~8 s and C=10: **~400 s → ~40 s** |
| PROD-37 | Removes the wait-for-slowest-article tail between stages |
| PROD-14 pooled client (Phase 1) | ~300–900 calls × ~150 ms handshake = **45–135 s** recovered |
| PROD-16b | Event loop no longer stalls for the duration of a PDF parse |
| PROD-38 | Unlocks all of the above; without it the ceilings cap the gain |

These are projections, not measurements. PROD-40 exists precisely so the next revision of this table can be replaced with measured numbers.

**Tests:**
- **Determinism (the gate that matters):** the same bill, same seed, run at concurrency 1 and at concurrency 10, must produce byte-identical findings JSON. This is the acceptance test for the whole phase — a speedup that changes output is a regression.
- Bounded fan-out honours its semaphore: assert peak in-flight never exceeds the configured ceiling.
- `verify_batch` and `review` return results in input order under out-of-order completion (simulate with a fake LLM whose latency is inversely proportional to index).
- One finding raising inside the fan-out isolates to that finding — the batch still returns the rest, with a `DEGRADED` event (non-negotiable #6).
- Live smoke: single-lens run before/after, comparing wall-clock **and** findings JSON. Speed must improve; output must not move.

---

## 5. Phase 2 — Deployability

**Goal:** Leggie runs correctly from any directory, in a container, on its real target OS, with configuration that either works or does not exist.

**Class:** B.
**Estimated effort:** ~1 week.

| Item | Change | Layer | Pattern |
|---|---|---|---|
| PROD-07 | Add `infrastructure/resources.py`: a `ResourceLocator` facade resolving (a) packaged read-only resources (`config/routes.yaml`, `data/citation_index.json`) via `importlib.resources`, (b) user-writable paths via an explicit `--output-dir` or a platform app-data directory. Ship `config/` and `data/` as package data. Replace all four hardcoded literals; `CascadeSettings.rules_path` becomes the authoritative override | Infra | Facade + Strategy |
| PROD-11 | For each of the ~14 dead settings: wire it or delete it. Concretely — wire `ingest.max_file_size_mb` (Phase 3), `cascade.rules_path`, `persistence.*` (Phase 4), the global `seed`; delete `retrieval.*`, `ingest.temp_dir`, `ingest.ocr_enabled`, `cascade.free/budget/premium_model`, `cascade.confidence_floor`, `cascade.premium_fallback_enabled`, `budget.degrade_*` unless a phase claims them. Add the reflection test that fails when a `Settings` field is unreferenced in `leggie/` | Config | Settings-as-data + reflection test |
| PROD-26 | Default `reasoner.autostart` to `false`; document manual start as the supported path; keep the PR #7 `finally` shutdown | Config |  — |
| PROD-32 | Make `ReasonerServerManager` genuinely lazy — `register`, not `register_instance(factory())` | Infra | Lazy factory |
| PROD-20 | Dockerfile: runtime stage installs runtime deps only from the lockfile; drop `COPY tests/`; add a non-root `USER`; pin base by digest; non-editable install; add `HEALTHCHECK` and OCI `LABEL`s; declare a volume or require `--output-dir`; add `.dockerignore` | Build | — |
| PROD-21 | CI: add `windows-latest` to the matrix as a **required** job (it is the primary target); add pip caching, a job timeout, a `concurrency` group, and a `pre-commit run --all-files` step | Build | — |
| PROD-17b | Generate and commit a hash-pinned lockfile; add `pip-audit` to CI and a Dependabot/Renovate config | Build | — |

**Tests:** `cd <anywhere> && leggie parse <bill>` produces byte-identical output to a repo-root run. `docker run --rm leggie parse …` succeeds as a non-root user and persists output to a mounted volume. CI green on Windows **and** Linux. The settings reflection test passes with zero unreferenced fields.

---

## 6. Phase 3 — Observability and process contract

**Goal:** every run is legible, attributable, and terminates predictably.

**Class:** B.
**Estimated effort:** ~1 week.

| Item | Change | Layer | Pattern |
|---|---|---|---|
| PROD-09 + PROD-10 | Mechanically replace stdlib `logging` with `observability.get_logger` across all 20 modules; convert `extra={...}` call sites to structlog keyword form so fields actually render. Make `configure_logging()` idempotent and invoke it from the composition root so library use is covered; add a **Null Object** logger for unconfigured contexts | App+Infra | Facade + Null Object |
| PROD-22 | Add `domain/manifest.py` (frozen `RunManifest` value object — a **new** domain module, not an edit to `domain/models/`), `application/services/run_manifest.py` (**Builder**, subscribed to `EventBusPort` as an **Observer**), and a new `ManifestSinkPort` + JSON adapter. Emit `Outputs/<stem>_manifest.json` capturing Leggie version, git SHA, resolved model per call site, route-table hash, prompt-template hashes, citation-index version, seed, per-tier token/cost totals, and wall-clock | Domain+App+Infra | Value Object + Builder + Observer + Port |
| PROD-19 | Wrap `main()` in a handler mapping exception type → documented exit code via a **Strategy table** (e.g. 0 ok, 2 config error, 3 budget exceeded, 4 degraded parse refused, 5 provider unavailable, 6 interrupted). Full detail to the log; a redacted actionable line to stderr. Install `SIGINT`/`SIGTERM` handlers that flush the checkpoint and mark the run `WORKFLOW_FAILED` | Interfaces | Strategy + Template Method |
| PROD-33 | Add `--json` to `analyze`/`preview`, and `--log-level`/`--quiet`; route the 21 `print()` calls through a single presenter so output is redirectable | Interfaces | Presenter |
| PROD-11 (seed) | Thread the global `seed` from settings into lens construction instead of `getattr(self,"_seed",0)` | App | — |

**Tests:** a smoke run emits a manifest whose `route_table_hash` changes when `routes.yaml` changes; an `llm.call` record round-trips with model/tokens/cost/latency intact (asserted on captured log output, not by eye); each documented exit code is produced by a test; `SIGINT` mid-run leaves a valid checkpoint.

---

## 7. Phase 4 — Durable audit spine

**Goal:** make the event-sourcing claim true. A legal conclusion that cannot be replayed is not auditable.

**Class:** B (adapters only — no port signature changes, per non-negotiable #3).
**Estimated effort:** ~1.5 weeks.

| Item | Change | Layer | Pattern |
|---|---|---|---|
| PROD-06a | `infrastructure/persistence/sqlite_event_store.py` — a durable `EventBusPort` adapter: append-only table `(run_id, seq, event_type, aggregate_id, payload_json, ts)`, SQLite in WAL mode, single writer, `seq` monotonic per run. Keep `InMemoryEventBus` for tests | Infra | Repository + Event Sourcing |
| PROD-06b | `infrastructure/persistence/sqlite_state_store.py` — durable `StatePort` adapter for `WorkflowState` and stage checkpoints, superseding `InMemoryStateStore` in `configure_defaults()` | Infra | Repository |
| PROD-06c | Wire `PersistenceSettings.url` / `wal_mode` / `echo`; add lightweight schema versioning (a `schema_version` table + forward-only migration function) | Config+Infra | — |
| PROD-06d | Add `leggie replay <run_id>` — reconstructs findings from the persisted event log, with a `--verify` mode that diffs against the stored findings JSON | Interfaces+App | Command (CQRS handler) |

**Tests:** a completed run replays in a **fresh process** to byte-identical findings JSON. Concurrent appends from the parallel fan-out preserve per-run `seq` monotonicity under `asyncio.gather`. A `SIGINT`-interrupted run replays to the last completed stage.

---

## 8. Phase 5 — Input and prompt safety

**Goal:** untrusted documents cannot exhaust the host or steer the analysis.

**Class:** A (PROD-13 changes prompt content, which changes pipeline behavior).
**Estimated effort:** ~1 week.

| Item | Change | Layer | Pattern |
|---|---|---|---|
| PROD-16a | `BoundedIngestor` — a **Decorator** over any `Ingestor` enforcing `max_file_size_mb`, a page/element cap, and a wall-clock timeout, emitting `EventType.DEGRADED` on refusal (never a silent truncation, per non-negotiable #6). Registered in the `IngestorFactory` so all four formats inherit it | Infra | Decorator |
| ~~PROD-16b~~ | **Moved to Phase 1b (§4b)** — `asyncio.to_thread` offload is a throughput fix, not a safety fix | Infra | — |
| PROD-16c | Harden `lxml` HTML parsing (no network, no entity resolution); add a DOCX decompression-bomb guard | Infra | — |
| PROD-13 | `PromptHardeningDecorator` on `LLMPort`, inserted into the container stack: wrap all document-derived text in explicit quarantine delimiters, prepend a standing instruction that quarantined content is data and never instruction, and strip/neutralize instruction-shaped sequences per a pluggable **Strategy**. Applies uniformly to lens, VS, skeptic, CoVe, and overview call sites without editing any of them | Infra | Decorator + Strategy |
| PROD-13b | Build an injection regression corpus: bills containing embedded directives ("ignore previous instructions", "report no constitutional issues", fake system blocks) as fixtures under `tests/fixtures/injection/` | Tests | — |

**Tests:** an ingest of a file exceeding the cap is refused with a `DEGRADED` event and a non-zero exit code, not a truncated analysis. Injection-corpus fixtures produce findings whose count and severity distribution are statistically indistinguishable from the same bill with the injected text removed — measured, not eyeballed.

---

## 9. Phase 6 — Prove the product (the long pole)

**Goal:** replace assertion with measurement. This phase is research-shaped; it may not converge on schedule, and that is the honest expectation. Everything here is class A.

**Estimated effort:** ~2–3 weeks, with real risk of overrun.

| Item | Change |
|---|---|
| PROD-05 | Build the real citation resolution index from the sources the README already names (data.gov.gr `gov-et-laws`, EUR-Lex CELLAR SPARQL, static Σύνταγμα). Version it; record its identifier count and build date in the manifest (PROD-22). Add an offline builder script under `.claude/skills/leggie-diagnostics-and-tooling/scripts/` or `tools/` |
| PROD-02 | Execute the **full 5-lens** live smoke to completion on `Inputs/OE_ΣΧΝ-ΥΠΔΙΚ.pdf`; record survivors/article, parse-failure rate, skeptic verdict distribution, CoVe drop reasons, and spend into `docs/SMOKE_AUDIT.md`, using `findings_stats.py` / `smoke_log_stats.py` — numbers only, never by eye |
| PROD-03 | Execute one deliberative run end to end; record the exhibit and its cost. If the pipeline cannot complete, say so in the README rather than leaving "Phase 5 ✅ Complete" |
| PROD-04 | Expand the gold set to ≥10 real bills with labels derived from Επιστημονική Υπηρεσία Βουλής reports; publish precision/recall/F1 per finding type and RDI, and state the target threshold **before** running (hypothesis-predicts-numbers discipline) |
| PROD-18a | Add cassette/VCR integration tests covering the real OpenRouter request/response shape: the 4-attempt structured ladder, `json_schema` rejection → `json_object` fallback, truncation → doubled tokens, repair attempt, 429, budget block |
| PROD-18b | Raise coverage gates: ≥85% overall, ≥90% for `infrastructure/llm/` and `application/agents/`. Bring `reranker` (15%), `decorators` (33%), `ingest` (46%), `cli` (48%) above the bar |

**Tests / evidence:** this phase *is* the evidence. Its output is `docs/SMOKE_AUDIT.md` sections and an eval report, not code.

---

## 10. Phase 7 — Release engineering and legal posture

**Class:** C.
**Estimated effort:** ~3 days.

| Item | Change |
|---|---|
| PROD-17c | Tag-triggered workflow: build wheel + sdist, run the full gate set, generate an SBOM (CycloneDX), sign artifacts, publish |
| PROD-34b | Add `CHANGELOG.md` (Keep-a-Changelog), `SECURITY.md` (disclosure contact + supported versions), `CONTRIBUTING.md`, `CODEOWNERS`, issue/PR templates |
| PROD-27 | Add `docs/DATA_HANDLING.md`: which providers receive bill text via OpenRouter, retention posture, and how to configure provider allowlisting / zero-retention routing. Configure that policy explicitly in the request payload rather than accepting defaults. Add a no-legal-advice disclaimer to CLI output and to the header of every generated report |
| PROD-34c | Move superseded plans to `docs/archive/` with status headers; refresh the two stale skill facts identified in §0 |

---

## 11. Execution order and dependencies

```
Phase 0  Safety net ─────────────────────────────────────┐
  PROD-01 hermetic tests   (gates everything below)      │
  PROD-17a asyncio dep     PROD-24 license/version       │
  PROD-23a ruff narrow     PROD-25 README truth          │
         │                                               │
         ├──────────────┬───────────────┐                │
         ▼              ▼               ▼                │
Phase 1 money path   Phase 2 deploy   Phase 3 observ.    │
  PROD-08 reserve      PROD-07 resources  PROD-09/10 logs│
  PROD-14 transport    PROD-11 settings   PROD-19 exits  │
  PROD-15 tokens       PROD-20 docker     PROD-33 --json │
  PROD-12 models       PROD-21 CI-win     PROD-22 manifest
  PROD-28/29/30/31     PROD-26/32/17b        │           │
         │                    │              │           │
         ▼                    │              │           │
Phase 1b THROUGHPUT           │              │           │
  HARD DEP on Phase 1:        │              │           │
   PROD-08 (cost overshoot    │              │           │
   scales with concurrency)   │              │           │
   PROD-14 (429 Retry-After)  │              │           │
  PROD-35 CoVe fan-out        │              │           │
  PROD-36 skeptic fan-out     │              │           │
  PROD-37 pipeline stages     │              │           │
  PROD-38 ceilings→settings   │              │           │
  PROD-16b to_thread ingest   │              │           │
  PROD-39 determinism         │              │           │
  PROD-40 stage timing ───────┼──────────────┤           │
   (needs PROD-22 manifest    │              │           │
    as its sink)              │              │           │
         │                    │              │           │
         │  (PROD-11 wires persistence ──────┼───┐       │
         │   settings, needed by Phase 4)    │   │       │
         └──────────┬─────────────────────────┘   │       │
                    ▼                             ▼       │
              Phase 4 durable spine  ◄────── PROD-22 manifest
                PROD-06 a/b/c/d              (Observer needs
                    │                         the event bus)
                    ▼
              Phase 5 input + prompt safety
                PROD-16a/c, PROD-13 a/b
                    │
                    ▼
              Phase 6 PROVE  ◄── needs Phase 1 (accurate spend),
                PROD-05 index      Phase 1b (a 5-lens run that
                PROD-02 5-lens       finishes in tolerable time),
                PROD-03 deliber.   Phase 2 (runs anywhere),
                PROD-04 gold set   Phase 3 (measurable logs),
                PROD-18 coverage   Phase 5 (safe inputs)
                    │
                    ▼
              Phase 7 release  PROD-17c, PROD-27, PROD-34
```

Phases 1, 2 and 3 are independent of one another and can run in parallel once Phase 0 lands. **Phase 1b is strictly gated on Phase 1** for the two reasons stated in §4b — it is the one hard serialisation in this plan. PROD-40 additionally wants PROD-22's manifest as its output sink; if Phase 3 has not landed, emit stage timings to the log and backfill the manifest field later rather than blocking.

**Phase 6 must be last** — proving quality on a build that cannot run outside the repo root, misreports its own spend, and discards its telemetry would produce evidence about a system nobody is going to ship. Phase 1b earns its place before Phase 6 for a practical reason too: the three full 5-lens attempts that have died so far died *on the clock*, in the stage this phase parallelises.

**Rough total: 7.5–8.5 weeks** for one engineer, with Phase 6 carrying the schedule risk. These figures are order-of-magnitude estimates from change surface, not from measured historical velocity in this repo.

---

## 12. Architecture guardrails — apply to every phase

Binding for all work in this plan. Violating any of these makes the change class A regardless of what it touches.

1. **Dependency rule.** Interfaces → Infrastructure → Application → Domain → Config. `lint-imports` must pass on every commit. New modules go in the layer that owns their concern: `ResourceLocator` and the SQLite stores are Infrastructure; `RunManifestBuilder` is Application; `RunManifest` is Domain.
2. **Domain models are frozen.** No edits to `leggie/domain/models/` — a guardrail hook denies them. `RunManifest` therefore lands as a **new** module `domain/manifest.py`, not as an addition to `domain/models/__init__.py`.
3. **No new methods on existing ports.** Every capability in this plan arrives as a new adapter (SQLite stores), a new decorator (budget reservation, prompt hardening, bounded ingest), or a genuinely new port (`ManifestSinkPort`). `LLMPort`, `EventBusPort`, `StatePort`, `IngestPort` keep their current signatures.
4. **The $5 cap is the governor and is never raised.** PROD-08 makes it enforceable; it does not change its value. A guardrail hook denies edits to `max_cost_per_run`.
5. **The ruff ignore list only ever shrinks.** PROD-23 narrows it. Nothing in this plan may add to it — if new code trips a rule, fix the code.
6. **No silent failure.** Every new degradation path (ingest refusal, budget reservation denial, prompt-hardening rejection, replay mismatch) emits `EventType.DEGRADED` or a logged warning with a counted signature.
7. **Structured output only.** Any new LLM interaction validates against a Pydantic schema in `domain/models/structured_output.py`.
8. **Green tests are never sufficient for class A.** The founding incident of this repo was a 199-green-test MVP that never called an LLM. Phases 1, 1b, 5 and 6 require live-smoke numbers.
9. **Plans before code, audits after.** Each phase closes with an audit doc using the house template (compliance matrix + severity findings + verdict), landing at repo root per convention.
10. **Behavior-preserving phases must prove it.** Phases 0–4 must not change findings output; compare findings JSON on a fixed bill before and after.
11. **Concurrency must not be observable in the output.** Any change to scheduling — fan-out width, stage pipelining, semaphore ceilings — must leave findings JSON byte-identical for a fixed bill and seed. Determinism is a stated design principle of this project ("deterministic = pipeline structure, prompts, seeds, cache keys, report assembly"); parallelism is an implementation detail and must stay one. A speedup that moves output is a regression, and the phase does not close.
12. **Never buy speed by widening the money ceiling.** Throughput work raises in-flight call counts, which raises peak spend rate. The response is a correct reservation (PROD-08), never a larger `max_cost_per_run`.

---

## 13. Definition of done — measurable only

A v1.0 tag is defensible when every line below is true and backed by a recorded artifact.

**Evidence (Phase 6)**
- [ ] Full 5-lens run completes on a real bill: findings/article ≥ 0.10, parse-failure signatures < 5% of LLM calls, ≥1 non-neutral skeptic verdict, CoVe `dropped=True` only on `cove_quote_fail`, spend < $5 — recorded in `docs/SMOKE_AUDIT.md`
- [ ] ≥1 deliberative run recorded with its cost, or the README status corrected to match reality
- [ ] Gold set ≥10 real bills; F1 published per finding type against a threshold stated in advance; RDI within a stated band
- [ ] Citation index ≥10,000 identifiers with a documented build pipeline; measured resolution rate published (not "unverified" for all)
- [ ] A completed run replays in a fresh process to byte-identical findings JSON

**Engineering**
- [ ] `pytest` makes 0 outbound connections with a populated `.env`, asserted by a socket guard in CI
- [ ] Coverage ≥85% overall; ≥90% for `infrastructure/llm/` and `application/agents/`
- [ ] `ruff` passes with `F821`, `F401`, `I001` **removed** from the ignore list; `mypy --strict` clean over `leggie/` **and** `tests/`
- [ ] CI green on `windows-latest` (required) and `ubuntu-latest`; `lint-imports`, `bandit`, `pip-audit` all pass
- [ ] Settings reflection test reports 0 unreferenced `Settings` fields
- [ ] Concurrency test: N simultaneous calls against a 1-call ceiling admit exactly 1; measured spend never exceeds `max_cost_per_run`
- [ ] `cd <arbitrary dir> && leggie parse <bill>` byte-identical to a repo-root run; `docker run` succeeds as non-root with persisted output

**Throughput (Phase 1b)**
- [ ] Same bill + same seed at concurrency 1 and concurrency 10 produce **byte-identical** findings JSON
- [ ] `verify_batch` and `review` return input-ordered results under deliberately out-of-order completion
- [ ] Peak in-flight LLM calls never exceed the configured ceiling (asserted, not assumed)
- [ ] A single failing finding inside a fan-out isolates: batch returns the remainder plus a `DEGRADED` event
- [ ] Run manifest carries per-stage wall-clock for all 8 stages; a `pytest-benchmark` case gates CI against timing regression
- [ ] Measured before/after wall-clock recorded in `docs/SMOKE_AUDIT.md` — the §4b payoff table replaced with real numbers
- [ ] Zero settings-invisible concurrency constants remain in `orchestrator.py` / `rate_limiter.py`

**Operations**
- [ ] Every LLM call emits model, prompt/completion/cached tokens, cost, latency, finish reason as **structured fields** verifiable in captured log output
- [ ] Every run emits a manifest sufficient to attribute any finding to model + prompt version + route-table hash + index version
- [ ] Every documented exit code is produced by a test; `SIGINT` leaves a valid checkpoint

**Release and legal**
- [ ] `LICENSE` present and consistent; version single-sourced; signed wheel published from a tag with an SBOM and lockfile
- [ ] `DATA_HANDLING.md` published; provider retention policy configured explicitly in the request payload
- [ ] No-legal-advice disclaimer on CLI output and every generated report
- [ ] README contains no claim that a recorded measurement does not support

---

## 14. Out of scope — target B (hosted service)

Not started, and deliberately not planned here. If Leggie becomes a service, the following is net-new beyond everything above: HTTP interface, authN/authZ, tenancy, per-tenant quotas and cost attribution (the budget guard is per-process), an async job queue (smoke runs took 35–45 minutes pre-Phase-1b, and even a parallelised run will exceed any reasonable HTTP timeout), upload validation and AV scanning, tenant-isolated storage, OTel/Prometheus export with SLOs and alerting, backup/restore and DR, and deployment manifests with managed secrets. Rough order: **8–12 additional weeks**. It should not begin until Phase 6 has demonstrated that the analysis quality is worth hosting.
