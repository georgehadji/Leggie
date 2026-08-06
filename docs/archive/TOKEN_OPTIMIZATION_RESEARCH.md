# Token Consumption Research — Leggie

> **Status:** Superseded by `docs/PRODUCTION_READINESS_PLAN.md`. Kept for reference only. Active work follows the phased plan in that document.


**Date:** 2026-07-28
**Scope:** where tokens actually go in an `analyze` run, which optimizations are available, and which of the ones already claimed in the codebase do not work.
**Companion doc:** [COST_OPTIMIZATION.md](COST_OPTIMIZATION.md) covers operational levers (subset runs, model shopping, budget caps). This document covers the *mechanisms*: caching, prompt shape, call-count amplification, and accounting.

Everything numeric below is measured from this repo unless marked **(estimate)**.

---

## 1. Measured baseline

### 1.1 Prompt shape (tiktoken `o200k_base`, used as a proxy for the Gemini tokenizer)

| Component | Tokens |
|---|---|
| `constitutional` SYSTEM_PROMPT | 451 |
| `legal_coherence` SYSTEM_PROMPT | 366 |
| `economic` SYSTEM_PROMPT | 373 |
| `implementation` SYSTEM_PROMPT | 377 |
| `eu_gdpr` SYSTEM_PROMPT | 388 |
| USER_PROMPT_TEMPLATE (per lens) | 145–181 |
| `LensFindings` JSON schema (sent as `response_format`) | 166 |

### 1.2 Article sizes (`parsed.json`, 91 articles)

| Statistic | Tokens |
|---|---|
| min | 67 |
| p25 | 88 |
| **median** | **110** |
| p75 | 237 |
| max | 877 |
| total (whole bill) | 17,255 |

### 1.3 The central fact

**The article is the small part of the prompt.** A median lens call is roughly:

```
system 391 + user template 157 + schema 166 = 714 tokens of fixed boilerplate
                                   + article 110 tokens of actual payload
                                   ≈ 890 tokens input
```

For a full 5-lens run over 91 articles (455 lens calls, no retries, no cascades):

| | Tokens | Share |
|---|---|---|
| Fixed boilerplate re-sent per call | 455 × 714 ≈ **325,000** | **79%** |
| Article content | 5 × 17,255 ≈ 86,000 | 21% |
| Total lens input | ≈ 411,000 | |

Four out of every five input tokens in the analysis stage are the same bytes sent over and over. That is the target.

### 1.4 Evidence from a real run (`subset2.log`, 2026-07-17)

| Signal | Count |
|---|---|
| HTTP POSTs to `/chat/completions` | 90 |
| Tier cascades fired | 8 (all `(empty)`) |
| `Failed to parse structured response` | 13 |
| Truncation retries (`finish_reason=length`) | 5 |
| Skeptic verdicts | 3 |
| CoVe results | 8 |
| Findings produced | 7 |
| **Guard-recorded usage** | **95,196 tokens / $0.0954** |

90 network calls produced 7 findings. Retries, cascades, and the structured-output ladder account for a large share of those calls — see §3.

---

## 2. Caching: what is claimed vs. what is true

### 2.1 `transforms: ["cache"]` does not enable prompt caching

`leggie/infrastructure/llm/adapters/openrouter.py:68` sends:

```python
"transforms": ["cache"],
```

and the class docstring claims `Prompt caching via transforms: ["cache"] (O6 cost optimization)`.

OpenRouter's `transforms` parameter is for **message transforms**, and the documented value is `middle-out` (context compression for over-length prompts); `transforms: []` disables it. `"cache"` is not a documented transform value. OpenRouter's prompt caching is a separate mechanism entirely: automatic for OpenAI / DeepSeek / Grok / Moonshot / Groq / Gemini (implicit) / Z.AI, and explicit via `cache_control` breakpoints for Anthropic, Qwen, and Gemini.

**Consequence:** the parameter buys nothing, and because supplying an explicit array replaces the default, it may also be switching `middle-out` off as a side effect. Either way the docstring is false and should not be trusted as "caching is handled".

### 2.2 Prompt caching cannot currently trigger anyway

Provider minimum prefix lengths (OpenRouter prompt-caching docs):

| Provider | Minimum cacheable prefix |
|---|---|
| Google Gemini 2.5 **Flash** | 1,024 tokens |
| Google Gemini 2.5 **Pro** | 4,096 tokens |
| OpenAI | 1,024 tokens |
| Anthropic Claude | 1,024–4,096 (model-dependent) |
| DeepSeek | not specified in docs |

