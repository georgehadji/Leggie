# Architecture Remediation Plan — 6/10 → 9+/10

**Baseline:** ARCH-AUDIT-V2, 2026-08-05, score 6/10 ("moderate drift, 1–2 high-severity violations, scalability concerns").
**Target:** >9/10 ("all layers correctly separated, patterns consistent, observable, testable, scalable").
**Scope:** `leggie/`, `pyproject.toml`, `.github/workflows/ci.yml`. No domain-model changes (change-control rule 2).

Findings carry permanent IDs (`ARCH-nn`) in the style of `PROD-nn` in
`docs/PRODUCTION_READINESS.md`. Reference them in commits.

---

## 0. The finding that reframes everything

The audit's Phase 3 reported 14 `application → infrastructure` layer violations
and separately noted `lint-imports` crashing locally. Direct investigation with
`grimp` (the graph library import-linter is built on) found the real cause, and
it is worse than either symptom:

**ARCH-01 [VERIFIED] — The layer contract has never been enforced over the layer where the violations live.**

Six directories under `leggie/` have no `__init__.py`:

| Directory | `.py` files |
|---|---|
| `leggie/application/agents` | 9 |
| `leggie/application/agents/prompts` | 8 |
| `leggie/application/cqrs` | 2 |
| `leggie/application/services` | 9 |
| `leggie/application/workflow` | 5 |
| `leggie/infrastructure/llm/adapters` | 1 |

Python 3.3+ namespace packages let these import fine at runtime, so nothing
visibly breaks. But `grimp` builds its module graph by walking packages, and
silently omits directories it does not recognise as packages. Measured:

```
grimp.build_graph('leggie')  →  74 modules
find leggie -name "*.py"     → 112 files
leggie.application.* in graph → 16 modules (all of them leggie.application.ports.*, plus blackboard/di)
'leggie.application.agents.skeptic' in graph → False
```

Consequence chain:

1. Every `application → infrastructure` import lives in `agents/`, `services/`,
   `workflow/`, `cqrs/` — precisely the invisible directories.
2. The `layers` contract in `pyproject.toml` therefore evaluates over a graph
   that contains none of the violations and **passes vacuously**.
3. CI step "Enforce architecture with import-linter" (`ci.yml:41-42`) has been
   green while enforcing nothing over 34 of 112 files.

Independently verified by evaluating the layer contract by hand against the
grimp graph: **0 violating imports found**, while `grep` finds 14 real ones.
The contract and reality disagree because the contract cannot see the code.

**ARCH-02 [VERIFIED] — `lint-imports` additionally crashes in this environment.**
`lint-imports`, `python -c "from importlinter.cli import lint_imports; ..."`, and a
clean non-tty subprocess (`TERM=dumb NO_COLOR=1`) all exit 1 with
`Only one live display may be active at once` (a Rich Live-display conflict)
before any contract is evaluated. Locally the tool produces no signal at all.
[UNKNOWN] whether GitHub's `ubuntu-latest` runner hits the same crash — but it
does not matter for correctness, because per ARCH-01 a successful run would
also have passed vacuously.

**Scope limit [VERIFIED]:** this blindness is specific to grimp/import-linter,
not to static analysis generally. `mypy leggie/application/agents/skeptic.py`
returns `Success: no issues found in 1 source file` — mypy recurses into
namespace packages by default. Ruff and bandit walk the filesystem and are
likewise unaffected. Only the *layer contract* was vacuous; type and lint
coverage were real.

**This is the highest-value fix in the plan.** Until enforcement is real, the
layer separation this repo advertises is asserted rather than checked, and any
fix made below can silently regress.

---

## 1. Findings register

