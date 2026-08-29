# CI Outage — GitHub Actions stopped executing jobs (2026-07-15 →)

> Status: **ROOT-CAUSED, NOT RESOLVED.** The remaining fix is an account-level
> billing/Actions action only the repository owner can take (§5). Everything
> fixable from a branch has been fixed (§6).
> Investigated 2026-08-29.

## 1. Symptom

`test (3.12)` reports **failure** on every branch, including `master`. The
GitHub UI shows a red X with no log output.

## 2. The jobs never ran

The failure is not a test failure. The job is created and then dies before any
step executes:

| Signal | Value | What it means |
|---|---|---|
| `runner_id` / `runner_name` | `0` / `""` | No runner was ever assigned |
| Step records | none | `actions/checkout@v4` never started |
| Check-run `output.title/summary/text` | all `""` | Nothing ever reported a result |
| Log download | HTTP 404 | No logs exist to download |
| Run `conclusion` | `failure` (**not** `startup_failure`) | Workflow file parsed fine |

## 3. Evidence: run durations split cleanly in two

`ci.yml` run history (`run_number`, duration, result):

| Run | Created | Duration | Result | Branch |
|---|---|---|---|---|
| #52 | 2026-07-15 12:28 | **45s** | success | `master` |
| #53 | 2026-07-17 23:28 | **5s** | failure | `claude/bold-nightingale-be6980` |
| #54 | 2026-07-19 18:16 | **8s** | failure | `master` |
| #55 | 2026-08-29 14:20 | **4s** | failure | `claude/leggie-concierge-mvp-lglq0k` |
| #55 (re-run) | 2026-08-29 14:23 | **3s** | failure | same commit |

Runs #26–#52 (2026-07-12 → 07-15) took **23–57s** and produced real
pass/fail results. Every run since — three, across three different branches,
spanning six weeks — died in **under 10 seconds**. The boundary is between
run #52 and run #53.

## 4. Ruled out, with evidence

| Hypothesis | Verdict | Evidence |
|---|---|---|
| A code regression on master | **No** | `master` @ `83fd14b` passes all five CI gates locally: ruff clean, mypy clean (91 files), `lint-imports` contract kept, bandit clean, **539 passed / 1 skipped, coverage 81.98%** |
| A bad workflow file | **No** | `.github/workflows/ci.yml` unchanged since `02c3ac6` (Phase 0), long before the boundary. YAML parses. Conclusion is `failure`, not `startup_failure` |
| A flake | **No** | Re-run of the same commit failed identically in 3s; reproduces on three branches over six weeks |
| Restricted-actions policy blocking `actions/checkout` | **No** | That produces a startup failure with output; here all check-run output is empty |
| Repo disabled or archived | **No** | API reports `"disabled": false`, `"archived": false` |
| This repo exhausting its own minutes | **Unlikely alone** | ~55 runs at ~40s ≈ **under an hour** of billed time, against 2,000 free private-repo minutes/month |

## 5. Root cause and the remaining fix (owner-only)

`georgehadji/Leggie` is a **private repository on a personal account**
(`"private": true`, owner type `User`). Private-repo Actions minutes are
billed. The signature — run created, no runner assigned, instant failure, no
logs, every branch — is what GitHub produces when **Actions is blocked at the
account level**.

One detail narrows it further: **the outage survived the monthly reset.**
Included minutes reset on the 1st of each month, so a July quota exhaustion
would have cleared by the 2026-08-29 run. It did not. That points away from a
one-off monthly overage and toward a persistent block:

- a spending limit of **$0** while over the included allowance, or
- a **failed payment / unpaid invoice** holding Actions, or
- a **plan change or lapse** on the account.

The exact reason is visible only to the account owner, and cannot be read
through the API or fixed from a branch.

**Owner action:** check <https://github.com/settings/billing> →
*Plans and usage* / *Spending limit*, and the Actions usage panel. Resolve any
payment hold, or raise the spending limit above $0. As an alternative that
removes the cost entirely: making the repository **public** gives unlimited
GitHub-hosted Actions minutes. A self-hosted runner would also bypass billed
minutes.

