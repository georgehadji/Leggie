---
name: leggie-research-frontier
description: >
  Open problems where Leggie could advance the state of the art, plus external
  positioning (what is genuinely novel vs known, what must be proven before
  any public claim, reproducibility standards). Load when planning
  next-generation work, evaluating a research idea, or preparing papers,
  releases, or public claims about this project. Everything here is OPEN or
  CANDIDATE — no result exists yet.
---

# Leggie Research Frontier

**Prerequisite:** finish the remediation campaign first
(**leggie-remediation-campaign**). Research on top of an unvalidated pipeline
produces unattributable numbers.

**Honesty baseline (verified 2026-07-10):** the only existing eval output
(a local, gitignored `eval_results.json`) is all zeros — a stub-era fossil.
Leggie has **no measured quality result yet**. Every item below states what a
result WOULD look like.

## Frontier problems

### F1. Beat the expert panel (the founding ambition)
- **Why current SOTA fails:** general LLM legal analysis is unbenchmarked for
  Greek legislative review; no public gold standard exists for
  Επιστημονική Υπηρεσία-grade bill analysis.
- **Leggie's asset:** eval-FIRST design — a working harness
  (`infrastructure/persistence/eval_harness.py`) with per-finding-type
  matching and RDI, plus an IRAC-grounded label schema already defined.
- **First three steps in this repo:**
  1. Expand `tests/eval/gold_set_sample.json` (currently 2 bills × 3 labels)
     to ≥5 real bills labeled from actual Επιστημονική Υπηρεσία reports
     (schema: **leggie-validation-and-qa** §6).
  2. Run `leggie analyze` on each, then `leggie eval` — record the first real
     precision/recall/F1/RDI table.
  3. Estimate inter-expert agreement on 1–2 bills (two independent labelers)
     to establish the human baseline F1.
- **You have a result when:** held-out-bill F1 ≥ human inter-expert agreement,
  at < $5/bill, reproduced twice.

### F2. Hallucination-proof legal citation verification
- **Why SOTA fails:** legal AI dies on fabricated citations; most systems
  verify nothing.
- **Asset:** deterministic ΦΕΚ/CELEX/ECLI parser with fail-closed semantics
  already wired into CoVe; D7 (empty resolution index) is the only gap.
- **Steps:**
  1. Ship a static `data/citation_index.json` of known-valid ΦΕΚ/CELEX ids and
     pass it to `GreekCitationParser(resolution_index=...)` in
     `infrastructure/container.py` (REMEDIATION Phase 4 spec).
  2. Add an online resolver adapter behind `CitationParserPort.resolve`
     (et.gr / EUR-Lex CELLAR SPARQL — endpoints named in README Data Sources).
  3. Add gold labels with deliberately-invalid citations; measure the drop rate.
- **You have a result when:** on a citation-labeled gold set, ≥95% of valid
  citations positively resolve AND 0 fabricated citations survive the
  pipeline.

### F3. Cost–quality frontier of model cascading
- **Why SOTA fails:** cascade routing is usually hand-tuned folklore;
  published evidence on cost-vs-quality Pareto for multi-stage legal pipelines
  is thin.
- **Asset:** static YAML router with per-task routes + budget guard already
  produce a controllable cost knob; a learned router was REJECTED (chicken/egg
  — no eval data), which is exactly the reopen condition.
- **Steps:** 1. after F1 exists, grid a few route configs (flash-only,
  current, pro-heavy) on the gold set; 2. record (cost, F1) pairs per config;
  3. check telemetry surface in `infrastructure/router/` for per-route
  attribution (verify what the tracker records before relying on it).
- **You have a result when:** a routing policy Pareto-dominates the current
  YAML (higher F1 at ≤ cost, or equal F1 at materially lower cost) on held-out
  bills. This is also the documented reopen condition for the learned-router
  decision (**leggie-failure-archaeology** §13).

