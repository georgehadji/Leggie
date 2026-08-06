# Token Optimization Implementation Plan

> **Status:** Superseded by `docs/PRODUCTION_READINESS_PLAN.md`. Kept for reference only. Active work follows the phased plan in that document.


**Date:** 2026-07-28
**Status:** PROPOSED — not started
**Diagnosis:** [TOKEN_OPTIMIZATION_RESEARCH.md](TOKEN_OPTIMIZATION_RESEARCH.md) (measurements, evidence, `subset2.log` counts)
**Operational levers:** [COST_OPTIMIZATION.md](COST_OPTIMIZATION.md) (subset runs, model shopping)
**Change gates:** every item below is classified per `leggie-change-control` §1 and carries its gate list.

Defect handles are `TOK-n` and are permanent — reference them in commits.

---

## 0. Goal and non-goals

**Goal.** Cut input tokens per full-bill run by ~70% and eliminate the call-count amplification that turns 455 logical lens calls into an unbounded number of billed calls — without losing findings.

**Non-goals.**
- Not raising `max_cost_per_run`. That is a frozen governor (change-control §3.4).
- Not swapping the Gemini family for an unproven Greek-legal model. That is COST_OPTIMIZATION.md's experiment track and is orthogonal to everything here.
- Not touching `Finding`, `IRAC`, or `Confidence`. Domain models are frozen during remediation (change-control §3.2).

**Success is a number, not a feeling.** Acceptance criteria in §7.

---

## 1. Architectural strategy

### 1.1 Paradigm

Keep the paradigm the codebase already commits to and apply it more strictly:

**Functional core, imperative shell, composed at the edges by decorators.**

- **Pure core (`leggie/domain/`)** — new logic that is *decidable without I/O* goes here as pure functions over frozen models: article→batch packing (TOK-9), lens relevance predicates (TOK-11), price arithmetic (TOK-2). These are trivially testable and need no mocks.
- **Orchestration (`leggie/application/`)** — decides *when* to call, never *how*. Batching policy, cascade policy, skeptic gating, and CoVe question policy live here, expressed against ports.
- **Effects (`leggie/infrastructure/`)** — HTTP, caching, budget accounting, retries. Each cross-cutting effect is a separate `LLMPort` implementation stacked as a Decorator, exactly as `BudgetGuardDecorator` already is.

Every optimization below lands in exactly one of those three places. If an item seems to need to straddle two, the design is wrong.

### 1.2 The one structural change everything else depends on

`LLMAdapter` (`leggie/infrastructure/llm/__init__.py`) currently does two unrelated jobs:

1. **Transport** — `generate()`: build body, POST, map errors.
2. **Structured-output ladder** — `generate_structured()`: the 4-attempt json_schema → json_object → truncation-retry → repair sequence.

Because the ladder calls `self.generate` — its *own* method, on the inside of any decorator wrapping it — every cross-cutting concern (budget, cache, usage) sees **one** call where up to **four** were billed. That single design flaw is the root of TOK-1 (undercounting), and it also blocks caching from helping retries.

**Fix: split the two responsibilities into two `LLMPort` implementations and re-stack them.**

```
                       ┌─ outermost (what callers get from the container) ─┐

  StructuredOutputDecorator   ← the 4-attempt ladder (was LLMAdapter.generate_structured)
        │  delegates every attempt to ↓ (an injected LLMPort, NOT self)
  ResponseCacheDecorator      ← content-addressed; a hit costs nothing and skips everything below
        │
  BudgetGuardDecorator        ← now sees EVERY attempt, because attempts pass through it
        │
  OpenRouterProvider          ← transport only; @with_retry stays here (transport-level concern)

                       └─ innermost ─┘
```

Properties this buys:

