# Cost Optimization Guide

Leggie's current spend is driven by the 5-lens ensemble on every article, followed by an adversarial critic (Skeptic) and Chain-of-Verification (CoVe) on every surviving finding. This document lists the concrete levers available now and the model experiments that are safe to run once the Phase 0 baseline is stable.

## Current cost drivers (approximate)

For a 78-article bill:

| Stage | Calls/article | Default model | ~Cost per 1M tokens |
|-------|---------------|---------------|---------------------|
| Lens analysis (5 lenses) | 5 | `google/gemini-2.5-flash` | $0.30 in / $2.50 out |
| Skeptic review | ~0.3 (only survivors) | `google/gemini-2.5-pro` | $1.25 in / $10.00 out |
| CoVe verification | ~0.3 (only survivors) | `google/gemini-2.5-pro` | $1.25 in / $10.00 out |
| Reports | 2-4 | `google/gemini-2.5-pro` | $1.25 in / $10.00 out |

The dominant cost is not the number of concurrent calls but the **output tokens consumed by long structured responses and retries**.

## Immediate levers (no code changes required)

1. **Analyze a subset of articles** — now supported via the CLI:
   ```bash
   python -m leggie analyze bill.pdf -a "1-10,15,20"
   ```
   This is the fastest way to cut cost for iterative development or spot-checks.

2. **Run only the lenses you need**:
   ```bash
   python -m leggie analyze bill.pdf -l constitutional legal_coherence
   ```

3. **Keep verbalized sampling off** (it is off by default). Enabling it multiplies calls per lens by the sample count.

4. **Lower `max_tokens` in `config/routes.yaml`** if smoke tests show no truncation. The current `lens_analysis` ceiling is `6144`; dropping it to `4096` reduces the worst-case spend on long completions by ~33%.

5. **Tighten the cost cap**:
   ```bash
   LEGGIE_BUDGET__MAX_COST_PER_RUN=2.0 python -m leggie analyze bill.pdf
   ```
   The BudgetGuard will hard-stop the run before it overspends.

6. **Use a checkpoint** so a crash does not re-bill already-completed work:
   ```bash
   python -m leggie analyze bill.pdf -c leggie.checkpoint.json
   ```

## Code-level optimizations to consider next

| Optimization | Effort | Impact | Notes |
|--------------|--------|--------|-------|
| **Response caching** for identical (article, lens, prompt) pairs | Medium | High on re-runs | Currently only a sync `lru_cache` decorator exists; an async cache keyed by prompt hash would avoid re-billing restarts. |
| **Prompt compression / truncation** to the relevant article only | Low-Medium | Medium | Lenses already receive single-article text; verify no full bill context is accidentally included. |
| **Adaptive skeptic/CoVe skipping** for very-low-confidence findings | Medium | Medium | Skip expensive verification on findings the lens itself graded as `ABSTAIN` or `VERY_LOW`. |
| **Tiered routing per task** (see below) | Low | High | Change `config/routes.yaml`; no application code needed. |
| **Shorter JSON schemas / stricter system prompts** | Low | Medium | Reduces parse failures and retry loops, which are a hidden cost driver. |
| **Batch skeptic/CoVe calls** | Medium | Low-Medium | Currently reviewed one-by-one; batching could reduce per-call overhead but increases complexity. |

## Model research — OpenRouter candidates

The Gemini 2.5 family is the **only proven Greek-legal pair** in the current pipeline. All other models are cheaper on paper but unproven for Greek statutory text; they should be treated as experiments, not drop-in replacements.

### Proven baseline

| Model | Role | In/1M | Out/1M | Struct output | Context |
|-------|------|-------|--------|---------------|---------|
| `google/gemini-2.5-flash-lite` | Free/cheap tier | $0.10 | $0.40 | Yes | 1M |
| `google/gemini-2.5-flash` | Budget/work tier | $0.30 | $2.50 | Yes | 1M |
| `google/gemini-2.5-pro` | Premium/critic tier | $1.25 | $10.00 | Yes | 1M |

### Experimental candidates by provider

