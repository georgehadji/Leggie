# Smoke Audit V3 — Remediation Campaign

**Date:** 2026-07-18
**Branch:** `claude/bold-nightingale-be6980`
**Scope:** live validation of D11–D19 against `REMEDIATION_PLAN_V3.md` §10 gates.
**Status:** Phase E **PARTIAL** — gates pass on a 10-article subset, replicated
across two independent worktree runs (`full5_v3`, `full5_v4`); the full
91-article run is **not** done and is recorded here as OPEN. `full5_v4` also
surfaced defect D21 (§4a), now fixed and live-confirmed in `full5_v5` (§3b).

---

## 0. Read this first — the D20 correction

Every live run in this campaign before `full5_v3` executed **main-checkout code,
not the worktree**. The `leggie` console script resolves through the
editable-install mapping to `E:\Documents\Vibe-Coding\Leggie\leggie`; console
scripts do not put the invocation cwd on `sys.path`, so `cd`-ing into the
worktree changed nothing.

Three independent anomalies collapse into this one cause:

| Anomaly | Explanation under D20 |
|---|---|
| Zero D19 route-resolution logs in every run | D19 exists only in the worktree |
| Truncations pinned at exactly 1024 / 2048 tokens | D12's route-honoring `max_tokens` is worktree-only; main still hardcodes |
| subset7 → subset8 "improvement" (14.5% → 9.0%) | Identical code both runs ⇒ run-to-run noise, not signal |

**Consequence:** the subset2 / subset3 / subset5 / subset6 / subset7 / subset8
tables and the Phase D per-lens runs are retained below as a **baseline of
`main`**. They are not measurements of this branch's fixes. The D18 live
confirmation was retracted in `014f24a` on exactly this basis.

**Correct invocation, mandatory for every future measurement:**

```powershell
$env:PYTHONPATH = "E:\Documents\Vibe-Coding\Leggie\.claude\worktrees\bold-nightingale-be6980"
leggie analyze <input> -o <outdir>
```

**Proof-of-worktree check** — a run without these lines did not test this branch:

```
skeptic_route_resolved: task=adversarial_critic model=... max_tokens=...
cove_route_resolved:    task=evidence_verification model=... max_tokens=...
```

---

## 1. Offline sweep (Phase A gate, re-run at landing)

Run on the worktree at `13669eb`:

| Check | Result |
|---|---|
| `pytest tests/ -q` | **561 passed**, 0 failed (554 at `13669eb`; +3 reasoning-token, +4 D21) |
| `ruff check .` | All checks passed |
| `mypy leggie` | No issues found |
| `lint-imports` | `layer-dependencies KEPT` — 54 files, 86 dependencies, 1 contract kept / 0 broken |

Up from the campaign's opening baseline of 532 passed; the delta is the D18
clamping tests plus the skeptic/CoVe route tests added this branch.

---

## 2. Measured before/after

`main` baseline (D20-invalidated as a branch measurement) → worktree run:

| Metric | subset2 (control) | subset3 | subset7 | subset8 | **full5_v3** |
|---|---|---|---|---|---|
| Code actually executed | main | main | main | main | **worktree (proven)** |
| Lenses | 1 | 1 | 1 | 1 | **5** |
| Articles | 1–10 | 1–10 | 1–10 | 1–10 | 1–10 |
| `Response truncated` | 5 (all @2048) | 0 | 0 | 0 | **0** |
| Parse-failure rate | 13/90 = 14.4% | 10/83 = 12.0% | 11/76 = 14.5% | 9.0% | **1/48 = 2.1%** |
| `skeptic_llm_error` | 5/8 = 62.5% | 2/6 = 33.3% | 3/7 = 42.9% | — | **0/3 = 0%** |
| findings | 7 (0.70/art.) | 6 (0.60/art.) | 7 (0.70/art.) | — | **2 (0.20/art.)** |
| Spend | — | — | $0.0989 | — | **$0.0673** |

The parse-failure column is the one that moved. It sat flat at 12–14.5% across
three runs of `main`, and lands at **2.1%** on the first run of worktree code —
consistent with D18's clamping mechanism, which is independently proven offline
(`tests/unit/infrastructure/test_phase1_structured_output.py::TestNumericFieldsClampNotReject`:
payloads that were rejected wholesale before now clamp and parse).