| Property | Why it follows |
|---|---|
| Ladder attempts are billed | Attempts traverse the guard instead of bypassing it |
| Cache hits are free | Cache sits *above* the guard, so a hit never records spend |
| Retries hit cache | A replayed truncated response is deterministic and served from cache |
| No port changes | Every layer implements the existing `LLMPort` — change-control §3.3 satisfied |
| Testable in isolation | Each decorator is unit-testable against a `FakeLLM` with zero network |

`LLMAdapter` is retained as a thin composition alias so no call site changes in this step. Composition happens in one place: `Container._create_llm()` (`leggie/infrastructure/container.py`), which already builds exactly this kind of stack.

### 1.3 Pattern assignments

| Item | Pattern | Layer | Precedent in repo |
|---|---|---|---|
| Cache / budget / usage / ladder | **Decorator** around `LLMPort` | infrastructure | `BudgetGuardDecorator` |
| Cache backend swap (memory ↔ SQLite) | **Strategy** behind a `CacheStore` Protocol | infrastructure | `CompositeReranker` vs `ModelBasedReranker` |
| Article→batch packing | **pure function** over frozen models | domain | `domain/scoring/`, `domain/clustering/` |
| "Does lens L apply to article A?" | **Specification** (`Spec[Article]`) | domain | `domain/specs/` `Spec[T]`, `AndSpec`, `FindingAdmissible` |
| Cascade / skeptic gate policy | **Policy object** injected into orchestrator/gate | application | skeptic gate Chain of Responsibility |
| Batched lens call | **Template Method** on `Lens` base (`analyze_batch` with a default loop) | application | CoVe 4-step template |
| Price table | **Value object** (frozen dataclass) | domain | frozen Pydantic models |

### 1.4 Guardrail conflicts — declare these up front, do not route around them

`.claude/hooks/guardrails.yaml` will block or prompt on three paths this plan touches. Each is legitimate; each needs an explicit decision recorded before implementation, not a workaround:

| Path | Hook behavior | Item | Justification to record |
|---|---|---|---|
| `leggie/domain/models/structured_output.py` | **DENY** | TOK-10 | Additive only: one new `BatchLensFindings` schema. Does **not** touch `Finding`/`IRAC`/`Confidence`, which is what the freeze protects. Requires explicit approval. |
| `config/routes.yaml` | **ASK** | TOK-4, TOK-8 | Model/token changes are class A by definition; the prompt is the checklist firing correctly. |
| `leggie/application/ports/` | **ASK** | none planned | The design deliberately avoids port edits. If an item starts needing one, the design is wrong — stop and redesign. |

---

## 2. Work items

Each item: what, where, how, class, gates, done-when.

### Phase 0 — Make the meter honest (blocks everything else)

No optimization can be *proven* while the accounting under-reports by up to 4×. Phase 0 ships no savings on purpose.

---

#### TOK-1 — Split transport from the structured-output ladder

**Class:** B (wiring/refactor — behavior-preserving)
**Layer:** infrastructure

**Change**
- New `leggie/infrastructure/llm/decorators.py` (or a new `ladder.py` if the file passes 400 lines): `StructuredOutputDecorator(LLMPort)` holding an injected `LLMPort` and the 4-attempt logic moved verbatim from `LLMAdapter.generate_structured`.
- Every internal `await self.generate(...)` becomes `await self._inner.generate(...)`.
- `LLMAdapter` becomes a composition helper that assembles `StructuredOutputDecorator(OpenRouterProvider(...))` and delegates. Public surface unchanged.
- `Container._create_llm()` builds the explicit stack from §1.2.

**Done when:** all existing LLM tests pass unmodified; a new test asserts that a 3-attempt ladder produces **3** `record_usage` calls on the guard (today: 1).

**Gates:** pytest, mypy, ruff, lint-imports.

---

#### TOK-2 — Correct cost arithmetic

**Class:** A (budget behavior)
**Layer:** domain (price table + arithmetic) + infrastructure (wiring)

