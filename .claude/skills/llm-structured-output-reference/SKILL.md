---
name: llm-structured-output-reference
description: >
  Domain-theory pack for Leggie's core engineering problem: reliable structured
  LLM output at low cost. Load when touching leggie/infrastructure/llm/,
  config/routes.yaml, Pydantic response schemas, the cascade/router, CoVe,
  or the skeptic — or when reasoning about why LLM JSON parsing fails, what
  the retry ladder does, or how model routing controls cost. Explains schema
  drift, truncation, the 4-attempt defense ladder, alias normalization, CoVe's
  4-step loop, and skeptic gates, all grounded in this repo's incidents.
---

# LLM Structured Output — Theory and Implementation in Leggie

Historical stakes: schema drift + truncation once destroyed ~99% of pipeline
yield — 134 `pydantic … Field required` errors across ~90 articles, ONE
finding survived a full run (docs/REMEDIATION_PLAN.md D1/D2). Everything in
this skill exists because of that.

## 1. Why LLM JSON fails (failure modes seen in this repo)

| Mode | What happens | Log signature |
|---|---|---|
| **Schema drift** | model invents field names (`lens_id`, `title`, `issue_id`, `legal_issue`, `problem` instead of `issue`) → Pydantic rejects → finding lost | `pydantic ... Field required` |
| **Truncation** | verbose Greek IRAC output overruns `max_tokens`; provider sets `finish_reason="length"`; JSON cut mid-string | `Unterminated string`, `Expecting value: line 1 col 1` |
| **Code fences** | model wraps JSON in ```` ```json ... ``` ```` | decode fails on raw content |
| **Bare arrays** | model returns `[...]` instead of `{"findings": [...]}` | "Expected dict" |
| **Top-level aliases** | `issues`/`candidates`/`items`/`results` instead of `findings` | field-required errors |

**Definitions:** `finish_reason` = provider's stop cause (`stop` normal,
`length` = token limit hit). `json_schema` strict mode = OpenRouter
response_format that forces the model to emit exactly the given JSON Schema;
`json_object` = weaker mode guaranteeing only syntactically valid JSON.

## 2. The defense ladder (`LLMAdapter.generate_structured`, `leggie/infrastructure/llm/__init__.py`, verified 2026-07-10)

```
Attempt 1: json_schema strict mode
           pydantic_to_json_schema(schema)  # schema_format.py: inlines $ref,
                                            # additionalProperties:false, all fields required
           → parse via StructuredResponseParser
Attempt 2: on LLMError containing "400"/"Bad Request" (model rejects json_schema)
           OR parse ValueError → retry once in json_object mode
Attempt 3: if response.finish_reason == "length" → retry json_object with
           max_tokens doubled, capped at _MAX_TRUNCATION_RETRY_TOKENS = 16_384
Attempt 4: repair round — feed response.content[:4000] back with
           _REPAIR_PROMPT_TEMPLATE ("Return ONLY valid JSON..."), single try
Exhausted: raise LLMError → caller degrades (cascade / DEGRADED event)
```

Status of the two HIGH findings from `implementation_audit_report.md`
(landed in commits cb7fde8/406f969, verified 2026-07-10):
- **H-1 (LLMError skipped truncation retry)**: addressed — `response` is
  initialized to `None` before attempt 1 with an explanatory comment, and
  attempt 3 guards `if response and response.finish_reason == "length"`.
- **H-2 (repair round burns budget on unrepairable content)**: partially
  addressed — attempt 4 now skips when `content_to_repair` is empty, but still
  pays one API call for any non-empty garbage.

## 3. The parse ladder (`StructuredResponseParser.parse`, `structured_parser.py`)

1. Strip markdown fences → 2. `json.loads` → 3. wrap bare array into schema's
first `list[...]` field → 4. rename top-level aliases
(`issues|candidates|items|results` → `findings`) → 5. normalize IRAC item
aliases → 6. `schema(**data)` validate. Raises `ValueError` with a descriptive
message; caller owns retry-vs-degrade.

`_IRAC_ALIASES` (exact, verified): `issue` ← issue, title, finding, summary,
concern, constitutional_concern, analysis, legal_issue, problem, finding_text;
`rule` ← rule, constitutional_provision, rule_id, legal_basis, provision,
article; `application` ← application, analysis, reasoning,
constitutional_concern; `conclusion` ← conclusion, verdict,
constitutional_concern, analysis; `verbatim_quote` ← verbatim_quote, excerpt,
quote, text_excerpt.

Pure function of `(content, schema)` — unit-testable without HTTP. Tests:
`tests/unit/infrastructure/test_phase1_structured_output.py` (includes real
malformed payloads from the smoke log as regression fixtures).

## 4. Response schemas (`leggie/domain/models/structured_output.py`)

