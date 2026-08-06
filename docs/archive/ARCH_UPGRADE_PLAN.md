# Architecture Upgrade Plan — Leggie 8/10 → 9.5+/10

> **Status:** Superseded by `docs/PRODUCTION_READINESS_PLAN.md`. Kept for reference only. Active work follows the phased plan in that document.


> **Baseline:** ARCH-AUDIT-V2 score 8/10, Early Production maturity
> **Target:** 9.5+/10, Production maturity
> **Principle:** Every change cites a specific audit finding and maps to a concrete file diff.

---

## Gap Inventory (from ARCH-AUDIT-V2)

| # | Finding | Severity | Phase | Est. hours |
|---|---|---|---|---|
| G1 | No resume-from-checkpoint — mid-flow crash loses all progress | MEDIUM | Phase 6 risk #2 | 4 |
| G2 | Orchestrator bottleneck — single-process, no multi-bill concurrency | MEDIUM | Phase 5 finding #1 | 6 |
| G3 | Lens failure propagation — TaskGroup abort drops all concurrent findings | HYPOTHESIS | Phase 4 failure semantics | 2 |
| G4 | Budget checkpointing not integrated into flow | VERIFIED | Phase 6 risk #4 | 1.5 |
| G5 | No rate limiter on OpenRouter calls | HYPOTHESIS | Phase 4 async/concurrency | 2 |
| G6 | LLM adapter monolithic (371 lines) — plan calls for `adapters/` + `decorators/` | LOW | Phase 2 compliance | 3 |
| G7 | No IngestPort / ParsePort — handlers import infrastructure directly | VERIFIED | Phase 3 layer leaks | 3 |
| G8 | BlackboardPort + RetrievalPort unimplemented beyond fakes | LOW | Phase 2 compliance | 4 |
| G9 | Lens-specific pattern config hardcoded in Python — not YAML/declarative | LOW | Code quality | 2 |
| G10 | Missing per-stage observability (no span/trace propagation across stages) | LOW | Phase 4 scalability | 2 |

---

## Plan

### Step 1 — Budget Checkpoint Integration (G4) — 1.5h

**What:** Integrate `BudgetGuard.save_state()` / `load_state()` into `BillAnalysisFlow` at every stage boundary. On flow start, load previous state if it exists. On each transition, save.

**Files changed:**
- `leggie/application/workflow/bill_analysis_flow.py` — add `_checkpoint_dir` parameter, call `budget.save_state()` after each `_transition()`
- `leggie/infrastructure/budget_guard/__init__.py` — add `to_file(path)` / `from_file(path)` convenience methods
- `tests/unit/infrastructure/test_budget_guard.py` — add file checkpoint test

**Design:** Add a `CheckpointMixin` to the flow — `_save_checkpoint()` writes budget state + flow state + event count to JSON. `_load_checkpoint()` restores if file exists. Checkpoint file: `run_{id}_checkpoint.json`.

**Expected outcome:** Run survives restart. On resume, budget state restored, flow picks up at last completed stage.

---

### Step 2 — Lens Failure Isolation (G3) — 2h

**What:** Wrap each lens execution in `Orchestrator` with try/except so one failing lens doesn't abort the TaskGroup.

**Files changed:**
- `leggie/application/agents/orchestrator.py` — `_run_lens()` catches exceptions, logs warning, returns `[]` instead of aborting

**Current (breaks on any failure):**
```python
async def _run_lens(name: str) -> list[Finding]:
    async with self._semaphore:
        lens = lens_cls()
        return await lens.analyze(article)
```

**Target (isolated failures):**
```python
async def _run_lens(name: str) -> list[Finding]:
    async with self._semaphore:
        try:
            lens = lens_cls()
            return await lens.analyze(article)
        except Exception as e:
            logger.warning("lens_failed", lens=name, article=article.id, error=str(e))
            return []
```

**Expected outcome:** One lens returning `[]` (failure) doesn't block other lenses. Findings from surviving lenses still reach the reranker + skeptic.

---

### Step 3 — Resume-from-Checkpoint (G1) — 4h

**What:** Persist `BillAnalysisFlow` state at every stage boundary. On start, detect incomplete runs and resume from the last checkpointed stage.

**Files changed:**
- `leggie/application/workflow/bill_analysis_flow.py` — add `run_id`, `_checkpoint_path`, `save_checkpoint()`, `load_checkpoint()`, `resume()` method
- `leggie/application/workflow/flow_state_machine.py` — add `resumable_states()` returning states from which a run can resume
- `tests/integration/test_e2e_pipeline.py` — add `test_resume_after_crash` test
- New: `leggie/infrastructure/persistence/checkpoint_store.py` — `CheckpointStore` class wrapping JSON file I/O with atomic writes

**Design:**

