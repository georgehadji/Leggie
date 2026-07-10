---
name: leggie-architecture-contract
description: >
  Load BEFORE designing any code change in the Leggie repo: adding a component,
  moving code between layers, adding a lens/adapter/pipeline stage, or when
  import-linter fails. Documents the load-bearing design decisions (Clean/
  Hexagonal layers, ports & adapters, event sourcing, CQRS, DI container,
  static router), the invariants that must hold, the file-by-file pattern map,
  the data-flow walkthrough, and the known weak points as of 2026-07-10.
---

# Leggie Architecture Contract

Leggie is a Greek legal bill analyzer built as a Clean/Hexagonal system.
Definitions used once here: a **layer** is a directory whose imports are
restricted; a **port** is an abstract interface (ABC) owned by the Application
layer; an **adapter** is an Infrastructure class implementing a port;
**event sourcing** means every significant step appends an immutable `Event`
for replay/audit; **CQRS** means CLI commands are dispatched through a
mediator to handlers instead of calling services directly.

## 1. Layers and the dependency rule

```
leggie/interfaces/      → CLI only; thin; dispatches via CQRS mediator
leggie/infrastructure/  → adapters (LLM, router, ingest, parse, citation, persistence, budget)
leggie/application/     → ports, workflow, agents (lenses/skeptic), services (CoVe, rerank, reports), CQRS
leggie/domain/          → frozen Pydantic models, pure scoring/clustering/specs — imports NOTHING outward
leggie/config/          → pydantic-settings + config/routes.yaml
```

Dependencies point inward only. Enforced by import-linter
(`pyproject.toml [tool.importlinter]`, layers contract). Check with:
`lint-imports` (needs `pip install -e ".[lint]"`).

## 2. The ports (verified 2026-07-10 — 10 ports, README says 7: README is stale)

| Port | File | Implemented by |
|---|---|---|
| `BlackboardPort` | `leggie/application/ports/blackboard.py` | blackboard aggregation |
| `CitationParserPort` | `ports/citation_parser.py` | `GreekCitationParser` (`infrastructure/citation/`) |
| `EventBusPort` | `ports/event_bus.py` | persistence/event bus |
| `IngestPort` | `ports/ingest.py` | `IngestAdapter` (`infrastructure/ingest_adapter.py`) |
| `LLMPort` | `ports/llm.py` | `LLMAdapter` (`infrastructure/llm/__init__.py`) + decorators |
| `ParsePort` | `ports/parse.py` | `ParseAdapter` (`infrastructure/parse_adapter.py`) |
| `RerankerPort` | `ports/reranker.py` | model-based reranker (built, wiring optional — D5) |
| `RetrievalPort` | `ports/retrieval.py` | retrieval (largely future work) |
| `RouterPort` | `ports/router.py` | `StaticRouter` (`infrastructure/router/`) |
| `StatePort` | `ports/state.py` | persistence |

## 3. Data flow (concrete classes, verified)

```
bill file (PDF/DOCX/HTML/TXT)
 → IngestAdapter.ingest()                      # infrastructure/ingest_adapter.py
 → ParseAdapter.parse() → Document(Article...) # Greek Άρθρο tree
 → BillAnalysisFlow.run()                      # application/workflow/bill_analysis_flow.py
    → Orchestrator.analyze_article() per article   # SEQUENTIAL loop, flow line ~157 (D3 open)
       → 5 lenses via asyncio.TaskGroup per article # agents/orchestrator.py, _DEFAULT_LENSES
    → BlackboardAggregator.aggregate()         # default path (use_blackboard=True)
       → dedup → CompositeReranker → CalibratedSkeptic → CoVeVerifier
    → ImprovementEngine.generate_suggestions()
    → ExecutiveSummaryRenderer + ArticleByArticleRenderer
    → auto-save to Outputs/<stem>_{executive_summary.md,article_by_article.md,findings.json}
```

Lens registry names (exact strings for `--lenses`): `constitutional`,
`legal_coherence`, `economic`, `implementation`, `eu_gdpr`
(`agents/orchestrator.py` `_DEFAULT_LENSES`).

CLI path: `interfaces/cli/__init__.py` → `Mediator` → handlers in
`application/cqrs/handlers/cli_handlers.py`, with a single DI composition root
`infrastructure/container.py` (`Container.configure_defaults()`), built once in
`_build_mediator()`.

## 4. Invariants (violating any of these is a class-A change — see leggie-change-control)

1. **Domain purity**: `leggie/domain/` imports nothing from outer layers.
   Models are frozen Pydantic (`model_config = {"frozen": True}` e.g.
   `Confidence`).
2. **Structured output**: every LLM response validates against a schema in
   `leggie/domain/models/structured_output.py` (`LensFindings`,
   `SkepticVerdictResponse`, `CoVe*Response`, `VSResponse`).
