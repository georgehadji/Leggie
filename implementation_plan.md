# Implementation Plan — Deliberative Multi-Model Pipeline (Reasoner Integration)

**Project:** Leggie — Greek Legal Bill Analyzer
**Branch:** `claude/multi-model-thinking-pipeline-q62bur`
**Author:** Engineering
**Status:** Draft for approval
**Date:** 2026-07-12

---

## 1. Executive Summary

### 1.1 Objective
Add a new, **selectable** analysis pipeline (`deliberative`) to Leggie that produces a two-stage, multi-model report for a Greek legislative bill:

1. **Stage 1 — Generation** (`Prompt01`): a structured report (introduction, summary, changes per Μέρος/Κεφάλαιο, purpose/provisions/consequences) plus a party-perspective evaluation.
2. **Stage 2 — Adversarial Audit** (`Prompt02`): consumes Stage 1's output and the full bill, and — thinking like an auditor — surfaces everything Stage 1 missed (ambiguities, contradictions, unconstitutionality, EU/ECHR conflicts, loopholes, Top-20 problems, Top-10 amendments, a 2-page executive briefing).

The multi-model "thinking + evaluate + synthesize" work is **delegated wholesale to the existing Reasoner service** via its HTTP Agent API (`POST /api/agent/run/sync`). Leggie contributes its deterministic strengths — Greek ingest/parse, domain prompt templating, two-stage orchestration, output persistence, and audit events.

### 1.2 Key Design Decisions (agreed)
| # | Decision | Rationale |
|---|----------|-----------|
| **A** | Reasoner is invoked over **HTTP**; Leggie does **not** re-implement ensembles/critique/synthesis. | Reasoner already implements the 8-phase, 20-method, 48-preset cross-lab engine with 800+ tests. Rebuilding it inside Leggie is waste. |
| **A′** | Leggie **auto-starts** the Reasoner backend when it is not already running. | Zero-friction UX. Only the FastAPI backend (`:8003`) is launched — not the Next.js UI or SearXNG. |
| **B** | Stage-2 output is persisted as a **prose Markdown report**, not mapped to structured `Finding` objects. | User choice. Keeps scope tight; the deterministic `analyze` pipeline (5 lenses + CoVe + eval) remains a separate path. |
| **C** | `deliberative` is a **selectable pipeline**, never the default. | Preserves Leggie's deterministic identity; avoids a hard runtime dependency on an external service for every run. |
| **D** | The party perspective is a **parameter** (`--perspective`, default `neutral`), not hardcoded. | Decouples the pipeline from any single political framing. |

### 1.3 Scope Boundary — What Leggie Keeps vs Delegates
- **Leggie (deterministic, unique):** Greek ingest, Άρθρο→παρ→εδάφιο parser, ΦΕΚ/CELEX/ECLI citation parser + verification, eval harness, event sourcing, budget guard, CLI.
- **Reasoner (delegated):** multi-model generation, independent critique/scoring, stress testing, synthesis with epistemic labels, cross-lab diversity, language-bias mitigation.

### 1.4 Out of Scope
- Mapping Reasoner prose into `Finding` objects and routing through Skeptic/CoVe/eval (deferred; see §9 optional appendix only).
- Any change to the existing deterministic `analyze` pipeline behavior.
- Deploying/packaging the Reasoner service itself.

---

## 2. Current Architecture Assessment

### 2.1 Overview
Leggie follows **Clean / Hexagonal Architecture** with import-linter–enforced layering:

```
interfaces → infrastructure → application → domain
(domain imports nothing outward; infrastructure implements application ports)
```

Enforced contract (`pyproject.toml`, `tool.import-linter`): `interfaces` may not import `infrastructure`/`domain` directly; `infrastructure` may not import `interfaces`. The CLI is thin and dispatches through the **CQRS mediator**.

