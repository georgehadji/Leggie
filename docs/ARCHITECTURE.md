# Leggie — Architecture Decision

> Question: *what is the optimal software architecture for Leggie?*
> Answer, in one line: **a layered Clean/Hexagonal core (reused from weebot) running a deterministic workflow-DAG, with orchestrator-worker parallel fan-out for independent analysis, a bounded schema-grounded blackboard for aggregation, over a durable event-sourced spine — NOT an autonomous agent swarm.**

---

## 1. The load-bearing insight

The spec says "multi-agent", which tempts an autonomous agent swarm. **Resist it.** Leggie's macro flow is *known and fixed*: ingest → parse → decompose → analyze → critic → evidence → rerank → dedupe → improve → report. The stages never change per run. That is a **workflow/DAG**, not an agent deciding its own control flow.

Why this matters (evidence):
- Autonomous multi-agent carries **+58% (independent) to +285% (centralized) token overhead**; it only pays when the task genuinely needs specialization/parallelism/critique ([multi-agent orchestration, 2026](https://beam.ai/agentic-insights/multi-agent-orchestration-patterns-production)).
- Anthropic: teams "invest months building elaborate multi-agent architectures only to discover improved prompting on a single agent achieved equivalent results" ([Anthropic, Building Effective Agents](https://www.anthropic.com/research/building-effective-agents)).
- Leggie's NFRs — **reproducible, deterministic-where-required, auditable, explainable** — are *directly served* by a deterministic control flow and *directly harmed* by an LLM choosing the pipeline.

**Rule: LLM autonomy is confined to *inside* a stage (analysis, critique, drafting). The control flow between stages is deterministic code.** This kills the "orchestrator LLM = bottleneck + single failure point" problem and makes every run replayable.

---

## 2. Pattern per stage (controlled → autonomous spectrum: pipeline · orchestrator-worker · hierarchy · blackboard)

| Stage | Pattern | Why |
|---|---|---|
| Ingest / Parse / KG | **Deterministic pipeline** | No LLM control flow; pure transforms. Predictable, trivially debuggable. |
| Article → lens decomposition | **Orchestrator-worker** | Orchestrator (thin, mostly deterministic) splits an article into lens-subtasks and dispatches. Single brain accountable, no worker collisions. |
| 5 lenses × k reasoning paths | **Parallelization (fan-out)** | Stateless lens-workers run **in parallel and independently** — they must NOT see each other's output (preserves diversity per spec §6 + Verbalized Sampling). Shared state here would cause premature convergence. |
| Findings → dedupe → rerank → adversarial Skeptic → evidence | **Bounded blackboard** | *After* independent analysis, findings post to a shared board where cross-pollination is now *wanted*. bMAS blackboard gives **+13–57% over RAG/master-slave** and **lower token use** (shorter per-agent prompts) ([arXiv:2510.01285](https://arxiv.org/pdf/2510.01285)). Use **schema-grounded, auditable mutations** (PatchBoard, [arXiv:2605.29313](https://arxiv.org/pdf/2605.29313)) → serves the auditability NFR. Rounds are adaptive: simple findings converge in one step, complex ones get more. |
| Improve / Report | **Deterministic pipeline** | One-shot suggestion + verify (per O2/DELEGATE-52: no long edit chains on legal text). |

The critical nuance: **independence during analysis, blackboard during aggregation.** Blackboard is the wrong tool for the analysis phase (it would collapse diversity); it is the right tool for the debate/critic/merge phase.

---

## 3. Layers (Clean / Hexagonal — reuse weebot)

```
Interfaces (CLI · Web/FastAPI · MCP)
        ↓ depends inward
Application (Workflow-DAG · Orchestrator · Lens-workers · Blackboard svc · CQRS)
        ↓
Infrastructure (LLM adapters · Router · Retrieval(hybrid) · Blackboard store · Event store · Budget guard)
        ↓
Domain (immutable Pydantic: Article · Finding(IRAC) · Evidence · Plan · Event)
```
Dependencies point **inward only** (weebot's existing rule, enforced by import-linter). Ports: `LLM`, `Router`, `Retrieval`, `State`, `EventBus`, `Blackboard`, `CitationParser`. Provider-agnostic by construction.

---

## 4. Durable execution spine

A bill run is long (hours), expensive (many LLM calls), and must survive crashes and be resumable (NFR: fault-tolerant, reproducible). Every stage boundary is a **checkpoint**; every finding mutation is an **immutable event** (event sourcing) → replay, audit, explainability all fall out of the same log.

**Choice:** extend weebot's **existing flow state-machine + event store** into the durable spine. It already has `IDLE→PLANNING→EXECUTING→…` + `event_store.py`. Add stage-level checkpointing.

Rejected for v1:
- **Temporal** — battle-tested but **cluster ops overhead** too heavy for this stage; revisit at multi-tenant scale.
- **DBOS** (Postgres-as-durability, minimal ops) — strong candidate *if* weebot's state machine proves insufficient; keep as the graduation path.
- **LangGraph** — good checkpointable graph + native cycles (retry/critic loops); acceptable to adopt *for the agentic stages only* if hand-rolled fan-out gets unwieldy. Do not let it own the whole control flow.

Principle: **don't add a heavy engine until the reused weebot spine actually breaks.** (KISS / YAGNI.)

---

## 5. Concurrency & scale

- Lens-workers are **stateless** → horizontally scalable; fan-out via an **async task queue** (weebot already async; reuse its queue/worker infra).
- **Budget guard** + **router** sit in front of every worker LLM call (cost-aware, provider-agnostic).
- Backpressure on external corpora (EUR-Lex CELLAR: <5 concurrent, backoff on 429/503).

---

## 6. Why this is optimal (NFR mapping)

| NFR | Served by |
|---|---|
| reproducible / deterministic-where-required | Deterministic DAG control flow; LLM only inside stages; seeded prompts |
| auditable / explainable | Event-sourced log + schema-grounded blackboard mutations |
| fault-tolerant | Durable stage checkpoints; resume-where-left-off |
| horizontally scalable / async | Stateless workers + task-queue fan-out |
| cost-aware | Blackboard shortens prompts; orchestrator-worker only where parallelism pays; avoid autonomous swarm (+285% overhead); router (~85% save @ 95% quality) |
| provider-agnostic | LLM/Router ports; no vendor lock in domain/application |
| modular / extensible | New lens = new stateless worker; new report = new pipeline sink; no core change |

---

## 7. What to reuse vs build

**Reuse from weebot:** Clean-Arch layering + import-linter rule · flow state-machine · `event_store.py` · CQRS mediator · DI container · LLM adapter + cascade/router · SQLite/WAL persistence · async queue · `bash_guard` pattern → budget guard · `application/eval` harness.

**Build new:** Greek legal parser (Άρθρο/παρ.) · IRAC `Finding` schema · lens-worker set (5) · Verbalized-Sampling wrapper · **bounded blackboard service** (schema-grounded) · adversarial Skeptic (calibrated) · **deterministic citation parser** (ΦΕΚ/CELEX/ECLI) · hybrid retrieval over EUR-Lex/legislation corpora · report renderers.

---

## 8. One-paragraph summary

Leggie is a **deterministic, event-sourced legal-analysis workflow** whose LLM intelligence is fanned out into independent, parallel, stateless lens-workers (orchestrator-worker + parallelization) and then converged on a **bounded, auditable blackboard** (dedupe → rerank → calibrated adversarial critique → evidence binding). It runs on a Clean/Hexagonal core reused from weebot, with a durable checkpointed spine for fault tolerance and full replayability. It is explicitly **not** an autonomous agent swarm — the control flow is code, the reasoning is model — which is exactly what makes it reproducible, auditable, cost-bounded, and cheap to extend.

---

## 9. The deliberative pipeline — a bounded exception, not a departure

`--pipeline deliberative` (§1's default remains `deterministic`) delegates multi-model
generation/critique/synthesis to an external service (Reasoner) rather than reimplementing
it inside Leggie. This is a deliberate, narrow exception to §1's rule, not a reversal of it:

- **Still a fixed DAG, not an autonomous agent.** The two-stage sequence (Stage 1 generate →
  Stage 2 audit → assemble → persist) is hard-coded in `DeliberativeFlow`. Leggie never lets
  an LLM choose its own control flow here either — it just hands one *step* of that fixed DAG
  to a specialist external system, the same way a lens-worker hands a step to an LLM call.
- **Opt-in, never default.** `LEGGIE_REASONER__ENABLED=false` by default; the deterministic
  `analyze` path (§2–§7 above) is byte-for-byte unaffected by this pipeline's existence.
- **Non-determinism is quarantined.** Raw synthesis, models used, tokens, and cost are
  captured as events for replay — the run is *auditable*, even though its content is not
  bit-reproducible run-to-run (unlike the deterministic pipeline's citation parser/scoring).
- **No verification claim.** Output is prose, not `Finding` objects — it explicitly does not
  go through Skeptic/CoVe, so it does not carry the same admissibility guarantees as the
  deterministic path's findings. An optional citation appendix (deterministic ΦΕΚ/CELEX/ECLI
  parser over the prose) is the only verification sliver retained.

See `implementation_plan.md` for the full design rationale and work breakdown.