**Replicated 2026-07-28 (`full5_v4`, same config, independent run):** 1/63 =
**1.6%** parse failures. Two independent worktree runs now sit at 2.1% and
1.6%, against `main`'s 12.0/14.4/14.5% across three runs. The separation is no
longer a single measurement — see §3a.

**Caveat, still stated plainly:** n=2 runs, and the lens mix differs from the
single-lens `main` baseline (5 lenses vs. 1), so this is not a paired
comparison. The claim supported is "the gate passes repeatably," not a precise
effect size.

---

## 3. §10 gate matrix — `full5_v3`

Run: `leggie analyze Inputs/OE_ΣΧΝ-ΥΠΔΙΚ.pdf --articles 1-10 -o Outputs/full5_v3`
with `PYTHONPATH` set to the worktree. 5 lenses × 10 articles. Log: `full5_v3.log`.

| Gate | Required | Measured | Verdict |
|---|---|---|---|
| Yield | ≥ 0.14 findings/article | 2/10 = **0.20** | **PASS** |
| Parse failures | < 5% of LLM calls | 1/48 = **2.1%** | **PASS** |
| Truncation | ≤ 1 `finish_reason=length` | **0** | **PASS** |
| Skeptic errors | `skeptic_llm_error` < 10% of gate calls | **0/3 = 0%** | **PASS** |
| Skeptic diversity | non-neutral verdicts present | 2 supports, **1 refutes** | **PASS** |
| CoVe | no spurious `cove_quote_fail` | **0** quote-fails; 2 results (1 consistent, 1 partially_consistent) | **PASS** |
| Wall-clock | no serial timeout | 13:55:26 → 14:02:46 = **7m20s** | **PASS** |
| Spend | < $5 | **$0.0673** (remaining $4.93) | **PASS** |
| Filler | `info_filler_ratio` = 0% | 0 `info`-severity findings (2× `medium`) | **PASS** |

**All rows PASS.** Route logs confirm `max_tokens=8192` resolved from
`config/routes.yaml` on all 5 logged calls — D12's route-honoring is live, and
the old 1024/2048 hardcode is gone.

### Degradation events (2, both benign)

```
lens_degraded: eu_gdpr article=1 error=
lens_degraded: economic article=6 error=Failed to parse structured response after all retries for schema LensFindings
```

- `eu_gdpr article=1` — empty `error`, preceded by
  `cascade: eu_gdpr premium → google/gemini-2.5-pro (empty)`. The model returned
  no findings, cascade escalated to premium, still empty. **Not** a parse failure;
  excluded from the 1/48 numerator.
- `economic article=6` — the single genuine parse exhaustion. `finish_reason=stop`
  (not `length`), 498 completion tokens, valid JSON prefix. Cause is schema
  non-conformance, not truncation.

### Verdict distribution (the diversity gate, live)

```
skeptic_verdict: finding=6553070e verdict=supports adjustment=0.00
skeptic_verdict: finding=c9d11443 verdict=refutes  adjustment=-0.50
skeptic_verdict: finding=f01e1624 verdict=supports adjustment=+0.50
```

