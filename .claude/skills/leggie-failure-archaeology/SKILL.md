---
name: leggie-failure-archaeology
description: >
  The chronicle of every major Leggie investigation, dead end, rejected
  approach, and revert — as symptom → root cause → evidence → status. Load
  BEFORE re-investigating any past symptom (missing findings, phantom
  articles, filler noise, budget blocks), before proposing an approach that
  may already have been rejected (learned router, debate, knowledge graph),
  or when asking "why is X this way". Prevents re-fighting settled battles.
---

# Leggie Failure Archaeology

Entry format: **symptom → root cause → evidence → status**. Statuses:
SETTLED (fixed and proven), OPEN, PARTIAL. Snapshot date 2026-07-14; the
working tree has uncommitted changes — re-verify statuses via Provenance.

## The chronicle

### 1. The stub-MVP scandal (the founding lesson) — SETTLED
- **Symptom:** MVP "worked", 199 green tests; real run produced 299 findings on
  the sample bill of which 202 were INFO filler; every suggestion identical.
- **Root cause:** no LLM was EVER called. Lenses were regex→canned-string
  templates (`constitutional_lens.py` regex → hardcoded IRAC); the
  orchestrator built lenses with no LLM; `OpenRouterProvider` was dead code;
  eval scored EMPTY findings lists.
- **Evidence:** `docs/FIX_PLAN.md` Part 1, defects D1–D7 (that plan's own
  numbering), reproduced against `analysis_report.md`.
- **Moral:** green tests are necessary, never sufficient. See
  **leggie-validation-and-qa**.

### 2. Phantom parser articles — SETTLED (FIX_PLAN F0)
- **Symptom:** fake articles "552", "622Γ", "58Α"; titles truncated mid-word
  ("Άρθρο 22 Κ"); 214 "articles" parsed from a bill with far fewer.
- **Root cause:** `ARTICLE_PATTERN` matched every "Άρθρο N" occurrence,
  including in-body cross-references to OTHER laws (ΚΠολΔ, ΠΚ); no PDF
  mid-token newline repair.
- **Evidence:** FIX_PLAN D2 (`parse/__init__.py:17-20` at the time); fixed in
  commit 703651e "FIX_PLAN F0+F2: parser fix + noise suppression".

### 3. Filler-findings flood — SETTLED (FIX_PLAN F2)
- **Symptom:** 68% of findings were INFO "Δεν εντοπίστηκαν προφανή
  συνταγματικά ζητήματα" (no issues found).
- **Root cause:** lenses emitted a baseline finding per article to satisfy an
  old Phase-1 exit gate.
- **Evidence:** FIX_PLAN D3; suppression in 703651e and f7c2ed8 ("suppress
  filler findings and recover real findings lost to schema drift").

### 4. Fake/dead model IDs (twice) — SETTLED, guard added
- **Symptom:** LLM calls failing pipeline-wide.
- **Root cause:** routes.yaml configured model IDs that don't exist on
  OpenRouter. Commit 2780339 (2026-07-09) added "GPT-5.6
  luna/terra" IDs (`openai/gpt-5.6-luna:thinking` etc.) which were replaced
  the next day by 39b42ef "repair LLM pipeline (real model ids + tolerant
  parser)" — routes now use verified google/anthropic IDs.
- **Guard:** `_OFFLINE_MODEL_ALLOWLIST` + `validate_model_ids()` in
  `infrastructure/llm/__init__.py`; adapter refuses unknown default model at
  init.
- **Moral:** never add a model ID without checking the OpenRouter catalog.

### 5. Schema drift massacre (D1) — SETTLED (Phase 1, commit 63fb25f)
- **Symptom:** live smoke: ~90 articles analyzed, ONE finding survived.
- **Root cause:** models returned JSON with invented field names (`lens_id`,
  `title`, `issue_id`) → Pydantic rejected → findings silently lost. 134
  `pydantic … Field required` errors in one run.
- **Fix:** OpenRouter strict json_schema mode + alias map + centralized
  `StructuredResponseParser` + repair round. Details:
  **llm-structured-output-reference**.
- **Evidence:** `docs/REMEDIATION_PLAN.md` D1; `implementation_audit_report.md`.

