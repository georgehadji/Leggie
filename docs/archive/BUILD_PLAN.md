# Leggie — Thorough Build Plan

> **Status:** Superseded by `docs/PRODUCTION_READINESS_PLAN.md`. Kept for reference only. Active work follows the phased plan in that document.


> Companion to [tasks/todo.md](../tasks/todo.md) (roadmap + research) and [docs/ARCHITECTURE.md](ARCHITECTURE.md) (architecture decision).
> This document binds **every module and routine to a programming paradigm and a set of design patterns**, then phases the build. The architecture is fixed: Clean/Hexagonal core · deterministic workflow-DAG · orchestrator-worker parallel fan-out · bounded schema-grounded blackboard · durable event-sourced spine. Nothing here deviates from it.

---

## 0. Invariants (every module obeys these)

1. **Dependencies point inward**: Interfaces → Infrastructure → Application → Domain. Domain imports nothing outward. Enforced by import-linter (reuse weebot config).
2. **Control flow is code; reasoning is model.** LLMs are called *inside* a stage. No LLM decides the pipeline.
3. **Everything crossing a boundary is validated** (Pydantic at edges). Never trust document content, API responses, or model output.
4. **Immutability by default.** Domain objects are frozen; state changes produce new objects + append an event.
5. **Every finding is traceable** to `(stage, model, prompt_hash, seed, evidence, counter_evidence, confidence)`.
6. **Chain-depth cap ≤ 4 hops** on any transform touching legal text (DELEGATE-52 / O2).

---

## 1. Paradigm policy — *Functional Core, Imperative Shell*

| Zone | Paradigm | Rationale |
|---|---|---|
| **Domain** (entities, value objects, scoring math, clustering, parsing rules) | **Pure functional / declarative.** Frozen `pydantic` models; pure functions `f(state) -> new_state`; no I/O, no clocks, no randomness passed in as args. | Determinism, testability, reproducibility NFR. Pure = trivially unit-testable + replayable. |
| **Application** (use-cases, workflow, orchestration, blackboard controller) | **OOP for structure (ports, interactors, DI) + functional for transforms.** Imperative shell that *sequences* pure operations and effects. | Dependency inversion needs interfaces (OOP); the logic inside stays composed of pure steps. |
| **Infrastructure** (adapters, repositories, resilience) | **OOP** — polymorphic adapters behind ports; decorators stack cross-cutting concerns. | Swappable providers, stackable resilience (retry/breaker/cache) = classic OOP composition. |
| **Interfaces** (CLI, Web, MCP) | **Thin imperative**; delegate immediately to Application via CQRS. | Keep entry points dumb; no business logic at the edge. |
| **Concurrency** | **Structured async** (`asyncio` + `TaskGroup`); bounded parallelism via semaphore. | Fan-out is I/O-bound (LLM/HTTP). Structured concurrency = no orphan tasks, clean cancellation on budget stop. |

**Hard rules:**
- No mutation of a passed-in object anywhere. Return a new one.
- Side effects (LLM call, HTTP, DB write) live only behind a **Port** in Application, implemented in Infrastructure.
- Randomness/seed/clock are **injected**, never ambient — reproducibility.

---

## 2. Design-pattern catalog (cross-cutting)