### 2.2 Components Relevant to This Work
| Component | Location | Role | Relevance |
|-----------|----------|------|-----------|
| Ports | `leggie/application/ports/*.py` | 9 abstract interfaces (LLM, Router, Retrieval, State, EventBus, Blackboard, CitationParser, Ingest, Parse) | **Add `ReasonerPort`** here, mirroring `llm.py`'s frozen request/response dataclasses. |
| DI Container | `leggie/infrastructure/container.py` | Service-locator; lazy `register(port, factory)` | **Bind `ReasonerPort → ReasonerAdapter`** in composition root. |
| Settings | `leggie/config/settings.py` | pydantic-settings, 12-factor, per-domain sub-settings with `env_prefix` | **Add `ReasonerSettings`** sub-model. |
| Workflow | `leggie/application/workflow/bill_analysis_flow.py` | Orchestrates the deterministic pipeline; records events | **Add `DeliberativeFlow`** as a sibling. |
| CLI | `leggie/interfaces/cli/__init__.py` | argparse → CQRS mediator | **Add `--pipeline` / `--perspective`** to `analyze`. |
| Ingest/Parse adapters | `leggie/infrastructure/ingest_adapter.py`, `parse_adapter.py` | Deterministic text + Άρθρο tree | Reused unchanged by `DeliberativeFlow`. |
| Event sourcing | `Event`/`EventType` domain models; `_record_event` | Audit/replay spine | Reused for reproducibility of deliberative runs. |
| Budget guard | `leggie/infrastructure/budget_guard/` | Token/$ ceiling, graceful degradation | Extended to account for Reasoner cost estimates. |
| Observability | `leggie/infrastructure/observability` (structlog, trace IDs) | Structured logs | Reused; new spans for Reasoner calls. |

### 2.3 Integration Points (New)
- **Outbound HTTP** to Reasoner `POST /api/agent/run/sync` with `Authorization: Bearer <key>`.
- **Local process management** to launch `uvicorn asgi:app` from `REASONER_HOME`.

### 2.4 Technical-Debt / Constraints Observed
- CLI currently exposes no pipeline selection; adding one must not break existing `analyze` invocations.
- `httpx` is already a dependency (used by LLM providers) — no new HTTP client needed.
- Reasoner lives in a **separate repository** on the user's machine; Leggie cannot vendor or test it in CI. E2E must be manual/local.
- Reasoner runs are **non-deterministic and billable** — this conflicts with Leggie's deterministic positioning unless kept behind an explicit opt-in.

---

## 3. Detailed Implementation Plan

### 3.1 Phased Roadmap

| Phase | Milestone | Depends on | Exit Criteria |
|-------|-----------|------------|---------------|
| **P0 — Config & Port** | `ReasonerSettings`, `ReasonerPort`, DTOs | — | Settings load; port + dataclasses defined; unit tests green. |
| **P1 — Adapter** | `ReasonerAdapter` (HTTP client) | P0 | Adapter parses `synthesis`/`citations`/`models_used`/cost against a mocked endpoint. |
| **P2 — Server lifecycle** | `ReasonerServerManager` (health-check + auto-start) | P0 | Reuse-if-up / start-if-down / poll-until-healthy logic unit-tested with fakes. |
| **P3 — Flow** | `DeliberativeFlow` (2-stage orchestration + prose assembly + events) | P1, P2 | Flow runs end-to-end against a fake `ReasonerPort`; report saved; events recorded. |
| **P4 — Prompts & Templating** | `Prompt01`/`Prompt02` templates with `{perspective}` | — | Templates render; snapshot tests pass. |
| **P5 — CLI wiring** | `--pipeline deliberative`, `--perspective` via CQRS | P3, P4 | `leggie analyze bill.txt --pipeline deliberative` dispatches correctly; default path unchanged. |
| **P6 — Hardening** | Graceful fallback, budget accounting, optional citation appendix, docs | P5 | Fallback path verified; README/ARCHITECTURE updated; import-linter + mypy + ruff clean. |

Phases P1 and P2 are independent and may proceed in parallel after P0.

### 3.2 Work Breakdown (per deliverable unit)

---

#### WU-1 — `ReasonerPort` + DTOs
- **Objective:** Provider-agnostic abstraction for a single Reasoner run.
- **Affected components:** `leggie/application/ports/reasoner.py` (new).
- **Design changes:** Mirror `llm.py`. Frozen dataclasses:
  - `ReasonerRequest(problem: str, preset: str, top_k: int = 2, sequential: bool = False, no_cache: bool = False, web_search: bool = False, client_run_id: str | None = None)`
  - `ReasonerResult(synthesis: str, critical_insights: list[str], open_questions: list[str], citations: list[Citation], models_used: list[str], total_tokens: dict[str,int], duration_seconds: float, errors: list[str])`
  - `class ReasonerPort(ABC): async def reason(self, request: ReasonerRequest) -> ReasonerResult`