A lens call's constant prefix is the system prompt: **366–451 tokens**. Even the whole request (≈890 tokens median) sits below the 1,024-token Flash floor. The `adversarial_critic` route runs on `gemini-2.5-pro`, whose floor is 4,096 — nowhere near reachable.

**So prompt caching is unavailable at the current prompt shape, regardless of configuration.** Any plan that starts with "turn on caching" is wrong for this codebase until the prompt shape changes. This is the single most important finding in this document.

Two ways to make caching reachable, if it is wanted:

- **Batch articles per call** (§3.1) — pushes the request over the floor *and* amortizes the boilerplate, which is the bigger win on its own.
- **Route the high-volume lens work to a provider with a lower/no floor.** DeepSeek's automatic caching has no stated minimum. This is a model-quality decision, not just a cost one — see COST_OPTIMIZATION.md on Greek-legal competence being unproven outside the Gemini family.

### 2.3 Response caching is dead code and broken

`leggie/infrastructure/llm/decorators.py:34`:

```python
def with_cache(max_size: int = 100):
    """Decorator: simple LRU cache for LLM responses keyed by prompt hash."""
    return functools.lru_cache(maxsize=max_size)
```

Three problems:
1. It is **never applied** to anything — `grep` finds only the definition and the `__all__` export.
2. `functools.lru_cache` on an `async def` caches the **coroutine object**, not the result. A second cache hit re-awaits an exhausted coroutine and raises `RuntimeError: cannot reuse already awaited coroutine`.
3. `LLMRequest` is a frozen dataclass containing a `dict` (`response_format`), so it is unhashable — `lru_cache` would `TypeError` on the first call regardless.

This is the highest-leverage *safe* fix in the list: a real content-addressed cache turns re-runs, crash-resumes, eval sweeps, and smoke tests into $0 operations.

---

## 3. Call-count amplification (the hidden multiplier)

Input-token arithmetic assumes one call per lens per article. The code does not guarantee that.

### 3.1 Cascade fires on *legitimately empty* findings

`leggie/application/agents/orchestrator.py:161-171`:

```python
findings = await lens.analyze(article)
if findings:
    return findings
# Empty findings from LLM lens: cascade on low confidence
if attempt < max_retries - 1 and self._router:
    next_result = await self._router.cascade("lens_analysis", tier, "empty_findings")
```

An empty result is treated as a failure signal and escalates **budget → premium**. But for a clean article, empty is the *correct* answer, and the lens prompts explicitly instruct the model to return `findings: []` rather than filler. So the pipeline systematically pays a premium-tier re-run for every article that is fine.

`subset2.log` confirms it firing: `cascade: constitutional premium → google/gemini-2.5-pro (empty)`, 8 times in a partial run.

**(Estimate)** If 60% of the 455 lens calls come back empty, that is ~273 extra premium calls. At `x-ai/grok-4.5` ($2.00 in / $6.00 out) and ~900 in / ~400 out tokens: ≈ 273 × ($0.0018 + $0.0024) ≈ **$1.15 per run, on articles with nothing wrong with them** — against a default `max_cost_per_run` of $5.00.

Fix: cascade on *exceptions and parse failures only*. If empty-result escalation is wanted as a recall mechanism, it must be justified by an eval delta (does tier-2 actually find things tier-1 missed?), not assumed.

### 3.2 The structured-output ladder can bill four calls for one logical call

`leggie/infrastructure/llm/__init__.py:192-276` runs: (1) `json_schema` strict → (2) `json_object` → (3) truncation retry at doubled `max_tokens` → (4) LLM repair round.

Attempt 2 fires on **any** `LLMError | ValueError` from attempt 1, not only the 400 "model doesn't support json_schema" case that the fallback exists for. A parse failure on an otherwise successful attempt 1 therefore buys a second full paid call that asks the same question in a weaker mode. `subset2.log` shows 13 parse failures and 5 truncation retries, with zero `json_schema rejected` events — i.e. every one of those ladders ran the json_object step for no structural reason.

Cheaper ordering:
- Attempt 2 (`json_object`) only on 400/unsupported-schema, which is what it was built for.
- On parse failure with a JSON skeleton present, go straight to local repair (`StructuredResponseParser.try_repair` already exists) before spending a token.
- On `finish_reason == "length"`, jump straight to the doubled-token retry — do not spend a blind json_object call first.