**Change**
- New frozen value object in `leggie/domain/` (new module `domain/pricing.py`, pure — no imports outward):
  ```python
  @dataclass(frozen=True)
  class ModelPrice:
      input_per_1m: float
      output_per_1m: float
      cached_input_per_1m: float | None = None   # None → falls back to input rate
  ```
  plus a pure `estimate_cost(price, prompt_tokens, completion_tokens, cached_tokens) -> float`.
- `BudgetGuard.COST_PER_1M_TOKENS` (`infrastructure/budget_guard/__init__.py`) becomes a `dict[str, ModelPrice]`; `_estimate_cost` delegates to the pure function.
- `check()` and `record_usage()` gain a `cached_tokens: int = 0` parameter (default preserves callers).

**Why it matters:** the current single blended rate under-bills output by 4–10×, and output is exactly where the retry loops burn.

**Done when:** unit tests assert `gemini-2.5-flash` at 1000 in / 1000 out costs `0.30/1M + 2.50/1M`, not `2 × 0.30/1M`; unknown models still fall back to a conservative default (raise it — an unknown model should be assumed expensive, not cheap).

**Gates:** pytest, mypy, ruff, lint-imports. Live smoke deferred to the Phase-1 batch.

---

#### TOK-3 — Read real usage from OpenRouter

**Class:** A (adapter)
**Layer:** infrastructure

**Change** in `leggie/infrastructure/llm/adapters/openrouter.py`:
- Parse `usage.prompt_tokens_details.cached_tokens` and `cache_write_tokens` into the `LLMResponse.usage` dict (it is already `dict[str, int]`, so **no port change**).
- Investigate the request-level `usage` accounting flag against current OpenRouter docs; if it returns upstream-computed cost, prefer it over the local price table and keep TOK-2 as the offline fallback.
- Emit a structured log line per call: model, prompt/completion/cached tokens, estimated cost.

**Done when:** a live smoke log contains non-zero `cached_tokens` on at least one call *or* proves it is always zero — which is itself the evidence for §4 of the research doc.

**Gates:** full set + live smoke (bundled with Phase 1).

---

### Phase 1 — Bug fixes that are pure savings

These are defects, not trade-offs. No eval needed to justify them; only regression evidence that nothing broke.

---

#### TOK-4 — Wire route `max_tokens` through to requests

**Class:** A (pipeline behavior)
**Layer:** application

`config/routes.yaml` declares `lens_analysis.max_tokens: 6144` and `adversarial_critic.max_tokens: 8192` — with a comment explaining that 2048 truncates every `gemini-2.5-pro` verdict. Neither value reaches a request today.

**Change**
- `Orchestrator._run_lens_with_cascade`: keep `result.max_tokens` alongside `result.model`/`result.tier`; pass it to the lens constructor.
- `Lens.__init__` takes `max_tokens: int = 4096`; `_call_llm_structured` and `_analyze_with_vs` put it on the `LLMRequest`.
- `RouterPort.cascade` already returns `max_tokens` — use it on escalation too.
- `Skeptic`: rename `_select_model` → `_select_route` returning `(model, max_tokens)`; drop the hardcoded `max_tokens=2048`.

**Explicitly not done:** no new field on `LLMRequest`, no new port method. The value travels as a constructor argument through the application layer, which already knows the route.

**Done when:** a test asserts a routed lens call carries `max_tokens=6144` and a routed skeptic call carries `8192`; smoke shows the truncation-retry count drop toward zero.

**Gates:** full set + live smoke.

---

#### TOK-5 — Narrow the structured-output ladder

**Class:** A
**Layer:** infrastructure (inside `StructuredOutputDecorator` from TOK-1)

Current ladder spends a full paid `json_object` call on *any* attempt-1 failure. `subset2.log`: 13 parse failures, **0** `json_schema rejected` events — every one of those json_object calls was structurally pointless.

**New ladder**
1. `json_schema` strict.
2. On parse failure with a JSON skeleton present → **local** repair first (`StructuredResponseParser.try_repair` already exists, costs nothing).
3. On `finish_reason == "length"` → retry with doubled `max_tokens`, reusing the strict schema. Do not spend a blind `json_object` call first.
4. `json_object` fallback **only** on a 400 / unsupported-schema error — the case it was built for.
5. Paid repair round last, still guarded by the existing "no JSON skeleton → don't bother" check.

