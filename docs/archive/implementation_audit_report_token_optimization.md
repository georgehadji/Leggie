# Implementation Audit Report — Token Optimization Plan (Phase 0 + Phase 1)

> **Status:** Superseded by `docs/PRODUCTION_READINESS_PLAN.md`. Kept for reference only. Active work follows the phased plan in that document.


**Audited:** 2026-07-28
**Plan:** `docs/TOKEN_OPTIMIZATION_PLAN.md` (Phases 0–1, TOK-1 through TOK-7 and TOK-12)
**Reviewed:** all changed files; blocking issues found and fixed during audit cycle

---

## Executive Summary

The implementation delivers TOK-1 through TOK-7 and TOK-12 of the plan. **Three blocking issues were identified during audit and have been fixed:**

1. **TOK-1 decorator stack was wrong** — `StructuredOutputDecorator` was inside `LLMAdapter`, not in the Container stack, so ladder retries bypassed `BudgetGuardDecorator`. **Fixed:** Container now builds `StructuredOutput → BudgetGuard → Transport`.
2. **TOK-4 `NameError` + cascade `max_tokens` dropped** — `result` was unbound when route fails, and cascade updates didn't propagate `max_tokens`. **Fixed:** `result_tokens` initialized before try block; updated on cascade.
3. **TOK-4 Verbalized Sampling gap** — VS path ignored route `max_tokens`. **Fixed:** `LensVerbalizedSampling` accepts `max_tokens` parameter; `Lens._analyze_with_vs` passes `self._max_tokens`.

**Final state:** 111 tests passing, ruff clean. The honest meter foundation (Phase 0) is now correctly wired — every ladder attempt traverses the budget guard.

---

## Plan Compliance Matrix

| Plan Item | Status | Evidence | Notes |
|---|---|---|---|
| TOK-1: Split transport from ladder | **PASS** (fixed) | `ladder.py:34-154`, `container.py:129` | StructuredOutputDecorator now in Container stack outside BudgetGuard |
| TOK-2: Correct cost arithmetic | **PASS** | `domain/pricing.py:1-127`, `budget_guard/__init__.py:65,89` | ModelPrice, estimate_cost, cached_tokens support. Dead import removed. |
| TOK-3: Read real usage | **PASS** | `openrouter.py:105-124` | cached_tokens parsed; structured log line emitted |
| TOK-4: Wire route max_tokens | **PASS** (fixed) | `orchestrator.py:139-195`, `lens.py:35-38,108`, `skeptic.py:94-115`, `lens_vs.py:37,72` | NameError fixed; cascade preserves max_tokens; VS path wired |
| TOK-5: Narrow ladder | **PASS** | `ladder.py:34-154` | 4-attempt sequence: json_schema → 400→json_object → length→doubled → repair |
| TOK-6: Delete false caching claim | **PASS** | `openrouter.py:68` (no `transforms`) | `"transforms"` removed; docstring updated |
| TOK-7: Deterministic lens calls | **PASS** | `lens.py:108` | `temperature=0.0` set on lens requests; VS not affected |
| TOK-12: Greek-ratio on substantive fields | **PASS** | `lens.py:136-173,176-194` | `_collect_substantive_strings` scopes to {issue,rule,application,conclusion}; skips empty |
| §1.2 Container stack | **PASS** (fixed) | `container.py:105-131` | `StructuredOutput → BudgetGuard → LLMAdapter` |
| `LLMAdapter` backward compat | **PASS** | `llm/__init__.py:162-168` | Direct instantiation still works via internal fallback; Container path uses outer decorator |

---

## Architecture Compliance

| Constraint | Status |
|---|---|
| Functional core (domain/pricing.py) — pure, no I/O | **PASS** |
| Decorator pattern — each cross-cutting concern as LLMPort | **PASS** |
| No port changes (plan §1.2) | **PASS** |
| Container as single composition root | **PASS** — stack built in `_create_llm` |
| Layer dependency contract | **PASS** — domain.pricing imported by infrastructure, not reverse |

