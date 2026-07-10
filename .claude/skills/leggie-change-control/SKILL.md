---
name: leggie-change-control
description: >
  Load BEFORE committing, merging, or classifying any change to the Leggie repo
  (E:\Documents\Vibe-Coding\Leggie). Defines how changes are classified by layer
  and risk, which gates each class must pass (tests, mypy, ruff, import-linter,
  live smoke), the project's non-negotiables with their historical incidents,
  and the plan→implement→audit workflow. Also load when deciding whether a
  change needs a plan doc or an audit doc, or when tempted to relax a lint
  ignore, budget cap, or acceptance threshold.
---

# Leggie Change Control

How changes are classified, gated, and promoted in this repo. A "change" is any
edit to `leggie/`, `tests/`, `config/`, or `pyproject.toml`.

## 1. Classify the change first

Two axes: **layer** and **risk**.

### Layer (Clean Architecture — enforced by import-linter)

| Layer | Path | Dependency rule |
|---|---|---|
| Interfaces | `leggie/interfaces/` | may import everything below |
| Infrastructure | `leggie/infrastructure/` | implements Application ports; imports Application + Domain |
| Application | `leggie/application/` | imports Domain only |
| Domain | `leggie/domain/` | imports nothing outward — pure frozen Pydantic models + pure functions |
| Config | `leggie/config/` + `config/routes.yaml` | leaf; static data + pydantic-settings |

The contract is declared in `pyproject.toml` under `[tool.importlinter]`
(layers: interfaces → infrastructure → application → domain → config).

### Risk class

| Class | Examples | Gates required (see §2) |
|---|---|---|
| **A — Pipeline-behavior-changing** | LLM adapter, parser, lens logic, skeptic/CoVe, reranker, routes.yaml model/tokens, budget defaults | ALL gates incl. live smoke |
| **B — Wiring/refactor** | DI container, CQRS handlers, CLI flags, decorators | offline gates (tests, mypy, ruff, import-linter) |
| **C — Docs/tests-only** | docs/, README, new tests | tests still green |

When unsure, treat as class A. The costliest incident in this repo's history
was misclassifying "the pipeline works" as proven by unit tests alone (§3.1).

## 2. Gates (run all that apply, in this order)

```powershell
# From repo root E:\Documents\Vibe-Coding\Leggie
python -m pytest tests/ -q            # full suite; baseline 361 passed (measured 2026-07-10)
mypy leggie/ --ignore-missing-imports # strict mode is set in pyproject; must be clean on touched modules
ruff check leggie/ tests/             # do NOT widen the ignore list to pass (see §3.5)
lint-imports                          # import-linter layer contract (install via pip install -e ".[lint]")
```

Class A additionally requires a **live smoke** (costs money, needs
`LEGGIE_LLM__OPENROUTER_API_KEY`) judged against the measurable thresholds in
`docs/REMEDIATION_PLAN.md` §10 — findings roughly proportional to article
count, <5% parse-failure rate, non-neutral skeptic verdicts present, CoVe drops
only invalid quotes, cost < $5. Procedure: see sibling skill
**leggie-run-and-operate**; measurement: **leggie-diagnostics-and-tooling**.
Never judge smoke output "by eye" — numbers only.

Pre-commit hooks exist (`.pre-commit-config.yaml`: ruff --fix, ruff-format,
mypy --strict, bandit). CI (`.github/workflows/ci.yml`) runs ruff + mypy +
pytest on ubuntu only — CI does NOT run import-linter, coverage gate, or live
smoke; those are your job locally.

## 3. Non-negotiables (each with its incident)

1. **Green tests ≠ working product.** The MVP shipped with 199 green tests and
   was a fake: no LLM was ever called; lenses were regex→canned-string stubs;
   the eval scored empty findings lists (diagnosis: `docs/FIX_PLAN.md` Part 1,
   defects D1–D7). Hence: class-A changes require live-smoke numbers, not just
   green tests.
