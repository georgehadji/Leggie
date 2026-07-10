---
name: leggie-debugging-playbook
description: >
  Symptom→triage playbook for Leggie run failures. Load when an analyze run
  misbehaves: findings missing or near-zero, parse errors in logs, phantom or
  truncated articles, all-INFO noise, skeptic verdicts all neutral, CoVe
  dropping everything, budget blocks, cascade churn, citations stuck
  "unverified", or Greek text mojibake on Windows. Gives the discriminating
  experiment for each symptom and where the evidence lives.
---

# Leggie Debugging Playbook

Triage order (always): **reproduce cheaply → read log signatures → count them
→ localize the layer → run ONE discriminating experiment → only then edit.**
Counting beats reading: one `pydantic` error is noise; 134 of them is the
mechanism (that's how D1 was found).

Cheap reproduction: `leggie parse <file>` is free/deterministic;
`leggie analyze <file> --lenses constitutional` runs one lens instead of five.

## Symptom → triage table

| # | Symptom | Likely cause | Discriminating experiment | Fix pointer |
|---|---|---|---|---|
| 1 | Run "succeeds" but 0–2 findings survive from many articles | schema drift or truncation eating findings (historical D1/D2) | count log signatures: `Field required` (drift), `Unterminated string` / `Expecting value` (truncation), `finish_reason=length` | retry ladder + aliases: **llm-structured-output-reference**; extend `_IRAC_ALIASES` with newly observed alias |
| 2 | Nonsense article numbers (552, 622Γ) or titles cut mid-word | parser matching in-body cross-references to other laws (historical, FIX_PLAN D2) | `leggie parse <file> -o parsed.json` → inspect article ids vs the actual bill TOC | `leggie/infrastructure/parse/` ARTICLE_PATTERN; settled fix in commit 703651e — check for regression |
| 3 | Flood of INFO findings "Δεν εντοπίστηκαν..." (nothing found) | baseline-filler suppression regressed (historical FIX_PLAN D3) | `python .claude/skills/leggie-diagnostics-and-tooling/scripts/findings_stats.py Outputs/<stem>_findings.json` — severity histogram dominated by info | lens filler suppression (commits 703651e, f7c2ed8) |
| 4 | Skeptic verdicts ALL neutral, nothing ever refuted | (a) no LLM configured → heuristic gates only, they never refute by design; (b) verdict parse blocked (historical: schema drift blocked `SkepticVerdictResponse`) | grep run log for `skeptic_llm_error` and `adversarial`; confirm API key set | `agents/skeptic.py`; if parse errors → symptom 1 |
| 5 | CoVe drops many findings | distinguish CORRECT drops (fabricated quotes: `cove_quote_fail` — working as designed, live-proven) from truncation-induced false drops | count `cove_quote_fail` vs parse-error signatures in same run | only investigate if drops correlate with parse errors, not quote failures |
| 6 | Run blocked early with budget error | token ceiling misconfigured (historical: 500k ceiling governed instead of $5 cap) | check effective settings: `python -c "from leggie.config.settings import get_settings; s=get_settings(); print(s.budget.max_tokens_per_run, s.budget.max_cost_per_run)"` — expect `20000000 5.0`; beware stale `.env` copied from `.env.example` (shows 500000) | `BudgetSettings`; **leggie-config-and-flags** |
| 7 | `LLMConfigurationError: Unknown model ID` at startup | model not in offline allowlist (guard against the fake-model-ID incident) | check `LEGGIE_LLM__OPENROUTER_DEFAULT_MODEL` and routes.yaml against `_OFFLINE_MODEL_ALLOWLIST` in `infrastructure/llm/__init__.py` | use a real OpenRouter ID; verify: `curl -s https://openrouter.ai/api/v1/models \| grep '<id>'` |
| 8 | Log shows `json_schema rejected, falling back to json_object` repeatedly | model on that route doesn't support strict json_schema (expected for some models; fallback is permanent by design) | acceptable if parse then succeeds; problem only if attempt 2 also fails | route the task to a json_schema-capable model (gemini flash/pro support it) |
| 9 | Citations all "unverified" | resolution index is EMPTY — by design fail-closed (D7 open): unverified ≠ invalid | `grep -n "resolution_index" leggie/infrastructure/container.py` — no index passed | populate index (REMEDIATION Phase 4) — do NOT "fix" by treating unverified as resolved |
| 10 | Greek text prints as `Î...` mojibake / UnicodeEncodeError on Windows console | legacy console codepage | CLI already forces UTF-8 (`_force_utf8_console` in `interfaces/cli/__init__.py`); for your own scripts add `sys.stdout.reconfigure(encoding="utf-8")` or run `chcp 65001`; set `PYTHONUTF8=1` | **leggie-build-and-env** |
| 11 | One article failing kills the whole batch | `asyncio.TaskGroup` sibling cancellation (D6) | check whether article-level fan-out wraps failures (`orchestrator.py analyze_document`); note flow currently loops sequentially (D3) so this bites only after parallelization | REMEDIATION Phase 3a |
| 12 | Crash mid-run, restart re-bills completed stages | resume-from-stage not implemented (D10); only budget spend survives via `--checkpoint` | — | use `--checkpoint PATH` to at least preserve spend; full resume is open work |

## Where evidence lives

- **Run log**: structured logs (structlog) to console; capture with
  `leggie analyze ... 2>&1 | tee run.log` (Git Bash) or
  `leggie analyze ... 2>&1 | Tee-Object run.log` (PowerShell).
- **Outputs/**: `<stem>_findings.json` (machine-readable survivors),
  `<stem>_executive_summary.md`, `<stem>_article_by_article.md`.
- **Event log**: `BillAnalysisFlow.get_event_log()` — event types incl.
  DEGRADED, DEDUP_REMOVED, FINDING_REFUTED, CITATION_FAILED, BUDGET_TRIPPED.
- **Historical exhibit**: `analysis_report.md` (root) — the pathological
  stub-era run, useful as a "what bad looks like" reference.
- **Signature counting**: `scripts/smoke_log_stats.py` in
  **leggie-diagnostics-and-tooling**.

## Key log signatures (exact strings to grep)

```
Field required                      # pydantic schema drift
Unterminated string                 # truncated JSON
Expecting value: line 1 col 1       # empty/garbage LLM content
json_schema rejected                # 400 fallback to json_object
finish_reason=length | Response truncated   # truncation retry path
cove_quote_fail                     # fabricated quote dropped (GOOD)
skeptic_llm_error                   # critic call failed → neutral
skeptic_route_failed                # router failed for adversarial_critic
Failed to parse structured response # ladder exhausted → degrade
```

## When NOT to use this skill

- Understanding WHY the ladder/cascade exists → **llm-structured-output-reference**
- Whether the symptom was already investigated and settled → **leggie-failure-archaeology**
- Systematic validation of the current uncommitted work → **leggie-remediation-campaign**
- Building measurements → **leggie-diagnostics-and-tooling**
- Env/install/encoding setup → **leggie-build-and-env**

## Provenance and maintenance

Dated 2026-07-10. Re-verify:
- Signatures still emitted: `grep -rn "cove_quote_fail\|skeptic_llm_error\|json_schema rejected" leggie/`
- D3 sequential loop: `grep -n "for article in self._doc.articles" leggie/application/workflow/bill_analysis_flow.py`
- Budget defaults: `python -c "from leggie.config.settings import get_settings; s=get_settings(); print(s.budget)"`
- UTF-8 guard: `grep -n "_force_utf8_console" leggie/interfaces/cli/__init__.py`
