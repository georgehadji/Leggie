# Branch reconciliation — `claude/bold-nightingale-be6980`

**Status:** unmerged, 30 commits ahead of master. **Not at risk** — it is committed
work on a branch; git will not lose it. But it is invisible unless you know to look,
which is why this file exists.

Facts below were verified on 2026-08-06 against the repo, not inferred.

---

## The divergence

Two AI work-streams branched from a common ancestor and never reconciled:

```
3c94ec3 (2026-07-15, "docs: refresh skill library")
   │
   ├── master ......................... 5 commits + ~130 files that were uncommitted
   │                                     until 4f41602 landed them
   │
   └── claude/bold-nightingale-be6980 .. 30 commits, 40 files, +4229 lines
       worktree: .claude/worktrees/bold-nightingale-be6980
```

Verify with:

```bash
git log --oneline master..claude/bold-nightingale-be6980
```

---

## What is unique to the branch

### Fixes master does not have

| Fix | Commit | Verified absent from master by |
|---|---|---|
| Clamp out-of-range numeric DTO fields instead of rejecting (D18) | `f2552a3` | `grep -rn "clamp" leggie/` → no matches |
| Surface `reasoning_tokens` from OpenRouter usage | `ea47f16` | `grep -rn "reasoning_tokens" leggie/` → no matches |
| Make route resolution observable at INFO, not DEBUG | `851f6d4` | not checked in detail |

### Convergent — already solved on master, independently

`14a9fc6` "honour lens_analysis route max_tokens in lens path (D21)" is **already
present on master** via the token-optimization work, tagged `TOK-4`
(`leggie/application/agents/lens.py:106`). Do not port it; check for conflicts
instead.

### Empirical evidence that cannot be regenerated for free

`docs/evidence/v3/` — 19 files of real run output: `full5_v3`, `full5_v4`, `full5_v5`
with findings JSON, per-lens logs, and parse probes. Plus `docs/SMOKE_AUDIT_V3.md`,
`docs/PARSER_REMEDIATION_PLAN.md`, `docs/REMEDIATION_PLAN_V3.md`,
`docs/IMPROVEMENT_RESEARCH.md`.

**Read the verdict carefully — it is narrower than the filenames suggest.**
`SMOKE_AUDIT_V3.md` records Phase E as **PARTIAL**: the 5-lens gates pass on a
**10-article subset**, replicated across two independent runs (`full5_v3`,
`full5_v4`), and D21 was found in `full5_v4` and live-confirmed fixed in `full5_v5`.
The **full 91-article run is explicitly still OPEN**.

So the `leggie-remediation-campaign` skill's "full 5-lens smoke has never completed"
is *right about full scale* and *wrong about subset scale* — a replicated 10-article
5-lens pass does exist. Worth amending the skill to say that precisely, since
"never completed" understates what has been proven and invites redoing paid work.
These logs cost real API spend to produce; they are the expensive artifact here.

### Operational trap recorded on the branch (D20)

`SMOKE_AUDIT_V3.md` §0: every live run before `full5_v3` **executed main-checkout
code, not the worktree**. The `leggie` console script resolves through the
editable-install mapping to `E:\Documents\Vibe-Coding\Leggie\leggie`, and console
scripts do not put the invocation cwd on `sys.path` — so `cd`-ing into a worktree
changed nothing and the runs silently measured the wrong tree.

Anyone running a smoke from a worktree must verify which code is executing first
(e.g. `python -c "import leggie; print(leggie.__file__)"`). This invalidated an
entire earlier round of results.

---

## Conflict surface

Measured, not estimated — 40 branch files against master's committed + uncommitted set:

- **24 files branch-only** → zero conflict, can be taken wholesale. Almost all of it
  is `docs/` and `docs/evidence/`, plus `test_lens_route_max_tokens.py`,
  `test_cove_verifier.py`, `test_skeptic.py`, `domain/models/structured_output.py`.
- **16 files overlap** → real conflicts. These are the core pipeline:
  `lens.py`, `orchestrator.py`, `skeptic.py`, `bill_analysis_flow.py`, `container.py`,
  `llm/__init__.py`, `llm/adapters/openrouter.py`, `cove_verifier.py`, `lens_vs.py`,
  `cli_handlers.py`, `config/routes.yaml`, `tests/conftest.py`, `README.md`,
  `test_openrouter_adapter.py`, `test_phase1_structured_output.py`, `test_cli.py`.

Reproduce with `docs`-free plumbing:

```bash
git diff --name-only 3c94ec3...claude/bold-nightingale-be6980
```

---

## Recommended path — do NOT `git merge` this branch

A straight merge would conflict across the entire pipeline, and master is
substantially further along (parser decomposition, token optimization, manifest +
SQLite persistence, headless CLI contract, architecture enforcement). Master should
stay the trunk.

Take it in three passes, cheapest first:

**Pass 1 — evidence and docs (zero risk, do this first). DONE — commit `467e981`.**
Checkout the 24 branch-only doc/evidence files directly. No conflict is possible;
they are pure additions.

```bash
git checkout claude/bold-nightingale-be6980 -- docs/evidence/ docs/SMOKE_AUDIT_V3.md \
  docs/PARSER_REMEDIATION_PLAN.md docs/REMEDIATION_PLAN_V3.md docs/IMPROVEMENT_RESEARCH.md
```

