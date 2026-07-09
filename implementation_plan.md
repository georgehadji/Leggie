# Leggie — Implementation Plan

**Document type:** Engineering implementation plan
**Scope:** Defect remediation + architecture-conformance + analytical-depth enhancements for the Leggie Greek legislative-bill analyzer
**Status of codebase at time of writing:** 289 unit/integration tests green; F0 parser fix and F1 LLM wiring landed and verified live; two silent blockers found and fixed during verification (invalid model ids; bare-list JSON parse). Multiple architecture components are **built but not wired into the runtime path**.
**Related docs:** `docs/ARCHITECTURE.md` · `docs/BUILD_PLAN.md` · `docs/FIX_PLAN.md` · `tasks/todo.md`

---

## 1. Executive Summary

Leggie analyzes Greek legislative bills through an ensemble of LLM "lenses" over a Clean/Hexagonal, event-sourced, deterministic workflow-DAG. The architecture is sound and the scaffold is complete, but a verification pass proved the analytical pipeline was **silently degrading to keyword-regex output** because every configured model id was invalid and the structured-output parser could not read the model's response shape. Both are now fixed; a live call produces real Greek legal findings.

Verification also revealed the core gap this plan addresses: **the differentiating architecture components — model router/cascade, bounded blackboard aggregation, budget guard, and finding de-duplication — exist as code and pass unit tests, but are not invoked by the runtime workflow.** The flow currently runs a single hard-defaulted model, mutates finding state in place, enforces no cost ceiling, and emits duplicate findings. In addition, model output is in English rather than Greek, which blocks real use.

This plan sequences the work in four phases:

- **Phase A — Make it real & correct** (days): force Greek output, replace the silent-fallback anti-pattern with fail-loud error surfacing, add startup model-id validation, wire de-duplication, and run the full bill end-to-end to regenerate a report the user can trust.
- **Phase B — Architecture conformance** (1–2 weeks): wire the router/cascade, budget-guard decorator, and event-sourced blackboard aggregation into the runtime; enforce the inward-dependency rule with `import-linter`. This makes the claimed architecture actually operative.
- **Phase C — Analytical depth** (1–2 weeks): real Verbalized Sampling, evidence + deterministic citation binding (CoVe), and an eval-driven model bake-off to lock per-stage model choices on measured precision/recall/cost.
- **Phase D — Scale & operations** (ongoing): observability (structured logs, metrics, tracing, cost telemetry), CI/CD gates, and hybrid retrieval over Greek/EU legal corpora.

Every change preserves the Architecture Conformance rules (A–K) defined in `docs/FIX_PLAN.md`: inward-only dependencies, LLM confined inside stages, independence-during-analysis, event-sourced aggregation, provenance/traceability, reproducibility, boundary validation, functional core, decorator-based resilience, bounded chain depth, small files.

---

## 2. Current Architecture Assessment

### 2.1 Architecture (as designed)
- **Style:** Clean/Hexagonal, 6 layers, dependencies inward only (`Interfaces → Infrastructure → Application → Domain`).
- **Control flow:** deterministic workflow-DAG (state machine + event sourcing). LLM autonomy confined *inside* a stage.
- **Analysis:** orchestrator-worker parallel fan-out; stateless lens workers, blind to one another (diversity preservation).
- **Aggregation:** bounded, schema-grounded blackboard with Observer subscribers (dedup/rerank/skeptic/CoVe).
- **Paradigm:** functional core (pure domain), imperative shell (application/infra).
- **Ports:** `LLMPort`, `RouterPort`, `RetrievalPort`, `StatePort`, `EventBusPort`, `BlackboardPort`, `CitationParserPort`. Composition root: `infrastructure/container.py`.