2. **Domain models are frozen during fixes.** `docs/REMEDIATION_PLAN.md` §9:
   `Finding`, `IRAC`, `Confidence` are not modified while remediating
   infrastructure defects. Domain changes are their own class-A change with a
   plan doc.
3. **No new methods on existing ports.** New behavior rides on new adapters or
   decorators (`REMEDIATION_PLAN` §9 "Ports unchanged"). Precedent: budget
   guard and retry/cache are decorators around `LLMPort`, not port methods.
4. **The $5 cost cap is the governor — never raise it to make a run pass.**
   Incident: the 500k token ceiling used to block runs long before money ran
   out; the fix was raising the TOKEN ceiling to 20M so the COST cap governs
   (`leggie/config/settings.py` `BudgetSettings` comment). Changing
   `max_cost_per_run` is a class-A change requiring explicit justification.
5. **The ruff ignore list is frozen debt.** `pyproject.toml` ignores
   (E501, F821, ARG001/2, F401, I001, B904, B017, B027, E741, SIM102) are
   documented as "pre-existing codebase issues, out of scope". Never add to
   this list to silence a new failure — fix the code.
6. **No silent failure.** Degradation paths must emit events
   (`EventType.DEGRADED`) or log warnings. Precedent: parse repairs are logged;
   skeptic LLM errors log `skeptic_llm_error` and return neutral rather than
   crash (`leggie/application/agents/skeptic.py:119-122`).
7. **Structured output only.** Every LLM response validates against a Pydantic
   schema in `leggie/domain/models/structured_output.py` (FIX_PLAN rule G).
8. **Plans before code; audits after.** Every major change traces to a
   `docs/*_PLAN.md`; significant phases get an audit report (pattern:
   `implementation_audit_report.md`, verdict + severity findings). Templates:
   sibling **leggie-docs-and-writing**.

## 4. Commit conventions (verified from git log)

Conventional-commit style with optional phase scope:

```
feat(phase1): structured-output reliability (D1+D2)
fix: repair LLM pipeline (real model ids + tolerant parser) and add VFM routing
config: ignore pre-existing ruff issues (E501, F821, ARG001, ARG002)
```

Types seen in history: `feat`, `fix`, `config`, plus plan-tagged messages
(`FIX_PLAN F1: ...`). Reference the defect/plan IDs (D1, F3, G4) — they are
permanent handles.

## 5. Change workflow checklist

- [ ] Classify: layer + risk class (§1)
- [ ] Class A/B: does a plan doc cover it? If not, write one (leggie-docs-and-writing)
- [ ] Implement within layer rules (leggie-architecture-contract)
- [ ] Run offline gates (§2)
- [ ] Class A: live smoke with measured numbers vs REMEDIATION_PLAN §10
- [ ] Significant phase: audit doc with compliance matrix + severity findings
- [ ] Commit with conventional message referencing plan/defect IDs
- [ ] Update docs of record if counts/claims drifted (README test badge is a known drifter)

## When NOT to use this skill

- Diagnosing a broken run → **leggie-debugging-playbook**
- "Why is the code shaped like this" → **leggie-architecture-contract**
- "Was this approach already tried and rejected" → **leggie-failure-archaeology**
- Executing the current remediation work → **leggie-remediation-campaign**
- What counts as test evidence → **leggie-validation-and-qa**

## Provenance and maintenance

Facts dated 2026-07-10. Re-verify before trusting:

- Test baseline: `python -m pytest tests/ -q` (was: 361 passed)
- Ruff ignore list: `grep -A2 "^ignore" pyproject.toml`
- Import contract: `grep -A8 importlinter pyproject.toml`
- Budget defaults: `grep -n "max_tokens_per_run\|max_cost_per_run" leggie/config/settings.py`
- CI gates: `cat .github/workflows/ci.yml`
- Commit style: `git log --oneline -15`
