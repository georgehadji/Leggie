# Leggie — Full Wiring Plan

**Purpose:** Close the gap between "architecture that's built" and "architecture that's live." Verified ground truth as of this plan (not assumption): 312 tests green, `mypy leggie/` clean (0 errors), `ruff check leggie/ tests/` clean, `lint-imports` passes (1 contract kept). The remaining gap is not missing code — it's **disconnected** code: real components exist, sit correctly behind ports, are unit-tested, and are never instantiated by the one path that matters, the CLI run.

**Governing rules:** `docs/FIX_PLAN.md` §Architecture Conformance (A–K). Every wiring step below cites the rule it satisfies or would violate if done carelessly.

---

## 1. Ground truth (verified, not assumed)

| Component | Built? | Tested? | Live in CLI run? | Evidence |
|---|---|---|---|---|
| Router + cascade (EN1) | ✅ | ✅ | ✅ **yes** | `orchestrator.py` calls `self._router.route()` / `.cascade()`; `cli_handlers._try_get_router()` constructs it |
| Budget guard (EN2) | ✅ | ✅ | ✅ **yes** | `BudgetGuardDecorator` wraps the LLM adapter in `_try_get_llm()`, $5 default ceiling |
| Dedup | ✅ | ✅ | ✅ **yes** | Runs in both aggregation paths |
| import-linter (rule A check) | ✅ | ✅ | ✅ **yes** | `lint-imports` → 1 contract kept |
| Constitutional lens LLM (F1) | ✅ | ✅ | ✅ **yes** | Verified live, real Greek findings |
| Noise suppression / alias-normalization / Greek retry | ✅ | ✅ | ✅ **yes** | This session's fixes |
| **Economic / legal-coherence / implementation / EU-GDPR lenses** | ❌ | regex-only | ❌ **no LLM ever** | No `_prompt_for`/`_call_llm_structured` call in any of the 4 files; no prompt module exists for them |
| Blackboard aggregation (EN3) | ✅ | ✅ | ❌ **no** | `BillAnalysisFlow(use_blackboard=False)` is the constructor default; `cli_handlers.py` never overrides it |
| Citation resolution (EN5) | ✅ | ✅ | ❌ **no** | `CoVeVerifier()` is constructed with no `citation_parser` in `bill_analysis_flow.py`; `container.py` *does* register `GreekCitationParser` but nothing reads it |
| Verbalized Sampling (EN4) | ✅ (`verbalized_sampling.py`, 67 lines) | partial | ❌ **no** | Zero references from `orchestrator.py` or any lens |
| `configure_logging()` | ✅ | — | ❌ **no** | Never called anywhere in `leggie/interfaces/cli/` — structlog processors/JSON renderer never applied |
| `infrastructure/container.py` (the actual composition root) | ✅ | partial | ❌ **dead code at runtime** | `cli_handlers.py` never imports `Container` — it has its own parallel `_try_get_llm` / `_try_get_router` / `_try_get_budget_guard` ad-hoc factories instead |

**The one finding that reframes everything else:** there are **two parallel object-construction paths** — `infrastructure/container.py` (the real DI composition root, registers 10 ports including `CitationParserPort`, `BlackboardPort`) and `cli_handlers.py`'s own `_try_get_*` helper functions (what actually runs). This is itself a **rule-A violation in spirit**: rule A wants one composition root; having two — one used, one orphaned — is worse than having none, because the orphaned one silently rots out of sync (it may already reference APIs that drifted). **W1 below is the prerequisite everything else is built on.**

---

## 2. Priority order and why

```
W1 Single composition root  ─┬─► W2 Blackboard on           ─┐
   (container.py is truth)   ├─► W3 Citation resolution on   ├─► W6 configure_logging()
                              └─► W4 Verbalized Sampling on  ─┘
                                        │
W5 Four missing lenses (biggest gap) ───┘  (independent — no dependency on W1–W4)
```

- **W5 (four lenses) is the single highest-value item.** 4 of 5 lenses have never called an LLM. Every "economic"/"legal_coherence"/"implementation"/"eu_compliance" finding in every report to date is a regex match, not analysis. This is bigger than the noise/schema-drift bugs fixed earlier this session.
- **W1 must land before W2–W4**, or each of those gets bolted onto the ad-hoc factories, doubling the duplication instead of resolving it.
- **W6 is cheap and independent** — do it any time, ideally first (5-minute fix, immediate operational value for debugging everything else).

