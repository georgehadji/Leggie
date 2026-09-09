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
| ~~`application/workflow/ingest_parse.py`~~ | Imported `ingest_adapter`, `parse_adapter` | Should receive ports | **RESOLVED 2026-09-09 (ARCH-05)** | ~~2 whitelisted violations~~ → 0; module deleted, ports injected by the composition root | — | `858de57` |
| `application/workflow/bill_analysis_flow.py` | Imports `checkpoint_store` concretely | Recorded as permanent, not debt | **NO** (severity withdrawn) | 1 whitelisted import, deliberately retained: `CheckpointStore(path)` takes a per-request `--checkpoint-path` a zero-arg container factory cannot supply (IMPL-1 Group B, reasoning inline in `pyproject.toml`). This audit rated it HIGH without engaging that decision | **LOW** | `pyproject.toml` waiver comment [VERIFIED 2026-09-09] |
| `application/workflow/bill_analysis_flow.py` | 830 lines, 26 methods, `run()` spans 223 | Coordinator | **YES** | God module | **MEDIUM** | `wc -l`, method census [VERIFIED] |
| `application/services/run_manifest.py` + `ports/manifest.py` + `infrastructure/manifest_sink.py` | PROD-22 run manifest | Emitted every run | **YES** | **Zero production call sites** — port, builder and `JsonManifestSink` adapter all exist and are unit-tested, but nothing outside those tests constructs a `RunManifestBuilder`, the container binds no `ManifestSinkPort`, and no flow writes a manifest. No run has ever produced one | **MEDIUM** | `grep -rn "RunManifestBuilder\|JsonManifestSink\|Manifest" leggie/` [VERIFIED, corrected 2026-09-09 — see Audit-of-the-audit] |
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
`ingest_adapter` and `parse_adapter` by name. **Corrected 2026-09-09:**
`ingest_adapter`/`parse_adapter` are gone (ARCH-05), leaving five. The claim
that `checkpoint_store` was "the highest-value single fix" is withdrawn — its
two entries are recorded as permanent by design (IMPL-1 Group B), so the
remaining five are not a ranked worklist at all.

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
- ~~**Resume is incomplete** — D10.~~ **FALSE, withdrawn 2026-09-09.**
  `_save_checkpoint` persists the full run state — findings, events, document,
  source_text, suggestions, reports, budget_state — keyed by stage on every
  `_transition()`, and `_load_checkpoint` restores all of it and re-enters at
  the saved stage. `TestResumeAfterCrash::test_resume_after_crash` asserts that
  after a crash at AGGREGATING, `_do_ingest`, `_do_parse`, `decompose` and
  `analyze_document` are each called **0** times on resume and the findings
  match a fresh run. Completed stages are restored and are not re-billed.
  `_load_legacy_budget_checkpoint` — the compat branch for pre-`02c3ac6`
  files — is the only budget-only path, and I mistook it for the live one.

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
  linearly with no partitioning. Resume does work (see the D10 correction
  above), so a failure at minute 50 re-enters at the last completed stage;
  what is missing at 10× is *partitioning*, not resume.
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
| **Premature abstraction** | **NO** (claim withdrawn) | This row asserted `ManifestPort` was a port ABC with zero implementations. That was **false** — `JsonManifestSink` implements it (`infrastructure/manifest_sink.py:17`). The real defect is unwired delivery, not premature abstraction; it is now recorded in the Phase 2 table and below. `BlackboardPort` has exactly one implementation [VERIFIED] — defensible, it is a genuine seam |
| **Declared-done-but-unwired** | **YES** | PROD-22 is marked "✅ Complete" in `docs/implementation_audit_report_phase3.md:34` on the strength of four files existing with five passing tests. No run emits a manifest, so the reproducibility guarantee the plan describes (`PRODUCTION_READINESS_PLAN.md:255`) does not hold for any run performed to date. PROD-40 (stage wall-clock) is blocked behind the same integration [VERIFIED] |
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
stateful 830-line God coordinator that is also the 10× bottleneck; no run
partitioning at 10× scale. (The first published version of this line also
blamed incomplete resume and a dead port; both were my errors — see
Audit-of-the-audit.)

### MATURITY LEVEL: Early Production

[VERIFIED] Infrastructure maturity is Production-grade: signed releases, SBOM,
matrix CI, security scanning. Output maturity is not: the 5-lens 91-article run
has **never completed**, and the verification chain was publishing ~0.02
findings/article until `db14097` two commits ago. The scaffolding is ahead of
the product.

### PRIMARY RISKS (ranked)

1. ~~**Incomplete resume (D10) × no run partitioning.**~~ **WITHDRAWN — the
   resume half is false.** A failure late in a paid run re-enters at the last
   completed stage and does not re-bill it. What remains true, and is a
   materially smaller risk: there is no run *partitioning*, so a single run is
   still all-or-nothing against the $5 cap at full-bill scale. Demoted; it is
   no longer risk #1.
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
#1. **That reasoning is withdrawn**: resume is not broken, and those two
waivers are recorded as permanent by design. The urgency rating stands on the
God module and the cycle contract alone.

