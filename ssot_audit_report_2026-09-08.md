# SSOT Audit — configuration constants defined in more than one place

**Audit date:** 2026-09-08 · **Commit:** `refactor: make IngestSettings the single
source of truth for the PROD-16a caps` · **Scope:** `leggie/`, `config/routes.yaml`,
`.env.example` · **Method:** read-only. Every claim below is a `file:line` I opened.

## 1. Executive summary

**Verdict: duplicated configuration is SYSTEMIC, not isolated.** Seven findings,
two HIGH. The pattern just fixed in the ingest caps — a value written out in
several places, only one of them reachable by configuration — recurs across the
budget guard, the router, and the concurrency ceilings. In several cases a
settings field exists, is documented, and is **read by nothing**, so setting the
environment variable does nothing at all and the code's own literal governs.

One copy has **already drifted**, and it drifted to the exact value of a past
production incident.

| ID | Constant | Severity | Drifted? | Status |
|---|---|---|---|---|
| SSOT-1 | Budget guard token ceiling | **HIGH** | **YES — 500k vs 20M** | FIXED |
| SSOT-2 | Tier → model mapping | **HIGH** | No (~11 copies agree) | FIXED |
| SSOT-3 | Verification / skeptic concurrency | MEDIUM | No (settings unread) | FIXED |
| SSOT-4 | Default model id | MEDIUM | No | FIXED |
| SSOT-5 | Rate-limiter ceiling | MEDIUM | No | FIXED |
| SSOT-6 | Reasoner autostart | LOW | **YES — true vs False** | FIXED |
| SSOT-7 | Route `max_tokens` fallbacks | LOW | No | FIXED |

**All seven fixed 2026-09-08**, same day as the audit — see §5 for the
implementation record. A fourth `base_url` copy (SSOT-4) was found during the
fix and folded in.

## 2. Findings

### SSOT-1 — Budget guard token ceiling — HIGH, ALREADY DRIFTED

The per-run token ceiling.

| Location | Literal |
|---|---|
| `leggie/config/settings.py:74` | `max_tokens_per_run: int = Field(default=20_000_000, ge=1_000)` |
| `leggie/infrastructure/budget_guard/__init__.py:45` | `def __init__(self, max_tokens: int = 500_000, max_cost: float = 5.0)` |
| `.env.example:18` | `LEGGIE_BUDGET__MAX_TOKENS_PER_RUN=20000000` |

**These disagree by a factor of 40**, and the guard's own default is
**`500_000` — the exact value of the historical budget-block incident** that
settings raised to 20M specifically to stop runs being throttled long before the
$5 cost cap governs. `settings.py:70-73` carries a comment explaining why the
ceiling must sit well above the cost runway; the guard sitting one module away
contradicts it.

Production is currently safe: `container.py:135-136` constructs
`BudgetGuard(max_tokens=s.budget.max_tokens_per_run, ...)`, the only
construction site in `leggie/`. So this is a **loaded trap, not an active bug** —
any second construction site, any test fixture treated as representative, or any
future refactor that drops the kwarg silently reinstates the incident.

`max_cost=5.0` on the same line is a second copy of the fenced $5 cap
(`settings.py:75`), which change control names as a non-negotiable.

**Fix:** default both parameters to `None` and resolve from `BudgetSettings`,
exactly as `BoundedIngestor` now does.

### SSOT-2 — Tier → model mapping — HIGH

Which model backs the free / budget / premium tier is written out in **three
independent systems**:

| Location | Content |
|---|---|
| `leggie/config/settings.py:58-60` | `free_model`, `budget_model`, `premium_model` — **read by nothing** |
| `leggie/infrastructure/router/__init__.py:83-86` | hardcoded `{FREE: ..., BUDGET: ..., PREMIUM: ...}` map plus a `.get(tier, "google/gemini-2.5-flash")` fallback |
| `config/routes.yaml` | `cascade_models` repeated per route — `x-ai/grok-4.5` appears **8 times**, `google/gemini-2.5-flash-lite` **6 times** |

`StaticRouter` never reads `CascadeSettings`. Verified: the only consumer of that
class anywhere in `leggie/` is `container.py:157-158`, and it reads
**`rules_path` only**. So `LEGGIE_CASCADE__PREMIUM_MODEL`, `__FREE_MODEL`,
`__BUDGET_MODEL`, `__CONFIDENCE_FLOOR` and `__PREMIUM_FALLBACK_ENABLED` are all
inert — five settings fields that look configurable and are not.

