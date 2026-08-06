# Leggie Remediation Plan V2 — Fix Everything

> **Status:** Superseded by `docs/PRODUCTION_READINESS_PLAN.md`. Kept for reference only. Active work follows the phased plan in that document.


**Date:** 2026-07-11
**Branch:** `fix/model-ids-vfm-and-plan` (HEAD `406f969`, clean tree, 367 tests green)
**Author:** engineering pass (post skill-library ground-truth audit)
**Supersedes:** `docs/REMEDIATION_PLAN.md` (V1) — V1 Phases 1 + verification-layer work landed
(`63fb25f`, `cb7fde8`, `406f969`); this plan carries forward everything still open and adds
doc/CI/config debt found during the 2026-07-10/11 audits.

Architecture rule for every phase (non-negotiable, per import-linter contract):
dependencies point inward — `interfaces → infrastructure → application → domain → config`.
Each item names its layer. Domain models (`Finding`, `IRAC`, `Confidence`) are not modified
anywhere in this plan. No new methods on existing ports; new behavior rides on adapters,
decorators, or new wiring only.

---

## 0. Current state (verified 2026-07-11 — do not re-touch)

- **Structured-output ladder** (json_schema strict → json_object fallback → truncation retry
  → repair round) — landed, H-1 audit finding fixed (`infrastructure/llm/__init__.py`).
- **LLM-powered verification layer** — CoVe 4-step factored loop
  (`application/services/cove_verifier.py`), Skeptic `LLMAdversarialGate`
  (`application/agents/skeptic.py`) — landed at `cb7fde8`/`406f969`.
- **Rate limiter (V1-D9)** — FIXED: `RateLimiter(max_rate=5.0)` injected into
  `OpenRouterProvider`, `await self._rate_limiter.acquire()` before each call
  (`adapters/openrouter.py:48`).
- **Budget governor** — token ceiling 20M, $5 cost cap governs (`config/settings.py`).
- **Fail-closed citation semantics** — unverified ≠ invalid (`infrastructure/citation/`).
- **Offline model-ID allowlist** — guards the fake-model-ID incident class.
- Offline gates all green: 367 pytest, mypy strict, ruff, import-linter.

**The single biggest gap:** no live smoke has EVER validated the post-fix pipeline.
Yield is unproven since the 1-survivor incident. Phase 0 below is therefore first
and gates everything else.

---

## 1. Defect inventory (ranked by yield/risk impact)

| # | Defect | Layer | Evidence (verified 2026-07-11) | Severity |
|---|--------|-------|-------------------------------|----------|
| V0 | **Live smoke never run post-fix** — pipeline yield unproven | (validation, not code) | no smoke evidence anywhere; local `Outputs/OE_ΣΧΝ-ΥΠΔΙΚ_findings.json` still shows the 1-survivor fossil | CRITICAL |
| D3 | **Sequential article loop** — flow awaits one article at a time; parallel `Orchestrator.analyze_document()` (TaskGroup + per-article fan-out) exists and is never called | Application (flow) | `bill_analysis_flow.py:157` `for article in self._doc.articles`; `orchestrator.py:185-207` unused | HIGH (perf ~N× wall-clock) |
| D6 | **No article-level failure isolation** — `analyze_document` uses bare `asyncio.TaskGroup`; one article's hard failure cancels all siblings | Application (orchestrator) | `orchestrator.py:201` `async with asyncio.TaskGroup()` — no per-task exception wrapping | HIGH (must land WITH D3, else D3 makes crashes batch-fatal) |
| H-2 | **Repair round burns one paid call on unrepairable content** (Phase-1 audit residual) | Infrastructure (LLM) | `llm/__init__.py` attempt 4 — empty-content guard exists, no looks-like-JSON check | MEDIUM |
| D7 | **Citation resolution index empty** — citations can only ever be "unverified", never positively resolved or disproven | Infrastructure (container wiring + data) | `container.py:125` `GreekCitationParser()` — no index argument | MEDIUM |
| D4 | **Verbalized Sampling dead** — all 5 lenses branch on `_use_verbalized_sampling`, nothing ever sets it true; no settings field, no CLI flag | Application (wiring) + Interfaces | `*_lens.py` `if self._use_verbalized_sampling:`; `grep -rn "verbalized" leggie/interfaces leggie/config` → nothing | MEDIUM (recall upside, cost-gated) |
| D5 | **ModelBasedReranker unwired** — flow hardcodes `CompositeReranker()` | Application (flow) + container | `bill_analysis_flow.py:78` | LOW |
| D8 | **Composition-root duplication** — handlers keep legacy `_try_get_*` factories beside the container | Application (handlers) | `cli_handlers.py:127-160` `_try_get_llm` etc. | LOW (drift hazard: two places to wire every new dependency) |
| D10 | **No resume-from-stage** — `CheckpointStore` exists in infrastructure, flow checkpoints budget spend only; crash re-runs (re-bills) completed stages | Application (flow) + Infrastructure | `persistence/checkpoint_store.py` present; `grep -rn CheckpointStore leggie/application leggie/interfaces` → nothing | LOW-MEDIUM (costs real money on crashes) |
| DOC1 | **README drift** — badges say 199 tests / 5,195 lines; "7 ports"; reality 367 / ~7.8k / 10 | Docs | README badges vs `pytest -q`, `ls application/ports/` | LOW (misleads newcomers) |
| DOC2 | **`.env.example` stale** — `LEGGIE_BUDGET__MAX_TOKENS_PER_RUN=500000` reintroduces the historical budget-block bug for anyone who copies it | Config docs | `.env.example` vs `settings.py` default 20,000,000 | MEDIUM (trap with a known incident behind it) |
| CI1 | **CI gate gaps** — CI runs ruff+mypy+pytest only; no import-linter, no coverage gate, no bandit | CI | `.github/workflows/ci.yml` | LOW |
| CFG1 | **Dead retrieval settings** — whole `RetrievalSettings` group defined, never consumed by pipeline | Config | `grep -rn "retrieval\." leggie --include=*.py | grep -v config` → nothing | LOW (either wire in F5 frontier work or mark experimental in .env.example) |