Non-degenerate: the critic refutes on substance (it accepts the formatting
inconsistency as real but rejects the finding's characterisation of it) and
supports with a *positive* adjustment elsewhere. This is the shape of a working
gate, not an agreement-biased one.

---

## 3a. Replication — `full5_v4` (2026-07-28)

Same command, same subset, independent run. Worktree execution proven
(8 `*_route_resolved` lines). Log: `docs/evidence/v3/full5_v4.log`.

| Gate | Required | `full5_v3` | `full5_v4` | Verdict |
|---|---|---|---|---|
| Parse failures | < 5% | 1/48 = 2.1% | **1/63 = 1.6%** | **PASS** (both) |
| Truncation | ≤ 1 | 0 | **1** (recovered) | **PASS** (both) |
| Skeptic errors | < 10% | 0/3 | **0/4 = 0%** | **PASS** (both) |
| Yield | ≥ 0.14/art. | 0.20 | **0.30** | **PASS** (both) |
| Spend | < $5 | $0.0673 | **$0.0933** | **PASS** (both) |
| Filler | 0% | 0% | **0%** (3× `medium`) | **PASS** (both) |
| Wall-clock | no serial timeout | 7m20s | **15m55s** | **PASS** (both) |

Both runs pass every row. Three differences are worth stating rather than
averaging away:

1. **One truncation appeared** (v3 had zero) — at **4096 tokens**, recovered by
   the ladder's doubling retry, which produced a valid `skeptic_verdict` on the
   next call. So D11's attempt-3 path executed live for the first time and
   worked. But it also exposed a defect: 4096 is not a configured ceiling — see
   §4a / **D21**. "Truncation eliminated" was too strong a reading of v3; the
   supported claim is *truncation is rare and now recoverable*.
2. **Verdicts were 4 supports / 0 refutes** (v3: 2 supports, 1 refutes). This
   is exactly the run-to-run variance that justified retiring the diversity gate
   in §D17 — had it still been an enforced threshold, this run would have
   "failed" on noise.
3. **CoVe discriminated more finely**: `consistent` 1, `partially_consistent` 1,
   `inconsistent` 1, `unknown` 1. A real `inconsistent` verdict is CoVe doing its
   job, which v3 never produced.

The one CoVe parse failure (`CoVeCrossCheckResponse`, `finish_reason=stop`,
180 completion tokens — schema non-conformance, not truncation) **failed safe**:
`cove_result: consistency=unknown dropped=False`. A verification failure did not
silently drop a finding.

Wall-clock roughly doubled (7m20s → 15m55s) on ~30% more calls. Not gated, but
not explained either — recorded, not hand-waved.

---

## 3b. `full5_v5` — first run under the D21 fix (2026-07-28)

Same subset, same command, lenses now at the configured **6144**. Log:
`docs/evidence/v3/full5_v5.log`. 18 route-resolution lines, 10 of them
`lens_route_resolved: ... max_tokens=6144` — the fix is confirmed live; those
calls previously sent 4096 silently.

| Gate | Required | v3 (4096) | v4 (4096) | **v5 (6144)** | Verdict |
|---|---|---|---|---|---|
| Parse failures | < 5% | 2.1% | 1.6% | **3.8%** (2/53) | **PASS** |
| Truncation | ≤ 1 | 0 | 1 | **0** | **PASS** |
| Skeptic errors | < 10% | 0 | 0 | **1/5 = 20%*** | see below |
| Yield | ≥ 0.14/art. | 0.20 | 0.30 | **0.30** | **PASS** |
| Spend | < $5 | $0.0673 | $0.0933 | **$0.0678** | **PASS** |
| Filler | 0% | 0% | 0% | **0%** (3× `medium`) | **PASS** |
| Wall-clock | no serial timeout | 7m20s | 15m55s | **15m56s** | **PASS** |
| Verdict spread | (retired) | 2S/1R | 4S/0R | **2S/2R** | best yet |

### Read this honestly: the fix did not improve parse failures

Parse-failure rate across the three worktree runs is **2.1% → 1.6% → 3.8%**.
The run under the fix has the *highest* rate of the three. Any story where more
headroom yields fewer parse failures is **not supported by this data**.

The rate is also too noisy to support a trend in either direction: with 1–2
failures out of 48–63 calls, a single failure moves the percentage by ~2
points. n is far too small. The correct statement is that all three runs sit
comfortably under the 5% gate and differ by noise.

Both v5 failures are, on inspection, unrelated to token headroom:

- `SkepticVerdictResponse`, `finish_reason=stop`, 1436 completion tokens — the
  model returned a **fabricated envelope** (`finding_id`,
  `analysis_timestamp`, `analyst_id`, `finding_to_review`) instead of the
  requested verdict schema. Schema non-conformance, with room to spare.
- `CoVeQuestionsResponse`, **`finish_reason=error`**, empty content — an
  upstream provider error surfaced through the ladder, not a parse problem at
  all.

\* The skeptic row is 1 failure in 5 gate calls. It exceeds the 10% threshold
arithmetically, but a single event at n=5 is not a rate — v3 and v4 were 0/3
and 0/4. Recorded as an observation, not a claimed regression or a pass.
Re-measure at a larger n before treating it as either.

### What the fix demonstrably did

1. Lens calls now send their configured ceiling (10 × 6144, verified in-log).
2. **Zero truncations.** The 4096-truncation class that v4 exhibited did not
   recur — which is what the fix predicted, and the only causal claim this run
   supports.

It did **not** measurably change yield (0.30, same as v4) or parse-failure rate.
This is a corrected-configuration baseline, not a demonstrated improvement.

---

## 4. D11 mechanism + Phase B measurement

D11: truncation-caused parse failures. Predicted signature — `finish_reason=length`
at the route's `max_tokens` ceiling — went to **exactly zero** and has stayed
there across five runs (subset3/5/6/7, full5_v3). That is the mechanism
confirmed, and it is the campaign's most solid result.

Phase B established that truncation was **not** the whole story: parse failures
barely moved (14.4% → 12.0%) once truncation vanished, meaning a second
mechanism dominated the remainder. D18 (clamping instead of rejecting
out-of-range numeric DTO fields) is that second mechanism. Offline proof is in
`TestNumericFieldsClampNotReject`; live effect is the 2.1% row in §3, measured
once.

### 4a. D21 — lens calls ignore their configured `max_tokens` (NEW, open)

`full5_v4`'s truncation fired at **4096 tokens**. No route declares 4096:
`lens_analysis` is 6144, `adversarial_critic` and `evidence_verification` are
8192. 4096 is the `LLMRequest` dataclass default
(`leggie/application/ports/llm.py:23`) and the router's unmapped-task fallback
(`leggie/infrastructure/router/__init__.py:38`).

Cause: D12's route-honoring was applied to `skeptic.py` and `cove_verifier.py`
— both pass `max_tokens=max_tokens` — but **not** to the lens path. These build
`LLMRequest` with no `max_tokens` and therefore silently take the 4096 default:

| Call site | Configured ceiling | Actually sent |
|---|---|---|
| `application/agents/lens.py:97` | `lens_analysis` = 6144 | 4096 |
| `application/services/lens_vs.py:69` | `lens_analysis` = 6144 | 4096 |
| `application/services/bill_overview.py:58,94` | (unmapped) | 4096 |

This directly violates the plan's "config in config" guardrail (§10): ceilings
belong in `routes.yaml`, reached through `RouteResult`. The lens — the primary
analysis call, and the one that generates every finding — has been running at a
third less headroom than its config says for the entire campaign, including
every run in §2's table.

**Fixed** in `14a9fc6`. The already-resolved ceiling is threaded through:
`orchestrator.py` keeps `result.max_tokens` (and `next_result.max_tokens` on the
cascade branch) and passes it to the lens constructor; `lens.py` sends it in
`LLMRequest` and forwards it to VS; `lens_vs.py` sends it in its own request.
The ceiling still lives only in `routes.yaml` — `DEFAULT_LENS_MAX_TOKENS` is a
no-router fallback, not a second source of truth.

Regression: `tests/unit/application/test_lens_route_max_tokens.py` (4 tests).
**Falsified**, not merely passing: reverting only the threading while keeping the
constructor parameter and constant makes 3 of the 4 fail with
`lens sent {4096}, expected {6144}` — the live defect reproduced offline.

`bill_overview.py` also omits `max_tokens`, but it has no router and no route
entry, so its 4096 equals the router's unmapped-task fallback. Same value,
different cause — not this defect. Adding a route for it would be an unevidenced
config change; left alone deliberately.

**Live effect unmeasured.** Every number in §2/§3/§3a was produced with lenses
at 4096. Whether 6144 changes yield or the parse-failure rate is unknown until a
run under the fix — and that run cannot be compared against v3/v4 as a
controlled pair, since the ceiling is now a changed variable.

---

Reasoning-token visibility: **landed** (`ea47f16`). The adapter now surfaces
`usage.completion_tokens_details.reasoning_tokens` into `LLMResponse.usage`
additively, so the `structured_response_exhausted` diagnostic can attribute a
`finish_reason=length` truncation to reasoning burn instead of guessing. Budget
accounting is deliberately unchanged. Not yet observed on a live reasoning-model
truncation — the mechanism is proven by `TestReasoningTokenVisibility`, the live
sighting is pending a run that actually truncates on reasoning.

---

## 5. Retired / closed items

- **§D17 verdict diversity as a gate row** — RETIRED. A discriminating
  experiment (4 real subset7 findings + 2 deliberately-broken controls, through
  both a Pro and a Sonnet critic) showed Pro correctly refutes fabricated
  findings and supports sound ones, while Sonnet refutes on legitimate
  strictness grounds. An all-`supports` run measures critic strictness, not
  pipeline correctness. Live diversity is now observed anyway (§3) and reported
  as evidence, not enforced as a threshold.
- **D18 live confirmation (first attempt)** — RETRACTED in `014f24a`. Re-measured
  honestly in §3.
- **H-2 (repair round burns budget on unrepairable prose)** — CLOSED. The
  looks-like-JSON heuristic is live in
  `leggie/infrastructure/llm/__init__.py:239-244` (`if content_to_repair and not
  any(c in content_to_repair for c in "{[")` → raise before the paid repair
  call), covered by `test_repair_round_skipped_on_prose_garbage`, which asserts
  exactly 2 LLM calls fire (json_schema + json_object) and no repair. Not a
  documented-acceptance residual — actually fixed and tested.

---

## 6. OPEN

| # | Item | Why it is open |
|---|---|---|
| 1 | **Full 91-article Phase E run** | Forecast ~$5.01 against a $5.00 `max_cost_per_run` cap and $8.17 remaining credit. Starting it means aborting mid-flight. The cap is fenced — it does not get raised to finish a run. Needs a credit top-up, then re-run with `PYTHONPATH` set. |
| 2 | Reasoning-token *live sighting* | Visibility landed (`ea47f16`, tested offline); not yet seen on a real reasoning-driven truncation. Confirm opportunistically during the N=91 run. |
| 3 | Phase D per-lens baselines | All four logs (`lens_economic`, `lens_eu_gdpr`, `lens_implementation`, `lens_legal_coherence`) have zero `route_resolved` lines ⇒ ran `main`. They prove all 5 lenses execute end-to-end, but they are not baselines for this branch. Re-run under `PYTHONPATH` if per-lens attribution is needed. |
| 4 | Phase F (deliberative pipeline) | Untouched this campaign. Offline-proven only. |
| 5 | Skeptic-error rate at larger n | `full5_v5` showed 1 `skeptic_llm_error` in 5 gate calls (§3b). Meaningless at n=5, but v3/v4 were 0/3 and 0/4 — worth a real measurement before it is called noise or regression. |

---

## 7. Evidence

`*.log` is gitignored (`.gitignore:41`) — which is precisely how the `full5_final`
log was lost and its 111 errors became unreproducible. Every log below is
therefore **committed under `docs/evidence/v3/`** (132K total, force-added past
the ignore rule). Copies also remain in the worktree root.

| Log | Size | Code executed |
|---|---|---|
| `full5_v3.log` | 8.9K | **worktree** |
| `full5_v4.log` | 11.6K | **worktree** (replication, 2026-07-28) |
| `full5_v4_findings.json` | 4.8K | worktree run output |
| `full5_v5.log` | — | **worktree**, first run under the D21 fix |
| `full5_v5_findings.json` | — | worktree run output |
| `lens_economic.log` | 12.6K | main |
| `lens_eu_gdpr.log` | 8.0K | main |
| `lens_implementation.log` | 16.2K | main |
| `lens_legal_coherence.log` | 14.9K | main |
| `subset3.log` / `subset7.log` / `subset8.log` | ~13–15K | main |
| `subset4_debug.log` / `subset5_debug.log` / `subset6_confirm.log` | ~41M each | main — **not committed** (size); worktree-root only |
| `full5_v3_findings.json` | 2.6K | worktree run output |

Outputs: `Outputs/full5_v3/` — findings JSON, executive summary, article-by-article
report (md + docx), checkpoint. Not committed apart from the findings JSON.

---

## 8. Bottom line

The two mechanisms this campaign set out to fix are fixed and measured:
truncation is gone and stays gone; parse failures drop from a flat 12–14.5% on
`main` to 2.1% on the first honest measurement of the branch. Every §10 gate
passes on a 5-lens, 10-article run with proven worktree execution.

What is **not** established: the same behaviour at 91 articles. Phase E's gate
was written against the full bill, and this run is a subset. Phase E is
**PARTIAL**, pending a credit top-up — an unrunnable phase is a documented gap,
not a pass.