---

## 3. Work items

### W1 — Consolidate on a single composition root
**Rule:** A (inward-only dependencies via *one* composition root)

**Problem:** `cli_handlers.py` has `_try_get_llm()`, `_try_get_router()`, `_try_get_budget_guard()` — each duplicates logic already in `container.py`'s `configure_defaults()`. The container registers `CitationParserPort → GreekCitationParser`, `BlackboardPort → BlackboardAdapter`, but nothing ever resolves them because nothing ever asks the container for anything.

**Design:** `cli_handlers.py` (interfaces→application boundary) should receive a `Container` instance (or resolve one at CLI startup in `leggie/interfaces/cli/__init__.py`) and pull every port through it: `container.resolve(LLMPort)`, `container.resolve(RouterPort)`, `container.resolve(CitationParserPort)`, `container.resolve(BlackboardPort)`, `container.resolve("budget_guard")`. Delete `_try_get_llm`/`_try_get_router`/`_try_get_budget_guard` once callers are migrated — don't leave both paths alive "for safety," that's exactly the drift that caused this audit.

**Tasks:**
1. Add `container.resolve(CitationParserPort)` and `container.resolve(BlackboardPort)` calls where needed (types already registered).
2. `leggie/interfaces/cli/__init__.py`: construct one `Container()`, call `configure_defaults()` once at startup, pass it into the CQRS handler(s).
3. `cli_handlers.py`: replace each `_try_get_*` body with a container resolve call; keep the *function names* if callers depend on them, but make the body one line.
4. Delete now-dead duplicate construction logic.

**Testing:** existing CLI/handler tests must keep passing unchanged (behavior-preserving refactor) — that's the acceptance bar, not new tests, unless resolve-path branching (e.g. "no API key configured") isn't already covered.

**Rollback:** this is a refactor of already-tested code paths; revert the commit if a resolve-order bug surfaces (e.g. container needs `configure_defaults()` called before first `resolve()`).

---

### W2 — Turn on Blackboard aggregation (EN3) by default
**Rules:** C (lenses blind during analysis — unaffected, aggregation-only change), D (event-sourced aggregation, no in-place mutation)

**Design:** Flip the default. `BillAnalysisFlow.__init__(..., use_blackboard: bool = True)`. Keep the inline path (`_aggregate_inline`) alive behind the flag for one release as an explicit rollback switch (per `implementation_plan.md` deploy-plan precedent), not deleted immediately.

**Tasks:**
1. Change the default in `bill_analysis_flow.py`.
2. `cli_handlers.py` (post-W1): resolve the flag from settings (`LEGGIE_USE_BLACKBOARD`, default true) rather than hardcoding, so it can be turned off in staging without a redeploy.
3. Re-run the real e2e (`OE_ΣΧΝ-ΥΠΔΙΚ.pdf`) with the flag on; diff the finding count/content against the last known-good inline-path run (44 findings, 3 high) — the blackboard path must produce equivalent or better results, not silently different ones.

**Testing:** `test_blackboard_aggregator.py` already exists — extend it with the exact `_finding_similarity_article_aware` fixture data to assert dedup/rerank/skeptic/CoVe order matches the inline path's semantics. Add one integration test asserting `BillAnalysisFlow(use_blackboard=True).run(...)` on a small fixture bill produces the same finding count as `use_blackboard=False`.

**Acceptance:** e2e run on the real bill via the blackboard path is not worse (finding count, severity mix) than the last inline-path baseline captured this session.

**Rollback:** flip the settings flag back to false; no code revert needed.

---

### W3 — Wire citation resolution (EN5)
**Rules:** B (no LLM in the fact-check loop — this is exactly that: a pure/deterministic resolver), H (reproducibility)

**Design:** `container.py` already binds `CitationParserPort → GreekCitationParser`. `CoVeVerifier` already accepts `citation_parser` in its constructor. The only gap is `bill_analysis_flow.py` constructing `CoVeVerifier()` with no argument. After W1, the flow receives its `CoVeVerifier` (or the citation parser to build one) from the container instead of self-constructing a bare one.

