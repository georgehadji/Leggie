# ARCH-AUDIT-V2 — Leggie

**Audit date:** 2026-09-09 · **Commit:** `db14097` · **Protocol:** EGFV
**Scope:** 116 Python modules, 13,539 lines under `leggie/`

## Step 0 — Input Gate

| Input | Status |
|---|---|
| Full codebase | PRESENT |
| Entry point | PRESENT — `leggie = "leggie.interfaces.cli:entry_point"` (`pyproject.toml:29`) |
| ADRs | PRESENT — `docs/ADR/0001`–`0008` |
| README / design docs | PRESENT — README, ~30 docs under `docs/` |
| Dependency manifests | PRESENT — `pyproject.toml`, hash-pinned `requirements.txt` |
| Deployment manifests | PARTIAL — `Dockerfile` + `.dockerignore` present; **no compose, no k8s** |
| CI/CD | PRESENT — `.github/workflows/ci.yml`, `release.yml` |

No REQUIRED input missing. Gate passed.

---

## Phase 1 — Architectural Fingerprinting

**DETECTED ARCHITECTURE: Clean/Hexagonal, 6 layers, single-process batch CLI
with CQRS dispatch and an event spine.** [VERIFIED]

Evidence:

1. **Layer declaration is machine-enforced, not aspirational** [VERIFIED] —
   `pyproject.toml` declares an import-linter `layers` contract:
   `interfaces → infrastructure → application → domain → observability → config`.
   `lint-imports` exits 0. Enforcement is real, but see Phase 2 for the
   whitelist that qualifies it.
2. **Ports and adapters** [VERIFIED] — 11 port ABCs in
   `leggie/application/ports/`; adapters live in `leggie/infrastructure/`.
3. **Composition root** [VERIFIED] — `infrastructure/container.py` (323 lines),
   `Container.configure_defaults()`, built once in `_build_mediator()`.
   ADR-0002 records the decision.
4. **CQRS dispatch** [VERIFIED] — `interfaces/cli/__init__.py` → `Mediator` →
   `application/cqrs/handlers/cli_handlers.py`. The CLI holds no business logic.
5. **Event spine** [VERIFIED] — `EventType` enum in `domain/models/__init__.py`;
   the flow appends immutable `Event` records at each stage.

**Layer mass distribution** [VERIFIED]:

| Layer | Files | Lines |
|---|---|---|
| application | 57 | 6,548 |
| infrastructure | 42 | 4,732 |
| domain | 9 | 1,251 |
| interfaces | 2 | 573 |
| config | 2 | 239 |

Application is the centre of gravity at 48% of the code. Correct for this
pattern — orchestration is the product.

**Data flow topology** [VERIFIED]: async/await throughout (179 `async def`,
177 `await`), fan-out via `asyncio.gather` in 3 modules, bounded by
`asyncio.Semaphore` in 5. Push-based, in-process, no queue or broker.

**Configuration** [VERIFIED]: `pydantic-settings`, one class per concern,
`get_settings()` lazy singleton. As of `55fa82c`/`d1db901`, settings is the
single source of truth for every duplicated constant (see
`ssot_audit_report_2026-09-08.md`).

**Secrets** [VERIFIED]: API key via `LEGGIE_LLM__OPENROUTER_API_KEY` only; no
credential literals found. `.env.example` ships placeholders.

---

## Phase 2 — Compliance Matrix