Then correct the `leggie-remediation-campaign` skill's "never completed" claim
against `SMOKE_AUDIT_V3.md`.

**Pass 2 — the two missing fixes (small, surgical). DONE 2026-08-07.**
Port D18 clamping (`f2552a3`) and `reasoning_tokens` surfacing (`ea47f16`) by hand
onto current master. Both touch `openrouter.py` / `structured_output.py`, which have
moved considerably; cherry-pick will likely conflict, so read the diff and re-apply
the intent rather than forcing the patch. Bring
`tests/unit/application/test_lens_route_max_tokens.py` across as-is — master has no
equivalent coverage.

What actually happened, so the next reader does not re-derive it:

- **D18 applied cleanly, not by luck.** `structured_output.py` on master was
  byte-identical to the branch's pre-D18 state — the campaign never touched it. The
  commit went on verbatim.
- **`reasoning_tokens` needed re-application, not the patch.** Master's
  `openrouter.py` had moved on: it now returns `cached_tokens` and calls
  `estimate_cost`, and uses `tier_used=request.tier` rather than a hard-coded
  `ModelTier.BUDGET`. The intent (surface
  `usage.completion_tokens_details.reasoning_tokens` additively, only when non-zero)
  was re-applied onto that shape.
- **Half of `ea47f16` was dropped as obsolete.** Its other hunk edited a comment on
  the `structured_response_exhausted` diagnostic in `llm/__init__.py`. That
  diagnostic no longer exists on master (`grep -rn "exhausted" leggie/` → no
  matches); the campaign removed it. There was nothing to update.
- **`DEFAULT_LENS_MAX_TOKENS` was introduced** in `agents/lens.py` and imported by
  `orchestrator.py`. Master had solved D21 as TOK-4 but left the ceiling as a bare
  `4096` literal duplicated across both files — the exact drift that caused the
  original defect. The ported test asserts the two agree, which needs a shared name.
- **`VSCandidate`/`VSResponse` are dead code.** D18 also relaxes
  `VSCandidate.probability` from required to `default=0.5`. That widening has zero
  blast radius: neither class is imported anywhere in `leggie/` or `tests/` (the VS
  path uses `VSSample` and `LensFindings`). Kept for fidelity to the ported commit.

Gates at the time of the commit: ruff clean; `lint-imports --debug` 2 kept / 0 broken;
`pytest tests/unit` 710 passed; the 11 ported tests pass individually; `mypy
--follow-imports=silent` clean on all four changed files.

**Caveat, since resolved — `mypy leggie/` was NOT clean when Pass 2 landed.**
At `e122bb4` the CI invocation reported **17 errors across 7 files**, despite several
campaign commit messages claiming "mypy clean". Pass 2 added none of them (verified by
type-checking its four files in isolation), but the debt was real. It was cleared in
the immediately following commit — `mypy leggie/ --ignore-missing-imports` now reports
no issues.

Worth recording how the list was obtained, because the first two attempts were wrong:
piping mypy through `| tail -20` silently truncated the report, which made the error
count look like 5–7 and hid an entire seventh file (`infrastructure/resources.py`).
Redirect the full output to a file and read that — do not size this problem from a
tailed pipe.

**Pass 3 — decide the branch's fate. PARTIAL 2026-08-08.**
Once 1 and 2 are done, everything of value is on master. Either delete the branch or
tag it `archive/bold-nightingale` and remove the worktree. Do not leave it live and
unmerged; that is how it got overlooked for three weeks.

Done: `archive/bold-nightingale` → `7b492aa` (branch tip) and
`archive/stash-83fd14b` → the stash commit. Both are annotated tags, so every
commit stays permanently reachable no matter what happens to the branch ref, the
worktree, or the stash.

Still to do, and deliberately left for an explicit decision because each is
irreversible in shape: remove the worktree, delete the branch ref, drop the
stash, and delete `safety-snapshot-20260806`.

**`stash@{0}` was examined, and it was not empty.** It carried a parser fix that
existed nowhere else — not on master, not on the branch:

- `PARAGRAPH_PATTERN` was unanchored, so `(\d+)\.` matched inside dates and
  decimals. "(L της 16.4.2024)" split Άρθρο 1 at "4.", inventing a paragraph and
  discarding the article's real opening text.
- `extract_paragraphs` returned `[]` for prose-bodied articles — 24% of the
  reference bill — so they reached the lenses with nothing in them.
- `ParseDocumentHandler` capped paragraph text at 200 chars, making the
  `parse -o` sanity artifact unable to show content recovery.

Ported to master in `35418d7` with 9 regression tests. Its other half (a
`_DEFAULT_CRITIC_MAX_TOKENS` / router-driven skeptic budget) was already solved
independently as TOK-4 and was dropped.

The lesson generalises: "contents not yet examined" in a recovery document is an
open finding, not a footnote. Two passes of reconciliation ran past this one.

---

## Do not lose

- Branch tip: `7b492aa` on `claude/bold-nightingale-be6980`
- Worktree: `.claude/worktrees/bold-nightingale-be6980` (remove only after Pass 3)
- `stash@{0}` — from `83fd14b`. Examined 2026-08-08; its parser fix is on master
  (`35418d7`) and the stash commit is pinned by `archive/stash-83fd14b`.
- `safety-snapshot-20260806` — tag holding every file that was uncommitted before
  `e09321a` / `4f41602` landed. Safe to delete once those commits are confirmed good.