Confirm recovery by re-running the failed job and checking that the duration
returns to the 40–60s range. **Anything under ~10s means it still is not
running.**

## 6. Fixed here (branch-level)

Neither item resolves the outage — that needs §5 — but both address what the
outage exposed.

### 6.1 `scripts/run_gates.py` — the gates, runnable locally

The gate sequence existed only inside `ci.yml`. When Actions went dark there
was no single command that reproduced it, and **three commits reached master
with no CI verification at all**: `3c94ec3`, `8d74f0b`, `83fd14b`. (All three
were checked retroactively during this investigation — see §4, master is
healthy.)

`python scripts/run_gates.py` now runs the same five gates, in CI order, on
Windows and Linux. It streams each gate's output, prints a PASS/FAIL summary,
and exits non-zero if any gate fails — verified in both directions, including
a planted `F841` violation and a failing test to confirm it actually detects
failure rather than always reporting green.

Keep it in lockstep with `ci.yml`: a gate changed in one must change in the
other, in the same commit.

### 6.2 `ci.yml` — lower minute burn, gates unchanged

- **`concurrency`** — supersedes in-flight runs for the same ref, so a rapid
  series of pushes bills one run instead of one per push. Runs #26–#35 were
  nine runs on one branch inside 55 minutes, most of them already superseded;
  that is the waste this removes. `master` is exempt (`cancel-in-progress` is
  false on `refs/heads/master`), so the default branch never loses a result.
- **`cache: pip`** on `actions/setup-python` — the `pip install -e ".[dev,lint]"`
  step dominates a ~45s run.

No gate was removed, reordered, or weakened; the coverage floor stays at 80%.

### 6.3 `.pre-commit-config.yaml` — the gates run automatically

With CI dark, hooks are the only thing that runs the gates without someone
remembering to. The previous config ran ruff, ruff-format, mypy and bandit —
but **not pytest and not import-linter**, the two gates CI was the only safety
net for. Both are now wired, on `pre-push` so they do not tax every commit:

| Stage | Gates | Cost |
|---|---|---|
| `pre-commit` | ruff autofix, ruff, mypy, bandit | sub-second to ~1s |
| `pre-push` | + import-linter, pytest + 80% coverage floor | ~10s |

Two deliberate choices:

- **Every hook shells out to `scripts/run_gates.py`**, so the hooks, the script
  and `ci.yml` share one definition of each gate and cannot drift. The hooks
  also use `language: system` — the project's own installed tools, not
  pre-commit's separately-pinned copies — which removes both version drift and
  the network dependency. (The old mypy hook ran in an isolated environment with
  only pydantic installed, so it type-checked against a different dependency set
  than CI did.)
- **`ruff format` is deliberately not a hook.** `ci.yml` has no format step, so
  formatting was never a gate, and the tree is ~85 files away from
  ruff-format-clean. Enabling it rewrites nearly every file. Worth doing — in
  its own commit, not silently attached to every future one.

Verified in both directions: with a planted `F841` and a failing test, the
pre-commit stage fails on `gate: ruff` and the pre-push stage fails on both
`gate: ruff` and `gate: pytest`, each exiting 1. Hooks that cannot block a bad
push are decoration.

Install with `pre-commit install` — `default_install_hook_types` wires both
stages in one command.

## 7. Standing risk

Until §5 is resolved, **no branch can be verified by CI**. A red X on a PR
carries no information, and a green tick is simply unavailable — neither can be
used as merge evidence.

The local gates are the substitute, and they are a real one: they run the same
five checks in the same order. What they do **not** replicate is CI's clean-room
guarantee — a fresh checkout on a fresh runner with a fresh dependency install.
A local pass can hide a missing dependency declaration, an uncommitted file, or
a machine-specific assumption. Two cheap habits cover most of that gap:

- run `git status` before trusting a green local run — an uncommitted file is
  the classic false pass;
- occasionally verify from a clean clone into a fresh virtualenv, which is what
  `pip install -e ".[dev,lint]"` on a bare checkout gives you.

State the local result explicitly in the PR, as
`docs/PLAN_VERIFICATION_CHAIN_ORDER.md` §4 does — say what was run and what it
returned, rather than pointing at a check mark.