| Module | Detected | Intended | Drift | Violations | Severity | Evidence |
|---|---|---|---|---|---|---|
| `application/cqrs/handlers/cli_handlers.py` | Handler importing infrastructure directly | Application depends inward only | **YES** | 4 whitelisted `application → infrastructure` imports | **HIGH** | `pyproject.toml` `ignore_imports` [VERIFIED] |
| `application/workflow/ingest_parse.py` | Imports `ingest_adapter`, `parse_adapter` | Should receive ports | **YES** | 2 whitelisted violations | **HIGH** | same [VERIFIED] |
| `application/workflow/bill_analysis_flow.py` | Imports `checkpoint_store` concretely | Should use `StatePort` | **YES** | 1 whitelisted violation | **HIGH** | same [VERIFIED] |
| `application/workflow/bill_analysis_flow.py` | 830 lines, 26 methods, `run()` spans 223 | Coordinator | **YES** | God module | **MEDIUM** | `wc -l`, method census [VERIFIED] |
| `application/ports/manifest.py` | `ManifestPort` | Port with adapter | **YES** | **Zero implementations** | **MEDIUM** | grep across `leggie/` [VERIFIED] |
| `application/agents/lens.py` ↔ `services/lens_vs.py` | Mutual dependency | Acyclic | **YES** | Cycle, deferred by function-local import at `lens.py:145` | **LOW** | AST scan [VERIFIED] |
| `domain/` | Pure | Pure | NO | none | — | `domain-purity` contract [VERIFIED] |
| `infrastructure/llm/` | Adapter + 5 decorators behind `LLMPort` | Ports/adapters | NO | none | — | 6 implementors [VERIFIED] |
| `interfaces/cli/` | Thin, dispatches via mediator | Thin | NO | none | — | 573 lines, no domain logic [VERIFIED] |

### CRITICAL finding: the layer contract passes on a waiver

[VERIFIED] `lint-imports` reports "2 contracts kept, 0 broken" — the line I have
been quoting all session — **but the layers contract carries seven
`ignore_imports` entries, every one an `application → infrastructure` import.**
That is the core dependency rule, violated in three modules, whitelisted:

```
cli_handlers      -> infrastructure.parse
cli_handlers      -> infrastructure.persistence.checkpoint_store
cli_handlers      -> infrastructure.persistence.eval_harness
cli_handlers      -> infrastructure.reasoner.server_manager
bill_analysis_flow-> infrastructure.persistence.checkpoint_store
ingest_parse      -> infrastructure.ingest_adapter
ingest_parse      -> infrastructure.parse_adapter
```

Mitigating, and it matters [VERIFIED]: the baseline is **itemised, commented
with the phase that will remove each entry, and cannot rot** —
`unmatched_ignore_imports_alerting` defaults to `error`, so a stale entry fails
the build. This is a managed debt register, not a silenced check. It is still
seven live violations of the architecture's central rule.

---

## Phase 3 — Dependency and Coupling Analysis

**Circular dependencies** [VERIFIED]: exactly one in the whole graph —
`application.agents.lens → application.services.lens_vs → application.agents.lens`.
Broken at runtime by a function-local import (`lens.py:145`). No import-time
failure; the design smell stands (a base class knowing a specific strategy).
Severity LOW. There is **no `independence` or cycles contract** in
import-linter, so nothing prevents the next cycle. [VERIFIED]

**Layer leaks** [VERIFIED]: the seven entries above. Concretely — the
application layer knows `checkpoint_store`, `eval_harness`, `server_manager`,
`ingest_adapter` and `parse_adapter` by name. `checkpoint_store` appears twice,
from two different modules, which makes it the highest-value single fix:
routing it through `StatePort` removes 2 of 7.

**Shared mutable state** [VERIFIED]:
- `get_settings()` module-level singleton, mutated only by `reload_settings()`.
  Test-visible but not concurrently mutated.
- `IngestorFactory.bounds` is a mutable class attribute. As of `d1db901` it is
  overrides-only and empty by default, so the blast radius is a deliberate test
  seam. [VERIFIED]
- `BudgetState` is a mutable dataclass inside `BudgetGuard`, single-owner,
  single-process. Safe today; would need a lock under threading. [HYPOTHESIS]

**Coupling hotspots** [VERIFIED]:
- `bill_analysis_flow.py` — highest efferent coupling in the codebase. It
  imports orchestrator, blackboard aggregator, CoVe, skeptic, rerank, reports,
  improver, budget guard, checkpoint store, state machine.
- `container.py` — highest afferent by design. Correct for a composition root
  (ADR-0002).

**Boundary violations** [VERIFIED]: none domain-side. `domain-purity` is a
`forbidden` contract with no waivers, and it holds.

---

## Phase 4 — AI Orchestrator Review

**ORCHESTRATION MODEL**