**Done when:** a regression test per branch (400 → json_object; length → doubled; malformed-but-repairable → zero extra calls). Smoke shows billed calls per logical call fall.

**Gates:** full set + live smoke.

---

#### TOK-6 — Delete the false caching claim

**Class:** A (adapter request body)
**Layer:** infrastructure

Remove `"transforms": ["cache"]` from the request body and the docstring claiming caching is handled. `"cache"` is not a documented transform value; supplying an explicit array may also be suppressing the `middle-out` default. Replace with either nothing (restores default behavior) or an explicit, documented choice.

**Done when:** the docstring describes what the adapter actually does. Smoke shows no regression in over-length handling.

**Gates:** full set + live smoke. **Note the hook will not block this, but it is class A** — it changes what goes on the wire.

---

#### TOK-7 — Deterministic lens calls (`temperature=0`)

**Class:** A
**Layer:** application

Lens calls currently inherit `LLMRequest.temperature = 0.7`. Lens analysis is an extraction/classification task, not a creative one; CoVe and Skeptic already use `0.0`. Non-zero temperature also makes the TOK-8 cache far less useful and makes runs unreproducible.

**Change:** set `temperature=0.0` on lens requests. Verbalized Sampling stays as-is — it is the *deliberate* diversity path and owns its own sampling semantics.

**Done when:** two consecutive smoke runs on the same articles produce the same finding count (allowing for provider non-determinism, which the smoke log should then quantify).

**Gates:** full set + live smoke. **Watch for a recall drop** — if findings fall materially, temperature was doing work and this reverts.

---

#### TOK-12 — Greek-ratio check on substantive fields only

**Class:** A
**Layer:** application

`Lens._maybe_retry_greek` flattens *every* string field, including citations, article numbers, and enum-ish labels, then re-issues the entire call if Greek script is under 30%. Short citation-heavy outputs can fail this legitimately, buying a duplicate call.

**Change:** score only the long free-text fields (`issue`, `rule`, `application`, `conclusion`); skip the retry entirely when there is no substantive text to judge. Keep the retry itself — it is a real quality control.

**Done when:** a unit test with a citation-heavy-but-Greek payload triggers zero retries; smoke shows the retry count fall without a Greek-quality regression.

**Gates:** full set + live smoke.

---

### Phase 2 — Response cache

---

#### TOK-8 — Content-addressed async response cache

**Class:** B (new decorator, no behavior change on cache miss)
**Layer:** infrastructure

Replaces the dead, broken `with_cache` (an `lru_cache` on an async function — it caches the coroutine, and `LLMRequest` is unhashable anyway).

**Design**
- `CacheStore` **Protocol** (infrastructure-local, not a port): `get(key) -> LLMResponse | None`, `put(key, response)`.
- Two Strategies: `MemoryCacheStore` (LRU, per-process, default in tests) and `SqliteCacheStore` (persistent, default for CLI runs — reuses the existing SQLite/WAL persistence idiom).
- `ResponseCacheDecorator(LLMPort)` computes
  `sha256(model ‖ system_prompt ‖ prompt ‖ max_tokens ‖ temperature ‖ seed ‖ canonical(response_format))`.
- **Cache only deterministic requests**: skip when `temperature > 0` and no `seed` is set. This is why TOK-7 comes first.
- Cache the response *including* `finish_reason` so a replayed truncated response drives the same ladder branch — replay must be faithful, not optimistic.
- Sits **above** `BudgetGuardDecorator`: a hit records no spend, because it costs nothing.
- Emit a `DEGRADED`-adjacent structured log on hit (`llm_cache_hit`) — change-control §3.6 forbids silent behavior.
- Opt-out flag for smoke runs that must exercise the live path.