This is the highest-churn constant in the project, and its history is why it
matters: fake or dead model IDs have broken the pipeline **twice** (commit
`2780339`, repaired by `39b42ef`). Changing a tier's model today means editing
around eleven places and hoping none is missed, with a twelfth hiding as a bare
fallback `RouteResult` at `router/__init__.py:39`.

**Fix:** one tier→model table. Either the router reads `CascadeSettings`, or the
settings fields are deleted as dead and `routes.yaml` anchors the per-tier
default once. Do not leave both.

### SSOT-3 — Verification and skeptic concurrency — MEDIUM

| Location | Literal |
|---|---|
| `leggie/config/settings.py:32-37` | `max_verification_concurrency: int = Field(default=10, ...)` — **read by nothing** |
| `leggie/config/settings.py:38-43` | `max_skeptic_concurrency: int = Field(default=10, ...)` — **read by nothing** |
| `leggie/application/services/cove_verifier.py:151` | `max_concurrency: int = 10` |
| `leggie/application/agents/skeptic.py:223` | `max_concurrency: int = 10` |

Values agree, so behaviour is correct — but the environment variables do
nothing. Already recorded as a known wiring gap at
`docs/implementation_audit_report_phase1b.md:36,73,135` (PROD-38, filed LOW).
It is listed here because it is the *same shape* as the ingest timeout, which
was also "harmless until a live run needed to change it".

**Fix:** `blackboard_aggregator.py:129,144` passes the settings values, per that
audit's own recommendation.

### SSOT-4 — Default model id — MEDIUM

`"google/gemini-2.5-flash"` appears as a literal default in eight places outside
the price table: `settings.py:25`, `settings.py:59`, `llm/__init__.py:103`,
`llm/adapters/openrouter.py:50`, `router/__init__.py:39,84,86`, plus
`application/agents/constitutional_lens.py:36` and
`application/agents/orchestrator.py:55`.

The application-layer ones are the notable pair: a lens and the orchestrator each
name a concrete vendor model, which is a configuration value living two layers
below where configuration belongs.

**Fix:** one default, resolved from `LLMSettings`; constructor parameters default
to `None`.

### SSOT-5 — Rate-limiter ceiling — MEDIUM

| Location | Literal |
|---|---|
| `leggie/config/settings.py:44-49` | `max_rate_per_second: float = Field(default=5.0, ...)` |
| `leggie/infrastructure/container.py:254-255` | reads the setting — **correct** |
| `leggie/infrastructure/llm/adapters/openrouter.py:59` | `rate_limiter or RateLimiter(max_rate=5.0)` |

The production path is properly wired; the adapter's fallback re-hardcodes the
same number. Copies agree today.

**Fix:** resolve the fallback from settings, or make the parameter required.

### SSOT-6 — Reasoner autostart — LOW, ALREADY DRIFTED

| Location | Value |
|---|---|
| `leggie/config/settings.py:169` | `autostart: bool = Field(default=False, ...)` |
| `.env.example:51` | `LEGGIE_REASONER__AUTOSTART=true` |

