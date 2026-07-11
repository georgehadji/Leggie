# Phase 0 Live Smoke Audit — Partial Results

**Bill:** `Inputs/OE_ΣΧΝ-ΥΠΔΙΚ.pdf`  
**Date:** 2026-07-11  
**Branch state:** post D3/D6/D4/D5/D7/D8/D10/DOC2/CFG1/CI1 + schema-drift + route fixes  
**Budget cap:** $5.00 / 20,000,000 tokens

## Parse sanity

```bash
leggie parse Inputs/OE_ΣΧΝ-ΥΠΔΙΚ.pdf -o parsed.json
```

- Total parsed article entries: **121**
- Unique article ids: **78**
- Duplicate ids: 41-46, 47-59, 62-74, 76, 78-81, 83-87, 90-91 (placeholder TOC entries vs. body entries)
- Parser completed without errors; the duplicate-id pattern is a pre-existing document-structure artifact, not a phantom 552/622Γ cross-reference.

## Smoke runs

| Run | Command | Key changes vs. previous | Outcome | Log lines | Notable signatures |
|-----|---------|--------------------------|---------|-----------|--------------------|
| v1 | serial baseline (`--lenses constitutional`) | none | **Timed out** at 300 s after ~6 articles (~50 s/article) | 30 | `cascade: constitutional premium → google/gemini-2.5-pro (empty)` |
| v2 | `LEGGIE_LLM__MAX_CONCURRENCY=10` + D3/D6 parallel fan-out | parallel articles | **Stopped** after ~35 min in skeptic/CoVe stage; no output written | 507 | 7 `skeptic_llm_error`, 8 `Response truncated`, 0 `json_schema rejected` |
| v3 | v2 + strip numeric constraints from json_schema + fix `NoneType` `verbatim_quote` + switch `adversarial_critic` route from Anthropic to Gemini | schema + route + lens robustness | **Stopped** after ~45 min in CoVe/skeptic stage; no output written | 553 | 4 `skeptic_llm_error`, 5 `Response truncated`, 1 `cove_llm_error`, 8 `Failed to parse structured response` |
| v4 | v3 + raise hard-coded token ceilings in `skeptic.py` (`768→2048`) and `cove_verifier.py` (`1024→2048`, `512→1024`, `1024→2048`) | more headroom for structured JSON responses | **Completed** — produced outputs | 555 | 2 `skeptic_llm_error`, 8 `cove_llm_error`, 2 `cove_quote_fail`, 2 `Response truncated`, 9 `Failed to parse structured response` (3.3% of 275 LLM calls) |
| v5 | v4 + add `skeptic_verdict`, `cove_result`, and `flow.budget_state` instrumentation | capture exit-gate evidence in the smoke log | **Completed** — produced outputs | 593 | 1 `skeptic_llm_error`, 9 `skeptic_verdict=refutes`, 9 `skeptic_verdict=supports`, 1 `skeptic_verdict=neutral`, 1 `cove_quote_fail`, 1 `Response truncated`, 12 `Failed to parse structured response` (4.0% of 299 LLM calls) |

## Phase 0 single-lens exit-gate assessment

| Gate | Required | v5 evidence | Status |
|------|----------|-------------|--------|
| Survivors ∝ article count | order-of-magnitude above 1-survivor fossil | 10 survivors / 121 parsed entries = **0.13/article** | **PASS** |
| Parse-failure signatures | < 5% of LLM calls | 12 / 299 = **4.0%** | **PASS** |
| Skeptic non-neutral verdict | ≥1 | 18 non-neutral (9 refutes, 9 supports) | **PASS** |
| CoVe drops only quote-invalid findings | `dropped=True` only on `cove_quote_fail` | 1 `dropped=True` → `cove_quote_fail` (quote not in source) | **PASS** |
| Spend | < $5 | cost_used=**$0.3577**, remaining=**$4.6423**, tokens_used=**390,036** | **PASS** |

**Result:** Single-lens constitutional smoke on `Inputs/OE_ΣΧΝ-ΥΠΔΙΚ.pdf` **passes** the Phase 0 exit gate.

## Observations

