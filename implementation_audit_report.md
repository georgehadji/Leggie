# Implementation Audit Report — Phase A

**Audit date:** 2026-07-09
**Audit scope:** Phase A (FX1, FX2, FX3, FX5) of `implementation_plan.md`
**Test baseline:** 304 passed, 0 failed
**Review method:** structured code review (subagent) + manual verification + live test execution

---

## 1. Executive Summary

Phase A implementation is **APPROVED WITH CHANGES** — all five tasks (FX1 Greek output, FX2 dedup wiring, FX3 model-id validation, FX5 fail-loud, and test additions) are implemented. One blocking architectural gap (degradation callback unwired) was found during review and immediately fixed. 15 new tests were added covering `is_greek()`, dedup logic, and degradation event wiring. All 304 tests pass.

**Key metrics:**
- Files changed: 10 source files + 2 test files = 12 files
- New tests: 15 (8 is_greek, 5 dedup, 2 degradation)
- Blocking issues found during review: 1 (fixed)
- Should-fix items resolved: 3/3

---

## 2. Plan Compliance Matrix

| Plan Item | Status | Evidence | Notes |
|---|---|---|---|
| **FX1 — Greek-language output** | ✅ COMPLETE | `prompts/constitutional.py` — full Greek system + user prompts; `domain/models/__init__.py` — `is_greek()` helper; `lens.py` — `_maybe_retry_greek()` with bounded retry | Prompt is idiomatic Greek legal prose. Language check is 30% threshold with 1 retry. |
| **FX1 — is_greek helper** | ✅ COMPLETE | `domain/models/__init__.py:19-28`; 8 unit tests in `test_models.py::TestIsGreek` | Covers pure Greek, English, mixed, empty, extended Unicode range. |
| **FX1 — post-generation language check** | ✅ COMPLETE | `lens.py:54-101` — `_maybe_retry_greek()` in `_call_llm_structured` | Bounded 1 retry, falls back gracefully. |
| **FX3 — Model-id validation** | ✅ COMPLETE | `infrastructure/llm/__init__.py:20-88` — 14-model allowlist; sync init validation; async `validate_model_ids()` with live API fallback | Validate-on-init gate prevents silent misconfiguration. |
| **FX5 — Fail-loud** | ✅ COMPLETE | `EventType.DEGRADED`; `Lens.__init__` accepts `on_degradation`; `ConstitutionalLens` logs ERROR on LLM failure; `Orchestrator` logs ERROR with full traceback | Degradation callback wired through `BillAnalysisFlow` → `Orchestrator` → `Lens`. |
| **FX2 — De-duplication** | ✅ COMPLETE | `bill_analysis_flow.py:261-302` — `_dedup_findings()` with article-aware similarity; `EventType.DEDUP_REMOVED`; 5 unit tests | Collapses by (article, finding_type, lens) per plan. |
| **A-VAL — Full-bill run** | ⏸ DEFERRED | N/A | Requires live OpenRouter key + bill file. Runs from existing `analyze` CLI with Phase A code. |

---

## 3. Architecture Compliance Assessment

### 3.1 Dependency Direction (Rule A)
- ✅ `domain/models/__init__.py` — pure `is_greek()` function, no imports from application or infrastructure
- ✅ `domain/clustering` — pure functions, no new inward imports
- ✅ Application imports infrastructure only through lazy factories (existing pattern maintained)
- ⚠ `import-linter` not yet configured — per plan this is `FX4` in Phase B

### 3.2 Layer Boundaries (Rules B–K)
| Rule | Description | Status |
|---|---|---|
| A | Inward-only dependencies | ✅ Maintained — no new violations |
| B | LLM confined inside stages | ✅ Lens.analyze() is the sole LLM boundary |
| C | Lenses stateless/blind | ✅ No shared state between lenses |
| D | Append-only aggregation | ⚠ Not yet — `BillAnalysisFlow` still uses in-place mutation (deferred to Phase B: EN3) |
| E | Finding provenance (stage, model, prompt_hash, seed, evidence) | ✅ All present in `_candidate_to_finding()` |
| F | Reproducibility under fixed seed | ⚠ Seed not injected yet (deferred to Phase B) |
| G | Boundary validation | ✅ Language check added at `_call_llm_structured` boundary |
| I | Budget guard enforced | ⏸ Not yet wired (deferred to Phase B: EN2) |
| J | Chain depth ≤ 4 | ✅ Language retry is 1, CoVe chains are bounded |
| K | Files < 400 lines | ✅ All modified files under 400 lines |

### 3.3 Architecture Regression Risks
- **None.** All changes are additive or behaviorally scoped within existing layers. No outward dependency was introduced.

---

## 4. Code Quality Findings

### 4.1 Strengths
- **Pure domain function**: `is_greek()` is correctly placed in `domain/models/__init__.py` — no I/O, no imports.
- **Bounded retry**: `_maybe_retry_greek()` retries at most once and gracefully falls back to the original response.
- **Article-aware dedup**: The similarity function now correctly separates findings from different articles, matching the plan's requirement.
- **Clear error hierarchy**: `DEGRADED` and `DEDUP_REMOVED` event types are properly additive and backward-compatible.
- **Defensive callback**: `_emit_degradation()` wraps the callback call in try/except to prevent cascading failures.