---

## Phase 7 — Refactoring Roadmap

### IMMEDIATE (before next feature)

- ~~**[Phase 5 / premature abstraction]** Delete `ManifestPort`.~~ **WITHDRAWN
  2026-09-09 — the premise was false.** `JsonManifestSink` implements the port.
  Deleting it would have destroyed a working, tested feature. The real item is
  the opposite: **wire PROD-22** so a run actually emits
  `Outputs/<run_id>_manifest.json`. Not started — it is a delivery change, not a
  cleanup, so it needs its own decision. PROD-40 rides on the same integration.
- ~~**[Phase 2 / `ingest_parse`]** Route `ingest_parse.py` through the ports.~~
  **DONE 2026-09-09 (ARCH-05).** `ingest_parse.py` deleted; the composition root
  injects `IngestPort`/`ParsePort` at all four production sites; both flows raise
  a named error rather than defaulting. Waivers 7 → 5. The deferral note in
  `pyproject.toml` estimated ~57 test edits; the real cost was 73 call sites
  behind four one-line test helpers, plus two container fixtures.

### HIGH-IMPACT (next sprint)

- ~~**[Risk 1 + Phase 2]** Complete D10 resume behind `StatePort`.~~
  **WITHDRAWN 2026-09-09 — wrong on both halves.** (a) Resume is already
  complete and tested, so there is nothing to "complete". (b) The two
  `checkpoint_store` waivers were deliberately reassessed as **permanent, not
  debt** in IMPL-1 Group B (2026-08-10); the reasoning is recorded inline in
  `pyproject.toml` — `CheckpointStore(path)` takes a per-request runtime value
  (`--checkpoint-path`) that a zero-arg container factory cannot supply. This
  audit proposed deleting those imports without engaging that recorded
  decision at all. `StatePort` is meanwhile fully live — two adapters
  (`SqliteStateStore`, `InMemoryStateStore`), bound in `container.py:237-241`.
  Moving checkpointing onto it would be a redesign of a working feature with a
  user-facing flag, not a waiver cleanup, and needs its own justification.
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

**[2026-09-09] The `ManifestPort` finding was wrong, and it was the audit's
headline IMMEDIATE item.** I wrote "zero implementations anywhere" and marked it
[VERIFIED]. `JsonManifestSink` implements the port at
`infrastructure/manifest_sink.py:17`, has five passing tests, and sits one
directory away. The evidence line said "grep across `leggie/`" — a grep for
`ManifestSinkPort` returns that file on the first screen. The check either was
not run or was run and misread; either way the label was unearned.

Acting on it would have deleted a working feature. The corrected finding inverts
the prescription: PROD-22 is built and unwired, so the fix is to emit the
manifest, not to remove the ability to.

**[2026-09-09] The D10 resume finding was also wrong — and it was risk #1.** I
wrote that "completed stages are not restored: a crash re-runs and re-bills
prior stages" and called it "the single largest cost risk in the design".
`_save_checkpoint` has persisted the full run state since `02c3ac6`
(2026-07-11), and `TestResumeAfterCrash::test_resume_after_crash` — a test that
was already green in the suite I ran for this audit — asserts `analyze_document`
is called **0** times on resume. I read `_load_legacy_budget_checkpoint`, the
pre-`02c3ac6` compatibility branch, and reported it as the live path.

This one is worse than the manifest error, because the correction was already
written down. `docs/DEFECT_HUNT_PLAN.md:169` recorded on **2026-09-05**, four
days before this audit, that D10 is "**not** 'PARTIAL... only budget spend' as
leggie-architecture-contract / leggie-failure-archaeology / this region's own
brief all describe it", citing the same commit and the same test. I inherited
the stale claim from those two skill files, marked it [VERIFIED], and ranked a
non-existent defect as the project's top risk — while a correction sat in the
repo. Both skill files and the debugging playbook are fixed in the same commit
as this note, so the claim stops propagating.

Two of this audit's three top-ranked action items were false, both in the same
way: an absence asserted from a stale secondary source rather than from the
code. A third — "route `checkpoint_store` through `StatePort`" — was not false
but was uninformed: it proposed reversing a decision recorded inline in
`pyproject.toml` without mentioning that the decision existed.

The general lesson, and the reason the rows are left visible rather than quietly
edited: *"has no implementations"* and *"has no callers"* are different claims
needing different greps, and I collapsed them. A port's implementors are found by
searching the port's name; a feature's reach is found by searching its entry
point. Every remaining [VERIFIED] label in this document that rests on absence —
rather than on a command whose output is quoted — carries the same risk and
should be re-checked before anyone deletes anything on its authority.
