# Leggie — Optimized Implementation Plan (v1)

> Reviewed & optimized from `Leggie_Initial.md`.
> Decisions locked: **base = fork weebot**, **scope = Lean MVP (Phases 0–3)**, **providers = Anthropic + OpenAI + Google, cost-constrained (cascade FREE→BUDGET→PREMIUM)**.

---

## 0. What changed vs the initial spec

The original spec is a strong *vision* but an unbuildable *v1*: ~25 personas × 20 reasoning paths × debate × adversarial × knowledge-graph × learned-router × continuous-learning, all at once. That stacks 5 hard research problems, costs ~40M tokens/bill, and has no way to measure quality.

**Optimizations applied:**

| Area | Initial spec | v1 decision | Why |
|---|---|---|---|
| Personas | 25+ | **5 fixed lenses** | Diversity value collapses fast; 5 diverse lenses catch most issues |
| Verbalized Sampling | 20 paths | **3–5 paths** | Cost; diminishing returns past ~5 |
| Debate | Full multi-round | **Deferred (post-MVP)** | Adversarial critic gives most of the benefit cheaper |
| Knowledge Graph | v1 Stage 3 | **Deferred to v3** | Parse + retrieval index covers 90% of need |
| Router | Learned/telemetry | **Static YAML rules table** | Learned router needs eval data that doesn't exist yet (chicken/egg) |
| Continuous learning | v1 | **Deferred** | Same reason |
| Reports | 10 types | **2 (Exec Summary + Article-by-Article)** | Rest are formatting variants, add later |
| **Evaluation** | Last / implicit | **FIRST (Phase 0)** | Can't prove "superior to expert panel" without a gold-set + harness |
| **Citation verification** | "optional" evidence | **Mandatory gate (Phase 3)** | Legal AI dies on hallucinated citations |

**Determinism clarified:** deterministic = pipeline structure, prompts, seeds, cache keys, report assembly. NOT model token output. Stated explicitly to kill the NFR contradiction.

---

## 1. Reuse map (fork weebot)

| Need | weebot module | Action |
|---|---|---|
| LLM calls | `weebot/infrastructure/llm/langchain_adapter.py` | Reuse; wrap 3 providers |
| Resilient adapters / cascade / router | locate in `infrastructure/llm`, `application/services`, `application/strategies` | **Phase 0 task: find exact modules**, port `ModelCascadeService` |
| Eval harness | `application/eval/{eval_runner,judges}.py` + `infrastructure/scoring/*` | Reuse directly for gold-set scoring |
| Structured output | `models/structured_output.py` | Extend with `Finding`, `Evidence`, `Article` Pydantic models |
| Task decomposition | `application/agents/{planner,parallel_planner}.py` | Adapt for article→lens fan-out |
| Lens execution | `application/agents/structured_executor.py` | Adapt as per-lens analyst |
| Audit / reproducibility | `infrastructure/*/event_store.py`, `event_bus.py` | Reuse for immutable finding trail |
| Safety-tier pattern | `weebot/core/bash_guard.py` | Mirror as **budget guard** (token/$ tier ceiling) |
| Persistence | SQLite + WAL pattern | Reuse; L1 mem / L2 SQLite cache |
| CLI / Web / MCP interfaces | `weebot/interfaces/` | Reuse skeleton |

**Fork approach:** copy weebot skeleton into `Leggie/`, strip weebot-specific domains (browser/email/discord/linkedin), keep the Clean-Arch spine + llm + eval + cqrs + persistence.

---

## 2. Target architecture (v1)

```
Ingest → Parse (Άρθρο N / παρ.) → per-Article fan-out:
   ├─ 5 fixed lenses: Constitutional | Legal-coherence | Economic
   │                  | Implementation | EU-&-GDPR
   ├─ each lens: 3–5 sampled reasoning paths → cluster → dedupe
   ↓
   Findings pool → Adversarial critic (attack each finding; survivors gain confidence)
   ↓
   survivors → Evidence bind (retrieve + VERIFY citations vs corpus; drop unverifiable)
   ↓
   Rerank (severity × confidence × novelty) → cross-article dedupe
   ↓
   Improvement engine (per surviving finding) → 2 reports
```

**Router = static YAML** `{task_type → {model, tier, max_tokens, latency}}`, cascade FREE→BUDGET→PREMIUM. Swap to learned later.
**Budget guard:** per-run token/$ ceiling, degrade gracefully (fewer paths → fewer lenses → Haiku-only) before hard stop.
**Every finding:** immutable, versioned, traceable to (model, prompt-hash, seed, evidence, counter-evidence, confidence).