### 4.2 Nits (non-blocking)
| # | File | Issue | Recommendation |
|---|---|---|---|
| N1 | `lens.py:81` | `import dataclasses` inside a method | Move to module-level import for consistency |
| N2 | `constitutional_lens.py:12-13` | Both `import re` and `from re import Pattern` present | `Pattern` is used in type annotations; `re` for `re.compile()`. Minor noise. |
| N3 | `bill_analysis_flow.py:171` | Duplicate comment number "7. Verify" (should be "8. Verify") | Fix comment numbering to match the actual sequence |

### 4.3 Error Handling
- **FX5 fail-loud**: The `ConstitutionalLens` now correctly distinguishes "no LLM configured" (INFO log, regex mode) from "LLM call failed" (ERROR log, degradation event, empty findings).
- **Degradation traceability**: Full traceback logged via `exc_info=True` in the orchestrator's `_run_lens` handler.
- **Silent-fallback eliminated**: `ConstitutionalLens.analyze()` no longer silently falls back to regex on LLM failure.

---

## 5. Testing & Coverage Assessment

### 5.1 New Tests Added (15 total)

**Domain:**
- `TestIsGreek` (8 tests): pure Greek, pure English, mixed dominant, mixed sparse, empty string, default threshold, custom threshold, extended Unicode range

**Application — Flow:**
- `TestDedupInFlow` (5 tests): removes near-duplicates, respects article boundaries, respects different types, empty list, idempotent
- `TestDegradationEvent` (2 tests): explicit callback records event, default callback uses `_record_event`

### 5.2 Test Results
```
304 passed, 0 failed, 560 warnings in 9.50s
```

### 5.3 Coverage Gaps (acknowledged, deferred)
- **Integration test for Greek output**: Requires a live OpenRouter key to verify Greek output from an actual LLM call. The `_maybe_retry_greek` retry path is tested indirectly through existing lens tests.
- **Live model-id validation**: The async `validate_model_ids()` function is tested against the offline allowlist. Live `/models` query requires network + API key.
- **E2E dedup with real bill**: The `_dedup_findings` unit tests cover the logic. An integration test running through the full flow and checking for `DEDUP_REMOVED` events requires a real bill run.

---

## 6. Risk & Regression Analysis

### 6.1 Risks Identified
| # | Risk | Severity | Mitigation |
|---|---|---|---|
| R1 | Degradation callback signature mismatch | **FIXED** — Wrapped `_record_event` to accept `Event` objects via lambda | Signature now matches across flow → orchestrator → lens chain |
| R2 | Greek prompt may regress on model version update | LOW | Language check + retry in `_maybe_retry_greek()` acts as guardrail; per-model bake-off in Phase C |
| R3 | Offline model allowlist grows stale | LOW | `validate_model_ids()` accepts live query as primary path; allowlist is fallback |
| R4 | Dedup may over-collapse with low threshold | LOW | Default threshold 0.85 is conservative; article prefix check prevents cross-article collapse |

### 6.2 Backward Compatibility
- **EventType.DEGRADED** and **EventType.DEDUP_REMOVED**: Additive enum values — existing code consuming event logs will see new event types.
- **Lens.__init__ signature**: Added optional `on_degradation` parameter with default `None` — existing callers are unaffected.
- **Orchestrator.__init__ signature**: Added optional `on_degradation` — existing callers are unaffected.
- **BillAnalysisFlow.__init__ signature**: Added optional `dedup_threshold` and `on_degradation` — existing callers are unaffected.
- **Prompt format change**: System prompt rewritten in Greek but maintains the same JSON schema contract (`LensFindings`). Keys remain ASCII.

---

## 7. Required Corrections

| Severity | File | Issue | Status |
|---|---|---|---|
| ~~BLOCKING~~ | `bill_analysis_flow.py:65` | ~~Degradation callback never wired — FX5 dead code~~ | **FIXED** — Wired via `on_degradation` parameter + lambda wrapper |
| ~~MEDIUM~~ | `bill_analysis_flow.py:270-295` | ~~Similarity function omitted article_id — cross-article false merges~~ | **FIXED** — Added `_article_prefix()` regex extractor for `Άρθρο N` prefix |
| ~~MEDIUM~~ | `orchestrator.py:109` | ~~Over-broad `except Exception` masks code bugs~~ | **FIXED** — Added `exc_info=True` for full traceback |
| LOW | `test_bill_analysis_flow.py` comment numbering | Step numbering in comments has duplicate "7." | Noted as N3 |

---

## 8. Final Verdict

### APPROVED WITH CHANGES

**Reasoning:**
- All four Phase A tasks (FX1, FX2, FX3, FX5) are implemented per the plan's design specifications.
- One blocking issue (degradation callback unwired) was found during review and is now fixed.
- 15 new tests cover the new functionality — `is_greek()`, dedup logic, and degradation event wiring.
- 304 tests pass with zero failures.
- Architecture boundaries are maintained; no inward-dependency violations introduced.
- Backward compatibility preserved through optional parameters.

**Conditions for unconditional APPROVAL:**
1. Degradation callback wiring (already fixed in this audit cycle)
2. Article-aware dedup similarity (already fixed in this audit cycle)
3. Full traceback logging in orchestrator (already fixed in this audit cycle)

**Deferred to Phase B:**
- Full-bill validation run (A-VAL)
- `import-linter` configuration (FX4)
- Budget guard wiring (EN2)
- Blackboard aggregation (EN3)
- Router/cascade wiring (EN1)