- **Implementation tasks:** define ABC + DTOs; export from `ports/__init__.py`.
- **Refactoring:** none.
- **Testing:** type/contract test that DTOs are frozen and importable from the application layer only.
- **Acceptance:** import-linter passes (port lives in `application`, no outward imports).
- **Rollback:** delete file; no external references yet.

---

#### WU-2 — `ReasonerAdapter` (HTTP)
- **Objective:** Implement `ReasonerPort` over Reasoner's Agent API.
- **Affected components:** `leggie/infrastructure/reasoner/adapter.py` (new).
- **Design changes:** `httpx.AsyncClient` with configurable `base_url`, `timeout`, Bearer auth. `POST /api/agent/run/sync` with the minimal JSON payload; map response → `ReasonerResult`. Retry with exponential backoff on transient (5xx/timeout) errors, bounded (e.g. 3 attempts). Raise a typed `ReasonerUnavailableError` on exhaustion.
- **Implementation tasks:** request builder; response parser (tolerant of missing optional keys); error taxonomy; structlog spans with trace ID + `models_used` + cost.
- **Refactoring:** none; reuse existing `httpx` dependency.
- **Testing:** unit tests with `respx`/mocked transport — happy path, empty `synthesis` (retry hint), 401 (auth), 503 (retry then fail), malformed JSON.
- **Acceptance:** deterministic parsing under all mocked responses; no secret logged.
- **Rollback:** unbind in container; adapter is inert if unreferenced.

---

#### WU-3 — `ReasonerServerManager` (auto-start)
- **Objective:** Ensure a healthy Reasoner backend before calls; auto-start if absent.
- **Affected components:** `leggie/infrastructure/reasoner/server_manager.py` (new).
- **Design changes:**
  - `async ensure_running()`: `GET {base_url}/openapi.json` → if 200, reuse. Else, if `autostart` enabled, spawn **backend only**: `python -m uvicorn asgi:app --port <port>` with `cwd=REASONER_HOME`, inheriting env (`OPENROUTER_API_KEY`, `ADMIN_API_KEY`). Poll health every 1s up to `startup_timeout`.
  - Process handle stored; **persistent by default** (leave running for reuse). Optional `ephemeral=True` → terminate on context exit.
  - Cross-platform spawn (`sys.executable`, no shell), `.venv` detection under `REASONER_HOME` if present.
- **Implementation tasks:** health poll; subprocess spawn; readiness gate; teardown; clear diagnostics when `REASONER_HOME` missing/invalid.
- **Refactoring:** none.
- **Testing:** fakes for the health probe and spawner — reuse-if-up, start-if-down, timeout→raise, ephemeral teardown. No real process in CI.
- **Acceptance:** never double-starts; surfaces actionable error (`REASONER_HOME not set`, `port busy`, `venv missing`).
- **Rollback:** disable via `REASONER_AUTOSTART=false` (manager becomes a pure health-checker).

---

#### WU-4 — `ReasonerSettings`
- **Objective:** 12-factor configuration for the integration.
- **Affected components:** `leggie/config/settings.py`.
- **Design changes:** new sub-model, wired into `Settings`:
  ```
  class ReasonerSettings(BaseSettings):
      model_config = SettingsConfigDict(env_prefix="LEGGIE_REASONER_", env_file=".env", extra="ignore")
      home: str = ""                    # REASONER_HOME (filesystem path to the repo)
      base_url: str = "http://localhost:8003"
      api_key: str = ""                 # Reasoner ADMIN_API_KEY (secret)
      autostart: bool = True
      startup_timeout: int = 60
      request_timeout: int = 300
      stage1_preset: str = "multi-perspective-premium"
      stage2_preset: str = "subagent-premium"
      perspective: str = "neutral"
      enabled: bool = False             # master switch; pipeline refuses if false
  ```
- **Testing:** settings load from env; secrets never rendered in `repr`/logs.
- **Acceptance:** `.env.example` documents all keys; defaults safe (disabled).
- **Rollback:** remove sub-model; no other code reads it until P5.

---