**Done when:** re-running an identical `analyze` twice bills ~$0 on the second run; a test proves a cache hit does not call the inner port and does not touch the guard.

**Gates:** pytest, mypy, ruff, lint-imports. One live smoke to confirm the second run is free.

**Risk:** stale cache masking a prompt change. Mitigated by including every request field in the key and adding a cache-version salt bumped whenever prompt templates change.

---

### Phase 3 — Call-policy changes (each needs its own eval)

Phase 3 items change *what the pipeline decides to do*. Each one trades tokens against recall, so each ships with a measured recall comparison, not an assertion.

---

#### TOK-9 — Stop cascading on legitimately empty findings

**Class:** A
**Layer:** application

`Orchestrator._run_lens_with_cascade` treats an empty finding list as a failure and escalates budget → premium. But the lens prompts *instruct* the model to return `findings: []` when an article is clean — so the pipeline pays a premium re-run for every article with nothing wrong with it. `subset2.log` shows this firing 8 times in a partial run. **(Estimate)** at 60% empty across 455 lens calls: ~273 premium calls ≈ $1.15/run against a $5 cap.

**Change** — introduce an explicit **policy object** rather than an `if`:

```python
class CascadePolicy(Protocol):
    def should_escalate(self, outcome: LensOutcome) -> bool: ...
```

- `FailureOnlyCascadePolicy` (**new default**): escalate on exception or parse failure only.
- `EmptyOrFailureCascadePolicy`: today's behavior, retained so the A/B is a config flip and the old path stays reachable.

Injected into `Orchestrator`; bound in `container.py`.

**Done when:** an A/B smoke on the same bill reports findings-count and per-lens recall for both policies. Adopt `FailureOnly` **only if** finding count is statistically unchanged. If empty-cascade genuinely recovers findings, the answer is a *targeted* retry (e.g. only for articles the Stage-0 preview flags as substantive), not a blanket one.

**Gates:** full set + live smoke A/B.

---

#### TOK-10 — CoVe: one batched answer call

**Class:** A
**Layer:** application

`CoVeVerifier` spends up to 5 premium calls per finding: plan (1) + one call **per question** (up to 3, sequential, each re-sending the full source article) + cross-check (1).

**Change**
- `_answer_factored` issues **one** call carrying the source text plus all questions, returning a list of answers.
- The factored property is preserved: what makes the step "factored" is that the **baseline finding is absent from context**, not that questions are isolated from each other. Batching does not reintroduce the baseline.
- `_MAX_QUESTIONS`: 3 → 2 unless the eval shows the third question changes verdicts.
- Route the answer step to the budget tier (extracting whether a source text supports a claim is cheap); keep cross-check on premium.
- Run the free deterministic quote/citation gate *before* LLM CoVe; only survivors get paid verification.

**Requires:** a new `CoVeAnswersResponse` schema (list of `{answer, supported_by_source}`) in `leggie/domain/models/structured_output.py` — **hook-guarded path, see §1.4**. Additive only.

**Done when:** CoVe calls per finding drop from ≤5 to ≤3; CoVe drop/revise rates unchanged vs. baseline.

**Gates:** full set + live smoke with CoVe drop-reason breakdown.

---

#### TOK-13 — Gate the skeptic on confidence/severity

**Class:** A
**Layer:** application

`adversarial_critic` runs `gemini-2.5-pro` — a reasoning model billing ~2000 thinking tokens — once per finding. The flow already dedups and reranks first (correct order); the remaining lever is not spending a premium critic on findings the lens itself graded `ABSTAIN` / very-low.

**Change:** express the gate as a **Specification** in `leggie/domain/specs/` — `WorthCriticising(Spec[Finding])`, composable with the existing `MeetsSeverityThreshold` via `AndSpec`. `CalibratedSkeptic` consults it before dispatching the LLM gate. Deterministic gates (Numeric/Temporal/Factual/Obligation) still run on everything — they are free.