### 6. Truncated JSON (D2) — SETTLED (Phase 1)
- **Symptom:** `Unterminated string`, `Expecting value: line 1 col 1`, cascade churn.
- **Root cause:** `finish_reason` ignored; verbose Greek IRAC overran 4096
  max_tokens.
- **Fix:** finish_reason threaded; truncation retry with doubled max_tokens
  (cap 16_384); `lens_analysis` max_tokens 4096→6144.

### 7. Phase-1 audit HIGH findings — PARTIAL
- H-1 (LLMError path skipped truncation retry): **fixed** (landed in
  cb7fde8/406f969; `response` initialized before attempt 1; guarded attempt 3).
- H-2 (repair round burns a paid call on unrepairable content): **partial** —
  empty-content guard exists; non-empty garbage still costs one call.
- **Evidence:** `implementation_audit_report.md` §1; current
  `infrastructure/llm/__init__.py`.

### 8. Budget guard premature blocks — SETTLED
- **Symptom:** runs throttled long before $5 spent.
- **Root cause:** 500k token ceiling governed instead of the cost cap.
- **Fix:** token ceiling → 20M; cost cap is the governor (comment in
  `settings.py BudgetSettings`). **Trap:** `.env.example` still shows the
  stale 500000 value.

### 9. Tier/model misalignment — SETTLED (7d5d1ef)
- report_generation route said one tier while naming a model of another;
  aligned to premium/gemini-2.5-pro.

### 10. The 108 mypy strict errors purge — SETTLED (dd71f6d + 7c75127)
- CI's mypy step had NEVER been green on master; 108 pre-existing strict
  errors across 27 files were fixed in one pass. The two commits share a
  message (same work, follow-up completion an hour later — not a revert).

### 11. Ruff debt freeze — SETTLED as policy
- Pre-existing ruff issues were frozen into the pyproject ignore list rather
  than fixed (commits 5b8d305, b2c15ab, e665668). The list is documented debt;
  never widen it (see **leggie-change-control** §3.5).

### 12. Architecture upgrade wave (G1–G10) — MOSTLY SETTLED
- `docs/ARCH_UPGRADE_PLAN.md` (8/10 → target 9.5): G3/G4/G5/G6/G7/G8/G10
  landed (commits 08c5875, 8d79951): budget checkpoint (--checkpoint), lens
  isolation, LLM module split, ports, trace_id, rate limiter registration.
- G1 resume-from-stage = REMEDIATION D10: **PARTIAL** —
  `infrastructure/persistence/checkpoint_store.py` exists, but
  `BillAnalysisFlow` checkpoints only budget spend; completed stages re-run
  (and re-bill) after a crash.

### 13. Deliberately rejected/deferred approaches — FENCED (do not re-propose without new evidence)
From `tasks/todo.md` §0 ("What changed vs the initial spec"):

| Idea | Decision | Rationale (recorded) | Reopen condition |
|---|---|---|---|
| 25+ personas | 5 fixed lenses | diversity value collapses fast | eval evidence that lenses miss whole categories |
| Verbalized Sampling 20 paths | 3–5 paths (wired 2026-07-12, delta unmeasured) | cost; diminishing returns past ~5 | recall delta measured on gold set |
| Multi-round debate | deferred post-MVP | adversarial critic gives most benefit cheaper | skeptic proven insufficient |
| Knowledge graph | deferred to v3 | parse + retrieval covers 90% | retrieval wired and hitting limits |
| Learned/telemetry router | REJECTED for static YAML | chicken/egg: needs eval data that doesn't exist | gold set large enough to train/eval routing |
| Continuous learning | deferred | same chicken/egg | same |
| 10 report types | 2 (Exec Summary + Article-by-Article) | rest are formatting variants | user demand |
| Eval last | moved FIRST (Phase 0) | can't prove "superior to experts" without gold set | — |

### 14. Deliberative-pipeline integration wave (PRs #3/#6, 2026-07-12→14) — LANDED, live-unproven
- Symptom/goal: opt-in multi-model deliberative analysis via external Reasoner
  backend (WU-1…WU-8 commits 37adcff…e2b4848) + Stage 0 bill preview (220ed45)
  + `--articles` selection (19102f4).