---

## 2. Phase 0 — Prove the pipeline (V0) ← **do first, gates everything**

**Layer:** none (validation). **Cost:** real money, < $5. **Prereq:** `.env` with OpenRouter key.

1. Free parse sanity: `leggie parse Inputs/OE_ΣΧΝ-ΥΠΔΙΚ.pdf -o parsed.json` — record article
   count N; no phantom ids (552/622Γ pattern).
2. Single-lens smoke: `leggie analyze Inputs/OE_ΣΧΝ-ΥΠΔΙΚ.pdf --lenses constitutional 2>&1 | Tee-Object smoke.log`
3. Measure (scripts in `.claude/skills/leggie-diagnostics-and-tooling/scripts/`):
   - `smoke_log_stats.py smoke.log` → drift+truncation signatures < 5% of LLM calls
   - `findings_stats.py Outputs/OE_ΣΧΝ-ΥΠΔΙΚ_findings.json --articles N` → survivors
     roughly ∝ N (order of magnitude above the 1-survivor fossil)
   - skeptic non-neutral verdicts present; `cove_quote_fail` only on genuinely absent quotes
4. Full 5-lens run; same thresholds; spend < $5.
5. Record numbers in a smoke-validation audit doc (root, house template) and commit it.

**Branch table:** 0–2 survivors → schema-drift ladder failing → count signatures, extend
`_IRAC_ALIASES` or raise `lens_analysis` max_tokens (one variable per run). Mass INFO → filler
regression. `skeptic_llm_error` flood → `adversarial_critic` route model reachable? Budget
block → stale 500k env value (DOC2).

**Exit gate:** all §8 DoD smoke numbers met. Only then proceed.

---

## 3. Phase 1 — Parallel fan-out with isolation (D3 + D6, one phase — they ship together)

**Layer:** Application only. Behavior-preserving except concurrency + robustness.

### 1a. Isolation first (D6)
In `orchestrator.py analyze_document`: replace bare `TaskGroup` with per-article wrapping —
either `asyncio.gather(*tasks, return_exceptions=True)` at article level, or keep TaskGroup
and wrap `_analyze_article` in try/except that logs + returns `[]` and emits a `DEGRADED`
event via `on_degradation`. One article's failure must not abort the batch. Lens-level
isolation already exists inside `analyze_article` — do not duplicate it.

### 1b. Switch the flow (D3)
`bill_analysis_flow.py:157`: replace the serial loop with
`await self._orchestrator.analyze_document(self._doc, lenses)`, then:
- preserve per-finding `FINDING_CREATED` events (emit after collection, deterministic order:
  sort by article id before emitting — report ordering must not change),