| Schema | Used by | Shape |
|---|---|---|
| `LensFindings` | every lens call | `{findings: [IRACCandidate]}`; IRACCandidate = issue/rule/application/conclusion + verbatim_quote, severity, probability |
| `VSResponse` | Verbalized Sampling (unwired — D4) | k candidates with probabilities |
| `SkepticVerdictResponse` | adversarial critic | verdict supports/refutes/neutral + reason + confidence_adjustment (±0.5) |
| `CoVeQuestionsResponse` | CoVe step 2 | open-ended questions (never yes/no — so the model can't rubber-stamp itself) |
| `CoVeAnswerResponse` | CoVe step 3 | factored answer + supported_by_source, answered WITHOUT the baseline in context |
| `CoVeCrossCheckResponse` | CoVe step 4 | consistency, keep, revised_conclusion, confidence_adjustment |

## 5. Routing, cascade, cost

Route table anatomy: see **leggie-config-and-flags** §2. Semantics: each task
type names a model + tier + max_tokens; on failure/low confidence the cascade
escalates FREE → BUDGET → PREMIUM (`confidence_floor` default 0.6). Cost
anchors from routes.yaml comments: flash-lite ~$0.10/1M in, flash ~$0.30/1M,
pro ~$1.25/1M. Cost driver: `lens_analysis` = 5 lenses × N articles; the
skeptic deliberately uses a SHARPER model (claude-sonnet-4.6 →
claude-opus-4.8) because its job is to catch what the lens missed.

Budget guard: cost cap $5/run is the governor; token ceiling 20M is a safety
net only (historical 500k ceiling throttled runs before money mattered —
never reintroduce a low token ceiling).

Model IDs must exist on OpenRouter. `LLMAdapter.__init__` validates the
default model against `_OFFLINE_MODEL_ALLOWLIST`;
`validate_model_ids()` queries the live `/models` catalog. Incident: fake IDs
shipped once (fixed in commit 39b42ef). When changing a model in routes.yaml,
check it: `curl -s https://openrouter.ai/api/v1/models | grep '<model-id>'`.

Rate limiting: `RateLimiter(max_rate=5.0)` is constructed in
`LLMAdapter.__init__` and passed to `OpenRouterProvider` —
verify consumption inside `adapters/openrouter.py` before assuming throttling.

## 6. CoVe and Skeptic (the verification layer)

**CoVe (Chain-of-Verification)** — `application/services/cove_verifier.py`,
4-step factored loop: baseline finding → plan open-ended verification
questions → answer each against the ARTICLE TEXT ONLY (baseline withheld, so
the verifier can't just agree with itself) → cross-check; `keep=False` drops
the finding, `revised_conclusion` repairs it. Quote validation drops
fabricated `verbatim_quote`s (log signature `cove_quote_fail` — live-proven:
dropped 2 fabricated-quote findings). Citation semantics are fail-closed:
empty resolution index ⇒ "unverified", never "invalid" (see
`infrastructure/citation/__init__.py resolve()`).

**Skeptic** — `application/agents/skeptic.py`, Chain of Responsibility:
4 typed heuristic gates (Numeric/Temporal/Factual/Obligation — currently
mostly "deferred"/neutral placeholders except Factual's constitution-reference
check) + `LLMAdversarialGate` (Greek adversarial prompt, temperature 0,
`json_object` mode, 768 max_tokens) that can actually refute. Any `refutes`
verdict drops the finding; confidence adjustments are summed and applied via
`model_copy` with a version bump. The skeptic never crashes the run — all
exceptions → neutral verdict + `skeptic_llm_error` warning.

## 7. How-to checklists

**Add a structured schema:** define in `domain/models/structured_output.py`
(defaults on optional fields; descriptions become part of the JSON Schema the
model sees) → callers use `generate_structured(request, Schema)` → if items
have IRAC-like fields, extend `_IRAC_ALIASES` when drift is observed → add
parser unit tests incl. a malformed-payload fixture.

**Add/replace a route:** edit `config/routes.yaml` → verify model ID against
OpenRouter catalog → confirm a consumer requests that task_type → live smoke
(class-A change).

## When NOT to use this skill

- Triaging a specific broken run → **leggie-debugging-playbook**
- Config value lookup → **leggie-config-and-flags**
- Greek legal meaning of findings → **greek-legal-domain-reference**
- Evidence standards for "the fix worked" → **leggie-validation-and-qa**

## Provenance and maintenance

- Ladder: `grep -n "Attempt\|finish_reason\|_MAX_TRUNCATION" leggie/infrastructure/llm/__init__.py`
- Aliases: `grep -n "_IRAC_ALIASES" -A20 leggie/infrastructure/llm/structured_parser.py`
- Schemas: `grep -n "class " leggie/domain/models/structured_output.py`
- Allowlist: `grep -n "_OFFLINE_MODEL_ALLOWLIST" -A25 leggie/infrastructure/llm/__init__.py`
- Parser tests: `python -m pytest tests/unit/infrastructure/test_phase1_structured_output.py -q`
