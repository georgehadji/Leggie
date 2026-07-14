# Deliberative Multi-Model Pipeline Plan (PR #3)
**Date:** 2026-07-14  **Branch:** `claude/multi-model-thinking-pipeline-q62bur` → master  **Author:** merge integration

Class-A architectural addition. Introduces a **new Port**
(`leggie/application/ports/reasoner.py`) → trips the ports ASK guardrail
(`guardrails.yaml` ask_paths `leggie/application/ports/`, change-control §3.3:
"no new methods on existing ports"). This doc justifies the exception: the rule
forbids *new methods on existing ports*; a **new, standalone port + adapter**
for a genuinely new capability is the sanctioned pattern (precedent: budget
guard / retry / cache are decorators, but a new external subsystem gets its own
port). Does **not** touch Domain (`Finding`/`IRAC`/`Confidence` untouched;
reuses existing `Citation`).

## 0. Current state
- Master (post-#1) has no deliberative/reasoner pipeline. `run()` is the single
  analysis path.

## 1. What this adds
| # | Addition | Layer | Notes |
|---|----------|-------|-------|
| R1 | `ReasonerPort` + `ReasonerRequest`/`ReasonerResult`/`ReasonerUnavailableError` | Application (ports) | NEW port — ASK guardrail |
| R2 | `ReasonerAdapter` (HTTP client) | Infrastructure | implements R1 |
| R3 | `ReasonerServerManager` (health-check + auto-start lifecycle) | Infrastructure | **external service dependency** |
| R4 | `DeliberativeFlow` (two-stage orchestration + prose report) | Application (workflow) | new flow alongside `BillAnalysisFlow` |
| R5 | `deliberative_stage1/2` prompts + `DeliberativePromptRenderer` | Application | new |
| R6 | `ReasonerSettings` | Config | additive; does not touch `max_cost_per_run` |
| R7 | `ReasonerPort` binding | Infrastructure (container) | DI wiring |
| R8 | `--pipeline`/`--perspective` CLI flags via CQRS | Interfaces | additive |

## 2. Blockers to resolve before merge (measured)
- **CI red:** `ruff check` reports **224 errors** on the branch
  (`gh run 29211625248`: unused imports, I001 import sort, F841). Must reach 0
  without widening the frozen ignore list (§3.5).
- **Draft status:** PR is a draft; undraft only after gates pass.
- **Conflicts:** touches `bill_analysis_flow.py`, `cli_handlers.py`,
  `container.py`, `cli/__init__.py` — conflicts with #1 and #2. Resolution
  principle: keep #1/#2 versions canonical, add R-items additively.
- **External dependency (open risk):** `ReasonerServerManager` auto-starts and
  health-checks an external Reasoner HTTP service. This is a runtime dependency
  master has never carried. Behavior when the service is absent must degrade
  cleanly (§3.6 no silent failure) and must not break the default `analyze`
  path. Confirm the default pipeline remains the non-deliberative one.

## 3. Invariants to hold
- Dependency rule (import-linter): Infra→App→Domain; the new port sits in
  Application, adapter/server_manager in Infrastructure. ✓ target
- No silent failure: `ReasonerUnavailableError` surfaces; degrade to standard
  flow with a logged warning.
- Structured output: reasoner responses parsed into `ReasonerResult`.

## 4. Gates (all must pass before merge)
- `ruff check leggie/ tests/` → 0 (fix the 224, do not ignore).
- `pytest tests/ -q` → green incl. the ~10 new deliberative test files.
- `mypy leggie/ --ignore-missing-imports` → clean on touched modules.
- `lint-imports` → layer contract holds with the new port/adapter.
- Class-A live smoke ONLY if the deliberative path is wired into a billable
  default; if `--pipeline deliberative` is strictly opt-in and off by default,
  the standard smoke from #1 still governs the default path.

## 5. Recommendation
Higher risk than #2: larger, CI-red, draft, and adds an external service
dependency. Merge only after §2 blockers clear and §4 gates are green. If the
external Reasoner service cannot be provisioned/verified in this environment,
keep the deliberative path strictly opt-in and merge with the dependency
documented, or defer to a dedicated session.