1. **D3/D6 parallel fan-out works.** Wall-clock per article dropped dramatically once concurrency was enabled; v2/v3 processed all 121 article entries without the serial timeout.
2. **Schema-drift 400s are fixed.** Stripping `minimum`/`maximum` from number schemas removed the Anthropic/Azure/Bedrock `json_schema` rejections seen in v1.
3. **Lens robustness improved.** The `NoneType` `verbatim_quote` crash in `constitutional_lens.py` was fixed; v3 shows no `NoneType` degradation.
4. **Skeptic/CoVe structured-output compliance improved but not eliminated.** v4 completed end-to-end. `Failed to parse structured response` dropped to 3.3% of LLM calls (below the 5% gate), and truncation events fell to 2. However, 8 `cove_llm_error` and 2 `skeptic_llm_error` remain, so the retry ladder still degrades some findings.
5. **Yield is now proven for a single-lens run.** v4 produced `Outputs/OE_ΣΧΝ-ΥΠΔΙΚ_findings.json` with **11 survivors** across 121 parsed article entries (0.14/article), well above the 1-survivor fossil. The remaining Phase 0 exit-gate items are: (a) ≥1 non-neutral skeptic verdict visible in the log, (b) CoVe drops only quote-invalid findings, (c) spend < $5.

## v4 findings summary

```bash
python .claude/skills/leggie-diagnostics-and-tooling/scripts/findings_stats.py Outputs/OE_ΣΧΝ-ΥΠΔΙΚ_findings.json --articles 78
```

- total_findings: 11
- by_type: constitutional=11
- by_severity: medium=11
- confidence: min=0.65, mean=0.82, max=1.00
- info_filler_ratio: 0%
- findings_per_article: 0.14

## Full 5-lens run

Per `REMEDIATION_PLAN_V2.md` §Phase 0 step 4, a full 5-lens run validates the same thresholds with all lenses enabled.

```bash
LEGGIE_LLM__MAX_CONCURRENCY=10 python -m leggie analyze Inputs/OE_ΣΧΝ-ΥΠΔΙΚ.pdf -o Outputs/full5_fixed 2>&1 | tee smoke_phase0_full5_fixed.log
```

| Run | Command | Key changes vs. previous | Outcome | Log lines | Notable signatures |
|-----|---------|--------------------------|---------|-----------|--------------------|
| full5 | all 5 lenses, `-o Outputs/full5` | validation of full pipeline vs single-lens baseline | **Stopped** mid-run (route-fix made it stale) | 1,175 | heavy `cascade: ... premium → google/gemini-2.5-pro (empty)` — dead `lens_analysis` route meant every lens ran on fallback 4096-token config |
| full5_fixed | all 5 lenses, `-o Outputs/full5_fixed` | `lens_analysis` route now active (`max_tokens=6144`, cascade to pro) | **Stopped** — OpenRouter 402 Payment Required | 2,892 | 714 `HTTP/1.1 402 Payment Required` responses; no output files written |

## Blocker: account balance

The full 5-lens smoke hit the OpenRouter credit wall (`402 Payment Required`) during the Skeptic/CoVe stage. The per-run budget guard was not the limiting factor — the OpenRouter account itself ran out of credits. No `Outputs/full5_fixed/*` files were produced.

**Next:** add OpenRouter credits, then re-run the full 5-lens smoke.

## Config changes applied since v5

| Change | File | Rationale |
|--------|------|-----------|
| Wire all lenses to `lens_analysis` route | `leggie/application/agents/orchestrator.py` | The route was dead because orchestrator queried `lens_<name>`; now all lenses get the configured `max_tokens=6144` and cascade. |
| Upgrade `adversarial_critic` to Gemini Pro | `config/routes.yaml` | Critic must be stronger than the lens model to catch errors. |

## Candidate next variables (after full5_fixed)

Per the plan's ablation discipline, change only one variable per smoke run:

1. **Strengthen the JSON-only system prompt** in the skeptic/CoVe prompt templates if JSON compliance failures persist.
2. **Raise hard-coded `max_tokens` further** if truncation remains the dominant failure mode.
3. **Test a cheaper lens model** (e.g. `deepseek/deepseek-v4-flash`) only after the Greek-legal yield is proven stable.

## Offline gates

All code changes pass the hardened CI pipeline:

```text
pytest tests/ -q                    -> 374 passed
ruff check leggie/ tests/            -> All checks passed
mypy leggie/ --ignore-missing-imports -> Success
lint-imports                         -> layer-dependencies KEPT
bandit -c pyproject.toml -r leggie/  -> No issues identified
```