**Done when:** premium critic calls fall; no finding that would have been *refuted* survives because it was skipped (measure by running both paths on one bill and diffing verdicts).

**Gates:** full set + live smoke A/B.

---

### Phase 4 — Prompt shape (the large win, the large risk)

---

#### TOK-11 — Batch articles per lens call

**Class:** A — the highest-risk item in this plan
**Layer:** domain (packing) + application (dispatch, prompts) + domain models (schema)

**Rationale.** Measured: articles are 110 tokens at the median; per-call boilerplate is 714. 79% of lens-stage input tokens are the same bytes re-sent 455 times.

```
before: 455 calls × 714 boilerplate + 86K article  ≈ 411K input tokens
after:   50 calls × 714 boilerplate + 86K article  ≈ 122K input tokens   (−70%)
```

It also drags each request over the 1,024-token Gemini Flash prompt-cache floor, making prompt caching reachable for the first time — the two compound.

**Design**

1. **Pure packing function** in `leggie/domain/` — `pack_articles(articles, max_tokens, max_count) -> list[tuple[Article, ...]]`. Token-budget-aware, deterministic, order-preserving, no I/O, exhaustively unit-testable. A single article larger than the budget forms its own batch.
2. **Schema** — new `BatchLensFindings` in `structured_output.py`: `list[ArticleFindings]` where `ArticleFindings = {article_id: str, findings: list[IRACCandidate]}`. `IRACCandidate` has no `article_id` field today, so attribution must be carried by the grouping. **Hook-guarded path — see §1.4.** Additive only; `LensFindings` stays for the single-article path.
3. **Template Method on `Lens`** — add `analyze_batch(articles) -> list[Finding]` to the base class with a **default implementation that loops over `analyze()`**. Every existing lens keeps working with zero edits; lenses opt into true batching by overriding. `Lens` is a Strategy in the application layer, not a Port — change-control §3.3 does not apply.
4. **Orchestrator** — `decompose()` emits one `LensTask` per (lens, batch) instead of per (lens, article). Parallel fan-out, semaphore, and per-batch failure isolation stay exactly as they are (D3/D6 must not regress).
5. **Failure isolation is now coarser** — one malformed batch response loses N articles instead of 1. Mitigation: on batch parse failure, fall back to per-article calls for that batch only. This is a bounded, explicit degradation and emits `DEGRADED`.
6. **Batch size is configuration**, not a constant: `LEGGIE_ANALYSIS__ARTICLES_PER_LENS_CALL` (default `1`, i.e. **off**) so the feature ships dark and is enabled by evidence.

**Done when:** a gold-set sweep at batch size 1 / 5 / 10 / 20 reports precision, recall, and F1 per size. Adopt the largest size whose F1 is within noise of size 1. **If no size holds quality, this item is retired and documented as retired** — the arithmetic alone is not permission to ship.

**Gates:** full set + live smoke + eval sweep. This item does not merge on smoke alone.

---

### Phase 5 — Skip work that was never needed

---

#### TOK-14 — Lens relevance pre-filter

**Class:** A
**Layer:** domain (specification) + application (dispatch)

Many articles are procedural boilerplate (entry into force, repeals, definitions). Running the EU/GDPR lens on an article with no personal-data concept costs a full call to produce `findings: []`.

**Design** — Specification pattern, reusing the existing `Spec[T]` machinery in `leggie/domain/specs/`:

```python
class LensApplicable(Spec[Article]):
    """Deterministic, zero-cost predicate: could lens L plausibly find anything here?"""
```

- Per-lens Greek lexicons/regexes (GDPR: personal-data, processing, controller terms; economic: fiscal/cost/appropriation terms; …), composable via the existing `AndSpec`/`OrSpec`/`NotSpec`.
- **Fail-open by default**: uncertain → run the lens. A pre-filter that loses findings is worse than one that saves nothing.
- Ships behind `LEGGIE_ANALYSIS__LENS_PREFILTER=off|advisory|enforcing`. **`advisory` first**: log what *would* have been skipped without skipping it, for one full run. Only promote to `enforcing` once the advisory log shows the skipped set contains no real findings.

