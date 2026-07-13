---
name: leggie-run-and-operate
description: >
  Operating manual for the Leggie CLI: command anatomy for parse/analyze/eval,
  which commands cost money, how to run a cheap smoke first, what artifacts
  land in Outputs/ and their shapes, checkpoint usage, and the canonical live
  smoke procedure. Load when running or operating Leggie, choosing flags,
  locating output files, estimating cost, or interpreting a findings JSON.
---

# Running and Operating Leggie

Entry point: `leggie` (installed by `pip install -e .`, per pyproject
`[project.scripts]`). Everything runs from repo root
`E:\Documents\Vibe-Coding\Leggie`. Greek output is UTF-8; the CLI forces UTF-8
console streams on Windows itself.

## 1. Commands (verified 2026-07-10)

### Free / deterministic — safe to run anytime

```powershell
leggie --version                        # Leggie v0.1.0
leggie parse Inputs/OE_ΣΧΝ-ΥΠΔΙΚ.pdf    # parse bill → JSON to stdout, NO LLM, $0
leggie parse <file> -o parsed.json      # also write JSON to file
```

Parse accepts PDF/DOCX/HTML/TXT. Use parse FIRST to sanity-check article
extraction (article count, no phantom ids) before spending money.

### Costs money — requires `LEGGIE_LLM__OPENROUTER_API_KEY` in `.env`

```powershell
# CHEAP smoke: single lens
leggie analyze Inputs/OE_ΣΧΝ-ΥΠΔΙΚ.pdf --lenses constitutional

# Full pipeline: all 5 lenses (cost driver = 5 lenses × N articles)
leggie analyze Inputs/OE_ΣΧΝ-ΥΠΔΙΚ.pdf

# With options
leggie analyze <file> -o MyOutputs --lenses constitutional eu_gdpr --checkpoint budget.ckpt
```

Valid lens names (exact): `constitutional`, `legal_coherence`, `economic`,
`implementation`, `eu_gdpr`.

Cost/safety rails: hard cap `$5.00` per run (`LEGGIE_BUDGET__MAX_COST_PER_RUN`
— the governor); token ceiling 20M (safety net only). When budget trips, the
pipeline degrades per `degrade_strategy` (default `fewer_paths`) and emits
BUDGET_TRIPPED/DEGRADED events. Without an API key, `LLMAdapter` raises
`LLMConfigurationError` at construction.

`--checkpoint PATH`: persists BUDGET SPEND across a crash (restores on start,
saves per stage). It does NOT resume completed stages — a re-run re-executes
and re-bills everything (open defect D10).

### Evaluation (free — scores existing findings vs gold labels)

```powershell
leggie eval --gold-set tests/eval/gold_set_sample.json
leggie eval -g tests/eval/gold_set_sample.json -r my_results.json
# Prints per bill: gold labels, findings, matched, precision, recall, F1, RDI
# Default results file: eval_results.json (gitignored)
```

## 2. Artifacts — what lands where

`analyze` auto-saves to `Outputs/` (or `--output` dir), named by input stem:

| File | Content |
|---|---|
| `<stem>_findings.json` | list of surviving findings: `id, type, severity, confidence, lens, issue, rule, conclusion, evidence[]` |
| `<stem>_executive_summary.md` | rendered executive report |
| `<stem>_article_by_article.md` | per-article report |

`Outputs/` is **gitignored** — never commit run artifacts. Existing files
there (e.g. `OE_ΣΧΝ-ΥΠΔΙΚ_findings.json`) are historical exhibits.

Findings JSON shape (from `bill_analysis_flow.py` auto-save, verified):

```json
[{"id": "...", "type": "constitutional", "severity": "high",
  "confidence": 0.72, "lens": "constitutional",
  "issue": "...", "rule": "...", "conclusion": "...",
  "evidence": ["verbatim excerpt", "..."]}]
```

Field semantics (legal meaning): **greek-legal-domain-reference**.

Other locations: SQLite state at `leggie.db` (`LEGGIE_DB__URL`); logs go to
console (structlog) — capture with `2>&1 | tee run.log` (Git Bash) /
`2>&1 | Tee-Object run.log` (PowerShell).

## 3. The canonical live smoke (class-A change validation)

1. `leggie parse Inputs/OE_ΣΧΝ-ΥΠΔΙΚ.pdf -o parsed.json` — record the article
   count; sanity-check ids (no phantom cross-reference articles).
2. Single-lens run, log captured:
   `leggie analyze Inputs/OE_ΣΧΝ-ΥΠΔΙΚ.pdf --lenses constitutional 2>&1 | tee smoke.log`
3. Measure — do NOT eyeball (**leggie-diagnostics-and-tooling** scripts):
   findings count ∝ article count (not ~1); parse-failure signatures <5% of
   LLM calls; skeptic shows non-neutral verdicts; CoVe drops only
   fabricated-quote findings; spend < $5.
4. Full run only after single-lens passes.

Healthy-vs-sick anchor: the historical pathological run had 134 schema-drift
errors and ONE survivor; the stub-era run had 299 findings of which 202 were
INFO filler. If you see either pattern → **leggie-debugging-playbook** rows 1/3.

## 4. Operational hygiene

- Run `parse` before any paid run on a new bill file.
- Keep run logs; the diagnostics scripts consume them.
- `eval_results.json` and `Outputs/` are gitignored — leave them out of commits.
- Re-runs are NOT idempotent in cost (no stage resume); use `--checkpoint` to
  at least carry spend accounting across a crash on the same file.
- Ingest cap: files > 50MB rejected (`LEGGIE_INGEST__MAX_FILE_SIZE_MB`).

## When NOT to use this skill

- Install/venv/encoding problems → **leggie-build-and-env**
- Run produced wrong/missing output → **leggie-debugging-playbook**
- Setting/flag catalog → **leggie-config-and-flags**
- Judging whether results are "good" → **leggie-validation-and-qa** (+ diagnostics scripts)
- Landing the current uncommitted work → **leggie-remediation-campaign**

## Provenance and maintenance

Dated 2026-07-10. Re-verify:
- Commands/flags: `leggie --help && leggie analyze --help && leggie eval --help && leggie parse --help`
- Lens names: `grep -A7 "_DEFAULT_LENSES" leggie/application/agents/orchestrator.py`
- Output naming: `grep -n "_findings.json\|_executive_summary\|_article_by_article" leggie/application/workflow/bill_analysis_flow.py`
- Budget defaults: `python -c "from leggie.config.settings import get_settings; print(get_settings().budget)"`
- Gitignore: `grep -n "Outputs\|eval_results" .gitignore`
