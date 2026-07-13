# Leggie Remediation Plan

**Date:** 2026-07-10
**Branch:** `fix/model-ids-vfm-and-plan`
**Author:** engineering pass (post live-smoke diagnosis)

This plan fixes every open defect found in the live smoke runs and the
plan-vs-implementation audit, in priority order, **respecting the Clean /
Hexagonal architecture** (dependency rule points inward:
`Interfaces → Infrastructure → Application → Domain`; Domain depends on nothing
outer). Each item names the exact layer it lives in and never violates that rule.

---

## 0. Current state (what already works — do not re-touch)

Confirmed working live this session:

- **Router** (`StaticRouter`, `config/routes.yaml`) — per-task model + cascade FREE→BUDGET→PREMIUM. Wired, tested, exercised live.
- **Budget guard token/cost cap** — fixed (token ceiling 500k→20M so the `$5` cost cap is the real governor). Live runs show zero premature blocks.
- **`--lenses` flag** — threaded CLI→handler→flow→orchestrator.
- **CoVe (Chain-of-Verification)** — real 4-step factored loop. Live proof: `cove_quote_fail` dropped 2 fabricated-quote findings; citation gate wired.
- **Skeptic LLM adversarial gate** — wired via `adversarial_critic` route (fires live; verdict parse currently blocked by the schema-drift defect below).
- **Budget checkpoint (G4)** — `--checkpoint PATH`, load-on-start + save-per-stage.

All 326 unit+integration tests green; mypy clean on touched modules.

---

## 1. Defect inventory (ranked by yield impact)

| # | Defect | Layer | Evidence | Severity |
|---|--------|-------|----------|----------|
| D1 | **Schema drift** — LLM returns JSON with wrong field names (`lens_id`, `title`, `issue_id`) → pydantic rejects → finding lost | Infrastructure (LLM adapter) | 134 `pydantic … Field required` errors across ~90 articles; **1 finding survived** the whole run | CRITICAL |
| D2 | **Truncated JSON** — `Unterminated string` / `Expecting value: line 1 col 1` → parse crash → cascade churn | Infrastructure (LLM adapter) | multiple `Failed to parse structured response: Unterminated string` | HIGH |
| D3 | **Sequential article loop** — flow awaits one article at a time; parallel `analyze_document()` exists but is never called | Application (flow) | `bill_analysis_flow.py:157` `for article in self._doc.articles` | HIGH (perf) |
| D4 | **Verbalized Sampling (EN4) dead** — `use_verbalized_sampling` flag never set true anywhere | Application (orchestrator/lens) | `lens.py:33,37`; no caller | MEDIUM |
| D5 | **ModelBasedReranker unwired** — LLM/Cohere reranker built, only `CompositeReranker` constructed | Application (flow) | `rerank.py`; flow line ~78 | MEDIUM |
| D6 | **Lens failure isolation** — one lens exception in `asyncio.TaskGroup` cancels sibling lenses for that article | Application (orchestrator) | `orchestrator.py` TaskGroup fan-out | MEDIUM |
| D7 | **Citation resolution corpus empty** — parser wired but `resolution_index` is empty → citations only "unverified", never positively resolved | Infrastructure (citation) | `container.py:125` `GreekCitationParser()` no index | MEDIUM |
| D8 | **Container vs ad-hoc factory duplication** — `_resolve_*` still falls back to parallel `_try_get_*` when `container is None` | Application (handlers) | `cli_handlers.py:95-167` | LOW |
| D9 | **No rate limiter on LLM calls** — `RateLimiter` instance registered but not applied to the OpenRouter path | Infrastructure | `container.py:137` registers, adapter never consumes it | LOW |
| D10 | **Resume-from-stage (G1)** — only budget spend is checkpointed, not flow stage/findings; a crash still re-runs completed stages | Application (flow) | G4 done, G1 open | LOW |

---

## 2. Phase 1 — Structured-output reliability (D1 + D2)  ← **highest yield, do first**

**Goal:** stop losing real findings to malformed JSON. This single phase should
take survivors-per-run from ~1 to the true signal count.

All changes live in **Infrastructure** (`leggie/infrastructure/llm/`) behind the
existing `LLMPort.generate_structured` contract — no Application/Domain change,
no interface drift.

### 1a. Use real JSON-schema enforcement, not bare `json_object`
`openrouter.py` currently only ever sends `{"type": "json_object"}` (valid-JSON,
free-form fields). Switch structured calls to OpenRouter **strict json_schema**
mode so the provider is forced to emit the exact field names:

- Add a helper `pydantic_to_json_schema(schema: type[BaseModel]) -> dict` (new file `leggie/infrastructure/llm/schema_format.py`).
- In `LLMAdapter.generate_structured`, build:
  ```python
  response_format = {
      "type": "json_schema",
      "json_schema": {"name": schema.__name__, "strict": True,
                      "schema": pydantic_to_json_schema(schema)},
  }
  ```