### 2.2 Verified current state (as built)
| Area | State | Evidence |
|---|---|---|
| Domain models (frozen IRAC Finding / Evidence / Citation / Event) | ✅ solid | `domain/models`, tests green |
| Parser (F0) | ✅ fixed | 214 → 121 real articles; line-anchor + stop-list + monotonic guard + PDF repair |
| LLM wiring (F1) | ✅ works live | probe returns real Greek findings via `google/gemini-2.5-flash` |
| Model ids | ✅ fixed | all 9 id sites consolidated to verified-real ids; `routes.yaml` VFM tiers |
| Structured-output parse | ✅ fixed | boundary parser now tolerates fenced / bare-list JSON |
| Router + cascade (`RouterPort`, `routes.yaml`) | ⚠ **built, not wired** | no `.route()` call in `application/workflow` or `agents` |
| Blackboard aggregation | ⚠ **built, not wired** | flow mutates `self._findings` in place; `application/blackboard` unused by flow |
| Budget guard | ⚠ **built, not enforced** | constructed in container; not wrapped around lens fan-out |
| De-duplication (`domain/clustering`) | ⚠ **built, not wired** | duplicate findings visible in report output |
| Evidence substring validation (F3) | ✅ partial | lens validates `verbatim_quote ⊂ article` |
| Citation binding (deterministic resolve) | ⚠ partial | parser exists; resolution not wired into the finding pipeline |
| Greek output | ❌ defect | prompts request analysis but not Greek-language output; model replies English |
| `import-linter` (rule A) | ❌ unconfigured | `lint-imports` → "Could not read any configuration" |
| Eval harness (F5) | ⚠ partial | scores real findings only when a bill file + key are present |

### 2.3 Technical debt / risk hotspots
1. **Silent-fallback anti-pattern (highest-severity design debt).** Lens catches all LLM exceptions and regex-falls-back with a `warning` only. A misconfiguration (invalid id, network, quota) therefore produces a *plausible-looking but worthless* report with no failure signal. This masked the invalid-id bug. Violates Defensive Programming ("never silently swallow errors").
2. **Claimed-but-inactive architecture.** Router, blackboard, budget guard, dedup pass tests in isolation but are not in the runtime path — the system does not behave as `ARCHITECTURE.md` describes. Integration-level gap.
3. **Configuration drift / unvalidated external ids.** Four sources disagreed on default model; ids were never validated against the live OpenRouter catalog. No guard prevents recurrence.
4. **In-place state mutation in the flow** contradicts the event-sourced, replayable design (rule D).
5. **No enforced dependency rule** — the inward-only invariant relies on discipline, not tooling; lazy `import leggie.infrastructure…` inside application already occurs.
6. **Language correctness untested** — no test asserts Greek output; the product's core UX requirement is unguarded.

---

## 3. Detailed Implementation Plan (phased roadmap)

```
Phase A ─ Make it real & correct     (unblocks trustworthy output)
   FX1 Greek output ─┐
   FX5 fail-loud ────┼─► FX2 dedup ─► A-VAL full-bill run
   FX3 id validation ┘
                        │
Phase B ─ Architecture conformance   (realize the design)
   EN1 router wiring ─► EN2 budget decorator ─► EN3 blackboard aggregation
   FX4 import-linter (parallel)
                        │
Phase C ─ Analytical depth
   EN4 Verbalized Sampling ─► EN5 evidence+citation binding ─► EN6 model bake-off
                        │
Phase D ─ Scale & ops
   EN7 observability ─► CI/CD gates ─► EN8 hybrid retrieval (optional)
```

**Milestones**
- **M1 (end Phase A):** full run on `OE_ΣΧΝ-ΥΠΔΙΚ.pdf` produces Greek, de-duplicated, substantive findings; any model/config error fails loudly.
- **M2 (end Phase B):** router selects per-task models; budget ceiling enforced; aggregation is event-sourced through the blackboard; `import-linter` green in CI.
- **M3 (end Phase C):** VS diversity active; every shipped citation resolves or is flagged; per-stage models chosen by measured eval.
- **M4 (end Phase D):** metrics/tracing/cost dashboards; CI/CD quality gates; optional live-corpus retrieval.

**Dependencies:** Phase A is prerequisite to trustworthy eval, which Phase C's bake-off depends on. EN3 (blackboard) depends on EN1/EN2 being injected via the container. FX4 can run in parallel throughout.

---

## 4. Task Breakdown Structure (WBS)

> Format per item: Objective · Affected components · Design changes · Implementation tasks · Refactoring · Testing · Acceptance · Rollback.