**(Estimate)** −30 to −50% of lens calls if it holds.

**Done when:** the advisory run proves zero findings would have been lost, then an enforcing run reproduces baseline findings at lower cost.

**Gates:** full set + live smoke + gold-set recall comparison.

---

## 3. Dependency graph

```
TOK-1 (split transport/ladder)
  ├── TOK-2 (price VO) ──┐
  ├── TOK-3 (real usage) ─┴── honest meter ── required by every item below
  ├── TOK-5 (narrow ladder)
  └── TOK-8 (cache decorator) ←── requires TOK-7 (temperature=0) to be useful

TOK-4 (route max_tokens) ── independent, do early: it removes a whole class of retries
TOK-6 (transforms) ─────── independent
TOK-12 (Greek check) ───── independent

TOK-9  (cascade policy) ── needs honest meter to prove the saving
TOK-10 (CoVe batching) ─── needs new schema (hook-guarded)
TOK-13 (skeptic gate) ──── needs domain spec

TOK-11 (article batching) ← needs TOK-1, TOK-4, honest meter, gold-set eval
TOK-14 (lens prefilter) ─── last; largest recall risk; needs the eval harness warm
```

**Critical path:** TOK-1 → TOK-2/3 → everything. Do not start Phase 3 or 4 before the meter is honest; you will not be able to tell whether a change helped.

---

## 4. Sequencing

| Order | Items | Ships savings? | Merge evidence |
|---|---|---|---|
| 1 | TOK-1, TOK-2, TOK-3 | No — instrumentation | Offline gates; one smoke to capture a **new honest baseline** |
| 2 | TOK-4, TOK-5, TOK-6, TOK-7, TOK-12 | Yes — pure defect removal | Offline gates + one bundled live smoke; billed-calls-per-logical-call must fall |
| 3 | TOK-8 | Yes — on re-runs | Second identical run costs ~$0 |
| 4 | TOK-9, TOK-13 | Yes — policy | A/B smoke, findings count unchanged |
| 5 | TOK-10 | Yes — policy | CoVe drop rates unchanged |
| 6 | TOK-11 | Yes — largest | Gold-set sweep; F1 within noise |
| 7 | TOK-14 | Yes | Advisory run clean, then enforcing |

Batch the Phase-1 items into **one** live smoke. Smoke costs money; five separate smokes for five defect fixes is its own waste.

---

## 5. Testing strategy

Per `leggie-validation-and-qa`: green tests are necessary and never sufficient.

**Unit (offline, no network)**
- Pure functions (`pack_articles`, `estimate_cost`, `LensApplicable`) — exhaustive, no mocks needed. This is the payoff for putting them in the domain.
- Each decorator against a `FakeLLM` recording call counts. The assertions that matter are **call counts**, not just outputs: "a 3-attempt ladder records 3 usages", "a cache hit calls the inner port zero times".
- Malformed-payload regression fixtures for every new ladder branch (existing pattern in `tests/unit/infrastructure/test_phase1_structured_output.py`).

**Integration**
- Full flow against a fake LLM asserting total call count for a known document — this is the regression net for every call-count change in this plan. Add it in Phase 0 so later phases have a baseline to diff.

**Live smoke (class A only)**
Per `REMEDIATION_PLAN.md` §10 thresholds, plus these plan-specific metrics:

| Metric | How | Direction |
|---|---|---|
| Billed calls per logical call | `grep -c 'chat/completions'` ÷ (lenses × articles) | must fall toward 1.0 |
| Total cost | `flow.budget_state` — **only comparable after TOK-2/3** | down |
| `cached_tokens` share | new adapter log | up (or provably 0) |
| Parse-failure count | smoke log stats | down |
| Truncation retries | smoke log stats | → 0 after TOK-4 |
| Finding count / survivor ratio | findings JSON | **unchanged** |