- add a concurrency cap: semaphore sized from new setting
  `settings.llm.max_concurrency` (default 5) inside `analyze_document` — protects the
  OpenRouter rate limiter from a 90-article stampede (limiter throttles, semaphore prevents
  90 queued coroutines burning timeout budget).

**Tests:** existing flow tests stay green; new: (a) N articles → same findings as serial
(order-insensitive set equality), (b) one article raising → others complete + DEGRADED event
emitted, (c) timing assertion that a slow article does not serialize the rest (fake clock).

---

## 4. Phase 2 — Kill the money leaks (H-2 + D10)

### 2a. H-2 close-out — Infrastructure (LLM)
Attempt-4 repair round: skip the paid call when content has no JSON skeleton —
`if not any(c in content_to_repair for c in "{[") : degrade`. One unit test with prose-only
garbage asserting zero repair calls (FakeLLM call counter).

### 2b. Resume-from-stage (D10 / G1) — Application + Infrastructure
- Extend `infrastructure/persistence/checkpoint_store.py` (already exists) to persist
  `{run_id, stage, findings, budget_state, event_count}` with atomic writes.
- `bill_analysis_flow.py`: `run_id`, `save_checkpoint()` at each `_transition` boundary
  (piggyback on the existing `_save_checkpoint` hook), `resume(run_id)` re-entering at last
  completed resumable state; add `FlowStateMachine.resumable_states()`.
- Guard against double-billing: stages with checkpointed output are not re-run.
- Port note: no `LLMPort`/`StatePort` changes — store is constructed in the container and
  handed to the flow constructor (new optional param, default None = current behavior).

**Tests:** integration `test_resume_after_crash` — kill mid-flow (raise between stages),
resume, assert identical findings and completed stages not re-executed (call counters).

---

## 5. Phase 3 — Verification depth (D7)

**Layer:** Infrastructure (data + container wiring). No call-site changes.

- Ship `data/citation_index.json`: known-good ΦΕΚ / CELEX / ECLI identifiers (seed from the
  sample bill's real citations + gold set citation_texts).
- `container.py:125`: load index, pass `GreekCitationParser(resolution_index=...)`.
- Keep fail-closed semantics untouched: index miss on populated index stays "not found in
  index" evidence; empty index stays "unverified". (Longer-term online resolver = new adapter
  behind `CitationParserPort.resolve`, separate ticket — frontier F2.)

**Tests:** index hit → resolved True with evidence; miss with populated index → unresolved
with "not found"; empty index → unverified passthrough (exists — keep green).

---

## 6. Phase 4 — Feature completion, opt-in only (D4 + D5)

**Layer:** Application wiring + Interfaces + Config. Both OFF by default (cost).

### 4a. Verbalized Sampling (D4)
`settings.analysis.use_verbalized_sampling: bool = False` (new `AnalysisSettings` group,
env `LEGGIE_ANALYSIS__USE_VERBALIZED_SAMPLING`) → CLI `--verbalized-sampling` on analyze →
`AnalyzeBillCommand` → handler → flow → `Orchestrator` → lens constructor param (already
exists — the `_use_verbalized_sampling` branches in all 5 lenses are waiting). Route
`verbalized_sampling` already in routes.yaml.

### 4b. Reranker selection (D5)
`settings.analysis.reranker: Literal["composite","model"] = "composite"` → flow builds
`ModelBasedReranker(...)` (with its built-in composite fallback) when `"model"`, needs a
`RerankerPort` binding in container. Default path byte-identical to today.

**Tests:** flag threading (CLI→command→flow→lens constructor arg) with fakes; default-off
snapshot: with both settings at defaults, constructed object graph identical to current.
**Adoption rule:** each feature graduates from experimental only via measured gold-set delta
(recall/F1 vs cost) per research-methodology lifecycle — otherwise stays opt-in or is retired
with documented rationale.

---

## 7. Phase 5 — Debt sweep (D8 + DOC1 + DOC2 + CI1 + CFG1)

### 5a. Single composition root (D8) — Application/Interfaces
Container is the only builder: delete `_try_get_*` fallbacks and `container is None` branches
in `cli_handlers.py`; handlers require a container (CLI already passes one). Update any test
constructing handlers bare. Pure refactor — zero behavior change, full suite must stay green.

### 5b. Docs truth (DOC1) — after Phase 0 numbers exist
README: test badge (367+), line-count badge, ports count (10), add smoke-validated status
line with date. Keep claims re-derivable (each badge value = one command).