### FX1 — Greek-language output *(Phase A, CRITICAL)*
- **Objective:** all findings (issue/rule/application/conclusion) and reports in Greek.
- **Affected:** `application/agents/prompts/*.py`, report renderers, a new language test.
- **Design:** add an explicit output-language contract to every lens system prompt ("Απάντησε αποκλειστικά στα Ελληνικά· επίστρεψε JSON..."); keep JSON keys ASCII, values Greek. Add a post-generation language check (heuristic: Greek-script ratio) at the boundary; on failure, one bounded retry with a stricter instruction (chain-depth ≤ 4).
- **Tasks:** edit 5 prompt modules; add `is_greek(text)` pure helper in `domain`; wire optional retry in the lens LLM path.
- **Refactoring:** none structural.
- **Testing:** unit test asserting Greek-script ratio on a mocked LLM response mapping; live smoke test (marked, key-gated).
- **Acceptance:** ≥ 95% of finding text is Greek script on the gold bills.
- **Rollback:** revert prompt files (pure text change; no schema/API impact).

### FX2 — Wire de-duplication into aggregation *(Phase A)*
- **Objective:** eliminate duplicate/near-duplicate findings (e.g. three identical GDPR findings on one article).
- **Affected:** `application/workflow/bill_analysis_flow.py`, `domain/clustering`, rerank service.
- **Design:** invoke the existing pure `domain/clustering` dedup as an aggregation step (interim: direct call; final form in EN3 as a blackboard Observer). Collapse by `(article_id, finding_type, issue-similarity ≥ threshold)`, keep highest confidence.
- **Tasks:** call dedup after fan-out, before rerank; expose similarity threshold via config.
- **Refactoring:** extract the inline aggregation block into a named step.
- **Testing:** unit test on a finding list containing exact + near duplicates; property test that dedup is idempotent.
- **Acceptance:** zero exact-duplicate findings in report; near-duplicate rate below threshold on gold bills.
- **Rollback:** feature-flag the dedup step; disable to restore prior behavior.

### FX3 — Model-id validation + FX5 fail-loud *(Phase A)*
- **Objective:** never silently regex-fall-back on a real misconfiguration; catch invalid ids at startup.
- **Affected:** `config/settings.py` (startup validation), `infrastructure/llm` adapter, lens error handling, `container.py`.
- **Design:** (a) at composition time, validate every configured model id against the OpenRouter `/models` catalog (cached, with offline allowlist fallback); fail fast with a clear `LLMConfigurationError` listing bad ids. (b) Separate *"no LLM configured"* (legitimate → deterministic/regex mode, logged INFO once) from *"LLM call failed"* (→ surface a typed error / mark the finding batch degraded, emit an event); do not disguise the second as the first.
- **Tasks:** add `validate_models()` to the container; add a `--offline` explicit switch for regex mode; change lens `except` to distinguish configuration-absent vs call-failure; emit `EventType` on degradation.
- **Refactoring:** narrow the broad `except Exception` in lenses; centralize fallback policy.
- **Testing:** unit test that an invalid id raises at startup; test that a call failure surfaces (does not silently regex); test that absent key → explicit regex mode.
- **Acceptance:** invalid id → startup failure with actionable message; runtime LLM error → visible in output + event log, never masked.
- **Rollback:** validation behind a config flag (`strict_model_validation`, default on).

### A-VAL — Full-bill validation run *(Phase A milestone)*
- **Objective:** regenerate the report on the real bill and confirm trustworthy output.
- **Affected:** none (execution + review).
- **Tasks:** run `analyze Inputs/OE_ΣΧΝ-ΥΠΔΙΚ.pdf`; inspect Greek, dedup, substantive findings, cost within ceiling.
- **Acceptance:** M1 met; findings reviewed as substantive by a Greek-legal reader.
- **Rollback:** n/a.

### EN1 — Wire router/cascade into the runtime *(Phase B)*
- **Objective:** per-task VFM model selection instead of one hard default; escalate on low confidence/failure.
- **Affected:** `infrastructure/router`, `application/agents/orchestrator.py`, lens base, `container.py`, `routes.yaml`.
- **Design:** inject `RouterPort` into the orchestrator via the container (rule A). Before each lens call, `route(task_type="lens_analysis")` yields `(model, tier, max_tokens)`; on low-confidence/failure, `cascade()` to the next tier (Chain of Responsibility). Deterministic, data-driven — no LLM chooses the model (rule B).
- **Tasks:** thread `RouterPort` through orchestrator → lens; map lens/skeptic/CoVe/report stages to `routes.yaml` task types; implement confidence-floor escalation.
- **Refactoring:** remove the single `model` default from orchestrator; source models from the router.
- **Testing:** contract test for `RouterPort`; test that low-confidence triggers cascade; test that routing is reproducible under a fixed seed.
- **Acceptance:** logs show per-task model + any tier escalation; no hard-coded model in the analysis path.
- **Rollback:** router binding swappable for a fixed-model stub via the container.