#### WU-5 — Prompt templates
- **Objective:** Version the domain prompts in-repo with a `{perspective}` slot.
- **Affected components:** `leggie/application/agents/prompts/deliberative/prompt01.md`, `prompt02.md`, plus a small renderer.
- **Design changes:** `Prompt01` parameterized on `{perspective}` (e.g. `neutral` → neutral analyst framing; `niki` → the conservative-patriotic framing). `Prompt02` unchanged in intent (auditor mindset; consumes prior report). A renderer injects `{bill_text}` and, for Stage 2, `{prior_report}`.
- **Testing:** snapshot tests for rendered prompts; guard that unknown perspective falls back to `neutral` with a warning.
- **Acceptance:** no hardcoded party string in code paths; perspective is data.
- **Rollback:** revert templates; renderer unused.

---

#### WU-6 — `DeliberativeFlow`
- **Objective:** Orchestrate the two-stage pipeline and persist a prose report.
- **Affected components:** `leggie/application/workflow/deliberative_flow.py` (new).
- **Design changes:** constructor takes `ReasonerPort` (+ ingest/parse ports, injected). `run(file_path, output_dir, perspective)`:
  1. `ensure_running()` (via injected manager or a port method) — else graceful fallback per §5.
  2. Ingest + parse (deterministic) → `bill_text`, Άρθρο tree.
  3. **Stage 1:** `reason(Prompt01(perspective)+bill_text, stage1_preset)` → `synthesis#1`.
  4. **Stage 2:** `reason(Prompt02 + bill_text + synthesis#1, stage2_preset)` → `synthesis#2`.
  5. Assemble prose: `# Περίληψη` + `# Κριτική (Stage 1)` + `# Έλεγχος/Audit (Stage 2)`.
  6. **Optional appendix (WU-9):** run ΦΕΚ/CELEX parser over prose → "Μη-επαληθευμένες παραπομπές".
  7. Save `Outputs/{bill}_deliberative.md`.
  8. Record events: preset per stage, `models_used`, `total_tokens`, cost, and **raw `synthesis` blobs** for replay.
- **Implementation tasks:** stage orchestration; assembly; event recording; trace-id propagation.
- **Refactoring:** extract the shared ingest/parse lazy-factory helper used by `BillAnalysisFlow` to avoid duplication (DRY).
- **Testing:** end-to-end against a **fake `ReasonerPort`** returning canned syntheses; assert file written, sections present, events recorded, Stage 2 receives Stage 1 output.
- **Acceptance:** no import of infrastructure from application (uses ports only); events replayable.
- **Rollback:** flow is only reachable via the new CLI flag; removing the binding disables it.

---

#### WU-7 — CLI + CQRS wiring
- **Objective:** Expose the pipeline without changing default behavior.
- **Affected components:** `leggie/interfaces/cli/__init__.py`, `application/cqrs/commands/cli_commands.py`, `handlers/cli_handlers.py`.
- **Design changes:** add `analyze --pipeline {deterministic,deliberative}` (default `deterministic`) and `--perspective`. New/extended `AnalyzeBillCommand` field `pipeline` + `perspective`; handler selects flow. Interface stays thin (no infra imports — dispatch through mediator, per existing contract).
- **Testing:** CLI parse tests; handler-routing tests (deterministic path untouched; deliberative path constructs `DeliberativeFlow`).
- **Acceptance:** existing `leggie analyze bill.txt` behaves identically; import-linter clean.
- **Rollback:** remove the flag/command field; default path unaffected.

---

#### WU-8 — Container binding + composition
- **Objective:** Wire `ReasonerPort → ReasonerAdapter` and inject the server manager.
- **Affected components:** `leggie/infrastructure/container.py`.
- **Design changes:** `container.register(ReasonerPort, lambda: ReasonerAdapter(settings.reasoner, server_manager=ReasonerServerManager(settings.reasoner)))`. Composition root only.
- **Testing:** container resolves the port to the adapter; lazy (not constructed unless requested).
- **Acceptance:** no eager Reasoner start on unrelated commands (`parse`, `eval`).
- **Rollback:** remove binding.

---

#### WU-9 — Hardening (fallback, budget, citation appendix, docs)
- **Objective:** Production-grade robustness and docs.
- **Affected components:** `DeliberativeFlow`, `budget_guard`, `README.md`, `docs/ARCHITECTURE.md`, `.env.example`.
- **Design changes:** graceful fallback (§7); pre-flight cost estimate vs `BudgetSettings.max_cost_per_run` with warn/abort; optional citation appendix; documentation of the new pipeline and config.
- **Testing:** fallback unit test; budget-ceiling test; docs lint.
- **Acceptance:** all quality gates (§6.4) green.
- **Rollback:** feature flag `LEGGIE_REASONER_ENABLED=false`.