| Pattern | Where | Purpose |
|---|---|---|
| **Ports & Adapters (Hexagonal)** | LLM, Router, Retrieval, State, EventBus, Blackboard, CitationParser | Provider-agnostic; testable via fakes. |
| **Repository** | Persistence of Article/Finding/Session/Events | Storage-agnostic data access. |
| **Strategy** | Lenses, retrieval modes, rerank scorers, similarity, report types, improvement modes | Interchangeable algorithms behind one interface. |
| **Chain of Responsibility** | Model cascade (FREE→BUDGET→PREMIUM), typed Skeptic gates, validation pipeline | Ordered handlers, pass-or-escalate. |
| **Decorator** | LLM resilience (retry · circuit-breaker · cache · budget), observability | Stack cross-cutting concerns without touching core. |
| **Command + Mediator (CQRS)** | Application use-cases | Decouple caller from handler; command = auditable unit of work. |
| **State** | Workflow flow machine (`IDLE→PLANNING→EXECUTING→AGGREGATING→VERIFYING→IMPROVING→REPORTING→DONE`) | Explicit, checkpointable transitions. |
| **Event Sourcing** | Durable spine; finding mutations | Replay, audit, explainability NFRs. |
| **Blackboard + Observer** | Aggregation stage | Independent contributors post to shared board; controller schedules reactions. |
| **Specification** | Citation validity, confidence/abstention gate, finding admissibility | Composable boolean business rules. |
| **Template Method** | CoVe evidence loop, stage lifecycle, report skeletons | Fixed skeleton, varying steps. |
| **Builder** | Structured document, suggestions, reports | Assemble complex immutables step-by-step. |
| **Composite** | Parsed doc tree (Άρθρο→παρ.→εδάφιο), hybrid-retrieval fusion | Tree / part-whole. |
| **Factory** | Ingest-per-format, lens instantiation | Construction by type. |
| **Circuit Breaker + Token Bucket** | Budget guard, external-corpus backpressure | Fault tolerance, cost ceiling. |

---

## 3. Layout (Clean layers; many small files < 400 lines)

```
leggie/
├── domain/                      # pure functional core
│   ├── models/                  # frozen Pydantic: Article, Finding(IRAC), Evidence, Citation, Plan, Event
│   ├── scoring/                 # pure: severity, novelty, confidence calc
│   ├── clustering/              # pure: dedup/cluster functions
│   └── specs/                   # Specification objects (admissibility, abstention)
├── application/
│   ├── ports/                   # LLM, Router, Retrieval, State, EventBus, Blackboard, CitationParser
│   ├── workflow/                # State machine + stage interactors (DAG)
│   ├── agents/                  # lens-workers, skeptic, improver (Strategy/Command)
│   ├── blackboard/              # controller + schema-grounded mutation svc
│   ├── cqrs/                    # commands, queries, mediator
│   └── services/                # VS sampler, CoVe verifier, rerank, dedup orchestration
├── infrastructure/
│   ├── llm/                     # provider adapters + decorators (retry/breaker/cache/budget)
│   ├── router/                  # rules table → cascade (CoR); telemetry
│   ├── retrieval/               # dense · sparse · hybrid (Strategy/Composite); corpus clients
│   ├── citation/                # deterministic parser (ΦΕΚ/CELEX/ECLI)
│   ├── persistence/             # repositories, event store, SQLite/WAL
│   ├── ingest/                  # per-format factories (PDF/DOCX/HTML)
│   ├── parse/                   # Greek legal structure builder
│   └── observability/           # structured logging, metrics, tracing
├── interfaces/                  # cli/ · web/ · mcp/
└── config/                      # typed Pydantic settings (12-factor)
```

---

## 4. Module-by-module spec

Legend — **Par** = paradigm, **Patterns** = design patterns, **Reuse** = from weebot.

### Domain
| Module | Responsibility | Par | Patterns | Key types |
|---|---|---|---|---|
| `models/` | Immutable entities/value objects | FP | Value Object, frozen dataclass/Pydantic | `Article`, `Finding`(IRAC: issue/rule/application/conclusion), `Evidence`, `Citation`, `Confidence`, `Event` |
| `scoring/` | Pure severity/novelty/confidence math | FP | Pure functions, function composition | `score_severity`, `score_novelty`, `combine` |
| `clustering/` | Group near-duplicate findings | FP | Strategy (similarity fn), pure map/reduce | `cluster(findings, sim) -> clusters` |
| `specs/` | Business rules as booleans | FP/OOP | **Specification** (`and/or/not`) | `IsAdmissible`, `MeetsConfidence`, `CitationResolves` |