### 3.3 Route `max_tokens` is never applied

`config/routes.yaml` sets `lens_analysis.max_tokens: 6144` and, with a long explanatory comment about `gemini-2.5-pro` burning ~1,900–2,200 reasoning tokens per verdict, `adversarial_critic.max_tokens: 8192`.

Neither reaches a request:
- `Lens._call_llm_structured` (`lens.py:97-102`) builds `LLMRequest` **without** `max_tokens` → falls back to the dataclass default of 4096.
- `Skeptic._select_model` (`skeptic.py:140-145`) returns `route.model` only and discards `route.max_tokens`; the request hardcodes `max_tokens=2048` (`skeptic.py:113-117`).
- `Orchestrator._run_lens_with_cascade` reads `result.model` and `result.tier` and drops `result.max_tokens`.

So the exact truncation bug the routes.yaml comment says was fixed is still live: the critic runs at 2048 against a reasoning model that spends ~2000 tokens thinking. Every such verdict truncates, fails to parse, and falls into the 4-call ladder in §3.2. This is simultaneously a quality bug and a token bug.

### 3.4 The Greek-ratio retry doubles a call

`Lens._maybe_retry_greek` re-issues the whole structured call when the flattened string fields score under 30% Greek. For findings that are mostly citations, article numbers, and short labels, that ratio can fail on legitimate output. Cheap mitigations: run the ratio check only over the long free-text fields (`issue`, `rule`, `application`, `conclusion`), and skip the retry entirely when there is no substantive text to judge.

### 3.5 CoVe costs up to 5 premium calls per finding

`cove_verifier.py`: `_plan_llm_questions` (1) + `_answer_factored` (1 per question, sequential, up to `_MAX_QUESTIONS = 3`) + `_cross_check` (1) = up to 5 calls per finding, on the `evidence_verification` route (`x-ai/grok-4.5`, premium).

Reductions that preserve the method:
- **Batch the answers into one call.** The anti-echo property of "factored" answering comes from excluding the *baseline finding* from context, not from isolating questions from each other. One call carrying the source text plus all 3 questions keeps the baseline out and cuts 3 calls to 1 — and stops re-sending the source article text three times.
- **`_MAX_QUESTIONS = 2`** unless an eval shows the third question changes verdicts.
- **Answer step on a cheaper tier.** Extracting whether a source text supports a claim is a cheap task; only the cross-check needs the strong model.
- Run the free deterministic quote/citation gate first and only spend LLM CoVe on findings that survive it.

### 3.6 Skeptic runs at premium on every finding

`adversarial_critic` routes to `gemini-2.5-pro`, a reasoning model billing thinking tokens, once per finding. The flow already dedups and reranks before verification (`bill_analysis_flow.py:286` → `:415`), which is the right order. The remaining lever is a confidence/severity gate: skip the critic on findings the lens itself graded `ABSTAIN` / very-low.

---

## 4. Accounting is wrong in three ways

Optimization without correct measurement is guesswork, and all three of these bias the numbers *downward* — the run looks cheaper than it is.

1. **Multi-call ladders are recorded once.** `BudgetGuardDecorator.generate_structured` (`decorators.py:85-109`) wraps `LLMAdapter`, but `LLMAdapter.generate_structured` calls `self.generate` — its *own* method, inside the decorator's blast radius — so attempts 2, 3, and 4 never reach `record_usage`. Up to a 4× undercount per structured call. `subset2.log` shows 90 actual POSTs against a guard total of 95,196 tokens; the guard cannot have seen all 90.

2. **One blended price for input and output.** `BudgetGuard._estimate_cost` (`budget_guard/__init__.py:106-109`) applies a single `COST_PER_1M_TOKENS` rate to `prompt_tokens + completion_tokens`. Real output prices run 4–10× input (`gemini-2.5-flash`: $0.30 in / $2.50 out; `gemini-2.5-pro`: $1.25 / $10.00). Output-heavy stages are dramatically under-billed, which is exactly where the retry loops live. Split the table into `{input, output, cached_input}`.