```
CheckpointStore(file_path)
  → save(data: dict) — atomic write (write to .tmp → os.rename)
  → load() → dict | None
  → delete() — remove checkpoint file

BillAnalysisFlow.resume(run_id: str)
  → load checkpoint
  → if state == EXECUTING: skip ingest, parse, plan → jump to execute
  → if state == AGGREGATING: skip ingest, parse, plan, execute → load findings → jump to aggregate
  → etc.
```

**Checkpoint schema:**
```json
{
  "run_id": "uuid",
  "state": "verifying",
  "bill_path": "/path/to/bill.txt",
  "findings_count": 12,
  "events_count": 18,
  "budget_state": { "tokens_used": 50000, "cost_used": 0.15, ... },
  "created_at": "2026-07-09T..."
}
```

**Expected outcome:** Crash mid-flow → re-run with same bill → picks up at last checkpointed stage. Full audit trail preserved via event log.

---

### Step 4 — IngestPort + ParsePort (G7) — 3h

**What:** Create abstract ports for ingest and parse operations. Wire them through the DI container. Update `BillAnalysisFlow` and CQRS handlers to depend on ports, not concrete infrastructure.

**Files changed:**
- New: `leggie/application/ports/ingest.py` — `IngestPort` ABC with `async ingest(path) -> str`
- New: `leggie/application/ports/parse.py` — `ParsePort` ABC with `parse(text, title, format) -> Document`
- `leggie/infrastructure/ingest/__init__.py` — `IngestAdapter` implementing `IngestPort`, delegates to `IngestorFactory`
- `leggie/infrastructure/parse/__init__.py` — `ParseAdapter` implementing `ParsePort`, delegates to `DocumentParser`
- `leggie/infrastructure/container.py` — register `IngestPort` and `ParsePort` in `configure_defaults()`
- `leggie/application/workflow/bill_analysis_flow.py` — inject `IngestPort` and `ParsePort` via constructor, replace `_do_ingest()`/`_do_parse()` with port calls
- `leggie/application/cqrs/handlers/cli_handlers.py` — inject ports instead of importing infrastructure
- `tests/unit/application/test_port_contracts.py` — add `TestIngestPortContract` and `TestParsePortContract` with fakes

**Expected outcome:** `BillAnalysisFlow` and CQRS handlers depend only on abstractions. Zero infrastructure imports in application layer (except DI container which is composition root).

---

### Step 5 — Rate Limiter (G5) — 2h

**What:** Add a token-bucket rate limiter to `OpenRouterProvider` to prevent 429 cascades under burst load.

**Files changed:**
- `leggie/infrastructure/llm/__init__.py` — `RateLimiter` class with `async acquire()`, injected into `OpenRouterProvider`
- `leggie/config/settings.py` — add `rate_limit_rps: float = 5.0` to `LLMSettings`
- `leggie/infrastructure/container.py` — wire rate limiter into `_create_llm()` factory
- `tests/unit/infrastructure/test_openrouter_adapter.py` — add rate limiter test

**Design:**
```python
class RateLimiter:
    def __init__(self, max_rate: float):
        self._semaphore = asyncio.Semaphore(max_rate)
        self._interval = 1.0 / max_rate

    async def acquire(self):
        async with self._semaphore:
            await asyncio.sleep(self._interval)
```

**Expected outcome:** Burst of 50 concurrent lens calls → serialized at `rate_limit_rps` (default 5/second). No 429 errors from OpenRouter free tier.

---

### Step 6 — Split LLM Module (G6) — 3h

**What:** Extract `leggie/infrastructure/llm/__init__.py` (371 lines) into sub-packages per BUILD_PLAN §3: `adapters/` and `decorators/`.

**Target structure:**
```
leggie/infrastructure/llm/
├── __init__.py           # Re-exports: LLMAdapter, OpenRouterProvider
├── base.py               # BaseLLMProvider, LLMError hierarchy
├── adapters/
│   ├── __init__.py
│   ├── anthropic.py      # AnthropicProvider
│   ├── openai.py         # OpenAIProvider
│   └── openrouter.py     # OpenRouterProvider + RateLimiter
├── decorators.py         # with_retry, with_cache
└── rate_limiter.py       # RateLimiter class
```

**Expected outcome:** Largest file drops from 371 to ~50 lines. Each adapter is independently testable. BUILD_PLAN §3 layout satisfied.

---

### Step 7 — Blackboard + Retrieval Port Implementation (G8) — 4h

**What:** Implement `BlackboardPort` adapter (the in-memory `Blackboard` class already exists in `application/blackboard/` — wire it to the port). Implement a stub `RetrievalPort` adapter that indexes local files (Phase 3 prep).

**Files changed:**
- `leggie/infrastructure/blackboard_adapter.py` — `BlackboardAdapter` wrapping `application/blackboard/Blackboard`, implementing `BlackboardPort`
- New: `leggie/infrastructure/retrieval/__init__.py` — `LocalRetrievalAdapter` implementing `RetrievalPort` with simple text search (Phase 3 prep; full hybrid retrieval deferred)
- `leggie/infrastructure/container.py` — register both adapters
- `tests/unit/application/test_port_contracts.py` — add retrieval adapter test