---

## 3. Phased roadmap

### Phase 0 — Foundation + Eval  *(do FIRST)*
- [ ] Fork weebot skeleton → `Leggie/`; strip non-core domains; `pip install`, tests green
- [ ] Locate & port: LLM cascade/router, resilient adapter, event store, cache
- [ ] Wire 3 providers (Anthropic, OpenAI, Google) behind provider-agnostic port
- [ ] Ingest: PDF / DOCX / HTML → clean text
- [ ] Parser: Greek legal structure (Άρθρο N, παράγραφοι, εδάφια, παραπομπές)
- [ ] **Eval harness + gold-set**: 2–3 real Greek bills with *Επιστημονική Υπηρεσία Βουλής* reports as ground truth; scorer = precision/recall of findings vs known issues (reuse `eval_runner`+`judges`)
- [ ] Budget guard (token/$ tier ceiling)

### Phase 1 — Single-lens vertical slice
- [ ] 1 lens (Constitutional), 1 path, no adversarial
- [ ] End-to-end: 1 article → 1 finding w/ citation
- [ ] Prove the loop; measure vs gold-set (baseline number)

### Phase 2 — Ensemble
- [ ] 5 lenses × 3–5 sampled paths (Verbalized Sampling — paper already in weebot)
- [ ] Cluster + dedupe within lens; rerank
- [ ] Measure precision/recall lift vs Phase 1

### Phase 3 — Adversarial + Evidence  *(credibility phase)*
- [ ] Adversarial critic layer (attack each finding; drop the falsifiable)
- [ ] Citation retrieval + **verification** against ingested corpus (Σύνταγμα, EUR-Lex, legislation.gr)
- [ ] Kill every hallucinated citation; confidence recalibration
- [ ] Cross-article dedupe

### Phase 4 — Improvement engine + Reports  *(MVP completion)*
- [ ] Suggestion generation per finding (minimal change / reform)
- [ ] 2 reports: Executive Summary + Article-by-Article

### Post-MVP (Phase 5+)
Knowledge graph · debate rounds · learned/telemetry router · interactive chat · continuous learning · remaining report types · more personas.

---

## 4. Corpus sources (Phase 0/3)
- **legislation.gr** / ΦΕΚ (ΕΤ) — current Greek law
- **EUR-Lex** — EU directives/regulations (open API)
- **Σύνταγμα** — static, embed once
- **ΣτΕ / ΑΠ νομολογία** — hard (no clean open API); Phase 3 best-effort, flag when unavailable rather than hallucinate
- **Ground truth**: Επιστημονική Υπηρεσία Βουλής reports (these *are* the expert panel to beat)

---

## 5. Open risks
1. **Cost blowup** — mitigate: cascade + budget guard + cache from day 1; cap paths×lenses.
2. **Citation hallucination** — mitigate: Phase 3 mandatory verification gate; unverifiable = dropped or flagged.
3. **Greek legal parsing** — non-trivial format; validate parser on gold-set bills early.
4. **Eval subjectivity** — "problem found" is fuzzy; use Βουλή reports as anchored truth + judge rubric.
5. **Νομολογία access** — no open API; scope honestly, don't fake it.

---

## 6. Research-backed optimizations

Sourced from the curated paper corpus already in `weebot/` (web search backend was down; these papers are higher-signal anyway). Each maps to a concrete plan change.

### O1. Citation verification = CoVe (factored)  → Phase 3 *(biggest win)*
`Chain_of_verification.txt`. Replace naive "check the citation" with the 4-step Chain-of-Verification:
1. Draft finding + citations
2. **Plan** verification questions (one per cited article/case)
3. **Execute independently — factored**: verifier answers each Q in a *fresh context, blind to the draft*, so it can't rubber-stamp its own hallucination
4. Revise/drop finding per verification result

The factored variant is the key: independent verification questions return more accurate facts than re-reading the draft. This is the single highest-leverage change for legal credibility.

