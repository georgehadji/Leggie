# Data Handling

This document describes how Leggie handles your data, which providers receive
bill text, the retention posture, and how to configure provider allowlisting /
zero-retention routing.

## What is sent where

When you run `leggie analyze`, Leggie sends:

- **Bill text** (the document you provide) to **OpenRouter's `/chat/completions`**
  endpoint as the user prompt, wrapped in quarantine delimiters by
  `PromptHardeningDecorator`.
- **Article/finding text** to OpenRouter for lens analysis, citation
  verification (CoVe), and skeptic review.
- If the **Reasoner** (deliberative) pipeline is enabled, bill text also goes to
  the Reasoner service (which delegates to its own models).

Leggie **does not** send bill text to any provider other than OpenRouter
(for the deterministic pipeline) or Reasoner (for the opt-in deliberative
pipeline).

## Retention posture

Leggie makes **no explicit retention guarantees**. OpenRouter/Reasoner are
third-party services with their own data policies. By default:

- Leggie does not store your bill text locally beyond the run's `Outputs/`
  artifacts you choose to keep.
- The CLI does not send bill text to any analytics / telemetry service.

**You should treat any bill text you send to OpenRouter as data that a
third-party provider may retain per its own policy.** For sensitive bills,
apply the mitigations below.

## Configuring provider allowlisting / zero-retention routing

OpenRouter supports per-request provider routing. To constrain which upstream
providers handle your requests, add the appropriate fields to the request
payload (see `leggie/infrastructure/llm/adapters/openrouter.py`).

To enable **zero-retention** routing (where the upstream provider supports it),
set these in the request body:

```json
{
  "provider": {
    "order": ["openai", "anthropic", "google"],
    "allow_fallbacks": false
  }
}
```

OpenRouter's zero-data-retention option is controlled via the
`provider.allow_fallbacks` and the upstream's retention flag. Leggie defaults
to **accepting whatever retention policy the upstream applies** unless you
explicitly configure this in the request payload — do not assume
zero-retention is active by default.

## Recommended posture

1. Set `LEGGIE_LLM__OPENROUTER_API_KEY` but **treat the key as a secret** —
   never commit it.
2. For classified or sensitive bills, prefer providers/OpenRouter configs that
   offer zero-retention, and set the `provider` routing block explicitly in
   the request payload.
3. Review `SECURITY.md` for the credential-handling posture.

## Disclaimer

Leggie provides **structured legal analysis, not legal advice**. See the
no-legal-advice disclaimer in every CLI invocation and report header.