- Keep `{"type": "json_object"}` as a **fallback** when a model rejects
  json_schema (some models 400 on it): catch that specific error and retry once
  in json_object mode. Model capability differs by tier — flash/pro support
  json_schema; keep the fallback path permanently.

### 1b. Truncation detection + retry (D2)
`OpenRouterProvider.generate` ignores `finish_reason`. Capture it:

- Thread `finish_reason` from `choice.get("finish_reason")` into `LLMResponse` (field already exists, currently hardcoded `"stop"`).
- In `generate_structured`, if `finish_reason == "length"` OR JSON parse fails
  with an unterminated-string error, retry **once** with `max_tokens` doubled
  (capped at a ceiling constant). Verbose Greek IRAC output overruns small caps.
- Raise `max_tokens` floor for structured lens calls in `config/routes.yaml`
  (`lens_analysis` 4096 is marginal for 5-field Greek IRAC — bump to 6144).

### 1c. Schema-repair retry as last resort
When both 1a and 1b still fail to parse, do one **repair round**: feed the raw
malformed content back with a terse "return ONLY valid JSON matching this schema"
instruction. Bounded to a single retry; on failure, degrade as today (empty →
cascade). Keep the existing `_normalize_irac_item` alias map as a cheap
pre-validation pass, and **extend `_IRAC_ALIASES`** with the newly observed
drifts: `lens_id`, `issue_id`, `title` → already partly covered; add
`legal_issue`, `problem`, `finding_text`.

### 1d. Centralize parsing
Extract the parse/normalize/repair ladder out of `LLMAdapter.generate_structured`
into a small `StructuredResponseParser` (Infrastructure) so both the lens path
and the CoVe/skeptic paths share identical, tested repair logic. Pure function of
`(content, schema) -> BaseModel`; unit-testable without HTTP.

**Tests (Phase 1):**
- Unit: `pydantic_to_json_schema` shape; parser handles bare array, `issues`
  alias, field aliases, truncated-string repair, code-fence stripping.
- Unit: provider sets json_schema body and falls back to json_object on 400.
- Regression: feed the 5 real malformed payloads captured from the smoke log as
  fixtures; assert each now parses or repairs.

---

## 3. Phase 2 — Parallel article fan-out (D3)

**Layer:** Application (`bill_analysis_flow.py`). Behaviour-preserving perf fix.

- Replace the `for article in self._doc.articles:` serial loop with a call to the
  already-built `Orchestrator.analyze_document(document, lens_names=lenses)`,
  which fans out articles via `asyncio.TaskGroup` + semaphore.
- Preserve event emission: `analyze_document` must emit the same
  `FINDING_CREATED` events per finding (thread the `_record_event` callback, or
  collect findings and emit after — keep order deterministic by sorting on
  article id for reporting).
- Respect concurrency limits: cap the semaphore from a config value
  (`settings.llm.max_concurrency`, default 5) so we do not trip OpenRouter rate
  limits. This dovetails with D9 (rate limiter).

**Tests:** existing flow tests must stay green; add one asserting N articles
produce N× findings and that a slow article does not serialize the rest
(fake-clock or gather-timing assertion).

---

## 4. Phase 3 — Reliability hardening (D6, D9)

### 3a. Lens failure isolation (D6)  — Application (orchestrator)
`asyncio.TaskGroup` cancels all siblings if one task raises. Lens dispatch
already catches per-lens exceptions in `_run_lens_with_cascade`, but confirm the
**article-level** fan-out in `analyze_document` wraps each article task so one
article's hard failure cannot abort the batch. Replace `TaskGroup` with
`asyncio.gather(*tasks, return_exceptions=True)` at the article level and log +
skip failures. (Lens level already isolated.)

### 3b. Apply the rate limiter (D9) — Infrastructure
`RateLimiter(max_rate=5.0)` is registered but never consumed. Inject it into the
OpenRouter path via a decorator (mirror `BudgetGuardDecorator`): a
`RateLimitedLLM` wrapper that `await`s the limiter before each call. Wire in
`container._create_llm` so it composes with the budget guard. Keeps Domain/
Application untouched.

---

## 5. Phase 4 — Verification depth (D7)

### Citation resolution corpus — Infrastructure
The CoVe citation gate currently can only say "unverified" (no index). Give it
real teeth:

- Build a resolution index from authoritative Greek sources. Minimum viable: a
  static JSON corpus of known ΦΕΚ / CELEX / ECLI identifiers shipped under
  `data/citation_index.json`, loaded in `container` and passed to
  `GreekCitationParser(resolution_index=...)`.