- Centralised [VERIFIED]. `BillAnalysisFlow.run()` drives ingest → parse →
  orchestrator fan-out → aggregation → verification → rerank → reports.
  `DeliberativeFlow` is a documented sibling (ADR-0005), not a second engine.
- Routing is separated from business logic [VERIFIED] — `RouterPort` +
  `StaticRouter` reading `config/routes.yaml`; task→tier→model is declarative.
- Provider details isolated [VERIFIED] — `OpenRouterProvider` behind
  `BaseLLMProvider` behind `LLMPort`. Retry, cache and budget are decorators
  (`infrastructure/llm/decorators.py`), not port methods. Six `LLMPort`
  implementors, all adapters or decorators.

**ASYNC AND CONCURRENCY**

- Consistent [VERIFIED]. Grep for `time.sleep`, `requests.`, bare `open()` in
  async paths outside `ingest/` returns **nothing**. Blocking extraction is
  explicitly moved off-loop via `run_off_loop` (DH-10).
- Bounded [VERIFIED]. Three semaphores govern article fan-out, skeptic review
  and CoVe verification; all three now read ceilings from `LLMSettings`
  (`55fa82c`). A token-bucket `RateLimiter` caps request rate.
- **Backpressure is partial** [VERIFIED]. Semaphores bound in-flight work, but
  `run_off_loop` spawns one unbounded daemon thread per call with no cap —
  documented as a `ponytail:` ceiling in `ingest/base.py`. Safe at one file per
  run; unsafe if ingest is ever batched. Severity LOW today, HIGH on that change.

**STATE AND CONTEXT**

- Explicit [VERIFIED]. `article_index: dict[str, str]` is threaded into CoVe;
  no implicit ambient context, no global conversation object.
- Workflow state transitions only through `FlowStateMachine` [VERIFIED].
- **Resume is incomplete** [VERIFIED] — D10. `checkpoint_store.py` exists and
  `_save_checkpoint`/`_load_checkpoint`/`_load_legacy_budget_checkpoint` are
  implemented, but completed stages are not restored: a crash re-runs and
  re-bills prior stages. This is the single largest cost risk in the design.

**FAILURE SEMANTICS**

- Retry defined and layered [VERIFIED] — `with_retry` decorator, plus a
  4-attempt structured-output ladder (`llm/ladder.py`) with a truncation retry
  that doubles `max_tokens`.
- Fallback routing present [VERIFIED] — `RouterPort.cascade()` escalates
  FREE→BUDGET→PREMIUM; `--fallback` degrades deliberative to deterministic.
- Partial failure isolated [VERIFIED] — `asyncio.gather(..., return_exceptions=True)`
  per article; skeptic and CoVe fail open and log rather than crash the run.
- Budget circuit breaker [VERIFIED] — `BudgetGuard` blocks past the $5 cap.

**TOOL EXECUTION** — [N/A] No tool-calling layer. The LLM is used for
structured generation only.

**SCALABILITY BOTTLENECKS**

- Single point most likely to fail at 10× [VERIFIED]: **`BillAnalysisFlow`
  itself.** It is a stateful, single-process coordinator holding the entire
  findings list and event log in memory for the whole run. A 10× bill (910
  articles × 5 lenses) multiplies in-memory findings, LLM calls and wall-clock
  linearly with no partitioning and no resume — and D10 means a failure at
  minute 50 restarts from zero.
- **The orchestrator is NOT stateless** [VERIFIED] — `self._findings`,
  `self._events`, `self._state`, `self._overview` are instance state.
  Appropriate for a batch CLI, disqualifying for a service.

**Stack-specific checks**