### O2. Cap chain depth; parallel > sequential  → architecture-wide
`LLMs_Corrupt_Your_Documents_When_You_Delegate.txt` (DELEGATE-52). Frontier models (Claude 4.6 Opus, GPT5.4, Gemini 3.1 Pro) **corrupt ~25% of document content over 20-step delegated chains** — sparse, severe, silent, compounding, and **not fixed by using a bigger model**. Implications:
- Keep every agent chain **short (≤3–4 hops)**; hard-cap depth.
- **Never** sequentially edit bill text through many hops. The Improvement engine (Phase 4) emits a *one-shot* suggestion + verify — no long multi-edit chains on legal text.
- Prefer the parallel independent single-pass fan-out (already the design) over long sequential delegation. Verify/checkpoint at each transform.

### O3. Verbalized Sampling done right  → Phase 2
`Verbalized_Sampling...txt`. Do **not** fire 5 separate calls hoping for diversity — they mode-collapse to the same finding (wasted tokens). Instead: **one prompt asks the model to verbalize a distribution of k candidate findings, each with a probability**, then sample from the tails. Cheaper (fewer calls) *and* more diverse. Recovers ~66.8% of lost diversity, +25.7% human-eval. Benefit scales with model capability → use a capable model for the sampling step. Params: k≈5, tail-weighted selection.

### O4. Confidence ≠ correctness  → Phase 12 / scoring
`Disentangling_Honesty_from_Accuracy...txt` (MASK). Larger models are more *accurate* but will **misstate beliefs under pressure** (low honesty). So a finding's self-reported confidence is **not** evidence of truth. Actions:
- Derive confidence from *independent verification* (O1), not self-report.
- **Calibrate** confidence against the gold-set: does `confidence > 0.9` actually correlate with true findings? If not, recalibrate.

### O5. Sycophancy guard in adversarial/debate  → Phase 3 critic (+ post-MVP debate)
Same MASK finding: models cave under adversarial pressure. So:
- Adversarial critic runs in **independent/fresh context, blind to the author** — not the same agent flipping its own answer.
- Defer full debate (post-MVP); when added, watch for agreement-collapse (all agents converging to please), not genuine consensus.

### O6. Cost cuts (cross-cutting, compounding)
- **Prompt-cache the invariant context.** Article text + Σύνταγμα + system framing are **identical across all 5 lenses × k paths** → cache once, pay once. Largest practical saving; not in the original spec.
- **Semantic dedup BEFORE rerank.** Cluster findings by embedding, drop dupes, rerank only cluster representatives → cuts reranker load.
- **Verify only survivors.** CoVe runs on adversarial survivors, not every raw finding.
- Net: O3 (fewer calls) + O6 (cache + targeted verify) materially lower the cost-per-bill vs the naive plan.

---

## 7. Live 2026 source validation & upgrades

Pulled from arXiv (June–July 2026) via raw HTTPS. First: the evidence that the risk is real.

**Threat confirmed (citation hallucination is existential):**
- Legal AI hallucinates at **~52% aggregate** (LegalHalluLens, arXiv:2606.18021).
- **1,000+ real court filings** with fabricated citations, **growing year-over-year; newer models do NOT hallucinate less** (Who Checks the Citations, arXiv:2606.21155) — echoes DELEGATE-52: scale won't save us.
- Best automated citation-checker (GPT-5, agentic) = **82.8% recall / 60.5% F1**, but **16.9 steps/excerpt** and crippled by restricted DB access. → verification is expensive + needs real corpus access. Confirms O1 + "verify only survivors" (O6) + the νομολογία-access caveat.

**Concrete upgrades (each maps to a phase):**

### U1. Deterministic citation parser + normalized IDs  → upgrades O1 / Phase 3
`From Judgments to Issues` (arXiv:2607.03325) beats pure-LLM verification: a **dedicated citation parser** (Linkoln) extracts references from the *source text*, normalizes to standard IDs (URN-NIR / ECLI / CELEX), then **diffs model citations vs parser-extracted** = hallucination filter. Port to Greece: parser for **ΦΕΚ/ΕΤ refs, CELEX (EU), ECLI (case law)**; a finding's citation is valid only if it resolves against the parser index. Deterministic → cheap, auditable, no model-in-the-loop for the fact check. Their model choice (DeepSeek V3, "capable yet cost-efficient" for 330k docs) also validates the cost-cascade.

### U2. IRAC finding schema  → extends `structured_output.py` / all phases
Same paper structures every legal issue as **IRAC (Issue · Rule · Application · Conclusion) grounded in the legal syllogism**. Adopt IRAC as the `Finding` Pydantic schema. Forces each finding to name the rule, apply it, conclude — kills vague "this might be problematic" output and makes findings machine-checkable.

