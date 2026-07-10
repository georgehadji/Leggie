---
name: leggie-remediation-campaign
description: >
  EXECUTABLE, decision-gated campaign for Leggie's hardest live problem as of
  2026-07-10: a large uncommitted, unvalidated working-tree diff (LLM-powered
  CoVe + skeptic adversarial gate + wiring) that has never been proven by live
  smoke, on top of open defects D3–D10. Load when asked to continue, validate,
  or land the remediation work, to fix pipeline yield, or when asking "what
  should I work on next" in this repo. Every phase has exact commands,
  expected numbers, and if-X-then-Y branches.
---

# Leggie Remediation Campaign

**Goal (falsifiable):** the working-tree verification work is committed, and a
live smoke on `Inputs/OE_ΣΧΝ-ΥΠΔΙΚ.pdf` meets every REMEDIATION_PLAN §10
threshold. Until both are true, the campaign is not done.

**Re-verify Phase 0 before trusting anything here — this skill goes stale
fastest.** All numbers below measured 2026-07-10 on branch
`fix/model-ids-vfm-and-plan`.

## Phase 0 — Snapshot and offline baseline

```powershell
git status --short          # EXPECT: ~17 modified files + docs/REMEDIATION_PLAN.md untracked
git diff --stat HEAD        # EXPECT: ~1,089 insertions; biggest: cove_verifier.py (+412), skeptic.py (+107), bill_analysis_flow.py (+93)
python -m pytest tests/ -q  # EXPECT: 361 passed (measured 2026-07-10)
mypy leggie/ --ignore-missing-imports   # EXPECT: clean
ruff check leggie/ tests/   # EXPECT: clean
lint-imports                # EXPECT: contract kept
```

**Gate:** all green → Phase 1.
**Branch:** any test/type failure → fix via **leggie-debugging-playbook** /
**build-error-resolver** discipline BEFORE touching anything else. If
`git status` shows a DIFFERENT shape than above, the campaign state has moved
— re-map with Phase 1 before acting.

## Phase 1 — Diff-vs-plan audit (know what you're landing)

Mapping established 2026-07-10 (re-verify with `git diff HEAD -- <file>`):

| Working-tree change | Implements | Verified how |
|---|---|---|
| `cove_verifier.py` +412 | REAL 4-step factored CoVe with LLM (HEAD version was heuristic-only: quote validation + citation gate, no LLM calls) | `git show HEAD:leggie/application/services/cove_verifier.py` has no LLM port usage |
| `skeptic.py` +107 | `LLMAdversarialGate` via `adversarial_critic` route (absent at HEAD: grep count 0) | `git show HEAD:...skeptic.py \| grep -c LLMAdversarialGate` → 0 |
| `bill_analysis_flow.py` +93 | article_index for factored CoVe answers; blackboard default; auto-save polish | diff hunks |
| `interfaces/cli/__init__.py` +21 | `--output/-o` on analyze; UTF-8 console guard | diff hunks |
| `citation/__init__.py` +8 | fail-closed `resolve()` semantics (unverified ≠ invalid) | diff hunks |
| `settings.py` +8 | token ceiling 20M default + governor comment | diff hunks |
| `structured_output.py` +49 | CoVe response schemas (Questions/Answer/CrossCheck) | file read |
| test files +~290 | coverage for all of the above | pytest green |
| `economic_lens.py`, `blackboard_aggregator.py`, `cli_commands.py`, `cli_handlers.py` | supporting wiring | diff hunks |

**Gate:** every hunk accounted for. Unexplained hunks → read them before
proceeding; do not land code you can't map to intent.

NOT in this diff (still open): D3 parallel fan-out (loop still sequential at
`bill_analysis_flow.py:157`), D4 verbalized sampling, D5 model reranker,
D7 citation index population, D10 stage resume.

## Phase 2 — Close the Phase-1 audit HIGH findings

From `implementation_audit_report.md`:

- **H-1** "Attempt 3 (truncation retry) skipped on LLMError" — **already fixed
  in working tree**: `infrastructure/llm/__init__.py` initializes
  `response: LLMResponse | None = None` before attempt 1 and guards attempt 3
  with `if response and response.finish_reason == "length"`. Confirm with:
  `grep -n "Initialise response" -A3 leggie/infrastructure/llm/__init__.py`.
- **H-2** "repair round burns budget for unrepairable content" — **partial**:
  empty-content guard exists (`if content_to_repair:`); non-empty garbage
  (e.g. pure error prose) still costs one paid call. Acceptable residual risk
  (bounded to 1 call) — EITHER add a cheap looks-like-JSON heuristic
  (`any(c in content_to_repair for c in "{[")`) with a unit test, OR document
  acceptance in the landing audit doc. Do not over-engineer.

**Gate:** H-1 verified fixed + H-2 fixed-or-documented, with tests green.

## Phase 3 — Offline validation sweep

Re-run the full Phase-0 command block. **EXPECT:** ≥361 passed (more if you
added H-2 tests), mypy/ruff/lint-imports clean.
**Branch:** ruff failure → fix the code, NEVER extend the pyproject ignore
list (fenced, see §Fences).

## Phase 4 — Live smoke (costs money; needs `LEGGIE_LLM__OPENROUTER_API_KEY`)

