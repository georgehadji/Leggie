# Leggie — Output Quality Fix Plan

> **Status:** Superseded by `docs/PRODUCTION_READINESS_PLAN.md`. Kept for reference only. Active work follows the phased plan in that document.


> Status: MVP runs end-to-end (199 tests green) but the analysis output is **noise**.
> Root cause: **no LLM is ever called** — every "lens" is a regex→canned-string stub —
> and the parser invents phantom articles. This plan fixes both, wiring the real
> OpenRouter LLM into the existing architecture without changing the architecture.
>
> Provider decision: **OpenRouter** (existing `OpenRouterProvider`, config already present).
> This document is the plan only. No code is changed here.

---

## Part 1 — Diagnosis (evidence)

Reproduced against the real run in `analysis_report.md` (`OE_ΣΧΝ-ΥΠΔΙΚ`, 214 "articles", 299 findings, 202 of them INFO).

| # | Defect | Symptom in report | Root cause (file:line) |
|---|--------|-------------------|------------------------|
| D1 | **No LLM runs.** Lenses are keyword→template. | "GDPR check" fires on the word *επεξεργασία*; "fiscal estimate" on *ποινή*/*κύρωση*; identical suggestion on every article. | `constitutional_lens.py:58-99` (regex → hardcoded IRAC). `orchestrator.py:95` builds `lens_cls()` with no LLM. `bill_analysis_flow.py:60` builds `Orchestrator()` with no LLM. `OpenRouterProvider` (`infrastructure/llm/__init__.py`) is dead code. |
| D2 | **Parser invents phantom articles.** | Fake articles `552`, `622Γ`, `58Α` (refs to ΚΠολΔ/ΠΚ, not bill articles); titles truncated mid-word ("Άρθρο 22 Κ", "Άρθρο 1 περ"); "Άρθρο 64\nθρου 22". | `parse/__init__.py:17-20` `ARTICLE_PATTERN` matches *every* "Άρθρο N" occurrence incl. in-body cross-references; span cut by next spurious match (`:65-91`); no PDF mid-token newline repair. |
| D3 | **68% of findings are "no issue" filler.** | 202/299 = INFO "Δεν εντοπίστηκαν προφανή συνταγματικά ζητήματα". | `constitutional_lens.py:96-97,185-199` emits a baseline finding per article to satisfy the old Phase-1 exit gate. |
| D4 | **Evidence is a trigger word, not a quote.** | `Evidence: _σχετική_`, `_ποινή_`, `_κύρωση_`. | Lenses set `text_excerpt=match.group(0)` — the matched keyword, not the passage. |
| D5 | **Suggestions are boilerplate.** | "Review delegation clause per Article 43" repeated on nearly every article. | `improver.py` maps finding type → static string; no article-specific reasoning. |
| D6 | **Skeptic / rerank / CoVe don't filter.** | All 299 survive. | Nothing to filter — there is no real signal or real confidence spread. They pass stubs through. |
| D7 | **Eval is stubbed too.** | `eval` reports precision 0 / RDI −1 for every bill. | `cli_handlers.py:88` calls `scorer.score(bill_id, [])` — empty findings, never runs the flow. |

**One-line conclusion:** the ensemble/blackboard/skeptic/CoVe/event-sourcing scaffold is real and correct, but every *intelligent* node is a placeholder. Fix = replace placeholders with real LLM calls + fix the segmentation feeding them.

---

## Part 2 — Target output (acceptance criteria)

Run on the same bill (`OE_ΣΧΝ-ΥΠΔΙΚ`) after the fix must produce:

1. **Correct segmentation** — article count matches the bill's actual article headings (no `552`/`622Γ` phantoms), titles complete, no mid-word truncation.
2. **Substantive findings** — each finding is a real legal issue with IRAC filled by the model, not a template. A human lawyer would recognise it as a real observation.
3. **Real evidence** — `text_excerpt` is a verbatim span copied from the article (validated as a substring), plus a resolved citation where one exists.
4. **Signal, not noise** — no "no issue found" INFO filler; near-duplicate findings deduped; findings ranked by severity × confidence; INFO suppressed from the default report.
5. **Bounded cost** — one bill stays under the budget guard ceiling (`LEGGIE_BUDGET_max_cost_per_run`, default $5), enforced, not just configured.
6. **Reproducible + auditable** — same seed → same routing decisions; event log records every LLM call, model tier, and refuted finding.
7. **Eval actually scores** — `eval` runs the real flow and reports non-trivial precision/recall/RDI against the gold set.

---

## Architecture Conformance — binding constraints (READ FIRST)

Verified against `docs/ARCHITECTURE.md` + `docs/BUILD_PLAN.md` invariants. Every fix obeys these. A fix that violates one is wrong even if the output looks right.

**A. Dependency rule (inward only).** Application/domain never import infrastructure. All adapters (OpenRouter LLM, router, citation, budget, ingest, parse) are built at the **composition root — `infrastructure/container.py`** (which already binds them) and consumed by **port** only (`LLMPort`, `RouterPort`, `CitationParserPort`, …). The container is invoked at the **outermost** layer (`interfaces/cli`); ports are injected **inward**: mediator → handler → `BillAnalysisFlow` → `Orchestrator` → `Lens`.
- **Delete the current dodges:** `bill_analysis_flow.py` `_lazy_ingest_adapter`/`_lazy_parse_adapter` and the in-handler `from leggie.infrastructure…` calls (`cli_handlers.py`) are lazy-import evasions of the dependency rule. Replace with constructor injection resolved from the container.
- `import-linter` must stay green after every phase.

**B. LLM autonomy inside stages only.** The model reasons *within* a lens/skeptic/CoVe stage. The stage sequence and the model **router** stay deterministic code + data (rules table) — never an LLM choosing control flow.

**C. Independence during analysis; blackboard during aggregation.** Lens workers stay **stateless and blind to each other** — VS is per-(lens, article), no shared memory, no reading a blackboard mid-analysis (protects diversity per spec §6). Cross-pollination (dedup / rerank / skeptic / CoVe) happens **only** in aggregation, on the **bounded blackboard** with schema-grounded, append-only mutations + Observer subscribers.

**D. Event-sourced aggregation (no in-place mutation).** Each aggregation transform emits an immutable `Event`; findings are never mutated in place. Replace the flow's `self._findings = survivors` reassignments (`bill_analysis_flow.py:144,164`) with append-only board mutations + events, so the run replays from the log.

**E. Traceability tuple on every Finding.** After F1 each Finding carries real `(stage, model, prompt_hash, seed, evidence, confidence)` provenance — not `model="rule-based-phase1"`. Enables audit + replay.

**F. Reproducibility despite a nondeterministic model.** Inject `seed` (already on `LLMRequest`) + low temperature where determinism is required; VS tail-sample uses the **injected seed/clock**, not global `random`. Replay is from **event-sourced cached responses** (`with_cache` + event log), not by re-calling the model. This is how the "reproducible" NFR survives an LLM.

**G. Validate at the boundary; keep the domain frozen.** LLM JSON is validated by a Pydantic **response DTO** at the infra/app boundary; malformed → typed error, never swallowed. The DTO maps to the **frozen** domain `Finding`. Response schemas live in `models/structured_output.py`.

**H. Functional core / imperative shell.** Pure logic — VS tail-select, dedup/clustering, evidence-substring check, confidence math — stays pure in `domain/` (`clustering`, `scoring`, `specs`). LLM I/O, budget, retries = imperative shell (`application`/`infrastructure`).

**I. Resilience + budget as a Decorator stack on the port.** Wrap the injected `LLMPort` at the container with retry → cache → budget-guard → rate-limit decorators. Application code just calls `llm.generate()`; cost ceiling + caching + backoff apply transparently. Budget guard is never imported by application.

**J. Chain-depth cap ≤ 4.** CoVe / improver revise loops on legal text bounded ≤ 4 transforms (O2 invariant).

**K. Small files.** Prompts → `application/agents/prompts/`; schemas → `models/structured_output.py`. Lens files stay < 400 lines.

---

## Part 3 — Fixes, phased

Each phase preserves the chosen architecture: lenses stay **Strategy** workers, the LLM stays behind the **`LLMPort`**, control flow stays deterministic, findings stay frozen, aggregation stays event-sourced. Exit gate must pass before the next phase. Each phase's **Arch:** line names the conformance rules it turns on.

### F0 — Parser correctness *(no API key needed; prerequisite for everything)*

**Files:** `infrastructure/parse/__init__.py`, `tests/unit/infrastructure/test_parse.py`

**Approach:**
1. **Line-anchor headings.** Only treat `Άρθρο N` as a heading when it starts a line (`^\s*Άρθρο\s+`, `re.MULTILINE`). In-body references (`του άρθρου 552`, `Άρθρο 43 του Συντάγματος`) are never line-initial after normalization.
2. **Number shape.** Bill articles are integers with an optional single Greek-letter suffix (`58Α`, `26Α`). Constrain the capture to `(\d+[Α-Ωα-ω]?)` — reject the multi-token garbage the current `[Α-Ωα-ω0-9]+(?:\s*[Α-Ωα-ω0-9]*)*` allows.
3. **Monotonic-sequence guard.** Real headings increase roughly 1→2→3. A candidate that jumps to 552 then back to 59 is a cross-reference → reject. Keep a running max; allow small gaps (repealed articles) but reject large backward/forward jumps.
4. **Cross-ref stop-list.** Reject a candidate immediately followed by `του ν.`, `του Κώδικα`, `ΚΠολΔ`, `ΚΠΔ`, `ΠΚ`, `ΑΚ`, `του Συντάγματος`, `της Οδηγίας`, `του Κανονισμού`.
5. **PDF newline repair in `_preprocess`.** Join mid-token line breaks (lowercase-`\n`-lowercase with no intervening punctuation) so "Άρθρο 64\nθρου" and split titles heal.
6. **Title = remainder of the heading line only** (already close; verify against healed text).

**Arch:** infrastructure-only, pure deterministic transform (`text → Document`); no port or dependency-graph change (rule H).

**Exit gate:** parsed article list for the real bill matches a hand-checked count; zero phantom numeric-only articles; titles intact. Add a parser test using a fixture with embedded cross-references proving they are *not* extracted as articles.

---

### F1 — Wire the real LLM (OpenRouter) into the lenses

**Files:** `application/agents/lens.py` (base), all 5 `*_lens.py`, `application/agents/orchestrator.py`, `application/workflow/bill_analysis_flow.py`, `application/services/verbalized_sampling.py`, `config/settings.py` (consume, don't add), new `application/ports/` usage of existing `LLMPort`.

**Approach:**
1. **Lens constructor takes the port.** `Lens.__init__(self, llm: LLMPort, model: str)`. Remove zero-arg construction. `analyze(article)` becomes a real LLM call, not regex.
2. **Prompt design per lens.** Each lens owns a system prompt = expert persona (constitutional-law / EU-law-transposition / fiscal-impact / implementation-feasibility / drafting-coherence) + explicit **IRAC output contract** + a hard instruction: *"Return only genuine issues. If the article raises none under your lens, return an empty list."* This is what kills D3 at the source. Include 1–2 few-shot examples grounded in Greek legislative style.
3. **Structured output.** Define a Pydantic response schema (`LensFindings(findings: list[IRACCandidate])`) and use the existing `generate_structured`. Each candidate carries issue/rule/application/conclusion, a **verbatim quote** field, severity, and a self-reported probability.
4. **Verbalized Sampling for real.** `verbalized_sampling.py` builds one prompt asking for *k* candidate findings each with a probability, parses the distribution, tail-samples (existing `sample_tail`). One call per (lens, article), not k calls. Keeps diversity per the architecture without k× cost.
5. **Inject down the stack — through the composition root, not around it.** The container (`infrastructure/container.py`) already binds `LLMPort` → OpenRouter `LLMAdapter`. The **outermost** layer (`interfaces/cli`) calls `container.configure_defaults()`, resolves `container.get(LLMPort)`, and injects it into the CQRS handler → `BillAnalysisFlow(orchestrator=Orchestrator(llm=...))` → each `Lens(llm=...)`. `Orchestrator.__init__(llm, lens_config, model)` passes the port to every lens it constructs (`orchestrator.py:95`). **No lens, orchestrator, flow, or handler imports infrastructure** — they name only `LLMPort`. This replaces default construction (`Orchestrator()`) and the lazy infra-import dodges.
6. **Response DTO → frozen Finding.** LLM returns a Pydantic DTO (`models/structured_output.py`), validated at the boundary; map it to the frozen domain `Finding`. Stamp provenance `(stage, model, prompt_hash, seed, evidence, confidence)` (rule E).
7. **Resilience/budget already available** — the container wraps `LLMPort` with the retry → cache → budget → rate-limit **Decorator stack** (rule I); lenses just call `llm.generate_structured()`.

**Arch:** rules A (inject via container, ports only), B (LLM inside stage), C (lenses stateless/blind, VS per-(lens,article)), E (provenance), F (seed + cached replay), G (DTO→frozen), K (prompts/schemas in own modules).

**Exit gate:** one lens (constitutional) produces a real, article-specific finding on the real bill via OpenRouter; empty list on an article with no constitutional issue (proves D3 gone). `import-linter` green. Unit tests mock `LLMPort` — no live calls in the test suite.

---

### F2 — Noise suppression

**Files:** `constitutional_lens.py` (+ peers), `application/services/rerank.py`, `application/services/reports.py`, `application/agents/skeptic.py`

**Approach:**
1. **Delete baseline findings.** Remove `_make_baseline_finding` and the "emit something" fallbacks. No finding is the correct output for a clean article.
2. **Dedupe.** In rerank/aggregation, collapse near-identical findings (same article + type + high issue-text similarity) to one, keeping the highest confidence. Cross-article dedupe for boilerplate.
3. **Report filtering.** Default report suppresses INFO; groups by severity then article; shows evidence quote + citation. Make the floor configurable.

**Arch:** dedup is a **pure** function in `domain/clustering/` (rule H); it runs as a **blackboard Observer** subscriber during aggregation, emitting append-only events — not in-place `self._findings=` reassignment (rules C, D).

**Exit gate:** report on the real bill contains only substantive findings; no "Δεν εντοπίστηκαν…" lines; no exact-duplicate findings.

---

### F3 — Real evidence + citation binding

**Files:** lenses, `application/services/cove_verifier.py`, `infrastructure/citation/` (existing parser), `application/ports/citation_parser.py`

**Approach:**
1. **Verbatim quote required.** The model returns the exact span it relied on → `Evidence.text_excerpt`. Validate it is a **substring of the article** (case/whitespace-normalized). Non-substring → hallucinated → drop the evidence or mark the finding unverified. This is the cheapest anti-hallucination gate and makes CoVe meaningful.
2. **Citation binding.** Run the existing deterministic citation parser over the article and over `finding.rule`; resolve ΦΕΚ/CELEX/ECLI against the index. A finding that cites a non-resolvable authority is flagged. No LLM in the fact-check loop — parser is pure/auditable.
3. **CoVe activates.** `cove_verifier` now has real drafts to verify (plan verification questions → independent check → revise), because there are real citations and quotes to check.

**Arch:** substring check + citation resolution are **pure Specifications** in `domain/specs/` — **no LLM in the fact-check loop** (rules H, B); revise loop bounded ≤ 4 (rule J); citation parser stays behind `CitationParserPort`.

**Exit gate:** every shipped finding's evidence quote is a real substring; every citation either resolves or is explicitly flagged; hallucinated citations → 0.

---

### F4 — Skeptic / rerank / cascade earn their place + cost control

**Files:** `application/agents/skeptic.py`, `infrastructure/router/`, `config/routes.yaml` (create), `infrastructure/budget_guard/`, `orchestrator.py`

**Approach:**
1. **Cascade routing (OpenRouter).** Consume the existing `CascadeSettings` (free → budget → premium). First-pass lenses run on the FREE/BUDGET tier; escalate a specific (article, lens) to PREMIUM only when the finding is high-severity or the model's probability is near the confidence floor. Chain-of-Responsibility, driven by `config/routes.yaml`.
2. **Budget guard enforced.** Wrap the fan-out so token/$ spend is checked against `BudgetSettings`; on warning, apply the configured degrade strategy (fewer paths → fewer lenses → cheaper tier). Currently configured but not enforced on the analysis path.
3. **Skeptic does real work.** Fresh-context, blind-to-author review of surviving findings with typed gates (numeric/temporal/obligation/factual); adjust confidence, refute the weak. Now meaningful because inputs vary.

**Arch:** router behind `RouterPort`, decision from the **rules table** not an LLM (rule B); budget guard is a **port decorator** wired at the container, never imported by application (rules A, I); skeptic runs as a blackboard Observer emitting events (rules C, D).

**Exit gate:** one bill completes under the $ ceiling with enforcement proven (log shows tier escalation + any degrade); skeptic refutes a measurable fraction; event log is replayable.

---

### F5 — Eval wiring *(fold in alongside F1)*

**Files:** `cli_handlers.py:88`, `infrastructure/persistence/eval_harness.py`

Run the real flow per gold-set bill and score actual findings (not `[]`). Report precision/recall/F1/RDI. This becomes the regression metric guarding F2–F4.

---

## Part 4 — Cost & model tiering (OpenRouter)

- **Segment first (F0).** Fewer, real articles = fewer calls. The phantom articles are wasted spend today.
- **Granularity.** Consider section-level or batched-article prompts where a lens can judge several short articles in one call; keep per-article for long/complex ones.
- **Tier map (from `CascadeSettings`, tune ids):** FREE `google/gemini-2.5-flash:free` → BUDGET `openai/gpt-5.6-luna` → PREMIUM `openai/gpt-5.6-luna-pro` (or an Anthropic premium id). First pass cheap; escalate on signal only.
- **Cache (`with_cache`).** Identical (article, lens, prompt_hash) never re-calls — helps re-runs and eval.
- **Ceiling.** `LEGGIE_BUDGET_max_cost_per_run` enforced in F4; degrade before blow-through.

Rough envelope: ~140 real articles × 5 lenses × 1 VS call ≈ 700 calls/bill first pass, most on FREE/BUDGET, a minority escalated. Well inside a few dollars with caching.

---

## Part 5 — Config & secrets

- Add `OPENROUTER_API_KEY` via env `LEGGIE_LLM_OPENROUTER_API_KEY` (settings already expects it, `settings.py:21`) in `.env` (git-ignored).
- **Fail fast:** when `analyze` runs without a key, raise a clear `LLMConfigurationError` at startup — do not silently fall back to stubs.
- Create `config/routes.yaml` for the cascade rules table (`CascadeSettings.rules_path`).

---

## Part 6 — Test / eval impact

- **~30 existing unit tests assert stub behavior** (e.g. `test_constitutional_lens` expects the baseline finding). These rewrite to: mock `LLMPort`, assert structured parsing, assert empty-on-no-issue, assert quote-substring validation. Expect the suite to shrink then regrow.
- **No live API calls in unit tests** — always mock the port. One optional integration test, marked and skipped without a key, hits OpenRouter on a tiny fixture.
- **Eval harness becomes the north star** — track precision/recall/RDI on the gold set across F1→F4 so noise reduction is measured, not vibes.

---

## Sequencing summary

```
F0 Parser  ──►  F1 LLM wiring  ──►  F2 Noise  ──►  F3 Evidence  ──►  F4 Skeptic/Cascade/Budget
(no key)        (OpenRouter)       (dedupe)       (quotes+cites)   (cost + real filtering)
                     │
                     └─► F5 Eval wiring (parallel)
```

F0 is the unblocker and needs no key. F1 is the value unlock. F2/F3 convert raw model output into a clean, trustworthy report. F4 makes it cheap and adds the adversarial layer. Do not skip F0 — a real LLM over phantom segments is still garbage.

---

## Part 7 — Model VFM (value-for-money) selection

Research basis: **GreekMMLU** (arXiv 2602.05150) — frontier ≫ open-weight on Greek; Greek-adapted models help. **GreekBarBench** (arXiv 2505.17267) — Greek legal reasoning is hard (best < 95th-pct expert), citations must be checked deterministically. → **Greek competence is the binding constraint, not raw intelligence.** All ids below are **verified real** on `openrouter.ai/api/v1/models`.

| Task | Model (real id) | ~$/1M in | Cascade → | Rationale |
|---|---|---|---|---|
| lens_analysis (cost driver) | `google/gemini-2.5-flash` | 0.30 | `google/gemini-2.5-pro` | Greek-capable, cheap, fast; 80% of spend |
| verbalized_sampling | `google/gemini-2.5-flash` | 0.30 | `gemini-2.5-pro` | diversity, one call |
| adversarial_critic (skeptic) | `anthropic/claude-sonnet-4.6` | 3.0 | `claude-opus-4.8` | must catch errors lens missed |
| evidence_verification | `google/gemini-2.5-pro` | 1.25 | — | quality-critical, low volume |
| report_generation | `google/gemini-2.5-pro` | 1.25 | — | long-ctx Greek prose, 2 calls |
| classification / summarization | `google/gemini-2.5-flash-lite` | 0.10 | `gemini-2.5-flash` | trivial, high volume |
| embeddings (retrieval) | `greek_legal_bert_v2` + `BGE-M3` | — | — | domain + general multilingual |

**Cheap open alt:** `deepseek/deepseek-v3.2` ($0.28) — near-frontier open weights, but verify Greek legal quality before trusting on the lens pass.

**Codified** in `config/routes.yaml` + `settings.py` cascade tiers (FREE `gemini-2.5-flash-lite` → BUDGET `gemini-2.5-flash` → PREMIUM `gemini-2.5-pro`).

**⚠ Invalid-id trap (found in verification):** placeholder ids (`openai/gpt-5.6-luna`, `…-terra`) and suffixed/date-stamped ids (`…-flash:free`, `claude-sonnet-4-20250514`) **404/400 on OpenRouter** → the resilience stack silently regex-falls-back → the whole report is garbage with no error. **Every model id must be validated against the live `/models` list.**

### Bake-off protocol (before locking the lens model)
Run the 5-model shortlist through the eval harness on the 2–3 gold bills; pick per-stage by measured precision/recall/cost. Eval-driven, matches the architecture.
- Candidates: `google/gemini-2.5-flash` · `google/gemini-3-flash-preview` · `deepseek/deepseek-v3.2` · `anthropic/claude-sonnet-4.6` · `openai/gpt-5-mini`
- Metrics: precision / recall / F1 / RDI (already in the harness) + $/bill from the budget guard.
- Winner = best F1 within the cost ceiling; escalate-on-signal to the premium tier for the rest.