- Longer term (separate ticket, not this pass): an online resolver adapter
  (`et.gr` / `eur-lex`) behind the existing `CitationParserPort.resolve` — no
  call-site change needed, just a new adapter implementation.
- Keep the fail-closed semantics already shipped: no index entry ≠ "wrong", it's
  "unverified"; only an explicit index that lists the citation as invalid drops.

**Tests:** index hit → resolved True; index miss with populated index →
disproven drop; empty index → unverified passthrough (already covered).

---

## 6. Phase 5 — Feature completion (D4, D5)

Lower priority; these are capability upgrades, not correctness bugs.

### 5a. Wire Verbalized Sampling (D4) — Application
Add `settings.analysis.use_verbalized_sampling: bool = False` and a CLI
`--verbalized-sampling` flag → thread through `AnalyzeBillCommand` → flow →
`Orchestrator` → set `Lens(use_verbalized_sampling=...)`. Off by default (cost);
opt-in. Routes already have a `verbalized_sampling` entry.

### 5b. Wire ModelBasedReranker (D5) — Application
Make the reranker chosen by config: `settings.analysis.reranker: "composite" |
"model"`. When `"model"`, construct `ModelBasedReranker(reranker_port=…)` with
its `CompositeReranker` fallback (already built-in). Requires a `RerankerPort`
binding in `container` (Cohere/NVIDIA via OpenRouter). Default stays
`composite` (no extra cost).

---

## 7. Phase 6 — Cleanup & durability (D8, D10)

### 6a. Collapse container/ad-hoc duplication (D8) — Application/Interfaces
`cli_handlers.py` keeps both `self._container` resolution and the legacy
`_try_get_*` factories. Make the container the single composition root: always
construct handlers with a container (CLI already does), delete the `_try_get_*`
fallbacks and the `container is None` branches. Confirm no test constructs a
handler without a container; update any that do.

### 6b. Resume-from-stage (D10 / G1) — Application + Infrastructure
Extend checkpointing from budget-only to full run state:

- New `leggie/infrastructure/persistence/checkpoint_store.py` — `CheckpointStore`
  with atomic JSON writes (`{run_id, stage, findings, budget_state,
  event_count}`).
- `bill_analysis_flow.py` — `run_id`, `save_checkpoint()` at each stage boundary,
  `resume(run_id)` that reloads and re-enters at the last completed
  `resumable_state()` (add `FlowStateMachine.resumable_states()`).
- Guard against double-billing: on resume, do **not** re-run stages whose output
  is already checkpointed.

**Tests:** integration `test_resume_after_crash` — kill mid-flow, resume, assert
identical findings and that completed stages are not re-executed.

---

## 8. Execution order & dependencies

```
Phase 1 (D1,D2)  ─ schema reliability ──┐  CRITICAL, unblocks real yield
                                         ├─> Phase 2 (D3) parallel fan-out
Phase 3 (D6,D9) ─ isolation + rate limit┘   (needs concurrency cap from D9)
Phase 4 (D7)  ─ citation corpus (independent)
Phase 5 (D4,D5) ─ VS + reranker (independent, opt-in)
Phase 6 (D8,D10) ─ cleanup + resume (D10 builds on D8 composition root)
```

- **Do Phase 1 first and re-run the live smoke** before anything else — it is the
  dominant lever (1 → N survivors). Everything downstream is easier to validate
  once findings actually flow.
- Phases 4 and 5 are parallelizable / independent.
- Each phase ends with: full `pytest tests/`, `mypy` clean on touched modules,
  and (for Phases 1–3) a single-lens live smoke on the sample bill.

## 9. Architecture guardrails (apply to every phase)

- **Dependency rule:** all LLM/HTTP/parse/rate-limit/citation changes stay in
  Infrastructure behind existing ports. Application changes only touch
  orchestration/wiring. Domain models (`Finding`, `IRAC`, `Confidence`) are not
  modified.
- **Structured output:** every LLM response continues to validate against a
  Pydantic model in `leggie/domain/models/structured_output.py` (rule G).
- **Immutability:** findings are rebuilt via `model_copy`, never mutated.
- **No silent failure:** degradation continues to emit events; parse repairs are
  logged, not swallowed.
- **Ports unchanged:** no new methods on `LLMPort` / `RouterPort` /
  `CitationParserPort` — new behaviour rides on new adapters/decorators.

## 10. Definition of done

- Live single-lens smoke on `Inputs/OE_ΣΧΝ-ΥΠΔΙΚ.pdf` yields findings roughly
  proportional to article count (not ~1), with < 5% parse-failure rate.
- Skeptic produces at least some non-neutral verdicts (parse no longer blocks it).
- CoVe drop/revise observed with valid (non-truncated) inputs.
- Full run wall-clock cut materially by parallel fan-out.
- `pytest tests/` green; `mypy leggie/` clean; no new ports.