**Tasks:**
1. `bill_analysis_flow.py`: accept `cove: CoVeVerifier | None = None` (already does) — the caller (post-W1 CLI handler) now passes `CoVeVerifier(citation_parser=container.resolve(CitationParserPort))` instead of leaving it `None` → default bare `CoVeVerifier()`.
2. Verify `GreekCitationParser` (`infrastructure/citation/__init__.py`, 137 lines) actually resolves ΦΕΚ/CELEX/ECLI patterns against something — check whether it's a real resolver or currently only a *parser* (extracts citation strings) without a resolution backend (confirms existence, not validity against a corpus). If it's parse-only, document that "resolution" here means "structurally valid citation extracted," not "verified against EUR-Lex" — don't overclaim in the report language.

**Testing:** unit test that a finding with a real ΦΕΚ citation in its evidence gets `citation` populated post-CoVe; a finding with a garbled/hallucinated citation gets flagged, not silently dropped or silently trusted.

**Acceptance:** e2e run shows non-null `citation` fields on findings that quote a real ΦΕΚ/CELEX reference (check the real bill's findings for this — GDPR findings cited "Κανονισμός (ΕΕ) 2016/679" verbatim in this session's runs, a good test case).

**Rollback:** revert to `CoVeVerifier()` with no parser — substring-quote validation (separate, already-working check) is unaffected.

---

### W4 — Wire Verbalized Sampling (EN4)
**Rule:** F (reproducibility — VS must be seeded), B (deterministic dispatch — VS is one LLM call producing k candidates, not k separate LLM calls)

**Design:** Read `verbalized_sampling.py` (67 lines) first to confirm its actual interface before deciding the integration point — do not assume shape. Likely integration point: `ConstitutionalLens._analyze_llm` currently does one `_call_llm_structured(LensFindings, ...)` call expecting a single best-effort answer; VS is meant to replace this with one call returning *k* candidates + probabilities, then tail-sample. Given only the constitutional lens has real LLM wiring today, **land W4 together with (or immediately after) W5** — extending 4 new lenses AND wiring VS into the LLM call path in the same pass avoids writing the plain single-candidate path for 4 lenses only to replace it with VS immediately after.

**Tasks:**
1. Read `verbalized_sampling.py` fully; confirm whether it wraps `LLMPort` directly or expects to be called by lens code with a raw response.
2. Decide: VS replaces `_call_llm_structured` inside `Lens` base (affects all 5 lenses uniformly), or is an optional opt-in per lens. Given rule K (small, focused files) and DRY, prefer changing it once in `Lens` base rather than duplicating into 5 lens files.
3. Wire tail-weighted seeded sampling — reuse the seed already threaded through `LLMRequest` (rule F).

**Testing:** deterministic-sampler unit test under a fixed seed (same seed → same tail pick); token-cost assertion that VS is genuinely one call, not k calls (mock the LLM port, assert `generate`/`generate_structured` call count == 1 per article-lens pair).

**Acceptance:** measurable diversity lift on a fixture with a known "textbook" and a known "edge-case" finding both present in the same article — VS should be able to surface both where single-candidate sampling picks only the more obvious one.

**Rollback:** VS behind a flag on `Lens.__init__` (`use_verbalized_sampling: bool = False` initially, flip once validated) — same staged-rollout pattern as W2.

---

### W5 — Give the four regex-only lenses real LLM analysis
**Rules:** C (stateless/blind lenses — same shape as constitutional), G (boundary validation), same alias-normalization/noise-suppression infra already proven this session

**This is the biggest single gap in the project right now.** Every economic/legal-coherence/implementation/EU-GDPR finding shipped to date is a keyword match, not analysis. The `implementation_plan.md`'s "constitutional lens now returns real Greek legal reasoning" claim does **not** extend to 80% of the lens ensemble.

**Design:** Mirror the constitutional lens exactly — it's the proven template (F1 wiring + F2 noise filter + boundary alias-normalization, all already generalized in `LLMAdapter.generate_structured`, not per-lens code). For each of the 4 lenses:

