# ARCH-AUDIT-V2: Leggie (Re-audit)

> **Prior audit:** 2026-07-09 (`ARCH-AUDIT-V2.md`) — 56 files · 5,239 LOC · 272 tests
> **This audit:** 2026-08-10 — 118 files · 12,181 LOC · 62 test files · 734 passed / 1 skipped · 82.70% cov
> **Protocol:** EGFV. `[VERIFIED]` = read/grepped this pass. `[UNKNOWN]` = input not provided.

Input gate: README/pyproject/CI present. **No ADR directory exists** — `[UNKNOWN — ADRs not provided]` applies to every design-rationale claim below; IMPL-6 already names this gap. Dockerfile present, no compose/k8s.

---

## Phase 1 — Architectural Fingerprinting

**DETECTED ARCHITECTURE: Clean/Hexagonal, Ports & Adapters, Event-Sourced Workflow DAG — unchanged since 2026-07-09, one layer added.**

1. [VERIFIED] Six-layer dependency tree, not four: `interfaces → infrastructure → application → domain → observability → config` (`pyproject.toml:126-133`). `observability` was split out of `infrastructure/` on 2026-08-05 (task #8-11) because it only ever imported `config` — it was never real infrastructure.
2. [VERIFIED] 11 ports in `application/ports/`, not 7 as the prior audit and README both state. `grep -rn "class.*Port" leggie/application/ports/*.py` → Blackboard, CitationParser, EventBus, Ingest, LLM, Parse, Reasoner, Reranker, Retrieval, Router, State. `ReasonerPort` is new since 2026-07-09 (deliberative pipeline).
3. [VERIFIED] Layer contract is enforced but was **silently vacuous until 2026-08-05**: `application/{agents,cqrs,services,workflow}` and `infrastructure/llm/adapters` lacked `__init__.py`, so grimp's module graph saw 74 of 118 modules and the layers contract passed over every real violation (pyproject.toml:134-141, task #1-3). Fixed — grimp now sees the full graph.
4. [VERIFIED] `infrastructure/llm/` was decomposed as the prior audit's HIGH-IMPACT item demanded: `adapters/openrouter.py`, `structured_parser.py`, `decorators.py`, `ladder.py`, `__init__.py` — no more 371-line monolith. Fixed this session's generics work touched exactly these files.
5. [VERIFIED] `pydantic-settings`, `env_prefix="LEGGIE_"`, no hardcoded secrets (unchanged from prior audit, not re-swept this pass).

---

## Phase 2 — Compliance Matrix

| Module | Detected | Intended | Drift | Violations | Severity | Evidence |
|---|---|---|---|---|---|---|
| `domain/` | Pure, frozen Pydantic | Pure functional | ✅ None | — | — | [VERIFIED] unchanged |
| `application/ports/` | 11 ABC interfaces | Ports & Adapters | ⚠️ | 1 dead port (`RetrievalPort`) | MEDIUM | Phase 5 |
| `application/workflow/` | FSM + flow + checkpoint hook | State + Template Method | ⚠️ | Composition-root leaks via `ignore_imports` | HIGH | Phase 3 |
| `application/cqrs/handlers/cli_handlers.py` | Imports 10 infrastructure modules directly | Mediator handlers, composition root elsewhere | ❌ | 10 of 13 whitelisted layer violations live here | HIGH | pyproject.toml:150-159 |
| `application/services/blackboard_aggregator.py` | 4 hardcoded rounds (dedup→rerank→skeptic→CoVe) | Blackboard + Observer, extensible rounds | ⚠️ | Rounds are inline, not a Strategy list | MEDIUM | `blackboard_aggregator.py:75-134` |
| `infrastructure/llm/` | adapter/parser/decorator/ladder split | Adapter + Decorator | ✅ Resolved | — | — | prior audit's HIGH-IMPACT item closed |
| `infrastructure/citation/` | fail-closed resolver, empty index by default | Adapter | ⚠️ | D7: always reports "unverified" — correct but toothless | LOW | `citation/__init__.py:112-126` |
| `infrastructure/retrieval_adapter.py` | file-glob stub, DI-registered | Ports & Adapters | ❌ | Zero call sites outside port/adapter/container | MEDIUM | Phase 5 |
| `observability/` | own layer below domain | Cross-cutting, not infra | ✅ Resolved | — | — | task #8-10, ARCH-03 drained |
| `interfaces/cli/` | thin, dispatches via mediator | Thin imperative | ✅ None | — | — | unchanged |
| Root `requirements.txt` | header claims `pip-compile` output | Pinned, hashed lockfile | ❌ | 9 loose `>=` bounds, no transitive pins, no hashes | HIGH | see Phase 3 |

---

## Phase 3 — Dependency & Coupling Analysis

- [VERIFIED] **13 layer-contract violations are whitelisted, not fixed.** `pyproject.toml:148-163`, `ignore_imports`, all labeled `ARCH-04 — removed by Phase 2 (composition-root construction moved to container.py)`. 10 of the 13 are `cli_handlers.py` importing infrastructure directly (citation, container, ingest, llm.base, parse, checkpoint_store, eval_harness, sqlite_event_store, reasoner.adapter, reasoner.server_manager); the other 3 are `bill_analysis_flow.py → checkpoint_store` and `ingest_parse.py → {ingest_adapter, parse_adapter}`. The comment names its own fix ("Phase 2") — this is IMPL-1, currently open. Severity **HIGH**: not a design choice, a self-declared debt item with the fix already planned but not executed.
- [VERIFIED] `unmatched_ignore_imports_alerting` defaults to `"error"` (pyproject.toml:140) — a real safeguard: if any of the 13 entries stop matching (i.e., someone fixes one), the contract fails until the entry is pruned. The baseline cannot silently rot upward, only downward.
- [VERIFIED] No circular dependencies. Domain has 0 outward imports (unchanged, re-confirmed via `application-ports`/`domain-purity` forbidden contract at pyproject.toml:165-177, which additionally forbids `domain → observability` even though the layers contract would permit it).
- [VERIFIED] **Dockerfile build-reproducibility claim is false.** `Dockerfile:12-14`: "Install runtime dependencies only from lockfile" → `COPY requirements.txt`. But `requirements.txt` itself (16 lines) claims to be `pip-compile` output while containing only 9 direct deps at loose `>=` bounds, zero transitive pins, zero hashes. A real `pip-compile --resolver=backtracking` run on this dependency set would produce 40-100+ pinned, hashed lines. Every image build resolves against whatever's latest-compatible at build time — this is exactly IMPL-5 ("Lockfile + SBOM"), and it's a supply-chain-relevant gap, not cosmetic: the file's own header is inaccurate. Severity **HIGH**.
- [VERIFIED] No SBOM generation anywhere — not in `ci.yml`, not in `release.yml` (both read this session for the mypy-in-CI change), no cyclonedx/syft step.

---

## Phase 4 — AI Orchestrator Review

- [VERIFIED] Orchestration centralized (`BillAnalysisFlow.run()`), routing (`StaticRouter`/`RouterPort`) and provider details (`OpenRouterProvider` behind `LLMPort`) stay isolated — unchanged from prior audit.
- [VERIFIED] Async consistent; concurrency bounded per-run via `asyncio.Semaphore` — but **every semaphore is instance-scoped, not global**: `orchestrator.py:66,218`, `skeptic.py:191`, `cove_verifier.py:152`, `bill_overview.py:41` each construct their own semaphore per `BillAnalysisFlow`/`Orchestrator` instance, sized from `LEGGIE_LLM__MAX_CONCURRENCY` (`config/settings.py:26`). **No process- or machine-wide governor exists.** Two concurrent `leggie analyze` invocations each independently spend up to `max_concurrency` — the ceiling is per-run, not per-host. This is IMPL-3 exactly as scoped, and matches the prior audit's Primary Risk #1 ("single-process bottleneck") which was never actually closed, only reworded. Severity **MEDIUM** (real under batch/CI use, not under interactive single-run use).
- [VERIFIED] D9 (rate limiter) is genuinely closed, not "likely" as the architecture-contract skill hedges: `adapters/openrouter.py:49,59` — `RateLimiter(max_rate=5.0)` constructed in `__init__`, `.acquire()` awaited before every call.
- [VERIFIED] D7 (citation resolution) is genuinely still open: `citation/__init__.py:117-126` — fail-closed by design when no index is supplied, and nothing in the live flow calls `build_resolution_index()` before `resolve()`. Confirmed correct-but-toothless, unchanged from the architecture-contract skill's 2026-07-14 note.
- [VERIFIED] Failure semantics: retry via `with_retry` (now correctly typed, this session's fix), fallback via `StaticRouter.cascade()`, partial-failure isolation via `return_exceptions=True` in `analyze_document()` (D6, closed 2026-07-11 — the prior audit's [HYPOTHESIS] "could isolate lens failures" IMMEDIATE item is done).
- [N/A] FastAPI/Redis/Docker stack checks: no FastAPI app in this repo (Reasoner is an external HTTP service, `ReasonerAdapter` is a plain client); no Redis; Docker exists (see Phase 3) but is a single CLI container, not a service topology — "are service boundaries reflected in container boundaries" doesn't apply to a single-binary CLI image.

---

## Phase 5 — Anti-Pattern Detection

1. **[VERIFIED] Premature abstraction, now concrete not hypothetical — `RetrievalPort` is dead code.** Prior audit called this "acceptable, Phase 2-3 planned" at LOW severity. It is no longer prospective: `SimpleRetrievalAdapter` exists (`retrieval_adapter.py`, a file-glob stub), is registered in `container.py:200-202`, and **has zero call sites** outside the port/adapter/container triangle itself (`grep -rn "RetrievalPort|SimpleRetrievalAdapter" leggie/` returns only those 3 files + `ports/__init__.py` re-export). No lens, no flow, no CLI command ever calls `.search()`/`.get_document()`/`.corpus_stats()`. This is IMPL-2. Severity raised to **MEDIUM** — a maintained, wired, but unreachable abstraction costs real reading/maintenance time for zero behavior.
2. **[VERIFIED] Composition-root leakage — `cli_handlers.py` imports infrastructure directly**, whitelisted rather than fixed (Phase 3). This is the modern form of the prior audit's LOW-severity "layer leak" finding (`bill_analysis_flow.py` importing `IngestorFactory` directly) — except it has since grown from 2 tolerated imports to 13 formally exempted ones, and moved from "arguably fine, imperative shell" to "self-declared debt with a named fix." Severity **HIGH**, upgraded from the prior audit's LOW.
3. **[VERIFIED] Orchestrator bottleneck, still present** — `BillAnalysisFlow` (740 lines, still under the 800-line project cap) remains the single convergence point for ingest/parse/aggregate/checkpoint. Unchanged assessment: acceptable for single-bill MVP, real under concurrent multi-bill load. Severity **MEDIUM**, same as before.
4. **[VERIFIED] Aggregation logic is a fixed pipeline, not a Strategy** — `BlackboardAggregator.aggregate()` (209 lines) hardcodes 4 sequential rounds inline (dedup → rerank → skeptic → CoVe) rather than iterating a list of pluggable stage objects. Every new aggregation step requires editing this method rather than registering a new stage. This is IMPL-4. Severity **LOW-MEDIUM** — works correctly today, costs flexibility for tomorrow.
5. **[VERIFIED] Supply-chain gap** — the `requirements.txt`/Dockerfile mismatch from Phase 3 is also an anti-pattern in its own right: a comment asserting a build guarantee ("only from lockfile") that the referenced file does not provide. Severity **HIGH**.

### Not detected (re-verified absent)
- [VERIFIED] No God module — largest is `bill_analysis_flow.py` at 740 lines, under the 800-line project ceiling.
- [VERIFIED] No circular imports, no domain leakage, no shared-database coupling — same as prior audit.
- [VERIFIED] Lens failure isolation, LLM module decomposition, and the rate limiter — all three prior-audit gaps — are closed.

---

## Phase 6 — Executive Summary

### ARCHITECTURE SCORE: 6 / 10
*(down from the prior audit's 8/10 — not because the architecture regressed, but because this pass found the prior audit's own LOW findings had hardened into self-declared, dated HIGH-severity debt items with names (ARCH-04, IMPL-1..5) and a fake-lockfile issue the prior audit never looked for.)*

Two HIGH-severity items now exist that didn't in July: 13 whitelisted composition-root violations, and a Dockerfile lockfile claim that's false on inspection. Rubric requires ≤2 HIGH findings and no CRITICAL for an 8; this audit has 3 HIGH (composition-root leak, fake lockfile, and — arguably — the RetrievalPort dead port once you count "shipped but unreachable" as more than cosmetic) and one open MEDIUM carried since July (cross-run concurrency). That places it at 6.

### MATURITY LEVEL: Early Production
Test/type/lint gates are all green (734 passed, 82.70% cov, ruff clean, `mypy leggie/` + `mypy tests/` clean, import-linter 2/2 contracts pass — with the caveat that one of those two contracts passes via 13 whitelisted exceptions, not zero violations). Docker packaging exists but its dependency-pinning story doesn't match what it claims.

### PRIMARY RISKS (ranked)
1. **Composition-root exceptions (13, `ARCH-04`)** — the contract can't currently distinguish "acceptable imperative shell" from "not yet refactored"; every one is dated and named as intended-to-be-temporary. [VERIFIED]
2. **False lockfile guarantee** — Docker builds are not actually reproducible despite the Dockerfile's own comment claiming they are. [VERIFIED]
3. **No cross-run concurrency governor** — per-run semaphores don't compose under concurrent invocations. [VERIFIED]
4. **`RetrievalPort` dead abstraction** — maintained surface area, zero reachable behavior. [VERIFIED]
5. **Citation resolution permanently toothless (D7)** — every citation reports "unverified" because no code path ever populates a resolution index; correct-by-design fail-closed behavior, but it means the CoVe/citation-verification story is currently vacuous in production runs. [VERIFIED]

### CRITICAL VIOLATIONS
None.

### REFACTOR URGENCY: Next Sprint
**Justification:** Nothing here blocks a feature today — all gates are green and the whitelist mechanism (`unmatched_ignore_imports_alerting="error"`) prevents silent rot. But three items (composition-root, lockfile, dead port) are self-labeled debt with named fixes already scoped as IMPL-1/2/5 — deferring further just accumulates interest on debt the project has already agreed to pay down.

---

## Phase 7 — Refactoring Roadmap

**IMMEDIATE**
- **[Phase 3, HIGH]** → Run `pip-compile --resolver=backtracking --generate-hashes --output-file=requirements.txt` for real, wire it into a CI check that fails if `requirements.txt` drifts from `pyproject.toml` → Dockerfile's "from lockfile" claim becomes true. (IMPL-5, narrow slice.)

**HIGH-IMPACT (next sprint)**
- **[Phase 3/5, HIGH]** → IMPL-1: inject the 10 `cli_handlers.py` infrastructure dependencies through the container instead of importing them directly; do the same for the 3 flow/ingest_parse entries → drains all 13 `ignore_imports`, contract passes with zero exceptions instead of 13.
- **[Phase 5, MEDIUM]** → IMPL-2: either wire `RetrievalPort` into a real call site (e.g., a citation-context or precedent-lookup use) or delete `SimpleRetrievalAdapter` + the DI registration + the port itself → no port should exist with zero callers.
- **[Phase 4, MEDIUM]** → IMPL-3: add a governor above the per-run semaphores (module-level `asyncio.Semaphore` sized from a new `LEGGIE_LLM__GLOBAL_MAX_CONCURRENCY`, or a lock file / OS-level mutex for the CLI's single-host case) → concurrent CLI invocations stop competing for the same OpenRouter rate budget blind to each other.
- **[Phase 2/5, LOW-MEDIUM]** → IMPL-4: extract the 4 aggregation rounds in `BlackboardAggregator.aggregate()` into a `list[AggregationStage]` iterated in order → new rounds register instead of requiring an edit to `aggregate()`.
- **[Phase 5, LOW]** → D7: either wire a real resolution index (e.g., from the bill's own citation set via `build_resolution_index()`) into the live flow, or rename the finding from "unverified" to something that doesn't imply verification was attempted, since today it never is.

**LONG-TERM**
- Target-state unchanged from the prior audit for genuine multi-bill scale (task queue, Redis/Postgres-backed budget + event store) — nothing this pass found changes that trajectory; it's still correctly deferred.
- **New long-term item**: write the ADRs IMPL-6 calls for — this audit exists because none do, and every "by design" judgment call above (fail-closed citations, whitelisted composition root, dead RetrievalPort as "future phase") is currently only recoverable by reading commit messages and skill files, not a durable record.

**SWITCHING TRIGGERS**
- Same as prior audit: >10 concurrent bills forces the task-queue migration; EUR-Lex CELLAR integration is the trigger that would finally give `RetrievalPort` a reason to exist (don't delete it without checking this first).

---

Score dropped 8→6 not from regression but resolution: closing the prior audit's easy items (LLM split, lens isolation, rate limiter) left the harder, previously-hedged ones — composition root, the lockfile, the dead port — standing alone and unhedged.