---

## 4. Task Breakdown Structure (WBS)

```
1. Foundation (P0)
   1.1 ReasonerPort + DTOs                    [WU-1]
   1.2 ReasonerSettings + .env.example        [WU-4]
2. Infrastructure (P1 ∥ P2)
   2.1 ReasonerAdapter (HTTP, retry, parse)   [WU-2]
   2.2 ReasonerServerManager (health/spawn)   [WU-3]
   2.3 Container binding                       [WU-8]
3. Prompts (P4)
   3.1 prompt01.md / prompt02.md + renderer   [WU-5]
4. Orchestration (P3)
   4.1 DeliberativeFlow (2-stage + prose)     [WU-6]
   4.2 Shared ingest/parse helper (DRY)       [WU-6]
   4.3 Event recording for replay             [WU-6]
5. Interface (P5)
   5.1 CLI --pipeline / --perspective         [WU-7]
   5.2 CQRS command/handler routing           [WU-7]
6. Hardening (P6)
   6.1 Graceful fallback                       [WU-9]
   6.2 Budget accounting                       [WU-9]
   6.3 Optional citation appendix              [WU-9]
   6.4 Docs (README, ARCHITECTURE, .env)       [WU-9]
   6.5 Quality gates (ruff/mypy/import-linter) [WU-9]
```

**Critical path:** 1.1 → (2.1 ∥ 2.2) → 4.1 → 5.1 → 6.x.

---

## 5. Risk & Mitigation Matrix

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| R1 | Reasoner service unreachable / boot failure | Med | High | Health-check + bounded auto-start; typed `ReasonerUnavailableError`; graceful fallback to deterministic pipeline with a clear message. |
| R2 | Non-deterministic output breaks reproducibility/audit | High | Med | Persist raw `synthesis`, `models_used`, presets, tokens, cost as events; support `no_cache=false` and `client_run_id` idempotency key. |
| R3 | Cost blow-up (premium presets × 2 stages × large bills) | Med | High | Pre-flight token estimate vs `BudgetSettings`; default presets configurable; document budget vs premium tiers. |
| R4 | Secret leakage (`ADMIN_API_KEY`, `OPENROUTER_API_KEY`) | Low | High | Secrets only via env; never logged; excluded from `repr`; `.env` git-ignored. |
| R5 | Cross-repo path coupling (`REASONER_HOME`) is brittle/Windows-specific | Med | Med | Validate path at startup; actionable error; allow "no autostart, external URL" mode. |
| R6 | Architecture erosion (interface/infra leak) | Low | Med | import-linter contract in CI; port-only access from application. |
| R7 | Default behavior regression | Low | High | `deliberative` strictly opt-in; snapshot/regression tests on the deterministic path. |
| R8 | Port already busy by a foreign process on `:8003` | Low | Med | Health probe validates it is *Reasoner* (OpenAPI title check) before reuse; else fail with guidance. |
| R9 | Cannot E2E test in CI (Reasoner absent) | High | Low | Full unit coverage with fakes; documented manual local E2E runbook. |
| R10 | Prose-only output bypasses Leggie's verification value | Known | Med | Accepted per Decision B; optional citation appendix retains a verification sliver; deterministic path remains available. |

---

## 6. Testing & Quality-Assurance Strategy

### 6.1 Unit (CI, no external deps)
- DTO immutability/import-boundary tests (WU-1).
- Adapter parsing/retry/error taxonomy via mocked transport (WU-2).
- Server-manager reuse/start/timeout/ephemeral via fakes (WU-3).
- Settings loading + secret redaction (WU-4).
- Prompt rendering snapshots + perspective fallback (WU-5).
- Flow orchestration against a **fake `ReasonerPort`**: section assembly, Stage-1→Stage-2 hand-off, event recording, file write (WU-6).
- CLI parse + handler routing; deterministic path unchanged (WU-7).
- Container lazy resolution (WU-8).
- Fallback + budget-ceiling behavior (WU-9).

### 6.2 Integration (local, opt-in, `-m integration`)
- Real `ReasonerAdapter` against a locally running Reasoner backend; assert non-empty `synthesis`, `models_used` populated, cost recorded.