### EN2 — Budget-guard decorator on the LLM port *(Phase B)*
- **Objective:** enforce the per-run token/$ ceiling; degrade before blow-through.
- **Affected:** `infrastructure/llm/decorators.py`, `container.py`, `infrastructure/budget_guard`.
- **Design:** wrap the injected `LLMPort` at the container with a resilience/decorator stack: `retry → cache → budget-guard → rate-limit` (rule I). Budget guard checks projected cost pre-call, records post-call; on warning applies the configured degrade strategy (fewer paths → fewer lenses → cheaper tier). Application calls `llm.generate()` unaware of the guard.
- **Tasks:** implement the budget decorator; assemble the decorator stack in `configure_defaults`; wire degrade policy to the orchestrator fan-out.
- **Refactoring:** ensure application never imports `BudgetGuard`.
- **Testing:** test that exceeding the ceiling blocks/degrades; test decorator ordering; test that spend is recorded to the event log.
- **Acceptance:** a run cannot exceed `max_cost_per_run`; degrade path exercised and logged.
- **Rollback:** decorator stack composed behind a flag; can bind the bare adapter.

### EN3 — Event-sourced blackboard aggregation *(Phase B)*
- **Objective:** replace in-place `self._findings = …` mutation with append-only, schema-grounded blackboard mutations + Observer subscribers (rules C, D).
- **Affected:** `application/blackboard`, `application/workflow/bill_analysis_flow.py`, `EventBusPort`, subscribers (dedup/rerank/skeptic/CoVe).
- **Design:** after independent fan-out, findings are *posted* to the bounded blackboard; dedup, rerank, skeptic, and CoVe subscribe as Observers, each emitting immutable events for its transformation. A Mediator schedules adaptive rounds (simple findings converge in one). The run reconstructs state from the event log (replayable).
- **Tasks:** define board mutation events; port the four aggregation steps to subscribers; drive rounds via the mediator; remove direct reassignment.
- **Refactoring:** substantial — extract aggregation from the flow into the blackboard controller.
- **Testing:** test each subscriber independently; test replay-from-log reproduces final findings; test independence (no lens reads the board during analysis).
- **Acceptance:** run replays deterministically from events; no in-place mutation remains.
- **Rollback:** keep the inline aggregation path behind a flag for one release; remove after M2 soak.

### FX4 — Enforce inward-dependency rule *(Phase B, parallel)*
- **Objective:** make rule A machine-enforced.
- **Affected:** `pyproject.toml`/`.importlinter`, CI.
- **Design:** add `import-linter` contracts (layered: domain ⊂ application ⊂ infrastructure ⊂ interfaces; forbid application → infrastructure). Fix any existing violations (lazy infra imports in application) by routing through the container.
- **Tasks:** author contracts; run; remediate violations; add to CI.
- **Testing:** `lint-imports` green; CI job fails on violation.
- **Acceptance:** `lint-imports` passes and is a required CI check.
- **Rollback:** non-blocking (report-only) mode initially; promote to blocking after remediation.

### EN4 — Verbalized Sampling (real) *(Phase C)*
- **Objective:** intra-call diversity — one call returns k candidate findings with probabilities; tail-weighted sampling.
- **Affected:** `application/services/verbalized_sampling.py`, lens LLM path, prompts.
- **Design:** Template-Method prompt builds a k-candidate request; parse the distribution; pure, seeded tail-sample (rule F/H). One call per (lens, article), not k calls.
- **Tasks:** VS prompt; distribution parser; seeded sampler injected clock/seed.
- **Testing:** deterministic sampler under fixed seed; parse robustness; token-cost bound vs k separate calls.
- **Acceptance:** measurable diversity lift on gold bills without k× cost.
- **Rollback:** VS off → single-candidate path.

### EN5 — Evidence + deterministic citation binding (CoVe) *(Phase C)*
- **Objective:** every shipped finding carries a verbatim quote (substring-validated) and citations that resolve deterministically.
- **Affected:** `services/cove_verifier.py`, `infrastructure/citation`, `CitationParserPort`, `domain/specs`.
- **Design:** CoVe factored verification (draft → verification questions → independent check → revise, depth ≤ 4). Citation *resolution* is a pure Specification against the retrieval/index — no LLM in the fact-check loop (rule B/H). Quote non-substring → drop/flag.
- **Tasks:** wire CoVe over survivors; resolve parsed citations; add `EvidenceGrounded`/`CitationResolves` specs to the pipeline.
- **Testing:** substring gate; citation-resolves spec; hallucinated-citation → flagged.
- **Acceptance:** hallucinated citations → 0; every shipped quote is a real substring.
- **Rollback:** binding step flag-gated.