| ID | Finding | Severity | Evidence |
|---|---|---|---|
| ARCH-01 | 6 dirs lack `__init__.py`; 34 files invisible to import-linter; layer contract passes vacuously | **CRITICAL** (enforcement) | grimp: 74 vs 112 modules |
| ARCH-02 | `lint-imports` crashes on Rich display conflict; no local signal | HIGH | RC=1, 3 invocation paths |
| ARCH-03 | 14 top-level `application → infrastructure.observability` imports | HIGH | grep, line refs §3.2 |
| ARCH-04 | 9 lazy in-function `application → infrastructure` imports in `cli_handlers.py` | MEDIUM | lines 44, 84, 85, 143, 204–206, 307, 356 |
| ARCH-05 | `BillAnalysisFlow` 740 lines; 3 aggregation strategies inline as branches | MEDIUM | file line count, methods 376/392/416 |
| ARCH-06 | `RetrievalPort`/`RetrievalAdapter` bound in container, zero consumers | LOW-MED | grep: 0 hits in workflow/agents/services/cqrs |
| ARCH-07 | No cross-run concurrency governor | MEDIUM | semaphores per-instance, `orchestrator.py:66,216` |
| ARCH-08 | `interfaces/cli/__init__.py` 537 lines, 22 functions | LOW | line count |

**Corrected from the audit:** Phase 4 hypothesised a process-global semaphore
starving concurrent runs. False. `orchestrator.py:66` creates `self._semaphore`
per instance and `:216` creates `article_sem` per call. Within a run,
concurrency is correctly bounded. The real gap is the inverse (ARCH-07): N
concurrent CLI invocations produce N × `max_concurrency` aggregate in-flight
requests with no shared ceiling — directly relevant now that
`docs/HEADLESS_CLI.md` invites external agents to drive the CLI.

**Not findings (verified clean):** domain purity (0 outward imports);
`infrastructure → application` direction (legal — infrastructure sits above
application); LLM decorator chain matches its documented order
(`container.py:112-135`); no circular dependencies detected; no anemic domain
model; no hardcoded secrets. The ruff ignore list has *shrunk* since the
skill-library snapshot (F821, F401, I001 removed) — debt is being paid down,
not accumulated.

---

## 2. Scoring model

The rubric is not additive, but the blockers are separable. Current 6/10 is
pinned by: one CRITICAL enforcement gap, one HIGH violation class, "patterns
consistent" failing on ARCH-05, and "scalable" qualified by ARCH-07.

| After phase | Expected | Why |
|---|---|---|
| Baseline | 6 | Moderate drift, HIGH violations, scalability concern |
| P0 | 6 | Score unchanged — but now *measured* instead of assumed. Violations become visible; may read worse before better. |
| P1 | 7.5 | Layer separation real and enforced; ARCH-03 closed |
| P2 | 8 | "Minor drift in 1–2 modules, no critical violations" satisfied |
| P3 | 8.5 | Patterns consistent — Strategy used uniformly |
| P4 | 8.5 | Dead abstraction removed; no score cliff, removes an inconsistency |
| P5 | 9 | Scalability claim becomes truthful and documented |
| P6 | 9+ | Re-audit confirms; regression guards prevent decay |

Honest note: P0 does not raise the score. It makes the score *trustworthy*.
Skipping it and doing P1–P5 would produce an unverifiable 9.

---

## 3. Phases

### Phase 0 — Restore enforcement (blocks everything else)

**Fixes:** ARCH-01, ARCH-02.
**Class:** B (wiring/refactor). **Effort:** 0.5–1 day. **Risk:** low-medium.

**Actions**

1. Add `__init__.py` to all six directories in §0. Content: a one-line module
   docstring. Do **not** add re-exports — that would create new import edges
   and change the dependency graph you are trying to measure.

2. Re-run the graph census and confirm it closes:

   ```bash
   python -c "import grimp; g=grimp.build_graph('leggie'); print(len(g.modules))"
   ```

   Expect ~112, not 74. Cross-check `leggie.application.agents.skeptic in g.modules` is `True`.

3. Run the contract. **It must now FAIL.** A failure here is the deliverable —
   it proves the tool sees the code. Capture the violation list verbatim.

4. Pin the enforcement baseline so CI stays green while P1–P2 land. Add the
   currently-known violations as explicit `ignore_imports` entries in the
   `[[tool.importlinter.contracts]]` block, each with a dated comment naming
   the phase that removes it:

   ```toml
   # ARCH-03 — removed by Phase 1 (observability relocation). Added 2026-08-05.
   ignore_imports = [
       "leggie.application.agents.* -> leggie.infrastructure.observability",
       # ... one line per real violation, enumerated from step 3
   ]
   ```

   Rationale: a debt baseline that shrinks to zero is enforcement. Deleting the
   contract or leaving CI red is not. This is *not* a change-control rule-5
   violation (that rule freezes the **ruff** ignore list); it is a
   time-boxed, itemised, shrinking allowlist on a contract that previously
   checked nothing.