### 6.3 Manual E2E Runbook (local only)
1. Set `LEGGIE_REASONER_ENABLED=true`, `LEGGIE_REASONER_HOME=…`, keys.
2. `leggie analyze sample_bill.txt --pipeline deliberative --perspective neutral`.
3. Verify auto-start, `Outputs/sample_bill_deliberative.md`, and the event log.

### 6.4 Quality Gates (must pass before merge)
- `pytest tests/ -v` (new suites green; existing 199 unchanged).
- `ruff check leggie/` clean.
- `mypy leggie/` clean.
- `import-linter` layer contract satisfied.
- No secret in logs (assert in tests).

---

## 7. Deployment & Rollback Plan

### 7.1 Rollout
- **Feature-flagged:** `LEGGIE_REASONER_ENABLED=false` by default. The `deliberative` pipeline refuses to run (clear message) until explicitly enabled and configured.
- **Config-only activation:** no code deploy needed to toggle; set env + `REASONER_HOME`.
- **Backward compatible:** `analyze` without `--pipeline` is byte-for-byte unchanged.

### 7.2 Graceful Fallback (runtime)
On `ReasonerUnavailableError` (autostart failed / health timeout / auth error):
1. Log a structured warning with the cause.
2. If invoked as `--pipeline deliberative`, either (a) abort with an actionable message, or (b) fall back to the deterministic pipeline when `--fallback` is set. Default: **abort with guidance** (no silent behavior swap).

### 7.3 Rollback
- **Toggle:** `LEGGIE_REASONER_ENABLED=false` disables the feature instantly.
- **Revert:** the change is additive (new files + guarded CLI flag). Reverting the branch removes the pipeline with zero impact on existing commands.
- **Process cleanup:** if auto-start left a backend running, `--reasoner-ephemeral` or manual stop; document PID/port.

---

## 8. Post-Implementation Validation Checklist

- [ ] `leggie analyze bill.txt` (no flag) produces identical output to pre-change (regression snapshot).
- [ ] `leggie analyze bill.txt --pipeline deliberative` runs both stages and writes `Outputs/{bill}_deliberative.md`.
- [ ] Report contains three sections: Περίληψη, Κριτική (Stage 1), Έλεγχος/Audit (Stage 2).
- [ ] Stage 2 provably receives Stage 1 output (verified via event log / fake).
- [ ] Auto-start: cold machine starts backend only; warm machine reuses; foreign `:8003` rejected safely.
- [ ] Events recorded: presets, `models_used`, tokens, cost, raw synthesis — run is replayable.
- [ ] `--perspective niki` vs `neutral` changes Stage 1 framing; unknown value falls back to `neutral`.
- [ ] Reasoner down → clear, actionable failure (or documented fallback), never a stack trace.
- [ ] No secrets in logs; `.env.example` documents every `LEGGIE_REASONER_*` key.
- [ ] Budget pre-flight warns/aborts above `max_cost_per_run`.
- [ ] `ruff`, `mypy`, `import-linter`, and full `pytest` all green.
- [ ] README + ARCHITECTURE document the new pipeline and its opt-in nature.

---

## 9. Appendix — Engineering Practices Applied

| Practice | Application |
|----------|-------------|
| **SOLID / Clean Architecture** | New `ReasonerPort` (DIP); adapter in infrastructure; flow in application; interface stays thin. Layer contract enforced by import-linter. |
| **Separation of Concerns** | Transport (adapter), lifecycle (server manager), orchestration (flow), config (settings), presentation (CLI) are distinct units. |
| **DRY / KISS / YAGNI** | Shared ingest/parse helper extracted; prose-only output (no premature Finding-mapping); reuse existing `httpx`, event sourcing, budget guard, structlog. |
| **Secure-by-Design** | Secrets via env only; no logging of keys; OpenAPI-title check before trusting a running port. |
| **Defensive Programming** | Tolerant response parsing; bounded retries; typed errors; health gating before calls; perspective fallback. |
| **Observability** | Structured logs with trace IDs; per-stage spans; `models_used`/cost/tokens captured as events. |
| **CI/CD** | Additive + feature-flagged; unit tests independent of external services; quality gates block merge. |
| **Documentation** | README, ARCHITECTURE, `.env.example`, and this plan updated alongside code. |
| **Performance / Scalability** | Persistent backend reuse avoids cold starts; presets tune the cost/latency/quality trade-off per stage; async throughout. |
```