The last row is the gate that matters. Cheaper-and-fewer-findings is not a win; it is a regression with a nice cost graph.

---

## 6. Rollback

Every behavior-changing item is a config flip, not a code revert:

| Item | Kill switch |
|---|---|
| TOK-8 cache | `LEGGIE_ANALYSIS__RESPONSE_CACHE=off` |
| TOK-9 cascade | rebind `EmptyOrFailureCascadePolicy` |
| TOK-11 batching | `ARTICLES_PER_LENS_CALL=1` (the default) |
| TOK-13 skeptic gate | spec returns always-true |
| TOK-14 prefilter | `LENS_PREFILTER=off` (the default) |

Items TOK-1 through TOK-6 and TOK-12 are defect fixes with no toggle — they revert by `git revert`.

---

## 7. Acceptance criteria

The plan is complete when a full 5-lens run on the 91-article reference bill shows **all** of:

1. **Input tokens** for the lens stage ≤ **150K** (baseline ≈ 411K) — i.e. ≥60% reduction.
2. **Billed calls ÷ (lenses × articles)** ≤ **1.15** (today: well above 1, exact figure to be established by the Phase-0 honest baseline).
3. **Truncation retries = 0**; parse-failure rate **< 5%** (REMEDIATION_PLAN §10).
4. **Finding count and survivor ratio within noise of the pre-optimization baseline** — measured on the gold set, not eyeballed.
5. **Total run cost** reported by a *corrected* `BudgetGuard`, with the correction itself validated against OpenRouter's own accounting.
6. A **second identical run costs ~$0** (cache).
7. `cached_tokens` is either non-zero, or documented as unreachable with the measured prefix length as evidence.

Criterion 4 outranks all the others. Any item that cannot hold it is retired and documented as retired — per `leggie-research-methodology`, a retired idea with a measured reason is a result, not a failure.

---

## 8. Rejected alternatives

| Alternative | Why rejected |
|---|---|
| "Just enable prompt caching" | **Impossible at current prompt shape.** Constant prefix is 366–451 tokens; Gemini Flash floor is 1,024, Pro is 4,096. Measured, not assumed. Becomes possible only *after* TOK-11. |
| Pad prompts to reach the cache floor | Paying for padding tokens on every call to unlock a discount on padding tokens. Net loss. |
| Put the article first so 5 lenses share a cached prefix | Median article is 110 tokens — three orders of magnitude short of a cacheable prefix. |
| Merge all 5 lenses into one call per article | Amortizes boilerplate but destroys the Strategy separation, the per-lens routing, and per-lens failure isolation. TOK-11 gets the same savings along the axis (articles) that does not carry architectural meaning. |
| Shrink the system prompts | The prompts encode the anti-filler rules that were added to fix a real quality incident. Cutting them re-opens a settled problem to save ~100 tokens/call. Wrong axis. |
| Add `task_type` / `max_tokens` resolution to `LLMPort` | New port surface, violates change-control §3.3. The application layer already knows the route; pass the value as an argument. |
| Raise `max_cost_per_run` so full runs complete | Frozen governor (change-control §3.4). The point is to fit the budget, not move it. |

---

## 9. Open questions to resolve during implementation

1. **Output-token split is still unmeasured.** All §2 arithmetic is input-side. COST_OPTIMIZATION.md asserts output dominates; that is untested and untestable until TOK-2/TOK-3 land. Both claims can be true at once. **Resolve in Phase 0.**
2. **Does empty-finding cascade recover any findings at all?** If not, TOK-9 is free. If yes, it needs a targeted trigger. **Resolve in Phase 3.**
3. **How many articles per batch before quality degrades?** Sweep 1/5/10/20. **Resolve in Phase 4.**
4. **Is the OpenRouter request-level `usage` accounting flag available and accurate?** If yes, it replaces the local price table and TOK-2 becomes an offline fallback only. **Resolve in Phase 0.**
