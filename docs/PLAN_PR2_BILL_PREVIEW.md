# Bill Preview Stage Plan (PR #2)
**Date:** 2026-07-14  **Branch:** integrate/pr2-preview-stage → master  **Author:** merge integration of `claude/leggie-bill-analysis-khkmkw`

Class-A change: adds new Domain models. Frozen-domain guardrail
(`guardrails.yaml` deny_paths `leggie/domain/models/`, change-control §3.2)
applies — this doc is the required plan-doc justification for the deliberate
exception. The additions are **purely additive**: no edit to the frozen
`Finding` / `IRAC` / `Confidence` models.

## 0. Current state (what already works — do not re-touch)
- `BillAnalysisFlow.run()` on master (post-#1): full pipeline with article
  selection via the `articles` string expression ("1-5,7"), checkpoint/resume,
  reranker, CoVe, skeptic. **Left intact.**
- `leggie preview` does not exist on master; `bill_overview.py` and
  `prompts/overview.py` are absent (verified `git cat-file` against master).

## 1. What this adds (ranked by surface)
| # | Addition | Layer | Notes |
|---|----------|-------|-------|
| P1 | `ArticleOverview`, `BillOverview` Pydantic models (frozen) | Domain | additive; `Finding`/`IRAC`/`Confidence` untouched |
| P2 | `WorkflowState.PREVIEWING`; `EventType.OVERVIEW_GENERATED`, `ARTICLES_SELECTED` | Domain | additive enum members |
| P3 | `BillIntroSummary`, `ArticleOverviewCandidate` response schemas | Domain (structured_output) | satisfies non-negotiable §3.7 (structured output only) |
| P4 | `BillOverviewGenerator` service (LLM + offline fallback) | Application | new file, no port change |
| P5 | `overview` prompts | Application | new file |
| P6 | `BillAnalysisFlow.preview()` + `overview` property + `selected_article_ids` run param | Application | additive; does not alter existing `articles`/checkpoint logic |
| P7 | `PreviewBillCommand` + `PreviewBillHandler` (container-DI style) | Application (CQRS) | mirrors existing handler shape |
| P8 | `leggie preview` CLI subcommand | Interfaces | additive |

## 2. Integration decisions (deviations from the source branch)
- Source branch's `run()` used an older signature (`selected_article_ids` only,
  no router/cove/checkpoint). Taking that side would **regress #1**. Resolution:
  keep #1's `run()` verbatim and add `selected_article_ids` as an *additional*
  optional param with an additive filter step. #1's `articles` expression stays.
- Source branch's `PreviewBillHandler` used a removed `_try_get_llm()` free
  function. Rewritten to resolve LLM via `_resolve_llm_from_container` (D8
  composition-root convention).
- Dropped the "reuse preview ingest/parse in run()" optimization: it conflicts
  with #1's deliberate `self._doc = None` reset for safe flow reuse + checkpoint
  restore. Preview and analyze are separate CLI commands; no test requires
  in-process reuse. Documented here so the omission is intentional, not a bug.

## 3. Invariants preserved
- Dependency rule: Domain imports nothing outward; Application→Domain only. ✓
- Ports unchanged (§3.3): no new port, no new method on an existing port. ✓
- No silent failure (§3.6): `BillOverviewGenerator` falls back deterministically
  and `preview()` records `OVERVIEW_GENERATED`. ✓
- Structured output (§3.7): overview LLM calls validate against the two new
  schemas. ✓

## 4. Gates
- `pytest tests/ -q` — full suite green, incl. 3 new preview tests + updated
  `test_flow_state_machine` (IDLE now has 2 valid events).
- `mypy leggie/ --ignore-missing-imports` — clean on touched modules.
- `ruff check leggie/ tests/` — no new violations; ignore list untouched (§3.5).
- `lint-imports` — layer contract holds.
- Class-A live smoke: preview is descriptive (no findings), offline-safe; deep
  analysis path is unchanged from #1's already-smoked build, so no *new* live
  smoke is gated by this merge specifically.

## 5. Rollback
Single squash-merge commit; revert reverts cleanly (all additions, no edits to
frozen models). Re-enable the domain guardrail after merge.