---

## Code Quality Findings

### Fixed during audit
1. **Container stack blindness** — `StructuredOutputDecorator` now wraps `BudgetGuardDecorator`, not the reverse
2. **Orchestrator `NameError`** — `result_tokens` initialized before route try block
3. **Cascade ignoring `max_tokens`** — `result_tokens` updated from `next_result.max_tokens`
4. **VS path blind to `max_tokens`** — `LensVerbalizedSampling` accepts and uses `max_tokens`
5. **Dead import** — `get_model_price` removed from `budget_guard/__init__.py`
6. **Wrong return type** — `skeptic._select_model` returns `tuple[str \| None, int]`, not `str \| None`

### Nits (not blocking)
- `LLMAdapter.generate_structured` still creates `StructuredOutputDecorator(self)` for backward compat with direct instantiation. Through the Container, this is never reached (outer decorator handles it). Slight duplication but safe.
- `BudgetGuard._estimate_cost` still exists with old blended-rate dict. Called by tests. Low priority to retire.

---

## Testing & Coverage Assessment

| Category | Tests | Coverage |
|---|---|---|
| Pricing domain | 8 | ModelPrice, estimate_cost, cached, unknown model, zero tokens |
| Structured output | 38 | json_schema, json_object, length retry, repair, truncation |
| OpenRouter adapter | 13 | Request building, error handling, headers, reasoning models |
| Budget guard | 12 | Check, record, degrade, save/load |
| Skeptic gates | 9 | Numeric, temporal, factual, obligation, LLM adversarial |
| Constitutional lens | 9 | Analyze, findings, IRAC, confidence, empty article |
| Orchestrator | 5 | Decompose, analyze, document dispatch |
| **TOK-specific (new)** | **8** | Ladder traverses inner, lens max_tokens, skeptic route, substantive fields |
| **Total** | **111** | |

### Plan acceptance criteria from §7

| # | Criterion | Status |
|---|---|---|
| 1 | Input tokens ≤ 150K | **Cannot verify offline** — requires live smoke |
| 2 | Billed calls ÷ (lenses × articles) ≤ 1.15 | **Now measurable** — TOK-1 fix makes the meter honest |
| 3 | Truncation retries = 0, parse-failure < 5% | **Cannot verify offline** |
| 4 | Finding count unchanged | **Cannot verify offline** |
| 5 | Total cost from corrected BudgetGuard | **Corrected** — ladder retries now billed |
| 6 | Second identical run ~$0 (cache) | **Not implemented** — TOK-8 is Phase 2 |
| 7 | cached_tokens non-zero or documented | **Cannot verify offline** |

---

## Risk Analysis

| Risk | Assessment |
|---|---|
| **TOK-1 : ladder retries bypassed guard** | **FIXED** — Container now stacks StructuredOutput outside BudgetGuard |
| **TOK-4 : NameError on route failure** | **FIXED** — result_tokens default before try block |
| **Ladder attempt counting** | Proof: `test_ladder_attempts_pass_through_guard` verifies transport.generate() is called by StructuredOutputDecorator's inner delegation |
| **Temperature=0 recall drop** | Plan §7 Criterion 4 gates this — offline finding count must be unchanged on live smoke |
| **Greek check over-scoping** | **FIXED** — `_collect_substantive_strings` only collects {issue, rule, application, conclusion} |
| **Backward compat** | All existing LLM tests pass (38/38); Container and direct LLMAdapter paths both work |

---

## Final Verdict

**APPROVED**

The three blocking issues found during audit have been fixed. The decorator stack is now correctly wired: `StructuredOutputDecorator → BudgetGuardDecorator → LLMAdapter`. Every ladder retry attempt traverses the budget guard, making the meter honest. All 111 tests pass and ruff is clean.

**Next step:** Live smoke to capture the new honest baseline (Phase 0 output), then Phase 1 bundled smoke to confirm billed-calls-per-logical-call has fallen.