### Application
| Module | Responsibility | Par | Patterns | Reuse |
|---|---|---|---|---|
| `ports/` | Abstract effect boundaries | OOP (ABC/Protocol) | Ports & Adapters | weebot ports |
| `workflow/` | Stage sequencing + durability | OOP shell | **State**, **Template Method** (stage lifecycle), **Saga** (compensating on failure) | weebot flow machine |
| `agents/lens_worker` | One perspective's analysis | FP body / OOP iface | **Strategy** (per lens) + **Command** (one analysis run) | structured_executor |
| `agents/skeptic` | Calibrated adversarial critic | OOP | **Chain of Responsibility** (typed gates), Strategy | — |
| `agents/improver` | Suggestion generation | FP/OOP | Strategy (minimal/reform), **Builder** | — |
| `blackboard/` | Shared aggregation substrate | OOP shell + FP reducers | **Blackboard**, **Observer**, **Event Sourcing** (append-only, schema-grounded per PatchBoard) | event_bus |
| `cqrs/` | Command/query dispatch | OOP | **Command**, **Mediator** | weebot cqrs |
| `services/vs_sampler` | Verbalized Sampling | FP | Template Method (prompt→distribution→tail-sample), pure selection | — |
| `services/cove_verifier` | Evidence verification loop | OOP shell/FP | **Template Method** (draft→plan→execute-factored→revise), Strategy (verifier) | — |
| `services/rerank` | Order findings | FP | Strategy (scorer), pipeline composition | scoring |
| `services/dedup` | Merge duplicates across articles | FP | Strategy (embedding sim), clustering | — |

### Infrastructure
| Module | Responsibility | Par | Patterns |
|---|---|---|---|
| `llm/adapters` | Anthropic/OpenAI/Google clients | OOP | **Adapter** behind `LLM` port |
| `llm/decorators` | retry · circuit-breaker · cache · budget | OOP | **Decorator** stack + **Circuit Breaker** + **Proxy** (cache) |
| `router/` | Pick model per task | OOP/decl | **Strategy** + **Chain of Responsibility** (cascade) + rules table (declarative YAML) |
| `retrieval/` | dense · sparse · hybrid over corpora | OOP | **Strategy** (mode) + **Composite** (hybrid fusion) + **Repository** (corpus) |
| `citation/` | Deterministic cite parse+resolve | FP | **Interpreter/Parser** + **Specification** (`resolves()`) |
| `persistence/` | Store entities + events | OOP | **Repository** + **Event Sourcing** + Unit of Work |
| `ingest/` | Bytes → clean text per format | OOP | **Factory** (per MIME) + **Adapter** (pdfplumber/docx/bs4) |
| `parse/` | Text → structured doc tree | FP/OOP | **Builder** + **Composite** (Άρθρο→παρ.→εδάφιο) |
| `observability/` | logs/metrics/traces | OOP | **Decorator** + structured `structlog` |
| `budget_guard/` | Token/€ ceiling, graceful degrade | OOP | **Circuit Breaker** + **Token Bucket** + Strategy (degrade policy) |

---

## 5. Key routine designs (the hard ones, with pattern rationale)

### 5.1 Model routing + cascade
- **Rules table** (declarative YAML): `task_type → {model, tier, max_tokens}`. Pure lookup.
- **Cascade** = **Chain of Responsibility**: FREE handler tries; on low-confidence/failure it escalates to BUDGET, then PREMIUM. Each handler is a **Strategy**.
- Confidence floor + hard premium fallback (router-fragility guard, arXiv:2504.07113).
- Graduation: swap rules table for a learned matrix-factorization router (RouteLLM) behind the same `Router` port — zero change upstream.

### 5.2 Lens fan-out (orchestrator-worker + parallelization)
- Orchestrator = deterministic `decompose(article) -> [LensTask]`. **No LLM** picks the lenses; the set is fixed config.
- Each `LensTask` = **Command**; each lens = **Strategy**. Run via `asyncio.TaskGroup` with a **semaphore** (bounded parallelism) + budget guard.
- Workers are **stateless and isolated** — no shared memory during analysis (preserves diversity; spec §6 + VS). Output = list of candidate `Finding`.