### EN6 — Eval-driven model bake-off *(Phase C)*
- **Objective:** lock per-stage models on measured precision/recall/cost, not assumption.
- **Affected:** `infrastructure/persistence/eval_harness.py`, CLI `eval`, `routes.yaml`.
- **Design:** run the real flow per gold bill for each candidate model; record P/R/F1/RDI + $/bill. Winner = best F1 within ceiling; escalate-on-signal for the rest.
- **Candidates:** `google/gemini-2.5-flash`, `google/gemini-3-flash-preview`, `deepseek/deepseek-v3.2`, `anthropic/claude-sonnet-4.6`, `openai/gpt-5-mini`.
- **Tasks:** parameterize eval by model; results table; update `routes.yaml` from winners.
- **Testing:** eval runs headless on gold set; deterministic under seed + cache.
- **Acceptance:** `routes.yaml` justified by a committed results table.
- **Rollback:** revert `routes.yaml` to the safe default (`gemini-2.5-flash`).

### EN7 — Observability *(Phase D)*
- **Objective:** logs, metrics, tracing, cost telemetry across stages.
- **Affected:** `infrastructure/observability`, all stages.
- **Design:** structured logs with a run/trace id propagated stage-to-stage (already partially present); Prometheus counters/histograms (findings/stage, tokens, $, latency, cascade escalations, refutations); span per stage.
- **Tasks:** metrics registry; trace propagation; cost/token emission from the budget decorator.
- **Testing:** metrics emitted in an integration run; trace id continuity.
- **Acceptance:** a run yields a coherent trace + cost/quality metrics.
- **Rollback:** metrics exporter optional/flag-gated.

### EN8 — Hybrid retrieval over Greek/EU corpora *(Phase D, optional)*
- **Objective:** ground findings against EUR-Lex/legislation corpora (BUILD_PLAN P3).
- **Affected:** `infrastructure/retrieval`, `RetrievalPort`.
- **Design:** Strategy (dense/sparse) + Composite (RRF fusion) + Repository per corpus; `greek_legal_bert_v2` + `BGE-M3`; backpressure on CELLAR.
- **Tasks:** implement adapters; wire into evidence stage.
- **Testing:** retrieval contract tests; backpressure/limits.
- **Acceptance:** citations resolve against a real index; latency/limits respected.
- **Rollback:** retrieval optional; falls back to inject-bill-only.

---

## 5. Risk & Mitigation Matrix

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Silent-fallback masks future misconfig (recurrence) | High | High | FX3/FX5 fail-loud + startup id validation; test that call-failure surfaces |
| R2 | External model id drift (OpenRouter renames/retires) | Med | High | Validate against live `/models` at startup; offline allowlist; pin + monitor |
| R3 | Greek-output regressions per model/version | Med | High | Language check + bounded retry; language assertion in eval; per-model bake-off |
| R4 | EN3 blackboard refactor destabilizes the flow | Med | Med | Flag-gate new path; keep inline path one release; replay tests; soak before cutover |
| R5 | Cost overrun on full-bill runs | Med | Med | Budget-guard decorator (EN2) enforced; cheap default tier; cache; escalate-on-signal only |
| R6 | Import-linter remediation surfaces hidden coupling | Med | Med | Report-only first; remediate via container injection; then block |
| R7 | LLM nondeterminism vs reproducibility NFR | High | Med | Seed + low temperature; replay from event-sourced cached responses, not re-calls |
| R8 | Provider/API instability, rate limits | Med | Med | Retry+circuit-breaker decorators; rate limiter; cascade to alt provider |
| R9 | Legal-quality shortfall (GreekBarBench: < 95th-pct expert) | High | Med | Position as decision-support not authority; skeptic + human review; premium escalation for high-severity |
| R10 | Citation hallucination | Med | High | Deterministic parser resolution; substring-validated quotes; hallucinated → flagged/dropped |
| R11 | PDF extraction noise (mid-token breaks, cross-refs) | Med | Med | Parser repair + monotonic guard (done); perturbation tests on messy bills |
| R12 | Secret/key leakage | Low | High | `.env` git-ignored; no key in logs; secret only via env; startup presence check |

