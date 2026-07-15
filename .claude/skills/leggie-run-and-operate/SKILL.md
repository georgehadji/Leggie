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

## 1. Commands (verified 2026-07-14)

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

# Restrict scope to selected articles (big cost saver on large bills)
leggie analyze <file> --articles "1-5,7,10" --lenses constitutional
```

### Stage 0 preview (cheap LLM call — one overview pass, not per-article lens analysis)

```powershell
leggie preview Inputs/OE_ΣΧΝ-ΥΠΔΙΚ.pdf -o preview.json
# Prints JSON: intro, summary, per-article {purpose, provisions, consequences}
# Then use the article ids to scope a paid run: leggie analyze <file> --articles <ids>
```

### Deliberative pipeline (opt-in, requires a running/startable Reasoner backend)

```powershell
# Prereqs in .env: LEGGIE_REASONER__ENABLED=true, LEGGIE_REASONER__HOME=<Reasoner repo>,
# LEGGIE_REASONER__API_KEY=<ADMIN_API_KEY>  (backend default http://localhost:8003)
leggie analyze <file> --pipeline deliberative --perspective neutral

# Auto-fall back to the deterministic 5-lens pipeline if Reasoner is unreachable
leggie analyze <file> --pipeline deliberative --fallback
```

Two Reasoner stages (Stage 1 structured critique per perspective, Stage 2
adversarial audit), producing a PROSE Markdown report — no Finding objects,
no Skeptic/CoVe pass. Output: `Outputs/<stem>_deliberative.md` with a
citation appendix listing all citations as UNVERIFIED. Pre-flight budget
check estimates ~3× bill tokens against `LEGGIE_BUDGET__MAX_TOKENS_PER_RUN`
and aborts before any Reasoner call if exceeded. If autostart spawned the
backend, the handler shuts it down after the run (PR #7 leak fix).

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
| `<stem>_deliberative.md` | prose report (deliberative pipeline only): Περίληψη, Stage 1 critique, Stage 2 audit, unverified-citations appendix |

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

Dated 2026-07-14. Re-verify:
- Commands/flags: `leggie --help && leggie analyze --help && leggie eval --help && leggie parse --help && leggie preview --help`
- Deliberative report path/appendix: `grep -n "_deliberative.md\|citation_appendix" leggie/application/workflow/deliberative_flow.py`
- Lens names: `grep -A7 "_DEFAULT_LENSES" leggie/application/agents/orchestrator.py`
- Output naming: `grep -n "_findings.json\|_executive_summary\|_article_by_article" leggie/application/workflow/bill_analysis_flow.py`
- Budget defaults: `python -c "from leggie.config.settings import get_settings; print(get_settings().budget)"`
- Gitignore: `grep -n "Outputs\|eval_results" .gitignore`