5. Fix ARCH-02. Diagnose the Rich conflict — pin `import-linter` and `rich` to
   known-good versions, or invoke the contract through a small wrapper that
   bypasses the Live display. Acceptance: `lint-imports` produces a readable
   pass/fail report locally on Windows.

6. **Regression guard** — add `tests/unit/test_architecture_contract.py`:

   ```python
   def test_every_source_file_is_visible_to_the_layer_contract():
       """ARCH-01: namespace packages silently hid 34 files from import-linter.

       If this fails, a new directory is missing __init__.py and the layer
       contract has stopped seeing it — exactly the failure that let 14
       violations sit in a 'green' CI for the life of the project.
       """
   ```

   Compare the grimp module count against a filesystem walk of `leggie/**/*.py`
   (excluding `__pycache__`). Assert equality, not a threshold.

**Gates:** `pytest tests/ -q` (baseline 702 passed) · `mypy leggie/` · `ruff check` · `lint-imports` reports (pass with baseline).
**Guardrail:** adding `__init__.py` converts namespace packages to regular packages. Full suite must stay green — any import-resolution change surfaces there.

---

### Phase 1 — Close the observability layer leak

**Fixes:** ARCH-03. **Class:** B. **Effort:** 0.5 day. **Risk:** low.

14 modules import `get_logger` from `leggie.infrastructure.observability` at
module top level:

```
agents/skeptic.py:19          agents/orchestrator.py:27      agents/lens.py:20
agents/constitutional_lens.py:27   agents/economic_lens.py:22
agents/eu_gdpr_lens.py:22     agents/implementation_lens.py:22
agents/legal_coherence_lens.py:22
services/cove_verifier.py:39  services/blackboard_aggregator.py:20
services/lens_vs.py:14        cqrs/mediator.py:21
cqrs/handlers/cli_handlers.py:30
workflow/bill_analysis_flow.py:199 (lazy)
```

**Chosen fix: relocate, don't abstract.**

`leggie/infrastructure/observability/__init__.py:16` imports only
`leggie.config.settings`. It has no other inward dependency. It is therefore
not infrastructure in the dependency-rule sense — it is a cross-cutting leaf
that was filed in the wrong package.

Move `leggie/infrastructure/observability/` → `leggie/observability/` and
declare it as a layer between `domain` and `config`:

```toml
layers = [
    "leggie.interfaces",
    "leggie.infrastructure",
    "leggie.application",
    "leggie.domain",
    "leggie.observability",
    "leggie.config",
]
```

All 14 imports become legal downward dependencies. The change at each call site
is one import path — no DI plumbing, no injected-logger ceremony.

Add a second contract to keep domain purity explicit (layers permits
domain → observability; we forbid it):

```toml
[[tool.importlinter.contracts]]
name = "domain-purity"
type = "forbidden"
source_modules = ["leggie.domain"]
forbidden_modules = ["leggie.observability"]
```

**Rejected alternatives**, recorded so this is not relitigated:
- *`LoggerPort` + adapter*: 14 call sites gain constructor params and the
  container gains a binding, to abstract a logger nobody will swap. Premature
  abstraction — the exact anti-pattern ARCH-06 already flags elsewhere.
- *Permanent `ignore_imports`*: encodes the leak as policy. The baseline in P0
  is temporary by construction; this would be permanent.

**Remove** the corresponding `ignore_imports` baseline entries in the same commit.
**Gates:** full offline set. `lint-imports` must pass with a *smaller* baseline.

---

### Phase 2 — Eliminate lazy infrastructure imports in handlers

**Fixes:** ARCH-04. **Class:** B. **Effort:** 1 day. **Risk:** medium.

`cli_handlers.py` performs 9 in-function `leggie.infrastructure.*` imports
(lines 44, 84, 85, 143, 204–206, 307, 356). Deferring an import to call time
hides the edge from a reader but not from the graph — post-P0 these are real,
visible violations.

These are genuine composition-root work being done inside handlers. Move
construction to `infrastructure/container.py` and inject:

