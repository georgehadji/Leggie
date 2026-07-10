---
name: leggie-research-methodology
description: >
  The discipline that turns a hunch into an accepted result in Leggie. Load
  when investigating an unexplained behavior, proposing a root-cause mechanism
  or fix, designing an experiment, or deciding whether a result is proven.
  Covers the evidence bar (one mechanism must explain ALL observations),
  adversarial refutation, hypothesis-predicts-numbers-before-running, the idea
  lifecycle (flag → measured delta → adopt or documented retirement), and
  first-principles analysis recipes with worked examples from this repo.
---

# Leggie Research Methodology

For engineers and models that tend to install fixes without proving
mechanisms. Every recipe below has a worked example from this repo's history.

## 1. The evidence bar: one mechanism, ALL observations

A root-cause claim is accepted only when a SINGLE mechanism explains every
observation — including the negatives (things that did NOT happen).

**Worked example (the stub-MVP diagnosis, `docs/FIX_PLAN.md` Part 1):** seven
symptoms — keyword-triggered findings, identical suggestions on every
article, evidence consisting of single trigger words, 68% filler findings,
skeptic filtering nothing, rerank doing nothing, eval scoring zero — all
explained by ONE mechanism: *no LLM was ever called; lenses were regex→
template stubs*. Each symptom is a corollary. A hypothesis that explained
only the filler ("bad thresholds") or only the identical suggestions ("bad
prompt") would have failed the bar — and did, until the full mechanism was
found.

If your mechanism explains 5 of 7 observations, you have a partial. Keep
digging or explicitly scope the residual.

## 2. Adversarial refutation (assigned, not optional)

Every proposed conclusion gets a devil's-advocate pass before acceptance.
This project builds refutation INTO the product (skeptic gates that try to
refute findings; audit reports with severity findings and
APPROVED-WITH-CHANGES verdicts) — mirror it institutionally.

Refutation checklist for any root-cause/fix claim:
- [ ] What OTHER mechanism produces the same signature? (enumerate ≥2)
- [ ] What observation would DISTINGUISH them? Run that, not another confirmation.
- [ ] Do the negatives fit? (e.g. "if it were truncation, we'd also see
      finish_reason=length — do we?")
- [ ] Does the fix's own evidence contradict anything else in the run?
- [ ] Who audited this? A significant phase is not done until a second pass
      (audit doc) has tried to break it — precedent:
      `implementation_audit_report.md` found 2 HIGH defects in an
      already-"working" phase.

## 3. Hypothesis predicts numbers BEFORE running

Template (fill in before spending tokens or money):

> If mechanism **M** is true, then command **C** will show **metric in range R**.
> If instead we see **X**, M is false and the next candidate is **M′**.

**Worked example (D1 schema drift):** hypothesis — findings die at Pydantic
validation, not generation. Prediction: strict json_schema mode + alias map
takes survivors from ~1 to roughly article-proportional and parse failures
below 5% (written into REMEDIATION_PLAN §10 BEFORE the smoke). That
pre-registration is what makes the post-fix smoke evidence instead of
anecdote.

No experiment runs without its predicted number written down first.

## 4. The idea lifecycle

```
hunch
 → evidence gathering (log-signature counts, artifact stats — leggie-diagnostics-and-tooling)
 → defect/hypothesis entry WITH AN ID (D-numbering; permanent handle)
 → experiment behind a flag / opt-in (precedent: VS off-by-default, reranker config-selected — REMEDIATION Phase 5)
 → measured delta (smoke numbers or gold-set eval, before/after)
 → EITHER adopt via leggie-change-control + audit doc
   OR documented retirement (descoping-table pattern: decision + rationale + reopen condition, tasks/todo.md §0)
```

Rejected ideas are never silently deleted — they get a rationale and a reopen
condition (see the learned-router entry: rejected for chicken/egg, reopens
when the gold set can evaluate routing).

## 5. First-principles analysis recipes (the proof toolkit)

### 5.1 Log-signature frequency analysis
Count before theorizing. Recipe: capture run log → `smoke_log_stats.py` →
rank signatures by count → the dominant signature names the mechanism class.
*Worked example:* 134 × `Field required` vs a handful of truncation errors
pointed at schema drift as primary (D1), truncation as secondary (D2) — and
the fix order followed the counts.

### 5.2 Single-mechanism sufficiency test
List every observation; for each candidate mechanism, mark explains/doesn't.
Accept only a full row. (§1 worked example.)

### 5.3 Layer localization
Every defect gets assigned an architecture layer BEFORE fixing —
REMEDIATION_PLAN's inventory has a Layer column for every D-item. Forces the
fix to land in the right place (D1/D2 = Infrastructure; D3 = Application) and
keeps Domain frozen.

### 5.4 Cost accounting from first principles
tokens × price/1M per tier (routes.yaml comments: flash-lite $0.10/1M in,
flash $0.30/1M, pro $1.25/1M input). Example arithmetic for one lens over a
90-article bill: 90 calls × (~2k in + ~2k out) tokens on flash ≈ 0.18M in +
0.18M out — order of $0.1–0.3 depending on output pricing; ×5 lenses stays
comfortably under the $5 cap unless cascade escalates to pro. Run this
arithmetic BEFORE proposing any "just use the premium model" fix.

### 5.5 Determinism audit
Know what is pinned vs not: `LEGGIE_SEED=42` pins local sampling/order;
parser and citation extraction are fully deterministic; LLM outputs are NOT
reproducible across provider model updates (OpenRouter models drift). Any
"it changed between runs" investigation must first split
deterministic-layer vs LLM-layer variance — re-run `leggie parse` (must be
identical) before blaming the pipeline.

### 5.6 Ablation discipline
One variable per run. Levers that exist today: `--lenses <one>` (single
lens), route model swap (one route), max_tokens (one route),
`use_blackboard` flag (flow constructor). Two changed variables = zero
attributable conclusions; the smoke budget is wasted.

## 6. Where good ideas historically came from (keep these channels open)

- **Live smoke logs**, not test suites — D1/D2 were invisible to 326 green tests.
- **Honest audits** — H-1/H-2 found in an approved phase.
- **Cost pressure** — cascade design, descoping decisions (tasks/todo.md §0).
- **Eval-first thinking** — moving evaluation to Phase 0 is the single
  decision that makes every future quality claim possible.
- **Reading the actual output** — a human reading `analysis_report.md` is
  what exposed the stub MVP.

## When NOT to use this skill

- Executing the known campaign → **leggie-remediation-campaign**
- Choosing WHAT to research → **leggie-research-frontier**
- Triage of a known symptom → **leggie-debugging-playbook**
- Measurement mechanics → **leggie-diagnostics-and-tooling**

## Provenance and maintenance

Dated 2026-07-10. Re-verify:
- FIX_PLAN diagnosis table: `head -40 docs/FIX_PLAN.md`
- Pre-registered thresholds: `grep -n "Definition of done" -A10 docs/REMEDIATION_PLAN.md`
- Descoping table: `head -30 tasks/todo.md`
- Route prices: `head -15 config/routes.yaml`
- Audit precedent: `head -30 implementation_audit_report.md`
