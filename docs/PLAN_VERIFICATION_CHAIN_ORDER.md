# Leggie — Verification Chain Order Fix (rerank must run last)

> Status: **IMPLEMENTED** (offline gates green; live smoke NOT run — see §5).
> Class: **A** (pipeline-behavior-changing — touches skeptic/CoVe/reranker sequencing).
> Layer: Application (`services/blackboard_aggregator.py`, `workflow/bill_analysis_flow.py`).

The specified credibility chain is:

```
Finding → Calibrated Skeptic → CoVe → deterministic citation verification → reranking
```

The implementation ran reranking **second**, not last.

---

## Part 1 — Diagnosis (evidence)

| # | Defect | Root cause (file:line at time of fix) | Consequence |
|---|--------|----------------------------------------|-------------|
| R1 | **Rerank ran before the verification gauntlet.** Both aggregation paths ordered findings as `dedup → rerank → skeptic → CoVe`. | `blackboard_aggregator.py` `aggregate()` Rounds 2–4 (default path, `use_blackboard=True`); `bill_analysis_flow.py` `_aggregate_inline_dedup_rerank()` + `_aggregate_inline_verify()` (inline path). | The published order was computed from state that no longer existed by the time the report was written. |
| R2 | **Stale confidence.** `CompositeReranker.score()` reads `finding.confidence.score` (`rerank.py:71`), but `CalibratedSkeptic.review()` *replaces* `Finding.confidence` afterwards (`skeptic.py:185-188`, provenance `skeptic-calibrated`), as does `CoVeVerifier._apply_revision()` (`cove_verifier.py:404-409`, provenance `cove-verified`). | — | A finding the skeptic downgraded from 0.9 → 0.1 kept its top rank. Severity-ordered output silently misrepresented which findings the system actually still believed. |
| R3 | **Stale novelty.** `_compute_novelty()` scores each finding against `all_findings` (`rerank.py:88-115`). Ranking before the filters measured novelty against a population that included findings the skeptic and CoVe then removed. | — | A finding suppressed as near-duplicate of a twin stayed suppressed after that twin was refuted and dropped. |
| R4 | **Missing completion event on empty terminal paths.** Three early returns in `aggregate()` returned `[]` without recording `AGGREGATION_COMPLETED`; only the CoVe-empties-everything path recorded it. | `blackboard_aggregator.py` early returns | Violated the event-spine invariant (architecture contract §4.6): a run whose findings were all refuted left no completion record to audit. |

`verify_batch()` preserves input order and neither the skeptic nor CoVe re-sorts,
so the pre-verification rank order survived unchanged into
`ExecutiveSummaryRenderer` / `ArticleByArticleRenderer` and `findings.json`.

**Note:** `README.md`'s prose ("Credibility pipeline" list) already described the
correct order. The mermaid diagram, `docs/ARCHITECTURE.md`, `ARCHITECTURE_MINDMAP.md`
and the architecture-contract skill all described the incorrect implemented order.

---

## Part 2 — Fix

Both aggregation paths now run:

```
dedup → skeptic → CoVe (+ deterministic citation resolution) → rerank
```

- `BlackboardAggregator.aggregate()` — rounds resequenced; board `agent_id`
  labels corrected to name the actual producer (`dedup`, `skeptic`, `cove`,
  `reranker`) instead of the previous `reranker-dedup`/`reranker` labels.
- `BillAnalysisFlow._aggregate_inline_dedup_rerank()` → renamed
  `_aggregate_inline_dedup()` (dedup only); `_aggregate_inline_verify()` now
  closes with the rerank step. State-machine semantics are unchanged:
  `AGGREGATING` = collapse duplicates, `VERIFYING` = verify then order.
- `BlackboardAggregator._complete()` — single terminal exit that always records
  `AGGREGATION_COMPLETED` (closes R4).

No port signatures, domain models, or layer boundaries changed.

### Cost note

Rerank now runs on the post-verification survivor set, so `ModelBasedReranker`
(`LEGGIE_ANALYSIS__RERANKER=model`) makes its single batch call over fewer
documents. Strictly cheaper; never more expensive.

---

## Part 3 — Tests

`tests/unit/application/test_verification_chain_order.py` (7 tests). Each is
written to fail under the old order — **verified**: 7/7 fail with the pre-fix
sources restored, 7/7 pass after.

| Test class | Proves |
|---|---|
| `TestRerankSeesPostSkepticConfidence` | Skeptic-downgraded findings sink; published order is monotonic in final confidence. |
| `TestRerankSeesPostCoVeConfidence` | CoVe's revision reorders output. |
| `TestNoveltyMeasuredAgainstSurvivors` | Novelty is measured against survivors, not the pre-filter population. |
| `TestInlinePathMatchesBlackboardPath` | Both aggregation paths produce the same published order. |
| `TestAggregationAlwaysCompletes` | `AGGREGATION_COMPLETED` recorded even when everything is refuted (R4). |

---

## Part 4 — Offline gates (all green)

| Gate | Result |
|---|---|
| `pytest tests/ -q` | 546 passed, 1 skipped (baseline before change: 539 passed, 1 skipped) |
| `mypy` (strict, touched modules) | clean |
| `ruff check leggie/ tests/` | clean — ignore list **not** widened |
| `lint-imports` | `layer-dependencies KEPT`, 1 contract kept / 0 broken |
| `bandit -c pyproject.toml -r leggie/` | no findings |

---

## Part 5 — Residual risk: live smoke NOT run

Class-A changes require a live smoke judged against `docs/REMEDIATION_PLAN.md`
§10. **It has not been run for this change** — no `LEGGIE_LLM__OPENROUTER_API_KEY`
was available in the environment where the fix was made, and a smoke run is
billable.

What the offline evidence does and does not establish:

- **Established:** the ordering defect was real, both paths now rank last, and
  the regression tests fail on the old code and pass on the new.
- **Not established:** the *magnitude* of the ordering change on a real bill —
  i.e. how far real findings move once ranked on post-verification confidence.
  That requires a live run.

Recommended verification when a key is available (procedure:
**leggie-run-and-operate**; measurement: **leggie-diagnostics-and-tooling**):

1. `leggie analyze Inputs/OE_ΣΧΝ-ΥΠΔΙΚ.pdf` on this branch.
2. Confirm in `Outputs/<stem>_findings.json` that confidence is non-increasing
   down the findings list — under the old order it was not.
3. Compare top-10 findings against a pre-fix run of the same bill to quantify
   the reordering.

Item 2 is a cheap, deterministic post-condition worth adding as a smoke
assertion.