- `IngestorFactory`, `DocumentParser` → resolve via existing ports
- `CheckpointStore`, `SqliteEventStore`, `EvalScorer`/`GoldSet` → container bindings
- `ReasonerAdapter`, `ReasonerServerManager`, `GreekCitationParser` → container bindings
- `LLMConfigurationError` → move the `except` to catch the port-level error

`TYPE_CHECKING`-guarded imports (`cli_handlers.py:33`, `bill_analysis_flow.py:50`)
are fine and stay — they create no runtime edge. Configure import-linter to
ignore type-only imports rather than removing them.

**Guardrail:** handlers currently swallow exceptions into `CommandResult.failure(e)`,
which the headless CLI exit-code contract depends on
(`tests/unit/interfaces/test_headless_cli_contract.py`). Constructor injection
moves some failures from handler-time to container-build time, which would
bypass that mapping and change exit codes. Keep construction lazy where an
error must surface as a `CommandResult`, or extend the contract test first.
Run the headless contract suite as a gate on this phase.

**Remove** remaining `ignore_imports` baseline entries. After P2 the list is empty.

---

### Phase 3 — Extract the aggregation strategy

**Fixes:** ARCH-05. **Class:** **A** (pipeline-behaviour-changing). **Effort:** 2–3 days. **Risk:** high.

`BillAnalysisFlow` (740 lines) selects among three aggregation paths inline:
`_aggregate_via_blackboard` (376), `_aggregate_inline_dedup_rerank` (392),
`_aggregate_inline_verify` (416). The repo already uses Strategy for rerankers
and lenses; aggregation is the outlier.

Introduce `AggregationStrategy` as a Protocol in `application/ports/` with three
implementations in `application/services/aggregation/`. Select via the same
settings-driven mechanism the reranker uses.

**This phase is gated on live smoke** per `leggie-change-control` §2 — measured
against `REMEDIATION_PLAN` §10 thresholds, not judged by eye.

**Sequencing risk [VERIFIED from skill library]:** the full 5-lens live smoke
has never completed a recorded run (three attempts died to a stale route, an
OpenRouter 402, and parse degradation). A Class-A gate that has never passed
cannot certify this refactor. Two options:

- **(a) Characterization-first (recommended).** Before refactoring, capture
  golden outputs of all three aggregation paths from recorded fixtures, assert
  byte-identical results after extraction. This makes P3 provably
  behaviour-preserving *offline* and downgrades the live-smoke gate to
  confirmation rather than proof.
- **(b) Defer P3** until `leggie-remediation-campaign` lands a passing 5-lens
  smoke. Costs the 0.5 score point until then.

Do not refactor and hope the live smoke certifies it later.

---

### Phase 4 — Resolve the dead abstraction

**Fixes:** ARCH-06. **Class:** B (delete) or A (wire). **Effort:** 0.5–2 days.

`RetrievalPort` + `RetrievalAdapter` are bound in the container with zero
consumers in `workflow/`, `agents/`, `services/`, `cqrs/`. Decide explicitly:

- **Delete** if retrieval is not on the near roadmap. Removes a port, an
  adapter, and a container binding. Class B.
- **Wire** if citation grounding depends on it — note
  `docs/HEADLESS_CLI.md` records the citation index holds 2 identifiers, so
  most citations resolve unverified. If retrieval is the intended fix, this is
  Class A and belongs with that work, not here.

Leaving it bound-but-unused is the one outcome to avoid: it is an interface
with one implementation and no caller, which is the textbook premature
abstraction the audit flagged.

---

### Phase 5 — Cross-run concurrency governor

**Fixes:** ARCH-07. **Class:** A. **Effort:** 1–2 days.

Per-instance semaphores bound one run correctly. Nothing bounds N concurrent
runs. `docs/HEADLESS_CLI.md` now tells external agents to invoke the CLI as a
subprocess, so N concurrent invocations → N × `max_concurrency` in-flight
requests against OpenRouter, with per-run budget caps that do not compose.

Options, cheapest first:

1. **Document the limit** (0.5 day). State in `HEADLESS_CLI.md` that
   concurrency is per-process and agents must serialise or self-limit. Honest,
   costs nothing, does not fix it.
2. **File-lock governor** (1 day). A lockfile-based counting semaphore in the
   OS temp dir bounding aggregate in-flight requests across processes.
   `# ponytail:` comment noting the ceiling — fails open if the lockfile is
   stale; upgrade path is a real broker if this ever runs multi-host.
