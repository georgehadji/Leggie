---
name: leggie-remediation-campaign
description: >
  EXECUTABLE, decision-gated campaign for Leggie's hardest live problem as of
  2026-07-28: a 5-lens live smoke DOES now pass every REMEDIATION_PLAN §10
  gate, but only on a 10-article subset (replicated across full5_v3/v4 plus
  full5_v5 under the D21 fix, docs/SMOKE_AUDIT_V3.md). The 91-article run the
  gates were written against has never completed, so full-bill yield is still
  unproven, and the deliberative pipeline has no recorded live run. Load when
  asked to continue, validate, or prove the remediation work, to fix pipeline
  yield, or when asking "what should I work on next" in this repo. Every phase
  has exact commands, expected numbers, and if-X-then-Y branches.
---

# Leggie Remediation Campaign

**Goal (falsifiable):** a FULL 5-lens live smoke on
`Inputs/OE_ΣΧΝ-ΥΠΔΙΚ.pdf` meets every REMEDIATION_PLAN §10 threshold, and
the result is recorded in a landing audit doc. Until both are true, the
campaign is not done.

**Campaign state as of 2026-07-28** (evidence: `docs/SMOKE_AUDIT_V3.md` and
the committed logs under `docs/evidence/v3/`; the 2026-07-14 rows below are
from `docs/SMOKE_AUDIT.md`, commit 02c3ac6, master):