### 4a. Free parse sanity

```powershell
leggie parse Inputs/OE_ΣΧΝ-ΥΠΔΙΚ.pdf -o parsed.json
```

Record the article count N. **EXPECT:** double-digit N with plausible
sequential ids. Historical anchors: stub-era parser hallucinated 214
"articles"; the drift-incident run analyzed ~90.
**Branch:** phantom ids (552, 622Γ) or mid-word titles → parser regression →
**leggie-debugging-playbook** row 2. STOP before spending money.

### 4b. Single-lens smoke

```powershell
leggie analyze Inputs/OE_ΣΧΝ-ΥΠΔΙΚ.pdf --lenses constitutional 2>&1 | Tee-Object smoke.log
python .claude/skills/leggie-diagnostics-and-tooling/scripts/smoke_log_stats.py smoke.log
python .claude/skills/leggie-diagnostics-and-tooling/scripts/findings_stats.py "Outputs/OE_ΣΧΝ-ΥΠΔΙΚ_findings.json" --articles <N>
```

**EXPECT (per REMEDIATION_PLAN §10):**
- survivors for one lens: same order of magnitude as N×(hit rate), NOT 0–2.
  The checked-in fossil shows `total_findings: 1` — beating that is the whole
  point.
- `parse_failure_signals`: drift+truncation < 5% of LLM calls
- `skeptic` signatures: no mass `skeptic_llm_error`; log shows non-neutral
  verdicts (grep `refutes`/`supports`)
- `cove_quote_fail` present only where quotes are genuinely absent from
  article text
- spend well under $5 for one lens

**Branches:**
- 0–2 survivors again → schema-drift ladder failing → count signatures; if
  `Field required` dominates, extend `_IRAC_ALIASES` with the NEW observed
  alias (Solution menu #1); if truncation dominates, raise `lens_analysis`
  max_tokens (menu #2).
- flood of `info` severity → filler regression → playbook row 3.
- `skeptic_llm_error` everywhere → check `adversarial_critic` route model
  reachable; verdict-parse errors → symptom 1 territory.
- budget block → stale 500k `.env` value (playbook row 6).

### 4c. Full run (all 5 lenses) — only after 4b passes

Same commands without `--lenses`. **EXPECT:** ~5× the single-lens call volume,
all §10 thresholds, spend < $5.

## Phase 5 — Promotion (through change control, never around it)

1. Re-run Phase-3 sweep one final time.
2. Write the landing audit doc (template: **leggie-docs-and-writing** §3),
   including the measured smoke numbers as the before/after table.
3. Commit in project style, e.g.
   `feat(phase-verify): LLM-powered CoVe factored loop + skeptic adversarial gate`
   — reference D-items and FIX_PLAN F4 lineage.
4. Update README drift while touching docs (test badge, ports count) — list in
   **leggie-docs-and-writing** §5.

## Solution menu for "yield still low" (ranked; each has a theory obligation)

| # | Option | Try when | Must show FIRST (theory obligation) |
|---|---|---|---|
| 1 | extend `_IRAC_ALIASES` | `Field required` errors name a specific new alias | the alias string appears ≥k times in smoke.log |
| 2 | raise route max_tokens | `finish_reason=length`/`Unterminated string` dominate | truncation count from smoke_log_stats |
| 3 | tighten lens prompt (field names in-prompt) | drift persists across aliases | json_schema mode being rejected (`json_schema rejected` count) |
| 4 | swap route model | one model consistently 400s or drifts | per-model failure attribution from log |
| 5 | improve repair round | ladder exhausts with near-valid JSON | samples of attempt-4 inputs that a human can fix |

One variable per run (ablation discipline — **leggie-research-methodology**).

## Fenced wrong paths (do NOT)

- Do NOT touch Domain models (`Finding`, `IRAC`, `Confidence`) to make parsing easier.
- Do NOT widen the pyproject ruff ignore list.
- Do NOT raise `max_cost_per_run` above $5 to make a run finish.
- Do NOT judge output "looks better" — numbers only.
- Do NOT add methods to existing ports.
- Do NOT re-propose learned router / debate / knowledge graph here (settled
  descoping — **leggie-failure-archaeology** §13).
- Do NOT treat "unverified" citations as resolved to green up CoVe.

## When NOT to use this skill

- General operation → **leggie-run-and-operate**
- A novel unexplained failure → **leggie-debugging-playbook**, then
  **leggie-research-methodology** for new-mechanism work
- Post-campaign ambitions → **leggie-research-frontier**

## Provenance and maintenance

Everything here dated 2026-07-10; the working tree WILL move.
- Re-snapshot: `git status --short && git diff --stat HEAD | tail -3`
- Diff-map spot check: `git show HEAD:leggie/application/agents/skeptic.py | grep -c LLMAdversarialGate` (0 = mapping still valid; ≥1 = work landed, retire Phases 1–2)
- Baseline: `python -m pytest tests/ -q`
- D3 still open? `grep -n "for article in self._doc.articles" leggie/application/workflow/bill_analysis_flow.py`
- H-1 fix present? `grep -n "Initialise response" leggie/infrastructure/llm/__init__.py`