3. **Shared budget ledger** (2 days). Extend `CheckpointStore` semantics to a
   cross-run spend ledger so the $5 cap composes.

Recommend 1 + 2. Option 3 only if concurrent automated runs become routine —
and note change-control rule 4: the cost cap is the governor and is never
raised to make a run pass.

---

### Phase 6 — Prove and lock

**Effort:** 0.5 day.

1. Re-run ARCH-AUDIT-V2 end to end. Target >9.
2. Confirm `ignore_imports` is empty and both contracts pass.
3. Confirm the ARCH-01 regression test fails when an `__init__.py` is deleted
   (test the test — an enforcement guard that cannot fail is ARCH-01 again).
4. Update `docs/ARCHITECTURE.md` with the observability layer and the
   aggregation Strategy.
5. Consider `docs/adr/` — the audit found no ADRs, and the observability
   relocation plus the aggregation extraction are exactly the decisions a
   future reader will otherwise relitigate.

**ARCH-08** (`cli/__init__.py`, 537 lines) is deliberately not addressed. It is
LOW severity, contains dispatch and presentation only, no business logic
leaked inward, and it was just restructured for the headless contract. Churning
it now risks the exit-code guarantees for a cosmetic line count. Revisit only
if a second interface (web/API) forces shared presentation logic.

---

## 4. Sequencing

```
P0 ──> P1 ──> P2 ──> P6
 │                    ↑
 └──> P4 ─────────────┤
      P5 ─────────────┤
      P3 ─────────────┘   (gated: characterization tests or passing 5-lens smoke)
```

P0 strictly first — it is the measurement instrument. P1→P2 are ordered because
both draw down the same baseline. P3/P4/P5 are mutually independent and can run
in parallel or be deferred; only P3 carries a Class-A live-smoke dependency.

**Total:** 6–10 working days, or 4–7 excluding P3.

---

## 5. Verification commands

```bash
# ARCH-01 — module census must match the filesystem
python -c "import grimp; g=grimp.build_graph('leggie'); print(len(g.modules))"
find leggie -name "*.py" -not -path "*__pycache__*" | wc -l

# Layer contracts
lint-imports

# Offline gate set (change-control §2)
python -m pytest tests/ -q
mypy leggie/ --ignore-missing-imports
ruff check leggie/ tests/

# Headless CLI contract — gate on P2
python -m pytest tests/unit/interfaces/test_headless_cli_contract.py -q
```

Hermetic test env (never omit — unit tests hit real OpenRouter without it):

```
LEGGIE_LLM__OPENROUTER_API_KEY=""  LEGGIE_REASONER__API_KEY=""
LEGGIE_REASONER__ENABLED=false     LEGGIE_REASONER__AUTOSTART=false
```

---

## 6. Risks

| Risk | Phase | Mitigation |
|---|---|---|
| Adding `__init__.py` changes import resolution | P0 | Full suite is the detector; namespace→regular is normally transparent |
| CI goes red once the contract can see | P0 | Itemised shrinking `ignore_imports` baseline, drained by P2 |
| Baseline becomes permanent debt | P0–P2 | Every entry carries a dated comment naming its removing phase; P6 asserts empty |
| Injection moves failures out of `CommandResult` and changes exit codes | P2 | Headless contract suite gates the phase; keep construction lazy where an error must map to an exit code |
| P3 refactor cannot be certified — 5-lens smoke never passed | P3 | Characterization tests first, or defer |
| Score improves while pipeline yield stays unproven | all | Architecture score measures structure, not correctness. Yield is `leggie-remediation-campaign`'s job — these are complementary, not substitutes |

---

## 7. What this plan does not claim

The audit scored *structure*. A 9/10 here means layers are separated,
enforced, and consistent. It does not mean Leggie produces good legal analysis.
Per `docs/HEADLESS_CLI.md` §Known limits, still true and untouched by this plan:
no recorded full 5-lens live run, gold-set eval last scored F1 = 0, citation
index holds 2 identifiers. Those are tracked in
`docs/PRODUCTION_READINESS_PLAN.md` and `leggie-remediation-campaign`.

A well-architected system that has not been proven to work is still unproven.