| Milestone | Status |
|---|---|
| Verification layer landed (cb7fde8/406f969) | DONE |
| Phase 0 fixes: parallel fan-out (D3/D6), lens_analysis route wired (was DEAD — orchestrator queried `lens_<name>`), Skeptic/CoVe token ceilings raised, critic → gemini-2.5-pro, citation index loaded (D7) | DONE (02c3ac6, 05aaa8f) |
| Single-lens smoke | **PASSED** — v5: 299 LLM calls, 4.0% parse failures (<5% gate), 9 refutes/9 supports/1 neutral skeptic verdicts, findings_per_article 0.14 |
| 5-lens smoke, 10-article subset | **PASSED** — every §10 gate met, replicated across `full5_v3` and `full5_v4` (2026-07-28), then `full5_v5` under the D21 fix. Parse failures 12–14.5% on main → 2.1%; truncation eliminated. First runs with *proven worktree execution* (D20: everything before `full5_v3` silently executed main-checkout code) |
| Full 5-lens smoke, all 91 articles | **NOT DONE** — the §10 gates were written against the full bill, so the subset pass above does not discharge them. Earlier attempts stopped on a stale route (fixed), an OpenRouter 402 credit wall (account credits, not the budget guard), and parse-failure/truncation degradation (189 parse failures, 111 skeptic_llm_error in `full5_final`, whose log was lost to `.gitignore:41`). Now blocked on credit, not on code — forecast ~$5.01 against the fenced $5.00 cap, which is never raised to make a run pass |
| Landing audit doc through change control | NOT DONE (`SMOKE_AUDIT_V3.md` is the interim record; it self-declares Phase E **PARTIAL**) |
| Deliberative pipeline live run | NOT DONE (landed + leak-fixed PR #7; offline-proven only) |

**Next concrete step:** the subset validation this line used to ask for is
done and replicated — go straight to the 91-article run (Phase 4c). Check the
OpenRouter credit balance BEFORE starting: the 402 wall wasted a full run, and
the forecast now sits just over the $5.00 cap, so top up the account rather
than raising the cap.

**Re-verify Phase 0 before trusting anything here — this skill goes stale
fastest.** Baseline numbers below measured 2026-07-10/11.

## Phase 0 — Snapshot and offline baseline

```powershell
git status --short          # EXPECT: clean-ish (skills/docs churn OK; no leggie/ changes)
git log --oneline -3        # EXPECT: HEAD at/after 5a20faa (PR #7 merge, 2026-07-14)
python -m pytest tests/ -q  # EXPECT: 531 passed (measured 2026-07-15)
mypy leggie/ --ignore-missing-imports   # EXPECT: clean
ruff check leggie/ tests/   # EXPECT: clean
lint-imports --debug --verbose  # EXPECT: contract kept (bare/--debug-only crashes — see leggie-change-control ARCH-02)
```

**Gate:** all green → Phase 2 (Phase 1 mapping below is now historical
record; skim it, don't redo it).
**Branch:** any test/type failure → fix via **leggie-debugging-playbook**
discipline BEFORE anything else. If the tree is DIRTY again, new work has
appeared — map it against the plan (Phase 1 method) before acting.

## Phase 1 — What landed (historical record of the diff-vs-plan mapping)

The formerly-uncommitted verification work was committed 2026-07-10 as
`cb7fde8` ("resolve all pre-existing type errors and add structured-output
reliability tests") and `406f969` ("defect-hunt V7 — 4 verified defects
fixed, 367 tests pass"). Content mapping (established while it was still a
working-tree diff):

| Change | Implements |
|---|---|
| `cove_verifier.py` (+~412) | REAL 4-step factored CoVe with LLM (previous version was heuristic-only: quote validation + citation gate, no LLM calls) |
| `skeptic.py` (+~107) | `LLMAdversarialGate` via `adversarial_critic` route (previously absent) |
| `bill_analysis_flow.py` (+~93) | article_index for factored CoVe answers; blackboard default; auto-save polish |
| `interfaces/cli/__init__.py` | `--output/-o` on analyze; UTF-8 console guard |
| `citation/__init__.py` | fail-closed `resolve()` semantics (unverified ≠ invalid) |
| `settings.py` | token ceiling 20M default + governor comment |
| `structured_output.py` (+~49) | CoVe response schemas (Questions/Answer/CrossCheck) |
| tests (+~290) | coverage for all of the above |
| budget_guard, container, llm/__init__, economic_lens, blackboard_aggregator, cli_commands/handlers | supporting wiring + defect-hunt fixes |

(Historical: at landing time D3/D4/D5/D7/D10 were still open and no live
smoke existed. Since then D3/D4/D6/D8 closed and single-lens smoke passed —
current ledger: **leggie-failure-archaeology** §Open defect ledger.)

## Phase 2 — Confirm the Phase-1 audit HIGH findings stayed closed

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

Re-run the full Phase-0 command block. **EXPECT:** ≥531 passed (more if you
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

**EXPECT (per REMEDIATION_PLAN §10, plus the project's $5 budget policy):**
- survivors for one lens: same order of magnitude as N×(hit rate), NOT 0–2.
  The local fossil `Outputs/OE_ΣΧΝ-ΥΠΔΙΚ_findings.json` (untracked, this
  machine only) shows `total_findings: 1` — beating that is the whole point.
- `parse_failure_signals`: drift+truncation < 5% of LLM calls (§10)
- `skeptic` signatures: no mass `skeptic_llm_error`; log shows non-neutral
  verdicts (grep `refutes`/`supports`) (§10)
- CoVe drop/revise observed with valid (non-truncated) inputs (§10 wording);
  `cove_quote_fail` should fire only where quotes are genuinely absent
- spend well under $5 for one lens ($5 cap = `max_cost_per_run` policy in
  settings.py, not a §10 bullet)

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
   including the measured smoke numbers as the before/after table — the code
   landed without this evidence; the audit doc closes that gap.
3. Commit the audit doc (+ any Phase-2/branch fixes) in project style, e.g.
   `docs: smoke-validation audit for verification layer (cb7fde8/406f969)` —
   reference D-items and FIX_PLAN F4 lineage.
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

Campaign state dated 2026-07-14 (smoke evidence 2026-07-11); the tree WILL move.
- Re-snapshot: `git status --short && git log --oneline -3`
- Work landed? `git show HEAD:leggie/application/agents/skeptic.py | grep -c LLMAdversarialGate` (≥1 = landed, Phase 1 is history; 0 = you are on an older commit)
- Baseline: `python -m pytest tests/ -q` (531 passed 2026-07-15)
- Smoke record: `head -40 docs/SMOKE_AUDIT.md`
- Parallel fan-out intact? `grep -n "analyze_document" leggie/application/workflow/bill_analysis_flow.py`
- H-1 fix present? `grep -n "Initialise response" leggie/infrastructure/llm/__init__.py`