### 5c. Config docs (DOC2, CFG1)
`.env.example`: fix `MAX_TOKENS_PER_RUN` to 20000000 (or delete the line — default is right);
mark retrieval group `# EXPERIMENTAL — not consumed by pipeline yet`.

### 5d. CI hardening (CI1)
Add to `ci.yml`: `lint-imports` step, `pytest --cov=leggie --cov-fail-under=80`, bandit step
(mirror pre-commit). Note: CI stays advisory for smoke/eval (needs secrets + money — keep
those local by policy).

---

## 8. Execution order & dependencies

```
Phase 0 (V0 live smoke)  ── CRITICAL GATE: proves current pipeline before touching it
        │
        ├─> Phase 1 (D3+D6 parallel fan-out + isolation)   ── re-run single-lens smoke after (wall-clock + same yield)
        │        │
        │        └─> Phase 2b (D10 resume) — easier once stage boundaries are stable
        ├─> Phase 2a (H-2) — independent, tiny
        ├─> Phase 3 (D7 citation index) — independent
        └─> Phase 4 (D4+D5 opt-in features) — independent, AFTER Phase 0 baseline exists
                 (their value is measured as delta vs that baseline)
Phase 5 (debt sweep) — 5a anytime; 5b AFTER Phase 0; 5c/5d anytime
```

Every phase ends with: full `python -m pytest tests/ -q`, `mypy leggie/
--ignore-missing-imports`, `ruff check leggie/ tests/`, `lint-imports`; Phases 1–3 also end
with a single-lens live smoke re-measurement (same scripts, same thresholds).

---

## 9. Architecture guardrails (apply to every phase)

- **Dependency rule:** interfaces → infrastructure → application → domain → config; enforced
  by `lint-imports` — run it before every commit.
- **Domain frozen:** `Finding`, `IRAC`, `Confidence` untouched; findings rebuilt via
  `model_copy`, never mutated.
- **Ports unchanged:** no new methods on `LLMPort`/`RouterPort`/`CitationParserPort`/
  `RerankerPort`; new behavior = new adapters/decorators/constructor wiring.
- **Structured output:** every LLM response validates against a schema in
  `domain/models/structured_output.py`.
- **No silent failure:** every degradation emits a `DEGRADED` event or logged warning
  (Phase 1 isolation explicitly required to emit, not swallow).
- **Budget:** $5 cap never raised to make a run pass; token ceiling stays a safety net.
- **Frozen debt:** ruff ignore list not widened; `.env.example` fixes only per 5c.
- **One variable per smoke run** (ablation discipline) when chasing yield.

---

## 10. Definition of done (measurable — nothing judged by eye)

1. Live smoke on `Inputs/OE_ΣΧΝ-ΥΠΔΙΚ.pdf`: survivors ∝ article count (document the actual
   N and count in the audit doc); parse-failure signatures < 5% of LLM calls; ≥1 non-neutral
   skeptic verdict; CoVe drops only quote-invalid findings; spend < $5. Evidence: smoke.log +
   script outputs committed in the audit doc.
2. Full-run wall-clock after Phase 1 measurably below the serial baseline (record both), with
   yield unchanged (±dedup noise) vs the Phase-0 single-run baseline.
3. Crash-resume test: interrupted run resumed with zero re-billed stages (call-counter
   assertion in `test_resume_after_crash`).
4. Repair round: zero paid repair calls on prose-only garbage (unit test).
5. Citation: at least one real citation from the sample bill positively resolves against
   `data/citation_index.json` in a live run's findings evidence.
6. VS and model-reranker: default-off object graph identical to pre-phase (snapshot test);
   each has one measured gold-set delta recorded (adopt/keep-experimental/retire decision
   written down).
7. `cli_handlers.py` contains no `_try_get_` symbols; suite green.
8. README badges regenerated from commands; `.env.example` token line correct; CI runs
   lint-imports + coverage gate + bandit.
9. All of the above committed in house style (conventional commits, D-ids referenced) with a
   closing audit doc per `implementation_audit_report.md` pattern.

---

## 11. Explicitly out of scope (fenced — see skill leggie-failure-archaeology §13)

Learned router, multi-round debate, knowledge graph, continuous learning, >2 report types,
online citation resolver (frontier F2 ticket), retrieval wiring (frontier F5). Reopen only
with the recorded reopen conditions (gold-set scale, measured lens gaps, etc.).