Anyone who copies `.env.example` to `.env` — which line 2 instructs — gets
autostart **enabled**, against a code default of disabled. Given that a leaked
autostarted Reasoner process was a real incident (PR #7, commit `af4e4a8`), the
template shipping the more aggressive value is worth correcting.

**Fix:** align `.env.example` to `false`, or change the code default deliberately.

### SSOT-7 — Route `max_tokens` fallbacks — LOW

`router/__init__.py:45` defaults a missing route's `max_tokens` to `4096`, `:65`
defaults a cascade escalation to `8192`, `:39` uses `4096` again, and
`application/services/lens_vs.py:35` independently defaults to `4096`. The real
values live per-route in `routes.yaml` (1024 / 2048 / 6144 / 8192).

These are fallbacks for a missing config entry rather than second definitions of
a live value, so drift risk is low — but `routes.yaml` is the SSOT, and a silent
4096 masks a missing entry instead of surfacing it. Precedent: the `lens_analysis`
route was DEAD for an entire smoke campaign because the orchestrator queried
`lens_<name>`, and nothing complained.

**Fix:** log a warning when a route is missing rather than silently substituting.

## 3. Categories checked with NOTHING found

- **`domain/pricing.py`** — a price table keyed by model id. Not a duplicate: it
  is the single definition of per-model pricing, regenerated by
  `tools/refresh_model_prices.py`. Correct as-is.
- **Seed, log level, app name** (`settings.py:198-202`) — single definitions, no
  second copies.
- **Persistence URL** — one definition, and `.env.example:36-41` carries an
  explicit comment about the single-underscore prefix quirk that would otherwise
  silently ignore `LEGGIE_DB__URL`. Exemplary; the model for the rest.
- **Ingest caps** — fixed, and now self-policing via
  `tests/unit/infrastructure/test_ingest.py::TestIngestCapsSingleSourceOfTruth::test_no_cap_literals_remain_outside_settings`.

## 4. Verdict

Duplicated configuration is **systemic**. The recurring anti-pattern is not
copy-paste for its own sake — it is **a settings field added without a consumer**,
leaving the code's own literal in charge. Seven settings fields are inert today:
the five `CascadeSettings` model and threshold fields, and the two concurrency
ceilings. Each was presumably added in good faith as "the configurable version",
but nothing was rewired to read it, so the project ships documented environment
variables that silently do nothing — the most expensive kind of configuration
bug, because it fails by *appearing* to work.

SSOT-1 is the one to fix first: it has already drifted, and it drifted to a value
this project has been burned by before.

A cheap structural guard already exists and is worth generalising: the
literal-grep test written for the ingest caps. One such test per settings group,
asserting a value appears nowhere but `settings.py`, would make this entire class
of defect self-policing rather than audit-dependent.

---

## 5. Implementation record (2026-09-08)

All seven fixed the same day. Every cap now resolves from settings with an
explicit argument still winning, the same shape as the ingest fix that prompted
the audit.

| ID | Change |
|---|---|
| SSOT-1 | `BudgetGuard.__init__` takes `max_tokens/max_cost: … \| None`, resolving from `BudgetSettings`. The `500_000` literal is gone. |
| SSOT-2 | `StaticRouter._default_for_tier` reads `CascadeSettings`; `rules_path` defaults from settings instead of a literal path. Three inert settings fields are now live. |
| SSOT-3 | `CalibratedSkeptic.review` and `CoVeVerifier.verify_batch` take `int \| None` and resolve from settings themselves. Closes PROD-38's wiring gap. **Deviation:** the phase1b audit recommended threading the value from `blackboard_aggregator`'s call sites; doing so broke `_DowngradingCoVe`, a test double implementing the old signature, and would break every other stub of those ports. The ceiling is the callee's business — resolving it there fixes the gap with a smaller diff and without a signature ripple. |
| SSOT-4 | `default_model` and `base_url` resolve from `LLMSettings` in `LLMAdapter`, `OpenRouterProvider`, `validate_model_ids` and `Orchestrator`. `ConstitutionalLens.__init__` deleted outright — it existed only to hardcode a vendor model id, and was the one lens of five to override the base. |
| SSOT-5 | `OpenRouterProvider`'s rate-limiter fallback reads `max_rate_per_second`. |
| SSOT-6 | `.env.example` set to `false`, matching `ReasonerSettings.autostart`, with the PR #7 leak recorded as the reason. |
| SSOT-7 | Fallback ceilings named `_FALLBACK_MAX_TOKENS` / `_FALLBACK_CASCADE_MAX_TOKENS`; a missing route or missing rules file now logs `router.route_missing` / `router.rules_missing` instead of silently substituting the budget tier. |

**Found during the fix, not in the audit:** `validate_model_ids` carried a
fourth copy of the OpenRouter base URL (`llm/__init__.py:58`). Folded into
SSOT-4.

### 5.1 The guard

`tests/unit/test_settings_ssot.py` (17 tests) generalises the ingest-caps
approach in three layers:

1. **Literal-greps** — each duplicated value, named against the exact module it
   must not reappear in, so a failure says precisely what regressed.
2. **Wiring assertions** — a module could drop its literal and still ignore the
   setting. These pin a synthetic settings value and assert it reaches the
   object: the budget guard's state, the router's tier map, and — via a
   `Semaphore` spy — the skeptic's actual concurrency, not just its signature.
3. **`.env.example` agreement** — the template is copied verbatim to `.env`, so
   a documented value disagreeing with the code default changes behaviour for
   every new checkout.

Layer 2 is the one that matters: the audit's core finding was **a settings field
with no consumer**, and a grep alone cannot detect that.

### 5.2 Gates

`lint-imports` 2/2 kept — `application/` importing `config/` is a legal edge
under the layer contract (config is the innermost leaf), and there was existing
precedent in `bill_analysis_flow.py` and `lens.py`. mypy clean, ruff clean.