1. Add `leggie/application/agents/prompts/{economic,legal_coherence,implementation,eu_gdpr}.py` — `SYSTEM_PROMPT` + `USER_PROMPT_TEMPLATE`, Greek-language-enforced (same pattern as `constitutional.py`, including the explicit "ΑΥΣΤΗΡΟΣ ΚΑΝΟΝΑΣ: do not emit filler" rule baked in from day one — don't repeat the noise-discovery cycle four more times).
2. Give each lens class an `_analyze_llm` method calling `self._call_llm_structured(LensFindings, prompt, system)`, same shape as `ConstitutionalLens._analyze_llm` — including the `verbatim_quote`-required filter.
3. Keep each lens's existing regex path as the `_analyze_regex` fallback (already exists in all 4 — don't discard proven fallback code, rule I's spirit: resilience stack, not single point of failure).
4. Domain-specific prompt content per lens (draft, refine against real bill output):
   - **Economic**: fiscal impact, budget-line citation requirement (Άρθρο 75 Σ — δημοσιονομική έκθεση), unfunded-mandate detection.
   - **Legal coherence**: internal contradiction between articles, undefined-term flag, ambiguous scope of application.
   - **Implementation**: unrealistic deadlines relative to administrative capacity, missing secondary-legislation trigger, enforcement-mechanism gap.
   - **EU-GDPR**: lawful-basis-for-processing gap (Άρθρο 6 ΓΚΠΔ), DPIA trigger, cross-border-transfer flag, retention-period absence.

**Testing:** for each lens, one unit test mirroring `test_constitutional_lens.py`'s live-shape pattern (mock LLM returning a known JSON shape → assert Finding produced with correct `finding_type`); one test confirming the `verbatim_quote` filter suppresses a fabricated no-evidence candidate.

**Acceptance:** re-run the real bill e2e; the type-breakdown in the executive summary should show substantive, non-boilerplate findings across all 5 types (compare against this session's 44-finding baseline, where `economic`/`implementation`/`procedural`/`factual` were 100% regex).

**Rollback:** per-lens — each lens's `except Exception` already falls back to `_analyze_regex`, so a broken prompt degrades to the pre-existing regex baseline automatically, not a crash. No explicit rollback needed beyond reverting the prompt file.

---

### W6 — Call `configure_logging()` at CLI startup
**Rule:** none directly, but supports E (traceability) — you can't debug W1–W5 well with unconfigured structlog

**Design:** trivial. `leggie/interfaces/cli/__init__.py`'s entry point calls `configure_logging()` once before dispatching any command, exactly like `configure_defaults()` in W1.

**Tasks:** one function call, placed before the CLI arg-dispatch, gated on not double-configuring if called twice (structlog raises if reconfigured — check for that, guard with a module-level `_configured` flag if needed).

**Testing:** smoke test that `leggie analyze` output contains structured JSON log lines (or console-rendered lines matching `settings.debug`), not raw unformatted logging.

**Acceptance:** trace_id appears consistently across every log line in one `leggie analyze` run (grep the output for the same UUID from `flow.started` through `flow.outputs_saved`).

---

## 4. Milestones

- **M1 (do first, ~1 hour):** W6. Immediate, isolated, makes every subsequent milestone's verification easier.
- **M2 (~1 day):** W1. Refactor-only, behavior-preserving, unblocks W2–W3.
- **M3 (~2–3 days):** W5. The highest-value item — 4 lenses go from regex-only to real analysis. Independent of W1–W4; can run in parallel with M2 if two people/sessions are available.
- **M4 (~1 day):** W2 + W3, landed together (both depend on W1, both are "point an existing constructor arg at an existing registered service").
- **M5 (~1–2 days):** W4, landed alongside or immediately after W5 so the 4 new lenses don't need a second pass.
- **M6 (validation):** full e2e re-run on `OE_ΣΧΝ-ΥΠΔΙΚ.pdf` after each milestone; the finding-count/type-breakdown/severity-mix trend across M1→M5 is the real acceptance signal, not any single number.

---

## 5. Engineering practice notes (apply throughout)

- **SOLID/DI:** every wiring change is "point an existing constructor parameter at an existing registered port" — if a task looks like it needs a *new* abstraction, stop; the port almost certainly already exists (this audit found 10 registered ports, most already unused-but-ready).
- **DRY:** W5's four lenses must reuse `Lens` base + the boundary alias-normalization in `LLMAdapter` — do not hand-roll a second noise filter or a second alias map per lens.
- **YAGNI:** do not build a new composition mechanism for W1 — `container.py` already exists and is correctly structured; the fix is "use it," not "redesign it."
- **Secure-by-design:** no new external calls introduced by any of W1–W6; W3's citation resolution stays a deterministic local parser (rule B) — do not let it grow into an LLM-based citation checker.
- **Observability:** W6 first specifically because W2–W5 all become easier to debug once trace IDs and structured logs are actually flowing.
- **Testing:** every wiring step's acceptance bar is behavior-observed on the real bill, not just unit-test-green — this session's experience (168→44 findings, constitutional bucket vanishing then recovering) is a direct lesson that unit tests alone did not catch either regression.
