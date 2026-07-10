---
name: leggie-diagnostics-and-tooling
description: >
  How to MEASURE Leggie run quality instead of eyeballing it. Ships three
  executable scripts (findings_stats.py, smoke_log_stats.py, eval_summary.py)
  with interpretation guides and healthy thresholds. Load before claiming any
  change improved anything, when counting error signatures in a run log,
  interpreting eval metrics (precision/recall/F1/RDI), or capturing evidence
  for a live smoke gate.
---

# Leggie Diagnostics and Tooling

Doctrine: this project shipped a fake MVP because nobody measured output
quality, and later lost 99% of findings to a bug only signature-COUNTING
revealed. Therefore: **before/after numbers are required for any pipeline
change; nothing is ever judged by eye.**

## 1. The scripts (in this skill's `scripts/` dir; all UTF-8-safe on Windows)

### findings_stats.py — summarize a findings JSON

```powershell
python .claude/skills/leggie-diagnostics-and-tooling/scripts/findings_stats.py "Outputs/OE_ΣΧΝ-ΥΠΔΙΚ_findings.json" --articles 90
```

Real output against the checked-in artifact (measured 2026-07-10):

```
total_findings: 1
by_type: constitutional=1
by_severity: medium=1
by_lens: constitutional=1
confidence: n=1 min=0.55 mean=0.55 max=0.55
info_filler_ratio: 0%  (historical pathology: 68%)
```

That `total_findings: 1` IS the D1 schema-drift pathology, preserved in the
repo — the checked-in `Outputs/OE_ΣΧΝ-ΥΠΔΙΚ_findings.json` predates the
Phase-1 fix. A healthy post-fix run must show findings roughly proportional
to article count.

**Interpretation:** `info_filler_ratio` near 68% = stub-era filler regression
(playbook row 3). `findings_per_article` near zero = drift/truncation
(playbook row 1). Confidence all-identical = suspicious (no calibration
spread — historically meant stub output).

### smoke_log_stats.py — count failure signatures in a run log

```powershell
leggie analyze Inputs/OE_ΣΧΝ-ΥΠΔΙΚ.pdf --lenses constitutional 2>&1 | Tee-Object smoke.log
python .claude/skills/leggie-diagnostics-and-tooling/scripts/smoke_log_stats.py smoke.log
```

Counts: `Field required` (schema drift), `Unterminated string` /
`Expecting value` (truncation), `json_schema rejected` (400 fallback),
`Response truncated` (retry engaged), `Failed to parse structured response`
(ladder exhausted), `cove_quote_fail` (correct drop), `skeptic_llm_error`,
`skeptic_route_failed`, `budget`, `DEGRADED`. Prints a triage table with
playbook row pointers, plus a combined `parse_failure_signals` line to test
against the <5% threshold.

Note (verified): running it on `analysis_report.md` finds ZERO signatures —
that file is a rendered REPORT, not a log. Signature counting needs the
captured console log, not the markdown outputs.

### eval_summary.py — print eval metrics

```powershell
python .claude/skills/leggie-diagnostics-and-tooling/scripts/eval_summary.py eval_results.json
```

Real output against the checked-in file (measured 2026-07-10):

```
bill_id                 gold  found  match    prec     rec      F1     RDI
bill_sample_001            3      0      0   0.000   0.000   0.000  -1.000
bill_sample_002            3      0      0   0.000   0.000   0.000  -1.000
```

The checked-in `eval_results.json` is the STUB-ERA artifact (eval once scored
empty findings lists — FIX_PLAN D7). All-zero rows with RDI −1.0 mean "no
findings were fed in", not "the analyzer is that bad". Regenerate with
`leggie eval --gold-set tests/eval/gold_set_sample.json`.

## 2. Metric interpretation

| Metric | Meaning | Healthy |
|---|---|---|
| precision | matched findings / all findings | higher = fewer inventions |
| recall | matched findings / gold labels | higher = fewer omissions |
| F1 | harmonic mean | headline number vs expert baseline |
| **RDI** (Risk Direction Index) | bias direction: **>0 invention bias** (asserts things not in gold), **<0 omission bias** (misses real issues), 0 balanced (`eval_harness.py:65`) | near 0; for legal analysis, omission (<0) is safer than invention (>0) — a fabricated legal claim is worse than a missed one |

Live-smoke thresholds (REMEDIATION_PLAN §10, the acceptance gate):
findings ∝ article count (not ~1); parse-failure rate <5% of LLM calls;
skeptic produces some non-neutral verdicts; CoVe drop/revise observed only on
invalid quotes; full-run wall-clock materially cut once fan-out lands; spend
< $5.

## 3. Other measurement surfaces

- **Event log**: `BillAnalysisFlow.get_event_log()` — count
  FINDING_CREATED vs FINDING_REFUTED vs DEDUP_REMOVED vs DEGRADED for a
  stage-by-stage survival funnel.
- **Eval harness**: `leggie/infrastructure/persistence/eval_harness.py`
  (GoldLabel/GoldSet/EvalResult; matching + RDI computation).
- **pytest-benchmark**: dependency installed, `.benchmarks/` exists —
  no benchmarks in active use as of 2026-07-10 (candidate, not established).
- **Coverage**: `python -m pytest tests/ --cov=leggie --cov-report=term-missing`
  (pyproject `fail_under = 80`).
- `.reasonix/` and `graphify-out/` are external-tool state, gitignored;
  not diagnostic surfaces.

## 4. Capture discipline

Always capture the log when a run's quality matters:
Git Bash `leggie analyze ... 2>&1 | tee smoke.log`;
PowerShell `leggie analyze ... 2>&1 | Tee-Object smoke.log`.
Record: date, git SHA + dirty-state, article count from `parse`, script
outputs. That tuple is the "before" for any improvement claim.

## When NOT to use this skill

- Deciding WHICH experiment to run → **leggie-debugging-playbook** (triage) or **leggie-research-methodology** (hypothesis design)
- What evidence a change class requires → **leggie-validation-and-qa**, **leggie-change-control**
- Running the CLI itself → **leggie-run-and-operate**

## Provenance and maintenance

Dated 2026-07-10. Re-verify:
- Scripts still run: `python .claude/skills/leggie-diagnostics-and-tooling/scripts/findings_stats.py "Outputs/OE_ΣΧΝ-ΥΠΔΙΚ_findings.json"`
- Log signatures still emitted by code: `grep -rn "cove_quote_fail\|skeptic_llm_error\|Response truncated\|json_schema rejected" leggie/`
- RDI definition: `grep -n "risk_direction_index" leggie/infrastructure/persistence/eval_harness.py`
- Findings JSON field names: `grep -n "findings_data.append" -A12 leggie/application/workflow/bill_analysis_flow.py`