3. **Cached tokens are invisible.** OpenRouter returns `usage.prompt_tokens_details.cached_tokens` and `cache_write_tokens`; the adapter reads only `prompt_tokens` / `completion_tokens` (`openrouter.py:96-102`). Without this there is no way to verify a caching change ever worked. Also worth checking against current OpenRouter docs: the request-level `usage` accounting flag, which returns the upstream-computed cost and removes the need for a local price table entirely.

---

## 5. Ranked recommendations

Ordered by (estimated savings × inverse effort). Percentages are **(estimate)** against the ~411K-token lens-stage input baseline in §1.3 unless stated.

| # | Change | Est. saving | Effort | Risk |
|---|---|---|---|---|
| 1 | **Batch N articles per lens call** (schema keyed by article id) | **−60 to −70% input** | M | Attribution/quality dilution — needs eval |
| 2 | **Stop cascading on empty findings** | **−$1+ per run** (est.) | S | Possible recall loss — measure it |
| 3 | **Real async response cache** (sha256 of model+system+prompt+schema+max_tokens+temp+seed) | −100% on re-runs | S–M | Staleness only; needs temp=0 |
| 4 | **Wire route `max_tokens` through** lens/skeptic/orchestrator | Removes a whole ladder class | S | None — it is a bug fix |
| 5 | **Narrow the structured-output ladder** (§3.2) | −1 to −2 calls per failure | S | None |
| 6 | **Batch CoVe answers into one call** | −3 premium calls per finding | S | None (factoring preserved) |
| 7 | **Fix cost accounting** (split in/out, count ladder calls, read `cached_tokens`) | 0 direct — enables everything else | S | None |
| 8 | **Cheap pre-filter: skip irrelevant lenses per article** (e.g. GDPR lens only where personal-data terms appear) | −30 to −50% of lens calls | M | Recall — must be validated on the gold set |
| 9 | **Delete/replace `transforms: ["cache"]`** and the false docstring | 0–small | S | May restore `middle-out` default |
| 10 | **`temperature=0` on lens calls** | 0 direct — makes #3 meaningful and runs reproducible | S | Slight diversity loss (VS mode is the deliberate diversity path) |
| 11 | **Greek-ratio check on long fields only** | −1 call per false positive | S | None |
| 12 | **Skeptic gate on confidence/severity** | −1 premium call per skipped finding | S | Recall — measure |

Items 4, 5, 7, 9, 10, 11 are bug fixes or near-free; they should land before any model-swap experiment, because right now the measurement apparatus cannot tell you whether a swap helped.

### Why batching (#1) leads

Articles have a median of 110 tokens against 714 tokens of per-call boilerplate. At 10 articles per call, the 91-article bill needs 10 calls per lens instead of 91:

```
before: 455 calls × 714 boilerplate  + 86K article  ≈ 411K input tokens
after:   50 calls × 714 boilerplate  + 86K article  ≈ 122K input tokens   (−70%)
```

It also drags each request over the 1,024-token Gemini Flash caching floor, making §2.2 reachable for the first time — the two optimizations compound. The cost is quality risk: a model asked about 10 articles at once may attend less carefully to each. That is an empirical question, and Leggie has a gold set and an eval harness to answer it. **Do not ship batching on the arithmetic alone.**

---

## 6. What to measure before/after

Per the project's evidence bar, no item above is "done" until a smoke run shows:

1. `flow.budget_state` total cost — **after** the accounting fixes in §4, otherwise the comparison is meaningless.
2. Actual POST count (`grep -c 'chat/completions'`) vs. the theoretical minimum (`lenses × articles`). The ratio is the amplification factor; today it is well above 1.
3. `cached_tokens` share of `prompt_tokens`, once the adapter reads it.
4. Parse-failure and truncation-retry counts.
5. Finding count and survivor ratio vs. the baseline — the whole point is cheaper, not fewer.

---

## 7. Open questions

- **Output-token split is unmeasured.** All §1 numbers are input-side. COST_OPTIMIZATION.md asserts output tokens dominate; that claim is untested here and cannot be tested until §4.1 and §4.2 are fixed. Both can be true — 79% boilerplate on input says nothing about the output side.
- **Does empty-finding cascade improve recall at all?** If not, §3.1 is pure waste. If it does, the fix is a targeted retry, not a blanket one.
- **How many articles per batch before quality degrades?** Sweep 1 / 5 / 10 / 20 against the gold set.
- **Is a non-Gemini provider viable for the high-volume lens route?** Greek-legal competence is the blocker, not price; DeepSeek's caching floor makes it attractive *if* quality holds.