---

## 6. Testing & Quality Assurance Strategy

- **Pyramid:** pure-domain unit tests (clustering/scoring/specs — fast, deterministic); port **contract tests** with fakes; **integration** on the gold set; **eval harness** (P/R/F1/RDI) as the analytical regression gate.
- **Coverage:** ≥ 80% overall; domain core ≥ 90%. New code TDD.
- **Determinism:** injected seed/clock; no global `random`; LLM mocked in unit tests; one marked, key-gated live smoke test.
- **Boundary/robustness:** malformed LLM JSON (fenced, bare-list, wrong keys), non-substring quotes, unresolvable citations, messy-PDF perturbation.
- **Language QA (new):** Greek-script ratio assertion on generated findings.
- **Architecture QA:** `import-linter` as a required check; test that no lens reads shared state during analysis; replay-from-event-log equivalence test.
- **Cost QA:** budget-guard tests (block/degrade/record); $/bill asserted under ceiling in the eval run.
- **Static analysis:** `ruff` + `mypy` (typed signatures), `bandit` (security), `black`/`isort` (format) — all in CI.

---

## 7. Deployment & Rollback Plan

- **Environments:** local (regex/offline mode, no key) → staging (key, low budget ceiling, full pipeline) → production.
- **Config/secrets:** `LEGGIE_LLM__OPENROUTER_API_KEY` via env only; `strict_model_validation=on`; `routes.yaml` versioned; budget ceiling per environment.
- **Release gating:** CI must be green — tests, `import-linter`, `ruff`/`mypy`/`bandit`, eval score ≥ prior baseline, budget within ceiling.
- **Feature flags:** dedup step, router wiring, budget decorator, blackboard aggregation, VS, citation binding each behind a flag for staged cutover.
- **Rollout order:** Phase A (low-risk, additive) → Phase B behind flags with the inline path retained one release → Phase C additive → Phase D operational.
- **Rollback triggers & actions:**
  - Eval score regression or Greek-ratio drop → revert prompts / `routes.yaml`; flag off the offending step.
  - Cost overrun → tighten ceiling / force cheapest tier / disable escalation.
  - Blackboard instability → flag back to inline aggregation path.
  - Invalid-id/config error → startup fails loudly; fix config, redeploy (no bad output shipped).
- **Data/state:** event store is append-only and replayable; no destructive migration; new event types are additive/back-compatible.

---

## 8. Post-Implementation Validation Checklist

**Correctness & UX**
- [ ] Full run on `OE_ΣΧΝ-ΥΠΔΙΚ.pdf` produces substantive, **Greek**, de-duplicated findings.
- [ ] No "no issue found" INFO filler; no exact-duplicate findings.
- [ ] Every shipped finding: verbatim quote is a real substring; citations resolve or are flagged.
- [ ] A Greek-legal reviewer confirms findings are substantive.

**Architecture conformance (rules A–K)**
- [ ] `import-linter` green and required in CI (A).
- [ ] Per-task model chosen by the router; no hard-coded analysis model (A, B).
- [ ] Lenses stateless/blind during analysis; aggregation only on the blackboard (C).
- [ ] Aggregation append-only; run replays from the event log (D).
- [ ] Every Finding carries `(stage, model, prompt_hash, seed, evidence, confidence)` (E).
- [ ] Reproducible under fixed seed via cached replay (F).
- [ ] LLM output validated at the boundary → frozen domain Finding (G).
- [ ] Budget guard enforced as a port decorator; cost within ceiling (I).
- [ ] CoVe/improver chains bounded ≤ 4 (J).
- [ ] Source files < 400 lines; prompts/schemas in their own modules (K).

**Reliability & ops**
- [ ] Misconfiguration (invalid id / missing key) fails **loudly**, never silent-regex.
- [ ] Metrics: findings/stage, tokens, $, latency, escalations, refutations emitted.
- [ ] Trace id propagated across all stages.
- [ ] Eval P/R/F1/RDI recorded and improved vs baseline; `routes.yaml` justified by the bake-off table.
- [ ] No secrets in logs; key only via env.
- [ ] `ruff`/`mypy`/`bandit` clean; coverage ≥ 80% (domain ≥ 90%).