### U3. Typed hallucination + Risk Direction Index  → changes the eval metric / Phase 0
LegalHalluLens (arXiv:2606.18021): **do not aggregate.** Type errors into **{numeric, temporal, obligation/entitlement, factual}** — the 52% average hides **38–40pp** gaps between categories. Track a **Risk Direction Index**: is the system *inventing* false problems or *omitting* real ones? For a bill analyzer, invented problems (false positives) and missed problems (false negatives) are *both* fatal and must be scored **separately**, per finding-type. Update the Phase 0 scorer accordingly.

### U4. Calibrated Skeptic debate — cheaply rehabilitated  → merges into O5 / Phase 3
Same paper: a **typed debate with a single Skeptic challenger + asymmetric gates targeted at measured failure modes** cut fabricated detections **45% with only a 4B backbone**, matching commercial APIs. This resurrects debate for v1 at low cost: the adversarial critic (O5) becomes a **calibrated Skeptic** whose attack strength is gated per finding-type (harder gates where U3 shows the model is weakest). Cheaper and better than generic debate; no full N-way debate needed for MVP.

### U5. Hybrid retrieval + inject-vs-navigate rule  → sharpens O6 / Phase 3 corpus
- **Hybrid (dense + BM25) retrieval** consistently beats pure-dense or pure-sparse for recall/ranking; good retrieval lets smaller models match bigger ones (Healthier LLMs, arXiv:2607.06641; As We May Search, arXiv:2606.29652). → Leggie retrieval = hybrid, not pure vector.
- **Caching-crossover rule** (Inject or Navigate, arXiv:2607.05764): semantic-retrieve+rerank ties full-corpus injection at **17–30× fewer tokens**; cached injection is cheaper *only while corpus < ~10× the retrieval payload*. → **Inject+cache the bill** (small, reused across 5 lenses × k paths); **retrieve** the large external corpus (legislation/EU/case-law). Quantitative decision rule for O6.

### U6. Eval judge: ensemble + per-dimension trust  → Phase 0 harness
- **Ensemble-of-judges** (multi-model arena, majority vote) hits **Cohen κ=0.92** vs human experts (SynthAVE) → use a small judge panel for gold-labeling, not one model.
- **Per-dimension reliability**: LLM-judge agrees with humans on **faithfulness/completeness**, is **unreliable on factual-consistency** (Healthier LLMs). → judge scores structure/faithfulness; **facts go to the deterministic citation parser (U1)**, not the judge.

### U7. Robustness / messy-input testing  → Phase 0 gold-set
`Legal Reasoning Is Not Lawyering` (arXiv:2606.23716): benchmarks measure the *upper bound* (expert-clean inputs); real inputs are noisy. → Gold-set must include **messy real bills** (OCR noise, mid-text amendments, buried cross-refs) + a small **perturbation test** on the parser. Don't certify on clean text only.