- FastAPI — [N/A] not used by Leggie. The external Reasoner backend is
  `uvicorn asgi:app`, out of scope; Leggie is an HTTP client to it
  (`ReasonerAdapter`) with lifecycle managed by `ReasonerServerManager` and
  shut down in a `finally` (PR #7).
- Redis — [N/A] not present. Persistence is SQLite/WAL.
- Docker — [VERIFIED] single-stage-built, multi-stage Dockerfile, pinned base
  image by digest, `--require-hashes` install, non-root layer separation,
  entry-point smoke-verified at build. One container for one CLI: service and
  container boundaries match because there is one service.

---

## Phase 5 — Anti-Pattern Detection

| Pattern | Detected | Evidence | Severity |
|---|---|---|---|
| **God module** | **YES** | `bill_analysis_flow.py` — 830 lines, 26 methods, `run()` 223 lines; owns ingest, parse, filtering, 3 aggregation strategies, checkpointing, events, state, dedup, reports [VERIFIED] | MEDIUM |
| **Orchestrator bottleneck** | **YES** | every execution path routes through `BillAnalysisFlow.run()`; stateful, single-process [VERIFIED] | MEDIUM |
| **Premature abstraction** | **YES** | `ManifestPort` — a port ABC with **zero implementations** anywhere in `leggie/` [VERIFIED]. `BlackboardPort` has exactly one [VERIFIED] — defensible, it is a genuine seam |
| **Infrastructure leakage into application** | **YES** | the 7 whitelisted imports (Phase 2) [VERIFIED] | HIGH |
| **Temporal coupling** | **YES** | verification chain order is load-bearing and undeclared in types: rerank MUST run after skeptic+CoVe because they rewrite `Finding.confidence`. Guarded only by `test_verification_chain_order.py` [VERIFIED] | MEDIUM |
| **Anemic domain model** | NO | domain carries real logic — `clustering.deduplicate`, `pricing.estimate_cost`, `Confidence.from_score`, parse-integrity specs [VERIFIED] |
| **Hidden monolith** | NO | it is an honest monolith; no microservice veneer claimed [VERIFIED] |
| **Shared database coupling** | NO | one SQLite store, one process [VERIFIED] |
| **Overengineering** | PARTIAL | 11 ports for a single-binary CLI is a lot of ceremony, but ADR-0002/0004 show the criteria are applied deliberately, and `RetrievalPort` was deleted when it went dead [VERIFIED] |
| **Underengineering** | NO | boundaries exist where they matter |

---

## Phase 6 — Executive Summary

### ARCHITECTURE SCORE: 7 / 10

Between rubric 6 ("moderate drift, 1–2 high-severity violations") and 8 ("minor
drift in 1–2 modules, no critical violations"). It sits at 7 because the
violations are **known, itemised and rot-proof** rather than discovered by this
audit — but there are seven of them in the central rule, across three modules,
which is more than "1–2 modules".

What earns the score: domain purity enforced with zero waivers; ports/adapters
applied with real discipline (decorators not port methods; a dead port actually
deleted); consistent async with no blocking calls in async paths; 934 tests;
CI with mypy strict, ruff, bandit, pip-audit and an 85% coverage floor;
hash-pinned, digest-pinned container build; eight ADRs recording *why*.

What costs it: application→infrastructure leakage in three modules; a
stateful 830-line God coordinator that is also the 10× bottleneck; incomplete
resume (D10) that makes any long-run failure expensive; one dead port.

### MATURITY LEVEL: Early Production

[VERIFIED] Infrastructure maturity is Production-grade: signed releases, SBOM,
matrix CI, security scanning. Output maturity is not: the 5-lens 91-article run
has **never completed**, and the verification chain was publishing ~0.02
findings/article until `db14097` two commits ago. The scaffolding is ahead of
the product.

### PRIMARY RISKS (ranked)

1. **[VERIFIED] Incomplete resume (D10) × no run partitioning.** A failure late
   in a paid run discards all prior work and re-bills from zero. The $5 cap
   makes this a hard stop, not a slowdown.
2. **[VERIFIED] `BillAnalysisFlow` is stateful and total.** Every path goes
   through it; it holds the full findings list in memory; it cannot be
   horizontally split without a rewrite.
3. **[VERIFIED] Seven application→infrastructure violations.** Each one is a
   place where swapping an adapter requires editing application code — the exact
   cost the architecture exists to avoid.
4. **[HYPOTHESIS] Verification-chain quality is under-determined.** Two gates
   were found in three days silently destroying valid findings (DH-37 citations,
   DH-42 quotes and refutations). Both were invisible without live runs. Others
   may remain; the offline suite cannot see them.
5. **[VERIFIED] Temporal coupling in the verification chain** is enforced only
   by a test, not by types or structure.

### CRITICAL VIOLATIONS

**None.** No security boundary violation, no systemic failure risk, no
credential exposure. The Phase 2 HIGH findings are architectural debt with a
managed register, not CRITICAL by this rubric's definition.

### REFACTOR URGENCY: Next Sprint

The seven layer violations are stable, itemised and cannot silently expand, so
nothing is on fire. But two of them name `checkpoint_store`, which is also risk
#1 — fixing resume and removing those imports is the same work, and it is the
change that most reduces the cost of every subsequent live run.

---

## Phase 7 — Refactoring Roadmap

### IMMEDIATE (before next feature)

- **[Phase 5 / premature abstraction]** Delete `ManifestPort` → removes a port
  ABC with zero implementations → the port list stops lying about the system's
  seams. Precedent: `RetrievalPort` deletion, ADR-0004. If it is a placeholder
  for planned work, an ADR should say so.
- **[Phase 2 / `ingest_parse`]** Route `ingest_parse.py` through `IngestPort`
  and `ParsePort` instead of importing both adapters → removes 2 of 7 waivers →
  the two most mechanical entries on the register.

### HIGH-IMPACT (next sprint)

- **[Risk 1 + Phase 2]** Complete D10 resume behind `StatePort`: persist
  completed stages, restore on load, and delete the direct
  `checkpoint_store` imports from `bill_analysis_flow` and `cli_handlers` →
  removes 2 more waivers **and** closes the largest cost risk in one change.
- **[Phase 5 / God module]** Extract from `BillAnalysisFlow`: (a) the three
  `_aggregate_*` strategies into an aggregation strategy object — ADR-0007
  already names the trigger; (b) checkpoint I/O into the store; (c) article
  selection (`_filter_document`, `_select_article_ids`, `_leading_number`) into
  a small domain service. Target under 400 lines. Expected outcome: `run()`
  reads as a stage sequence.
- **[Phase 2 / `cli_handlers`]** Resolve `eval_harness` and `server_manager`
  from the container rather than importing them → removes the last 2 waivers →
  contract passes with an empty `ignore_imports`.

### LONG-TERM

**Target state:** the same hexagon, with `BillAnalysisFlow` reduced to a stage
sequencer over a persisted run record, so a run is resumable, inspectable and
partitionable by article range.

Migration sequence (dependency-ordered):

1. `StatePort`-backed stage persistence *(prerequisite for everything below)* —
   risk: LOW, additive, existing store.
2. Extract aggregation strategy — risk: MEDIUM, `test_verification_chain_order.py`
   is the safety net and must stay green.
3. Article-range partitioning of a run — risk: MEDIUM, changes the event log's
   shape; needs the parse-integrity gate to hold per partition.
4. Only then, if ever, a service front end — risk: HIGH, requires the
   coordinator to be stateless, which steps 1–3 are the precondition for.

Do **not** attempt 4 before 1–3. The current coordinator's instance state makes
any service wrapper a correctness hazard.

### SWITCHING TRIGGERS

- **Ingest becomes batched or concurrent** → `run_off_loop`'s unbounded daemon
  threads need the semaphore its own comment specifies.
- **A second bill format or jurisdiction** → the parse layer's Greek-specific
  regex constants stop being a detail and need a strategy boundary.
- **Any multi-user or hosted use** → stateless coordinator becomes mandatory,
  and `BudgetGuard`'s mutable single-owner state needs locking.
- **A live citation register lands (EUR-Lex CELLAR)** → arms
  `authoritative_schemes` (ADR-0008) and reopens `RetrievalPort` (ADR-0004).
- **Runs routinely exceed the $5 cap** → partitioning stops being an
  optimisation and becomes the execution model.

---

## Audit-of-the-audit

[VERIFIED] I reported "lint-imports 2/2 kept" in five commit messages and
several summaries during this session without stating that the layers contract
carries seven waivers. The claim was true and incomplete. Corrected here.