### 5.3 Verbalized Sampling (inside each lens)
- **Template Method**: `build_vs_prompt(lens, article, k) → call → parse_distribution → sample_tail`.
- One call returns k candidates *with probabilities* (not k separate calls — avoids mode collapse + saves tokens, O3). Tail-weighted `sample` is a pure function.

### 5.4 Aggregation blackboard
- Append-only, **schema-grounded** mutations (PatchBoard): a contribution is a typed `BlackboardEvent`, validated against schema before it lands → auditability.
- **Observer**: dedup, rerank, skeptic subscribe to postings. **Controller** (Mediator) schedules bounded rounds; adaptive stop (converged simple findings exit in 1 round).
- Public space (findings) + private space (agent scratch). State derived by **reducer** over events (event sourcing).

### 5.5 Adversarial Skeptic (calibrated)
- **Chain of Responsibility** of typed gates: numeric · temporal · obligation/entitlement · factual (per LegalHalluLens typing, U3/U4). Gate strength is **asymmetric**, tuned per measured failure mode.
- Runs in **fresh context, blind to author** (anti-sycophancy, U5). Survivor → confidence up; refuted → dropped with recorded reason.

### 5.6 Evidence binding = CoVe factored (O1/U1)
- **Template Method**: `draft → plan_verification_questions → execute_independently(factored) → revise`.
- The factual check is **not** an LLM: it calls the **deterministic citation parser** (§5.7). LLM only frames questions; facts resolve against the corpus index. Judge handles faithfulness/structure only (U6).

### 5.7 Deterministic citation parser (Interpreter + Specification)
- **Interpreter/Parser**: regex+grammar extract references from source text → normalize to `Citation` value objects (ΦΕΚ issue/year/no, CELEX, ECLI).
- **Specification** `CitationResolves`: valid iff the id resolves against the retrieval index. Pure, deterministic, auditable — the cheapest anti-hallucination component. No model in the fact loop.

### 5.8 Hybrid retrieval (Strategy + Composite)
- Strategies: `DenseRetriever` (GreekLegalBERT v2 / e5), `SparseRetriever` (BM25). **Composite** `HybridRetriever` fuses (RRF) + rerank.
- **Repository** per corpus (EUR-Lex CELLAR, gov-et-laws, Nomothesia) behind one `Retrieval` port; backpressure (token bucket) on CELLAR.
- Decision rule (U5): **inject+cache the bill**, **retrieve** the large external corpus.

### 5.9 Report generation (Builder + Visitor + Template Method)
- **Visitor** walks the finalized finding graph; **Builder** assembles each report; **Template Method** = per-report skeleton; **Strategy** selects report type (Exec Summary, Article-by-Article for v1).

---

## 6. Cross-cutting concerns

- **Error handling**: typed domain exceptions; Result-style returns at pure boundaries; explicit handling at every layer; never swallow. External failures wrapped at the adapter.
- **Config**: `pydantic-settings`, 12-factor, secrets from env/secret-manager; validate at startup (fail fast).
- **Observability**: `structlog` structured events + Prometheus metrics + trace id per run; every stage emits an event (doubles as audit log).
- **Concurrency**: structured async, semaphore-bounded fan-out, cooperative cancellation on budget trip.
- **Cost**: budget guard (token bucket) per run; degrade policy = fewer paths → fewer lenses → cheaper tier → abstain; prompt-cache invariant context (O6).
- **Testing** (TDD, ≥80%): pure domain = exhaustive unit tests; ports = contract tests with fakes; workflow = integration on gold-set; **eval harness** = precision/recall + Risk-Direction-Index (invented vs missed), per finding-type (U3); parser perturbation tests on messy bills (U7).
- **Reproducibility**: injected seed/clock; prompt hashing; replay from event log.

---

## 7. Phased build (each phase = paradigms/patterns that land + exit gate)