- Incident within it: **autostarted Reasoner backend process leaked** after
  deliberative runs — fixed in PR #7 (af8d… commit af4e4a8): handler wraps
  `flow.run()` in `try/finally` calling `server_manager.shutdown()`, cleanup
  failure never shadows the real exception (the `ReasonerUnavailableError`
  that `--fallback` depends on). Evidence: `tests/unit/application/
  test_deliberative_resource_lifecycle.py` (263 lines).
- Status: offline-proven only; no recorded live deliberative run.

### 15. Phase 0 live smoke campaign (2026-07-11, commit 02c3ac6) — PARTIAL
- Single-lens smoke COMPLETED after 5 iterations (v1 timeout → v4/v5 outputs):
  fixes = parallel fan-out (D3/D6), schema constraint stripping, route fix
  (orchestrator queried `lens_<name>` so the `lens_analysis` route was DEAD),
  raised Skeptic/CoVe hard-coded token ceilings, critic → gemini-2.5-pro.
- v5 numbers: 299 LLM calls, 4.0% parse failures, 9 refutes/9 supports/1
  neutral skeptic verdicts, findings_per_article 0.14.
- Full 5-lens run: three attempts ALL stopped (stale route; OpenRouter 402
  credit wall — account, not budget guard; parse-failure degradation).
  Evidence: `docs/SMOKE_AUDIT.md`.
- **Superseded 2026-07-28** by `docs/SMOKE_AUDIT_V3.md`: a 5-lens run now
  passes every §10 gate on a *10-article subset*, replicated (`full5_v3`,
  `full5_v4`, then `full5_v5` under the D21 fix), with parse failures down
  from 12–14.5% to 2.1%. Yield at the full 91 articles remains unproven —
  that, not the 5-lens configuration itself, is the open gap.

## Open defect ledger (2026-07-14 snapshot)

| ID | Defect | Status |
|---|---|---|
| D3 | sequential article loop | CLOSED — flow calls parallel `analyze_document()` (`bill_analysis_flow.py:264`) |
| D4 | Verbalized Sampling unwired | CLOSED — `--verbalized-sampling` flag + `LEGGIE_ANALYSIS__USE_VERBALIZED_SAMPLING` wired into flow |
| D5 | ModelBasedReranker unwired | PARTIAL — `LEGGIE_ANALYSIS__RERANKER=model` selector exists, but `configure_defaults()` binds no RerankerPort → silently composite |
| D6 | article-level failure isolation | CLOSED with D3 (smoke v2 onward ran parallel fan-out) |
| D7 | citation resolution index empty | PARTIAL — container loads `data/citation_index.json` into `GreekCitationParser` (`container.py:134`); index file is tiny (309B) — coverage, not wiring, is the gap |
| D8 | cli_handlers container/ad-hoc duplication | CLOSED — no `_try_get_*` fallbacks remain in `cli_handlers.py` |
| D9 | rate limiter | LIKELY FIXED (constructed in LLMAdapter → OpenRouterProvider) — verify consumption |
| D10 | resume-from-stage | PARTIAL (store exists, flow checkpoints only budget spend) |
| — | verification layer (LLM CoVe + skeptic gate): single-lens smoke PASSED (v5, 2026-07-11); 5-lens smoke PASSED on a 10-article subset, replicated (2026-07-28); **the 91-article run has never completed** | THE current campaign — **leggie-remediation-campaign** |
| — | deliberative pipeline: landed + leak-fixed, no live run recorded | OPEN validation |

## When NOT to use this skill

- Live triage of a currently broken run → **leggie-debugging-playbook**
- Executing the fix campaign → **leggie-remediation-campaign**
- Design rules going forward → **leggie-architecture-contract**, **leggie-change-control**

## Provenance and maintenance

- Commit trail: `git log --oneline -30`
- D3 check: `grep -n "for article in self._doc.articles" leggie/application/workflow/bill_analysis_flow.py`
- D4 check: `grep -rn "verbalized" leggie/interfaces leggie/application/cqrs`
- D7 check: `grep -n "resolution_index" leggie/infrastructure/container.py leggie/infrastructure/citation/__init__.py`
- D10 check: `grep -rn "checkpoint_store\|CheckpointStore" leggie/`
- Model-ID incident: `git show 2780339 --stat && git show 39b42ef --stat`