### U8. Confidentiality NFR + on-prem swap  → NFR / provider abstraction
Three data-leak surfaces — model params, context window, RAG store (Privilege & confidentiality, arXiv:2607.05479). Pre-publication Greek legislative drafts can be **embargoed/sensitive**. **CHARLIE** (arXiv:2607.05428) = on-prem multi-agent RAG blueprint with traceability/auditability (matches Leggie's auditability NFR); **As We May Search** shows local 7B + HNSW stays within ~4 pts of cloud. → v1 on public bills = cloud OK, but keep provider + retrieval-store abstraction so a **local/on-prem backend** can be swapped in for sensitive drafts. Add to NFR list.

### U9. Abstention gate under uncertainty  → Phase 4 emission
Clinician's Veto (arXiv:2606.25108) + active-rejection (MMAgent-R², arXiv:2607.07383): gate output on **calibrated per-prediction confidence** — emit competing-options when ambiguity is *aleatoric*, **abstain/escalate to human** when *epistemic*. → Leggie emits findings above a calibrated threshold; below it, flag **"needs human expert"** rather than ship low-confidence noise. Operationalizes O4.

*(Deferred/low-priority: internal-artifact correctness detection (arXiv:2606.20929) needs logit access — infeasible with closed API models. Post-MVP research only.)*

---

## 8. Concrete data sources & stack (researched)

Fills the §4 gap — these are real, mostly-free, documented endpoints. Replaces "retrieve from somewhere" with actual wiring.

### Greek primary legislation
- **data.gov.gr → `gov-et-laws`** ("Δημοσιευμένοι νόμοι από το Εθνικό Τυπογραφείο") — published-laws open dataset. Bulk ingest source.
- **search.et.gr** — ΦΕΚ full-text search UI; all ΦΕΚ free. No official REST API found → scrape/structured-fetch, or use the data.gov.gr dataset as the API-shaped path.
- **Nomothesia** — Greek legislation as **linked open data** (RDF); also the training corpus behind GreekLegalBERT. Structured source for articles/refs.
- **api.mitos.gov.gr** (ΕΜΔΔ / Mitos) — National Registry of Administrative Procedures, REST. Feeds the *administrative-burden* lens (what procedures a bill actually touches).

### Government acts / implementation reality
- **Διαύγεια `diavgeia.gov.gr/luminapi/opendata`** — REST OpenData API, JSON (append `.json`). Decisions, ministerial acts, org structure, search. GitHub client samples (java/php); help at `/api/help`. Feeds *implementation / enforcement* lens.

### EU law (the EU/GDPR lens)
- **EUR-Lex CELLAR** — **public SPARQL endpoint (no auth)** + RESTful API for metadata + content download. IDs = **CELEX**; model = CDM (RDF/OWL). Limits: 60s query timeout, **<5 concurrent, backoff on 429/503, paginate OFFSET/LIMIT**. Port the `eurlex` R package logic (michalovadek) as the client blueprint.

### Case law / νομολογία (still the hard gap)
- **ECLI** is the identifier scheme. Greek courts (ΣτΕ, ΑΠ, adjustice.gr) have **no clean open API** — EU ECLI search + selective scraping only. Honor the earlier caveat: **flag-when-unavailable, don't fabricate** (this is exactly where the ~52% hallucination lives).

### Parliament + ground truth (eval gold-set)
- **`hellenicparliament.gr/api.ashx`** — REST, JSON/XML, `q` param selects method; bills with recorded voting date.
- Open-data portals: `hellenicparliament.gr/opendata`, `diafaneia.hellenicparliament.gr/OpenData`.
- **Επιστημονική Υπηρεσία Βουλής** reports + **Hellenic OCR Team** linked-open-data / XML → the **expert ground truth** to beat (Phase 0 gold-set).

### Embeddings / retrieval models (Greek)
- **GreekLegalBERT v2** (`spyrosbriakos/greek_legal_bert_v2`) — trained on RAPTARCHIS47k (~47k Greek legal texts, 1834–2015). Primary Greek-legal retrieval/rerank encoder.
- **GreekLegalRoBERTa** (arXiv:2410.12852, 4 variants, Nomothesia-trained) — alternative.
- **GREEK-BERT** (`nlpaueb/bert-base-greek-uncased-v1`) — general Greek fallback.
- **nlpaueb/legal-bert-base-uncased** — English legal, for EU English texts.
- **multilingual-e5** — for the **hybrid (dense+BM25)** leg per U5.
- Generation stays on frontier APIs (Claude/GPT/Gemini); these encoders are retrieval-only. No fine-tuning (arXiv:2607.05582: prompting beats fine-tuning for statutory terms).

### Citation parser (U1) grounding
Build the deterministic parser against these ID schemes: **ΦΕΚ issue/year/number** (search.et.gr / gov-et-laws), **CELEX** (EU), **ECLI** (case law). A finding's citation is valid only if it resolves here.

### Router graduation target
- Start **static YAML rules**. Graduate to a **RouteLLM matrix-factorization router** (arXiv:2406.18665, framework `lm-sys/RouteLLM`): benchmarked **~85% cost cut at 95% GPT-4 quality, routing only ~14% of queries to the strong model** (MT-Bench). Fits the cost-constrained mandate.
- **Caution**: router fragility (arXiv:2504.07113) — keep a confidence floor + hard fallback to premium on low-confidence routes.

---

## 9. Immediate next action
Confirm this plan → begin **Phase 0**: fork weebot into `Leggie/`, locate the cascade/router modules, stand up ingest+parse+eval-harness. Nothing else until the eval harness can score a bill.
Bake in from commit 1: **O2** (chain-depth cap), **O6/U5** (cache-bill / retrieve-corpus, hybrid retrieval), **U2** (IRAC `Finding` schema), **U3** (typed + direction-separated scorer). First data wiring: **EUR-Lex CELLAR** + **data.gov.gr gov-et-laws** + **hellenicparliament.gr** gold-set. Build the **U1 deterministic citation parser** early — cheapest, highest-leverage anti-hallucination component.