**Expected outcome:** All 7 ports have working implementations (not just fakes). `BlackboardPort` usable in Phase 3. `RetrievalPort` has placeholder ready for EUR-Lex integration.

---

### Step 8 — Declarative Lens Config (G9) — 2h

**What:** Extract hardcoded regex patterns from lens files into YAML config. Each lens loads its patterns from `config/lenses/{lens_name}.yaml`.

**Files changed:**
- New: `config/lenses/constitutional.yaml`, `config/lenses/legal_coherence.yaml`, `config/lenses/economic.yaml`, `config/lenses/implementation.yaml`, `config/lenses/eu_gdpr.yaml`
- Each lens's `__init__.py` — `_PATTERNS` loaded from YAML at module level with fallback to hardcoded defaults
- `tests/unit/application/test_phase2_lenses.py` — add config loading test

**Example YAML:**
```yaml
# config/lenses/constitutional.yaml
name: constitutional
description: Checks compatibility with the Greek Constitution
patterns:
  delegation:
    - "εξουσιοδότηση"
    - "εξουσιοδοτεί"
    - "έκδοση π.δ."
  retroactive:
    - "αναδρομική ισχύ"
    - "αναδρομικά"
  rights:
    - "περιορισμός θεμελιώδους δικαιώματος"
    - "προσωπικά δεδομένα"
```

**Expected outcome:** Legal experts can update lens patterns without touching Python code. No re-deployment needed for pattern changes.

---

### Step 9 — Stage Observability (G10) — 2h

**What:** Propagate a `trace_id` through all stage executions. Emit structured log events at each stage boundary with timing, finding count, and status.

**Files changed:**
- `leggie/application/workflow/stage.py` — `StageContext` gets `trace_id` field
- `leggie/application/workflow/bill_analysis_flow.py` — generate `trace_id` at flow start, pass through to each stage
- `leggie/infrastructure/observability/__init__.py` — add `TraceContext` contextvar for implicit propagation
- `tests/unit/test_observability.py` — add trace propagation test

**Design:**
```python
# observability/__init__.py
_trace_id: ContextVar[str] = ContextVar("trace_id", default="")

def get_trace_id() -> str:
    return _trace_id.get() or str(uuid4())

def set_trace_id(tid: str) -> None:
    _trace_id.set(tid)

# bill_analysis_flow.py
trace_id = str(uuid4())
set_trace_id(trace_id)
logger.info("flow.started", trace_id=trace_id, bill_path=str(file_path))
```

**Expected outcome:** Every log line in a bill run has the same `trace_id`. Cross-stage timing visible in structured logs.

---

## Dependency Graph

```
G4 (budget checkpoint) ──┐
                          ├──> G3 (lens isolation) ──> G1 (resume-from-checkpoint)
G9 (declarative config) ─┘
                                      │
G5 (rate limiter) ──────────────────> G6 (split LLM) ──┐
                                      │                  │
G7 (IngestPart/ParsePort) ───────────┘                  │
                                      │                  │
G8 (Blackboard/Retrieval) ───────────┘                  │
                                      │                  │
                                      └──> G10 (stage observability) ── final
```

- **G4 + G9** → independent, do first (no dependencies)
- **G3** → depends on G4 (checkpoint pattern established), then **G1** builds on G3
- **G5 + G7 + G8** → independent of each other, parallelizable
- **G6** → depends on G5 (rate limiter needs to be extracted into its own file)
- **G10** → depends on everything else (instrumentation touches all modules)

---

## Effort Summary

| Step | Hours | Parallelizable |
|---|---|---|
| G4 | 1.5 | ✅ with G9 |
| G9 | 2 | ✅ with G4 |
| G3 | 2 | After G4 |
| G1 | 4 | After G3 |
| G5 | 2 | ✅ with G7, G8 |
| G7 | 3 | ✅ with G5, G8 |
| G8 | 4 | ✅ with G5, G7 |
| G6 | 3 | After G5 |
| G10 | 2 | After all |
| **Total** | **23.5h** | **~14h sequential if parallelized** |

---

## Success Criteria

After all steps, the following must hold:
- [ ] ARCH-AUDIT-V2 re-score ≥ 9.5/10
- [ ] All 7 ports have working non-fake implementations
- [ ] `BillAnalysisFlow` and CQRS handlers import 0 infrastructure modules directly
- [ ] Mid-flow crash → resume from checkpoint → identical findings produced
- [ ] One lens failure → other 4 lenses complete normally
- [ ] Budget state persists across restarts
- [ ] LLM rate limited at configurable RPS
- [ ] `leggie/infrastructure/llm/__init__.py` ≤ 50 lines
- [ ] Lens patterns in YAML, modifiable without code changes
- [ ] All log lines carry `trace_id`
- [ ] 300+ tests (up from 272)