| Provider | Model | In/1M | Out/1M | Struct | Notes |
|----------|-------|-------|--------|--------|-------|
| **Qwen** | `qwen/qwen3.5-flash-02-23` | $0.065 | $0.26 | Yes | Very cheap, 1M ctx. Strong multilingual base, but Greek legal nuance untested. |
| **Qwen** | `qwen/qwen3.6-flash` | $0.19 | $1.13 | Yes | Good cost/ctx ratio; candidate for lens_analysis after A/B validation. |
| **DeepSeek** | `deepseek/deepseek-v4-flash` | $0.077 | $0.15 | Yes | Cheapest structured option. Reasoning quality for Greek law unknown. |
| **DeepSeek** | `deepseek/deepseek-v4-pro` | $0.44 | $0.87 | Yes | Mid-cost, may rival Flash on reasoning. |
| **Z.ai (GLM)** | `z-ai/glm-4.7-flash` | $0.06 | $0.40 | Yes | Extremely cheap; Chinese-base model, Greek legal competence unproven. |
| **Z.ai (GLM)** | `z-ai/glm-5` | $0.60 | $1.92 | Yes | Better reasoning; still untested for Greek legal text. |
| **xAI** | `x-ai/grok-4.3` | $1.25 | $2.50 | Yes | Mid-cost, long context. No Greek legal track record in this project. |
| **xAI** | `x-ai/grok-4.5` | $2.00 | $6.00 | Yes | More capable, but more expensive than Gemini Pro for the critic role. |
| **MoonshotAI** | `moonshotai/kimi-k2.5` | $0.375 | $2.025 | Yes | Cheap 1M ctx; good candidate for large-context summarization. |
| **MoonshotAI** | `moonshotai/kimi-k2.7-code` | $0.72 | $3.49 | Yes | Code/math focused; less obviously suited to legal reasoning. |
| **MoonshotAI** | `moonshotai/kimi-k3` | $3.00 | $15.00 | Yes | 2.8T multimodal reasoning, 1M ctx; premium-tier quality-critical routes. |
| **MiniMax** | `minimax/minimax-m2.5` | $0.15 | $0.90 | Yes | Very cheap; untested for Greek law. |
| **MiniMax** | `minimax/minimax-m3` | $0.30 | $1.20 | Yes | Comparable cost to Gemini Flash; candidate for lens_analysis. |
| **NVIDIA** | `nvidia/nemotron-3-super-120b-a12b` | $0.08 | $0.45 | Yes | Extremely cheap; safety/classification focused, legal reasoning unknown. |
| **NVIDIA** | `nvidia/nemotron-3-ultra-550b-a55b` | $0.50 | $2.20 | Yes | Larger variant; still untested. |
| **Xiaomi** | `xiaomi/mimo-v2.5` | $0.11 | $0.28 | Yes | Very cheap; not known for legal reasoning. |
| **Perplexity** | `perplexity/sonar` | $1.00 | $1.00 | No | Web-search augmented. No structured output; useful for factual lookups, not lens analysis. |
| **Perplexity** | `perplexity/sonar-pro` | $3.00 | $15.00 | No | Expensive; no structured output. Not a fit for the current pipeline. |

### Providers with no relevant models in the current catalog

- **Mistral**: not present in the captured `openrouter_models.json` snapshot with a competitive Greek-legal offering.
- **AionLabs**: no models listed in the catalog.
- **Mimo**: available under the `xiaomi/mimo-*` namespace, not a separate provider.

## Suggested tiered routing experiment

The safest first experiment is to keep the **critic/verification/report tiers on Gemini Pro** (quality-critical, low volume) and try cheaper models only on the high-volume `lens_analysis` route.

Example progressive rollout in `config/routes.yaml`:

```yaml
lens_analysis:
  model: "google/gemini-2.5-flash"   # current proven baseline
  tier: "budget"
  max_tokens: 4096                    # reduce from 6144 if smoke passes
  cascade: true
  cascade_models:
    free: "qwen/qwen3.5-flash-02-23"  # experiment only after baseline
    budget: "google/gemini-2.5-flash"
    premium: "google/gemini-2.5-pro"
```

Do not make the cheap model the default until an A/B run shows:
- Parse failure rate < 5%
- Survivor/finding ratio stable vs. the Gemini baseline
- No increase in CoVe drops for reasons other than `cove_quote_fail`

## Measurement checklist

Before declaring any optimization a success, verify:

1. Total cost per bill (from `flow.budget_state` log line).
2. Parse-failure rate (from smoke log stats).
3. Survivor count and per-article ratio.
4. CoVe drop reasons — only `cove_quote_fail` should be acceptable.
5. End-to-end report quality (manual sample of IRAC findings).

## Summary

- **Biggest guaranteed savings**: analyze selected articles/lenses, lower `max_tokens`, keep cost cap tight.
- **Next high-lever code change**: async response cache keyed by prompt hash.
- **Model cost reduction**: experiment with `qwen3.5-flash`, `deepseek-v4-flash`, or `z-ai/glm-4.7-flash` on `lens_analysis` only, while keeping Gemini Pro for critic/CoVe/reports.
- **Do not switch** the adversarial critic or CoVe verifier to a cheaper, unproven model until the baseline is stable.