3. **Immutability**: findings are updated via `model_copy(update={...})`
   with a version bump — see `skeptic.py` `review()` — never mutated in place.
4. **Port stability**: no new methods on existing ports; new behavior via new
   adapters/decorators (precedent: retry/cache/budget decorators around
   `LLMPort` in `infrastructure/llm/decorators.py`).
5. **No silent failure**: degradation emits `EventType.DEGRADED` events or
   warnings (skeptic catches all exceptions, logs `skeptic_llm_error`, returns
   neutral).
6. **Event spine**: flow records `Event`s (types in
   `leggie/domain/models/__init__.py` `EventType`: ANALYSIS_STARTED,
   LENS_COMPLETED, FINDING_CREATED/REFUTED/CONFIRMED, CITATION_VERIFIED/FAILED,
   STAGE_COMPLETED, WORKFLOW_COMPLETED/FAILED, BUDGET_TRIPPED, DEGRADED,
   DEDUP_REMOVED, AGGREGATION_COMPLETED).
7. **State machine**: workflow transitions only through `FlowStateMachine`
   (`application/workflow/flow_state_machine.py`); states in `WorkflowState`.

## 5. Pattern map (verified locations)

| Pattern | Where |
|---|---|
| Ports & Adapters | `application/ports/*` ↔ `infrastructure/*` |
| Strategy | 5 lens classes; `CompositeReranker` vs `ModelBasedReranker` (`services/rerank.py`) |
| Chain of Responsibility | skeptic gates (`agents/skeptic.py`: Numeric/Temporal/Factual/Obligation + LLMAdversarialGate); model cascade |
| Template Method | stage lifecycle, CoVe 4-step loop (`services/cove_verifier.py`) |
| Command + Mediator (CQRS) | `application/cqrs/` |
| State | `FlowStateMachine` |
| Event Sourcing | `Event` log in flow + persistence event store |
| Blackboard + Observer | `services/blackboard_aggregator.py` |
| Decorator | `infrastructure/llm/decorators.py` (retry, cache), budget guard |
| Factory | ingest per format |
| Interpreter | `GreekCitationParser` regexes (ΦΕΚ/CELEX/ECLI/URL/law-ref) |

## 6. Known weak points (stated plainly; status 2026-07-10)

| ID | Weakness | Status |
|---|---|---|
| D3 | Article loop is SEQUENTIAL (`bill_analysis_flow.py:157`); parallel `Orchestrator.analyze_document()` exists but is never called by the flow | OPEN |
| D7 | Citation `resolution_index` empty → citations only ever "unverified" (fail-closed, correct but toothless) — `infrastructure/citation/__init__.py` `resolve()` | OPEN |
| D8 | `cli_handlers.py` retains legacy `_try_get_*` fallbacks beside the container | verify in source |
| D9 | `RateLimiter(max_rate=5.0)` now constructed inside `LLMAdapter.__init__` and passed to `OpenRouterProvider` — appears wired; confirm consumption in `adapters/openrouter.py` | LIKELY FIXED, verify |
| D10 | Resume-from-stage: only budget spend is checkpointed by the flow; `infrastructure/persistence/checkpoint_store.py` exists — check whether flow uses it | PARTIAL, verify |
| — | README claims 7 ports / 199 tests; source has 10 ports / 361 tests | DOC DRIFT |

## 7. How to add things

**New lens**: subclass pattern of existing lens in `application/agents/`,
register in `_DEFAULT_LENSES` dict in `orchestrator.py`, add route entry in
`config/routes.yaml` if it needs its own model, add unit tests mirroring
`tests/unit/application/test_*_lens.py`.

**New adapter**: implement the port ABC in `infrastructure/`, bind it in
`container.py` `configure_defaults()`, never import it from Application.

**New pipeline stage**: add a `WorkflowState` + `FlowStateMachine` transition,
emit `STAGE_COMPLETED` events, keep aggregation logic in a service class.

## When NOT to use this skill

- Looking up a config value/env var → **leggie-config-and-flags**
- Why a defect exists / history → **leggie-failure-archaeology**
- Commit gates → **leggie-change-control**
- LLM retry-ladder specifics → **llm-structured-output-reference**
- Greek legal semantics → **greek-legal-domain-reference**

## Provenance and maintenance

- Ports: `grep -rn "class.*Port" leggie/application/ports/*.py`
- Lens names: `grep -n "_DEFAULT_LENSES" -A7 leggie/application/agents/orchestrator.py`
- Sequential loop (D3): `grep -n "for article in self._doc.articles" leggie/application/workflow/bill_analysis_flow.py`
- Event types: `grep -n "class EventType" -A16 leggie/domain/models/__init__.py`
- Layer contract: `lint-imports` or `grep -A10 importlinter pyproject.toml`