### F4. Verbalized Sampling for finding recall (D4 wired 2026-07-12, delta unmeasured)
- **Why SOTA fails:** single-pass extraction misses low-salience issues;
  naive multi-sampling explodes cost.
- **Asset:** VS is fully wired: route `verbalized_sampling`, `VSResponse`
  schema, `services/verbalized_sampling.py` + `lens_vs.py`, CLI
  `--verbalized-sampling` / `LEGGIE_ANALYSIS__USE_VERBALIZED_SAMPLING`.
  What's missing is the MEASUREMENT, not the wiring.
- **Steps:** 1. run gold-set eval VS-off vs VS-on (k=3–5), one variable;
  2. compute recall delta and cost delta; 3. adopt or retire per
  **leggie-research-methodology** idea lifecycle.
- **You have a result when:** VS-on shows a recall gain at acceptable cost on
  the gold set, or is retired with the measured negative documented.

### F5. Greek legal retrieval (mostly greenfield)
- **Status:** `RetrievalPort` + `RetrievalSettings`
  (greek_legal_bert_v2, RRF hybrid params, CELLAR concurrency) exist but
  retrieval is effectively unwired — settings unconsumed by the pipeline
  (verify: `grep -rn "retrieval\." leggie --include="*.py" | grep -v config`).
- **First steps:** wire a minimal corpus (Σύνταγμα text embed-once per README
  Data Sources) → give the constitutional lens real rule-retrieval → measure
  precision delta on constitutional findings.

## Deferred vision items (fenced — reopen conditions in leggie-failure-archaeology §13)

Multi-round debate; knowledge graph; continuous learning; 25 personas.
Do not restart these without the recorded reopen evidence.

## External positioning (before ANY public claim)

**What is genuinely novel vs known — be honest:**
- CoVe is published prior art (Chain-of-Verification, Dhuliawala et al., 2023
  [general knowledge]); adversarial critics and typed verification gates are
  established patterns. Do NOT claim these as inventions.
- Plausibly novel [candidate, unproven]: the composed pipeline
  (deterministic citation parsing + factored CoVe + typed skeptic gates) for
  LEGISLATIVE analysis; the RDI metric framing (invention-vs-omission bias)
  for legal findings; an eval-first Greek legislative benchmark, if the gold
  set is built and released.
- **Nothing may be claimed until F1 produces real numbers.**

**Reproducibility standard for any external claim:**
- [ ] pinned commit + clean tree (no uncommitted diff)
- [ ] gold set versioned and published
- [ ] seed recorded (`LEGGIE_SEED`, default 42) — AND state plainly that LLM
  outputs are provider-nondeterministic; seed pins only local sampling/order
- [ ] model ids + routes.yaml snapshot recorded — OpenRouter models drift/
  retire, which UNDERMINES long-term reproducibility; treat this as an open
  problem and record model version metadata per run
- [ ] cost per run reported
- [ ] event log retained (replayable audit trail is a differentiator)
- [ ] two consecutive runs within stated variance

## When NOT to use this skill

- The current fix campaign → **leggie-remediation-campaign** (do it first)
- HOW to run an experiment properly → **leggie-research-methodology**
- Adding gold labels mechanics → **leggie-validation-and-qa**

## Provenance and maintenance

Dated 2026-07-10. Re-verify:
- Eval still stub? `python .claude/skills/leggie-diagnostics-and-tooling/scripts/eval_summary.py eval_results.json`
- VS still unwired? `grep -rn "use_verbalized_sampling" leggie/ | grep -v "def \|#"`
- Retrieval unconsumed? `grep -rn "retrieval\." leggie --include="*.py" | grep -v config`
- Gold set size: `python -c "import json;d=json.load(open('tests/eval/gold_set_sample.json',encoding='utf-8'));print(sum(map(len,d.values())),'labels in',len(d),'bills')"`
- Citation index still empty? `grep -n "GreekCitationParser(" leggie/infrastructure/container.py`