### Phase 0 — Foundation + Eval  *(nothing ships until the harness scores a bill)*
- Fork weebot skeleton → strip non-core domains; keep Clean-Arch spine, import-linter, LLM adapter, event store, CQRS, eval.
- Stand up: `config/` (pydantic-settings), `ports/`, `domain/models` (Article, **Finding=IRAC**, Evidence, Citation — frozen).
- `ingest/` (Factory per format), `parse/` (Builder+Composite → Άρθρο tree).
- Wire providers behind `LLM` port + **budget guard** (Circuit Breaker + Token Bucket) from commit 1.
- **Eval harness**: gold-set (2–3 real bills + Βουλή reports); scorer with **RDI + typed** metrics (U3); reuse `application/eval`.
- Patterns landed: Ports&Adapters, Factory, Builder, Composite, Repository, Circuit Breaker, Specification (skeleton).
- **Exit:** harness runs end-to-end on a stored bill and emits a (baseline, empty-model) score.

### Phase 1 — Single-lens vertical slice
- `workflow/` **State** machine (minimal stages); one lens as **Strategy/Command**; deterministic orchestrator `decompose`.
- 1 path, no VS yet. Emit 1 `Finding` with a raw citation. Measure vs gold-set → baseline number.
- Patterns: State, Strategy, Command, Template Method (stage lifecycle).
- **Exit:** one article → one scored finding; run is replayable from the event log.

### Phase 2 — Ensemble
- 5 lens-workers; **Verbalized Sampling** service (Template Method, k≈5, tail-sample); parallel **fan-out** (TaskGroup+semaphore).
- `dedup` + `rerank` (Strategy/functional); intra-run only (no cross-article yet).
- **Router** rules table + **cascade** (CoR) live.
- Patterns: Strategy (×lenses/scorers), CoR (cascade), structured concurrency.
- **Exit:** measured precision/recall lift over Phase 1; cost/bill within budget-guard ceiling.

### Phase 3 — Adversarial + Evidence *(credibility)*
- **Blackboard** aggregation (Observer + schema-grounded events); **Skeptic** (CoR typed gates, blind/fresh); **CoVe** evidence loop (Template Method, factored).
- **Deterministic citation parser** (Interpreter + Specification) + **hybrid retrieval** (Strategy+Composite) over EUR-Lex CELLAR + gov-et-laws.
- Cross-article dedup; confidence calibration + **abstention gate** (Specification).
- Patterns: Blackboard, Observer, Event Sourcing, Interpreter, Specification, Composite.
- **Exit:** every shipped citation resolves against the parser; hallucinated cites → 0 shipped; RDI shows controlled invention rate.

### Phase 4 — Improvement + Reports *(MVP complete)*
- `improver` (Strategy minimal/reform, Builder); report renderers (**Visitor + Builder + Template Method**): Exec Summary + Article-by-Article.
- Patterns: Visitor, Builder, Template Method, Strategy.
- **Exit:** full run on a real bill produces both reports; end-to-end replayable + auditable; eval beats the single-pass baseline on the gold-set.

### Post-MVP (Phase 5+)
Knowledge graph (Composite+Graph) · full debate · learned RouteLLM router (swap behind `Router` port) · interactive chat (CQRS query side) · continuous learning · on-prem retrieval swap (U8) · more lenses/report types.

---

## 8. Definition of Done (per phase gate)

- [ ] import-linter: no inward-dependency violations.
- [ ] Domain modules are pure (no I/O import); ≥80% coverage; property tests where math/clustering.
- [ ] Every effect behind a Port; each Port has a fake + contract test.
- [ ] Run is reproducible from the event log (same seed → same findings).
- [ ] Budget guard enforced; cost/bill recorded.
- [ ] Eval harness score recorded (typed precision/recall + RDI) and improved vs prior phase.
- [ ] No mutation of passed objects; no swallowed errors; no hardcoded secrets.
- [ ] Files < 400 lines (800 hard max); functions < 50 lines.
```
