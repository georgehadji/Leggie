# Remediation Plan V3 — Prove the Full Pipeline

**Date:** 2026-07-17
**Branch:** `claude/bold-nightingale-be6980` (checkpoint fix; see §0)
**Supersedes:** nothing — continues `REMEDIATION_PLAN.md` (D1–D10, §10 DoD) and
`REMEDIATION_PLAN_V2.md`. Those IDs stand; this plan opens **D11–D14**.
**Scope:** the one thing the project has never done — a FULL 5-lens live smoke
that meets `REMEDIATION_PLAN.md` §10, plus the first live run of the
deliberative pipeline, both recorded through change control.

---

## 0. Current state (what already works — do not re-touch)

| Fact | Evidence |
|---|---|
| Offline gates green | `pytest tests/ -q` → 532 passed; `mypy leggie/ --ignore-missing-imports` clean; `ruff check leggie/ tests/` clean; `lint-imports` → 1 contract kept (2026-07-17, this branch) |
| Single-lens smoke PASSED | `docs/SMOKE_AUDIT.md` v5, 2026-07-11: 299 calls, 4.0% parse failures, 19 skeptic verdicts (9 refutes / 9 supports / 1 neutral), $0.3577, 11 findings / 121 entries |
| D3/D6 parallel fan-out | `bill_analysis_flow.py:271` `analyze_document` |
| H-1 truncation-retry guard | `llm/__init__.py:179-182` `response: LLMResponse | None = None` |
| `LLMAdversarialGate` landed | `skeptic.py:169` |
| `lens_analysis` route live | `config/routes.yaml`, flash @ 6144, cascade to pro |
| Checkpoint honours `-o` | this branch, `02c87f5` — `output_dir/leggie_checkpoint.json`; container binding removed |
| Suite is hermetic | `tests/conftest.py` blanks provider credentials (`cdc3abc`) |

**Fenced (from the campaign skill; unchanged):** no Domain model edits to ease
parsing; no ruff-ignore widening; no `max_cost_per_run` above $5; no new port
methods; no learned-router / debate / KG re-proposals; "unverified" citations
stay unverified; numbers only, never "looks better".

---

## 1. Defect inventory (ranked by yield impact)

### D11 — Premium-tier structured calls truncate: reasoning tokens eat a 2048 ceiling — **CRITICAL**

**Layer:** Application (call sites) + config (routes).

**Mechanism (hypothesis, not yet confirmed — Phase B confirms or kills it):**
`adversarial_critic` and `evidence_verification` both route to
`google/gemini-2.5-pro` at `max_tokens: 2048`. Gemini 2.5 Pro emits thinking
tokens that count against the completion ceiling and cannot be disabled on Pro.
A `SkepticVerdictResponse` is ~100 tokens of JSON, so a 2048-token ceiling
should never be reached — unless something else is consuming it first. The
response is cut at `finish_reason=length` before the JSON closes, the ladder's
attempt-3 doubles to 4096, that is often still short, the ladder exhausts, and
`skeptic.py:119` swallows the failure into a forced **neutral** verdict.

**Evidence:**

| Observation | Source |
|---|---|
| All 5 truncations at *exactly* 2048 tokens | `subset2.log:44,52,62,68,74` — `Response truncated (finish_reason=length, 2048 tokens)` |
| Truncation immediately precedes ladder exhaustion, repeatedly | `subset.log` — `Response truncated` → 2× POST → `skeptic_llm_error: … Failed to parse structured response after all retries for schema SkepticVerdictResponse` |
| Skeptic failure rate 5% → **62.5%** | v5: 1 error / 20 calls. subset2: 5 errors / 8 calls (3 verdicts + 5 errors) |
| Parse-failure rate 4.0% → **14.4%** (gate is 5%) | v5: 12/299. subset2: 13/90 |
| Skeptic verdicts collapsed to unanimous `supports` | subset2: 3 verdicts, 0 refutes (v5: 9 refutes / 9 supports / 1 neutral) |
| The only skeptic-affecting change between v5 and subset2 is the Pro critic | `SMOKE_AUDIT.md` §"Config changes applied since v5" — *"Upgrade `adversarial_critic` to Gemini Pro"* |
| No HTTP errors — all 90 calls returned 200 | `subset2.log` — failure is in content, not transport |

**Why this is the campaign's root cause, not a symptom:** one mechanism
explains every stalled full-5 observation without a second assumption.
`full5_final`'s 111 `skeptic_llm_error` + 189 parse failures + 88 truncation
retries are what this rate produces at 5× the finding volume. It also explains
why v5 passed (its critic was flash, not Pro) and why the CoVe errors predate
the critic upgrade (`evidence_verification` was already Pro @ 2048 — v4 showed
8 `cove_llm_error`). **The v5 → full5 regression was caused by a config change
made to improve quality, applied without re-validating single-lens first — two
variables (route wiring + Pro critic) moved at once.**

**Falsifier:** if Phase B shows Pro returning <500 completion tokens with no
reasoning-token consumption, D11 is wrong and the truncation has another cause.

### D12 — `RouteResult.max_tokens` is dead config for skeptic and CoVe — **HIGH**

**Layer:** Application.

`RouteResult` already carries `max_tokens` (`ports/router.py:20`), but both
consumers take `route.model` and discard the rest:

| Call site | Hardcoded | Route says |
|---|---|---|
| `skeptic.py:115` | `max_tokens=2048` | `adversarial_critic` → 2048 (coincides today; coupling is broken) |
| `cove_verifier.py:287` | `max_tokens=2048` | `evidence_verification` → 2048 |
| `cove_verifier.py:317` | `max_tokens=1024` | 2048 |
| `cove_verifier.py:359` | `max_tokens=2048` | 2048 |

`skeptic.py:140-145` `_select_model()` returns `route.model` only.

**Consequence:** the config knob that would fix D11 does not reach the code.
Editing `routes.yaml` `max_tokens` for these two routes changes *nothing* —
which makes the campaign skill's solution-menu option #2 ("raise route
max_tokens") a **silent no-op** for exactly the two call sites that are
failing. This must be fixed before any token-ceiling experiment is meaningful.

### D13 — A disabled skeptic is invisible in the run result — **MEDIUM**

**Layer:** Application.

`skeptic.py:119-122` catches `Exception` → logs one line → returns `neutral`.
Correct in isolation ("skeptic must never crash the run"), but there is no
aggregate signal: `full5_final` had the adversarial gate effectively **off for
111 findings** and the run would have reported success. A run whose critic
failed 62.5% of the time is not a run whose findings were criticised. Failure
is currently only countable by grepping a log the user happens to have kept —
and the `full5_final` log is gone.

### D14 — Container's `rate_limiter` singleton is dead — **LOW**

`container.py:156` registers `RateLimiter(max_rate=5.0)`; nothing resolves it.
`llm/__init__.py:132` constructs its own. Same class of defect as the
`CheckpointStore` binding fixed on this branch: a container binding with no
consumer. Behaviourally inert today (the adapter's own limiter is active), so
it is a cleanup, not a fix — but it is a live trap for the next person who
tunes `max_rate` in the container and sees no effect.

### Addendum to D11 (found during Phase A, not itself a defect to fix here)

`adapters/openrouter.py:96-99` extracts only `prompt_tokens` and
`completion_tokens` from OpenRouter's `usage` object into `LLMResponse.usage`;
`completion_tokens_details.reasoning_tokens` is discarded before it ever
reaches application code. **This is why D11 was never visible from a
production log** — even with A4's new debug line (`llm/__init__.py`, logged on
ladder exhaustion), `response.usage` will never show the reasoning-token
breakdown Phase B needs. Phase B's discriminating experiment therefore MUST
be the standalone script hitting OpenRouter directly (§3), not something
inferred from `smoke.log`. If Phase B confirms D11, capturing
`reasoning_tokens` in the adapter's `usage` dict going forward is worth a
follow-up (`LLMResponse.usage` is `dict[str, int]`, so this is additive, not a
port-signature change) — out of scope for this plan's Phase A/B/C.

### Gap (not a defect) — 4 of 5 lenses have never been smoked

`constitutional` is the only lens with live evidence. `economic`, `eu_gdpr`,
`implementation`, `legal_coherence` have never run against a real bill on their
own. The campaign has twice jumped 1 lens → 5 lenses in a single step, which
attributes nothing when it degrades. **Per-lens attribution comes before the
full run.**

---

## 2. Phase A — Offline: make the ceiling reachable and the failure visible (FREE)

**Status: COMPLETE (2026-07-17).** All four items landed. Evidence: 541
passed (was 532; +9 targeted tests), mypy/ruff/lint-imports clean. See the D11
addendum above for a real gap A4 surfaced (adapter drops reasoning-token
detail) — noted, not fixed here; does not block Phase B, which bypasses the
adapter by design.

**A1 (D12).** Honour `route.max_tokens` at both call sites.
- `skeptic.py`: `_select_model()` → return the `RouteResult`, not `route.model`;
  use `route.max_tokens` in the `LLMRequest`. Keep the current 2048 as the
  fallback when no router is wired.
- `cove_verifier.py:287/317/359`: same, via the existing `_structured(...,
  max_tokens=…)` parameter. The per-step 1024 for answers stays a *floor*, not
  a hardcode: pass `route.max_tokens`.
- No port change, no new method — `RouteResult.max_tokens` already exists.

**A2 (D13).** Count degradation into the run, not just the log.
- Skeptic and CoVe both gained `on_degradation` constructors, threaded from
  `BillAnalysisFlow` (which already had the pattern for lenses/orchestrator —
  skeptic/cove were the gap). On ladder exhaustion / `cove_llm_error`, both now
  emit `Event(EventType.DEGRADED, data={"gate"|"stage": ..., "finding_id": ...,
  "error": ...})` through the same callback that already lands events in the
  checkpoint JSON.
- `BillAnalysisFlow.events` (new property) and the CLI summary
  (`cli_handlers.py`) both surface a `DEGRADED` count so the audit doc can cite
  a number without a log grep — implemented as a count, not the two named
  fields originally sketched here; the count is what the §6/§8 gate tables
  actually need.
- Fixing the wiring gap required giving `BillAnalysisFlow` a `citation_parser`
  param so it constructs `CoVeVerifier` internally (with `on_degradation`)
  instead of `cli_handlers.py` pre-building a `CoVeVerifier` with no callback
  and handing it in — `_resolve_cove_from_container` is gone, replaced by
  `_resolve_citation_parser_from_container`.

**A3 (D14).** Delete the dead `rate_limiter` registration, or inject it into
`LLMAdapter`. Prefer **delete** (YAGNI; the adapter's own limiter works).

**A4.** Instrument for Phase B: on ladder exhaustion log
`response.content[:200]` and `usage` (completion/reasoning token counts) at
DEBUG. This is what turns the next failure into evidence instead of a rerun.

**Tests:** unit test per item —
(A1) a fake router returning `max_tokens=8192` ⇒ the `LLMRequest` carries 8192,
and no-router ⇒ 2048 fallback;
(A2) a skeptic whose LLM always raises ⇒ verdict neutral **and** one degradation
event emitted per failure;
(A3) `pytest tests/unit/test_port_contracts.py` + container tests still green.
All fakes — nothing reaches a provider (`tests/conftest.py` stays intact).

**Gate:** full offline sweep green (532+ passed, mypy/ruff/lint-imports clean).

---

## 3. Phase B — The discriminating experiment for D11 (~$0.02)

**Status: COMPLETE (2026-07-17). D11 CONFIRMED — every prediction held.**

Ran `phase_b_reasoning_probe.py` (scratchpad, not committed) against finding
`973a8394-5df5-42bf-bd90-bfd3c189a413` from `Outputs/subset2/..._findings.json`
(one of subset2's 3 unanimous-`supports` verdicts), talking to OpenRouter
directly with the exact `LLMAdversarialGate` system+prompt:

| | `max_tokens=2048` (today) | `max_tokens=8192` (proposed) |
|---|---|---|
| `finish_reason` | `length` | `stop` |
| `completion_tokens` | 2032 | 2246 |
| `reasoning_tokens` | **1962** (97% of budget) | 1857 |
| content | truncated, unparseable | 1012 chars, valid JSON |
| cost | $0.0207 | $0.0229 |

Gemini 2.5 Pro spends ~1,900 reasoning tokens on this prompt **regardless of
the ceiling** — non-negotiable overhead before any JSON is emitted. At 2048
that leaves ~86 tokens for content (guaranteed truncation); at 8192 it leaves
~6,300 (comfortable). Actual spend: $0.044 (forecast was $0.02 — Pro
reasoning costs more per call than a flash-based estimate suggested; still
trivial). Falsifier did not trigger — proceed to Phase C.

**Do not skip this. It is the cheapest step in the campaign and it decides
whether Phase C is a fix or a guess.**

A standalone script (scratchpad, not committed) that sends the *exact*
`LLMAdversarialGate` system+prompt with a real finding from
`Outputs/subset2/OE_ΣΧΝ-ΥΠΔΙΚ_findings.json` to `google/gemini-2.5-pro`,
`max_tokens=2048`, and prints the raw OpenRouter `usage` block plus
`finish_reason`.

**The hypothesis predicts, before the run:**
- `finish_reason == "length"`
- `usage.completion_tokens ≈ 2048`
- `usage.completion_tokens_details.reasoning_tokens` ≫ 0 (most of the 2048)
- `content` = prose/empty, not closed JSON

**Then vary one thing** — the same call at `max_tokens=8192`:
- predicts `finish_reason == "stop"`, valid `SkepticVerdictResponse` JSON,
  reasoning_tokens in the low thousands.

**Branches:**
- **Predictions hold** → D11 confirmed → Phase C.
- `reasoning_tokens == 0` and completion is 2048 tokens of JSON → D11 is wrong:
  the verdict schema/prompt invites an essay → fix the prompt (cap `reason`
  length in-prompt), not the ceiling. Re-plan from **leggie-debugging-playbook**
  row 1.
- 8192 still truncates → Pro's thinking is unbounded for this prompt → the
  ceiling cannot be the whole fix → go to Phase C option 3 (critic model swap).

---

## 4. Phase C — Fix D11, one variable, measured against a real control (~$0.10)

**Status: PARTIAL (2026-07-17).** Option 1 applied (`b16f17e`) and validated
against the subset2 control with `Outputs/subset3/` (actual spend $0.0951,
close to forecast). **D11's specific mechanism is confirmed fixed. The Phase
C exit gate as a whole does not pass** — a second, distinct failure mode
survives that is NOT truncation. See results table and D15 below; do not
proceed to Phase D/E until D15 is root-caused.

**Control already exists:** `subset2.log` + `Outputs/subset2/` — articles 1-10,
constitutional, current config. That is the before-column; do not re-measure it.

Ranked options. **Change exactly one per run** (ablation discipline —
**leggie-research-methodology**):

| # | Change | Try when | Cost risk |
|---|---|---|---|
| 1 | `adversarial_critic` + `evidence_verification` `max_tokens: 2048 → 8192` (works only after A1) | Phase B confirms reasoning consumes the ceiling | Pro output ~$10/M — a real spend increase; watch §6 |
| 2 | Bound the reasoning instead: pass OpenRouter `reasoning: {max_tokens: 512}` with `max_tokens: 4096` | option 1 works but spend is unacceptable | needs an adapter param — Infrastructure change, more code |
| 3 | Critic → `google/gemini-2.5-flash` with a raised ceiling | 1 and 2 both fail, or Pro stays unreliable | cheapest; costs the "critic stronger than lens" property — **document the trade, do not silently revert** |

Option 1 first: it is config-only once A1 lands, and it is the direct test of
the confirmed mechanism.

**Re-run the control command:**

```powershell
leggie analyze Inputs/OE_ΣΧΝ-ΥΠΔΙΚ.pdf --articles "1-10" -o Outputs/subset3 2>&1 | Tee-Object subset3.log
python .claude/skills/leggie-diagnostics-and-tooling/scripts/smoke_log_stats.py subset3.log
python .claude/skills/leggie-diagnostics-and-tooling/scripts/findings_stats.py "Outputs/subset3/OE_ΣΧΝ-ΥΠΔΙΚ_findings.json" --articles 10
```

**Exit gate (vs. the subset2 control) — actual subset3 results:**

| Metric | subset2 (control) | Required | subset3 (actual) | Gate |
|---|---|---|---|---|
| `skeptic_llm_error` | 5 / 8 = 62.5% | ≤ 10% of calls | 2 / 6 = **33.3%** | FAIL (big improvement, not enough) |
| `skeptic_verdict` lines | 3, all `supports` | ≥ 6, **≥1 non-`supports`** | 4, all `supports` | FAIL |
| `Failed to parse structured response` | 13 / 90 = 14.4% | < 5% of calls | 10 / 83 = **12.0%** | FAIL |
| `Response truncated` | 5 (all @2048) | ≤ 1 | **0** | **PASS** |
| findings | 7 (0.70/article) | ≥ 7 | 6 (0.60/article) | FAIL (marginal) |

The verdict-diversity row matters more than it looks: a critic that only ever
says `supports` is indistinguishable from a critic that is off. v5's
9-refutes/9-supports split is the shape of a working gate.

**Read this honestly, not as a failed fix.** `Response truncated` going to
exactly zero is not noise — it's D11's predicted signature disappearing
completely, confirming the mechanism and the fix. But `Failed to parse
structured response` barely moved (14.4% → 12.0%) while truncation-caused
failures vanished, which means **most of subset3's remaining parse failures
were never truncation to begin with** — a second mechanism was hiding behind
D11's larger one. That's D15.

### D15 — A second structured-output failure mode, independent of truncation — **MEDIUM, ROOT-CAUSED, FIXED (confirmed improvement; one observability gap left open, see below)**

**Layer:** Infrastructure (`adapters/openrouter.py`) + Application (`llm/__init__.py`).

**Evidence:** `subset3.log` shows 10 `Failed to parse structured response`
(2 skeptic, 4 CoVe across `_plan_llm_questions`/`_cross_check`, 2
`lens_degraded` on the `lens_analysis` route) with **zero** accompanying
`Response truncated` lines — so `finish_reason` was not `length` for any of
them. The retry ladder's truncation branch (attempt 3) never engaged; these
exhausted through attempts 1/2/4 (json_schema → json_object → repair) on
content that was not cut short by the token ceiling.

A follow-up 3-article DEBUG-level probe (`subset4_debug.log`) to capture the
new A4 `structured_response_exhausted` log line was **inconclusive by
accident, not by evidence**: those 3 articles produced zero lens findings
(0 findings, 0 reports — likely TOC/preamble entries per the known
duplicate-id pattern from `docs/SMOKE_AUDIT.md`), so skeptic/CoVe never ran
and the line was never exercised. This is a wasted-run lesson, not a result —
**do not conclude anything from subset4_debug.log.**

**What's ruled out:** it isn't the `_IRAC_ALIASES` ladder (`_normalize_findings`
in `structured_parser.py` only touches a `data["findings"]` list, which
skeptic/CoVe schemas — flat objects like `SkepticVerdictResponse` — never
have).

**Root cause, found offline (free) after a targeted paid probe (`Outputs/subset5_debug/`,
articles 5/8/9 — chosen from `leggie parse`'s free id→title dump, all
confirmed substantive: "Απόρριψη αγωγής...", "Εγγυοδοσία...", "Κυρώσεις...").
That probe reproduced the failure (3 findings, 1 skeptic error + 2 CoVe
errors, still zero truncation) but A4's `structured_response_exhausted` debug
line **still never fired** — 0 matches across the debug log, despite the
ladder demonstrably exhausting. Chasing *that* absence (not the original
symptom) found the real bug:

`adapters/openrouter.py:92` called `data = resp.json()` with **no exception
handling**, and `choice = data.get("choices", [{}])[0]` with no guard against
an empty `choices` list. A 200 status does not guarantee a well-formed body.
When either fails, the exception is a plain `json.JSONDecodeError` /
`IndexError` — a `ValueError` subclass — which the retry ladder's generic
`except (LLMError, ValueError)` catches **before `response` is ever
assigned**. A4's debug line only fires `if response is not None`, so this
failure mode was invisible to it by construction: not a logging-level
problem, a code-path gap. The two failure modes (LLM produced malformed
JSON *content* vs. OpenRouter returned a malformed HTTP *envelope*) were
completely indistinguishable in every log this campaign has ever collected.

**Fixed:**
- `openrouter.py`: `resp.json()` wrapped, empty/missing `choices` guarded —
  both now raise `LLMError` carrying a body preview (`resp.text[:500]`),
  instead of an opaque `ValueError`/`IndexError`.
- `llm/__init__.py`: the ladder now tracks `last_exc` across all four
  attempts. The final block logs `structured_response_exhausted` with
  content/usage when `response is not None` (D11's case, working since
  Phase A), **or** with `last_exc` when it isn't (D15's case, new) — so the
  next occurrence of either failure mode is legible without a rerun.
- 5 new tests (`test_phase1_structured_output.py`): malformed-JSON body,
  empty `choices`, missing `choices` key, and the ladder exhausting cleanly
  (not an unbound-variable error) plus logging `last_exc` when `response`
  stays `None` throughout.

**Confirmation run (`Outputs/subset6_confirm/`, same articles 5/8/9, $0.0371):**

| Metric | subset5 (before this fix) | subset6 (after this fix) |
|---|---|---|
| skeptic_llm_error | 1 / 3 findings | **0 / 3** |
| CoVe error | 2 / 3 findings | **1 / 3** |
| `Response truncated` | 0 | 1 (at 2048 tokens) |
| findings | 3 | 3 (same — CoVe fails open, `dropped=False`, so a CoVe error costs verification confidence, not yield) |

**Real, measured improvement on identical articles/config** — skeptic error
rate 33%→0%, CoVe error rate 67%→33%. Whatever the openrouter.py guard
changed, it made a practical difference, not just a diagnostic one.

**One thread not closed.** `structured_response_exhausted` — my own new debug
line — **still did not fire once** in subset6_confirm.log, despite the ladder
demonstrably exhausting (the CoVe error's message is unambiguously the
final-raise text, confirmed as the only occurrence of that string in the
entire repo). This is NOT a general logging bug: an offline reproduction
(`configure_logging()` called exactly as `main()` does, `LEGGIE_LOG_LEVEL=DEBUG`,
a mocked LLM returning unparseable-but-braced content) fires the exact same
debug line correctly. The gap only appears inside a real concurrent CLI run.
Separately, the one `Response truncated` event fired at exactly 2048 tokens —
`_DEFAULT_CROSSCHECK_MAX_TOKENS`'s fallback value, not `evidence_verification`'s
configured 8192 — with no `cove_route_failed` warning logged, meaning the
router did not raise; why `route` would resolve to `max_tokens=8192` for two
of three findings' CoVe calls but a 2048-shaped outcome for the third,
in the same process, same router instance, is unexplained.

**Decision: stop here, do not chase further right now.** Both observations
are now purely diagnostic/observability gaps — the functional behavior
(fail-open, degradation events via `on_degradation`, findings preserved) is
correct regardless of whether the debug line fires or which ceiling a given
call used. The measured error-rate improvement is real and stands on its
own. Re-open this investigation if a future full-lens run shows the same
symptom at higher volume, where the pattern (which finding index, which
concurrency slot) might become statistically distinguishable instead of an
n=1 anomaly.

---

## 5. Phase D — Per-lens attribution: smoke the 4 unproven lenses (~$0.20)

**Gate status: STILL FAIL, re-measured (`Outputs/subset7/`, articles 1-10,
$0.0989).** Full comparison against the official Phase C exit gate (§4):

| Metric | subset2 control | Required | subset3 (D11 only) | subset7 (D11+D15) | Gate |
|---|---|---|---|---|---|
| `Response truncated` | 5 | ≤1 | 0 | **0** | **PASS** (3rd confirming run) |
| findings | 7 (0.70/art.) | ≥7 | 6 (0.60/art.) | **7 (0.70/art.)** | **PASS** — matches control exactly |
| `skeptic_llm_error` | 5/8=62.5% | ≤10% of calls | 2/6=33.3% | 3/7=**42.9%** | FAIL |
| ~~`skeptic_verdict` diversity~~ | 3, all supports | ~~≥1 non-supports~~ | 4, all supports | 4, all supports | **RETIRED** — measures critic strictness, not correctness; see §D17 |
| parse-failure rate | 13/90=14.4% | <5% of calls | 10/83=12.0% | 11/76=**14.5%** | FAIL — flat vs. control |

**Read honestly:** two rows are unambiguous wins, confirmed across three
separate runs now (subset3/5/6/7) for truncation specifically. But
skeptic/parse-failure rates did not improve on this larger, differently-composed
sample the way the paired subset5→subset6 comparison suggested (that
comparison held articles fixed; this run's specific findings mix differs run
to run, since the lens itself is not temperature-0, so it is not a clean
paired comparison — read subset3→subset7 as "still broadly flat," not as
contradicting subset5→subset6's real, controlled improvement).

**What's left unexplained, in order of likely payoff:**
1. ~~Verdict diversity stuck at unanimous `supports`.~~ **RESOLVED — not a
   bug (2026-07-17).** See §D17 below.
2. ~~Parse-failure rate flat at ~14.5%.~~ **One concrete mechanism found and
   fixed (2026-07-17), aggregate effect unmeasured.** See §D18 below.
3. The two open observability gaps from §D15 (debug line not firing;
   2048-ceiling truncation with no route-failure warning) — still n=1/n=3,
   still not reproduced with enough volume to localize. The debug-line-not-firing
   was chased to ground offline (§D18 note): the ladder + logging code is
   correct — it fires in five isolated reproductions including the full
   BudgetGuard→Adapter→CoVe stack — so the real-run non-appearance is an
   environment interaction (most likely the CLI's UTF-8 stdout reassignment
   binding the log handler to a stale stream, plus Greek `content_head`
   triggering encode errors that `logging` swallows). Cosmetic; not chased
   further.

**Decision needed before Phase D:** the truncation-specific fix (D11) is
proven and should stay. Whether to treat parse-failure/skeptic-error rate as
"good enough, ship it" (both are dramatically better than the original
pre-campaign baseline, even if not meeting the exact §10 numeric bar) or to
keep investigating is a call for the user, not something to decide unprompted
by spending further. The verdict-diversity row of the §4 gate should be
**retired** — see §D17.

### D17 — "Verdict diversity" measures critic STRICTNESS, not correctness — **the gate row is wrong, not the pipeline**

**Question:** the adversarial critic returned unanimous `supports` on every
constitutional-lens run since v5's one-time 9-refutes/9-supports split
(2026-07-11), which the §4 gate treats as a FAIL ("≥1 non-`supports`
required").

**Discriminating experiment (`scratchpad/verdict_diversity_probe.py`, ~$0.20):**
the exact `LLMAdversarialGate` prompt (`skeptic.py`), on 4 real subset7
findings (the observed all-`supports` baseline) + 2 deliberately-broken
controls (a fabricated `Άρθρο 250 Σ` — the Constitution has 120 articles; and
a religious-freedom-via-court-fees non-sequitur), through both
`google/gemini-2.5-pro` (current critic) and `anthropic/claude-sonnet-4.6`.

| | real ×4 | broken ×2 |
|---|---|---|
| gemini-2.5-pro | supports ×4 | **refutes ×2** |
| claude-sonnet-4.6 | **refutes** (substantive) | refutes |

**Result — three things established:**
1. **Pro is not agreement-biased.** It refuted both broken controls, naming
   the exact defect ("το Σύνταγμα… αριθμεί 120 άρθρα… Άρθρο 250… δεν υπάρχει").
   A critic that catches fabricated articles and non-sequiturs but supports the
   real findings is working correctly — the unanimous `supports` reflects that
   subset7's 7 findings clear a reasonable correctness bar, **not** a broken
   critic.
2. **Sonnet is a genuinely stricter critic.** It *refuted* a real finding Pro
   supported, on legitimate grounds (empty `Εφαρμογή` field → no bridge from
   rule to conclusion; triple-hedge "ενδέχεται να παραβιάζει" with no
   proportionality-test analysis; ΕΣΔΑ Art.6 cited without case law). Not
   wrong — but a strictness/policy difference, not a correctness one. This is
   almost certainly why v5 (whose critic was Gemini **flash**, per SMOKE_AUDIT
   v3 "Anthropic→Gemini" with the Pro upgrade applied *after* v5 — correcting
   an earlier mis-attribution in this campaign's notes) showed more refutes:
   the critic model, not the pipeline, sets the refute rate.
3. **Sonnet is not a drop-in.** Its raw output with the current prompt is
   markdown prose with the verdict buried in a fence (` ```VERDICT: refutes``` `),
   which the production JSON parser can't consume (all 6 probe calls came back
   unparseable). Switching the critic to Sonnet would reintroduce parse
   failures unless its output-format contract is fixed first.

**Conclusion:** the "≥1 non-`supports`" gate criterion measures how *strict*
the critic model is, not whether the pipeline is healthy. On a clean finding
set with a reasonable-bar critic, unanimous `supports` is the correct outcome.
**Retire it as a hard gate row; keep it as an observability signal.** If
stricter adversarial review is wanted, that is a scoped *product* change
(tighten Pro's prompt to raise its bar, OR adopt Sonnet + fix its JSON-output
contract), each with a real cost — refuting under-argued-but-not-wrong
findings discards genuine constitutional concerns — and is out of scope for
closing this campaign. **No code change made; this is a gate-definition fix,
not a pipeline fix.**

### D18 — Out-of-range numeric DTO fields REJECTED the whole structured response — **HIGH, one mechanism fixed; aggregate effect on the 14.5% unmeasured**

**Free offline analysis** of the two schemas that fail most
(`SkepticVerdictResponse`, `CoVeCrossCheckResponse`) plus the lens schema:
each had a hard-bounded float — `confidence_adjustment` (`ge=-0.5, le=0.5`)
and `IRACCandidate/VSCandidate.probability` (`ge=0.0, le=1.0`). A model that
emits e.g. `confidence_adjustment: -0.8` or `probability: 1.5` produces valid
JSON that **pydantic then rejects** with a `ValidationError` — a `ValueError`
the `generate_structured` ladder catches, retries identically (temperature 0),
and exhausts, discarding the entire verdict / whole finding-set. Verified
offline: `StructuredResponseParser.parse` rejected these payloads before the
fix, clamps-and-accepts after.

Same *class* as the earlier "strip `minimum`/`maximum` from the json_schema so
providers don't 400" fix (`test_number_constraints_stripped_for_provider_compatibility`),
but on the **pydantic-validation** side, which that fix never touched — so a
model freed to emit out-of-range values in `json_object` fallback mode then
hit a local hard bound.

**Fix (`structured_output.py`):** replaced the hard `ge/le` bounds with
`mode="before"` field validators that **clamp** (`-0.8 → -0.5`, `1.5 → 1.0`,
non-numeric → the field's neutral default) instead of raising. Safe because
both consumers (`skeptic.review`, `cove._apply_revision`) already clamp the
*final* `Confidence.score` into `[0,1]`, and the real invariant
(`Confidence.score ∈ [0,1]`) is untouched — so this is a DTO-robustness change,
not a weakening of a Domain invariant (the fenced `Finding`/`IRAC`/`Confidence`
entities are unchanged). 4 new tests; 550 passed, mypy/ruff/lint-imports clean.

**Confirmation run (`Outputs/subset8/`, same command as subset3/7, $0.1101):**

| Metric | subset7 (pre-D18) | subset8 (post-D18) | Read |
|---|---|---|---|
| parse-failure rate | 11/76 = 14.5% | **8/89 = 9.0%** | Real drop (~38% relative) — gate (<5%) still not met, but D18 is a genuine contributing mechanism, not a no-op |
| CoVe error rate | 4/7 = 57.1% | **2/7 = 28.6%** | Real improvement |
| skeptic error rate | 3/7 = 42.9% | 4/7 = 57.1% | Worse — noisy, small-n (different findings mix each run; not paired) |
| `Response truncated` | 0 | **3** (at 2048, 2048, **1024**) | Reopens something — see below |
| findings | 7 | 7 | Held |

**D18 is confirmed as a real, positive mechanism** — parse-failure rate and
CoVe error rate both dropped substantially in a like-for-like re-run. It is
not the *only* mechanism (rate is still well above the 5% gate), and the
remaining failures need real content to diagnose further, which the §D15
debug-line gap still denies us.

**But truncation reappeared, and one instance is diagnostic.** A 1024-token
truncation can only come from `_answer_factored`'s route-`None` fallback
(`_DEFAULT_ANSWER_MAX_TOKENS`) — `evidence_verification`'s configured ceiling
is 8192, and no code path produces 1024 except that specific fallback. This
is the **same unexplained pattern as subset6's confirmation run** (a 2048
truncation with no `cove_route_failed` warning, `route.max_tokens` not
reaching the request) — now reproduced a second time, on a *different* CoVe
call site (`_answer_factored` instead of `_cross_check`), across a different
run. Two independent occurrences, in two different steps of the same
`CoVeVerifier`, both with zero exception logged, is no longer a comfortable
"n=1 anomaly" — it looks like `_select_route()` intermittently returns `None`
(or a route with the wrong `max_tokens`) for a small fraction of calls,
silently, for a reason not yet identified. **Re-opened as a genuine
observability/correctness gap, not filed away.** Next step if pursued: log
`route` at DEBUG (not just on failure) for every `_select_route()` call, so
a future run can show the resolution rate directly instead of inferring it
from truncation ceilings after the fact — a $0 code change, gated behind the
same DEBUG-visibility problem noted in §D15/§D18's observability item, which
still needs solving first for the log line to actually appear.

```powershell
foreach ($lens in "economic","eu_gdpr","implementation","legal_coherence") {
  leggie analyze Inputs/OE_ΣΧΝ-ΥΠΔΙΚ.pdf --articles "1-10" --lenses $lens `
    -o "Outputs/lens_$lens" 2>&1 | Tee-Object "lens_$lens.log"
  python .claude/skills/leggie-diagnostics-and-tooling/scripts/smoke_log_stats.py "lens_$lens.log"
}
```

**Per lens:** parse failures < 5% of calls, ≥1 finding, `info_filler_ratio` = 0%,
no lens-specific crash signature (the `NoneType verbatim_quote` class of bug was
found in `constitutional_lens.py` and the other four have never been exercised
live — expect at least one to surface something).

**Branch:** a lens that fails alone will fail in the full run — fix it here,
where the log has one lens in it, not in a 3,000-line 5-lens log.

**Gate:** all 5 lenses individually green on the subset.

---

## 6. Phase E — The full 5-lens run (the campaign's actual goal) (~$2–3)

**Pre-flight, in order — the 402 wall already cost one full run:**
1. OpenRouter credit balance ≥ $10 (check the dashboard; the guard does not see it).
2. `LEGGIE_LLM__MAX_TOKENS_PER_RUN` — confirm not the stale 500k (playbook row 6;
   `.env.example` drift is known).
3. `max_cost_per_run` = $5, unchanged. **Fenced: do not raise it to finish a run.**
4. Budget forecast from Phase C/D actuals: v5 was $0.3577 for 1 lens × 121
   entries. 5 lenses ≈ 5× lens calls and ≈5× skeptic/CoVe volume ⇒ ~$1.8
   baseline, plus whatever Phase C's ceiling change adds. **If the forecast
   exceeds $4, stop and reconsider Phase C option 3 (flash critic) rather than
   starting a run that will hit the cap mid-flight.**
5. Checkpoint: this branch writes it to `-o`'s directory, so a mid-run stop is
   resumable — verify `Outputs/full5_v3/leggie_checkpoint.json` appears early.

```powershell
$env:LEGGIE_LLM__MAX_CONCURRENCY=10
leggie analyze Inputs/OE_ΣΧΝ-ΥΠΔΙΚ.pdf -o Outputs/full5_v3 2>&1 | Tee-Object full5_v3.log
```

**Exit gate — `REMEDIATION_PLAN.md` §10, all rows, numbers only:**

| Gate | Required | Anchor |
|---|---|---|
| Yield | findings/article ≥ single-lens 0.14, and > 5× v5's 11 survivors is *not* required — proportionality is | §10 "roughly proportional, not ~1" |
| Parse failures | < 5% of LLM calls | §10 |
| Skeptic | non-neutral verdicts present; `skeptic_llm_error` < 10% of gate calls | §10 + D13 counter |
| CoVe | drop/revise observed on valid inputs; `cove_quote_fail` only where quotes are genuinely absent | §10 |
| Wall-clock | completes without the serial timeout | §10 "cut materially by parallel fan-out" |
| Spend | < $5 | budget policy |
| Filler | `info_filler_ratio` = 0% (historical pathology: 68%) | findings_stats |

**Branch — if it degrades again:** stop, count signatures, attribute to a lens
or a route using Phase D's per-lens baselines, and change ONE thing. Do not
restart the full run to "see if it's better".

---

## 7. Phase F — Deliberative pipeline: first live run (gated, separate)

Landed and leak-fixed (PR #7, `af4e4a8`), **offline-proven only**. This is a
distinct pipeline (`--pipeline deliberative`) with its own failure surface; it
does not block Phase E and Phase E does not validate it.

**Pre-flight:**
- Verify the env-var spelling *empirically* — `ReasonerSettings` declares
  `env_prefix="LEGGIE_REASONER_"` (`settings.py:105`) while `cli_handlers.py`
  tells users `LEGGIE_REASONER__ENABLED` (double underscore) and
  `tests/conftest.py` blanks **both** spellings. One of those is wrong. Confirm
  with a throwaway `python -c "from leggie.config.settings import get_settings;
  print(get_settings().reasoner)"` under each spelling **before** blaming the
  Reasoner backend for being unreachable.
- Reasoner backend reachable at `base_url`; `LEGGIE_REASONER__HOME` set if
  relying on autostart.
- Pre-flight budget abort and the autostarted-process release path both have
  playbook entries — read **leggie-debugging-playbook** §deliberative first.

**Run:** subset first (`--articles "1-5"`), never the full bill cold.

**Gate:** completes; produces findings; the autostarted backend process is
released on exit (the PR #7 leak — verify no orphaned process); spend recorded.

**Branch:** if the Reasoner is not installed/reachable on this machine, **stop
and record that** in the audit doc as OPEN. An unrunnable phase is a documented
gap, not a failure to hide.

---

## 8. Phase G — Land it through change control

The code landed without evidence once already; that gap is what this campaign
exists to close.

1. Final offline sweep (Phase A's gate, re-run).
2. Write `docs/SMOKE_AUDIT_V3.md` — template: **leggie-docs-and-writing** §3
   audit-report. Must contain the measured before/after table (subset2 control
   → subset3 → per-lens → full5_v3), the §10 gate matrix with PASS/FAIL per row,
   the D11 mechanism with its Phase B measurement, and spend.
   **Copy the smoke logs into the audit doc.** The `full5_final` log is gone and
   its 111 errors are now unreproducible evidence — do not repeat that.
3. Close H-2 explicitly (campaign Phase 2): either the looks-like-JSON heuristic
   with a unit test, or a written acceptance of the bounded 1-call residual risk.
   Pick one; "partial" is not a landing state.
4. Commit in project style, referencing IDs:
   `fix: honour route max_tokens in skeptic/CoVe (D11/D12)`,
   `docs: full 5-lens smoke audit — D11 root cause and §10 gate results`.
5. README drift while touching docs (**leggie-docs-and-writing** §5): test badge
   199 → current, code-lines, ports count 7 → 10, `.env.example`
   `MAX_TOKENS_PER_RUN=500000` → 20,000,000.
6. Update the campaign skill's state table — it is dated 2026-07-14 and this
   plan invalidates its "next concrete step".

---

## 9. Execution order & dependencies

```
A (offline: D12 fix, D13 counter, D14, instrumentation)   FREE
└─> B (measure reasoning tokens — confirm or kill D11)     ~$0.02
    └─> C (fix D11, one variable, vs subset2 control)      ~$0.10
        └─> D (4 unproven lenses, per-lens attribution)    ~$0.20
            └─> E (FULL 5-LENS — the goal)                 ~$2–3
                └─> G (audit doc + change control)         FREE
    F (deliberative live run) ── independent of C/D/E ──────┘  ~$0.10
```

A→B is mandatory: without A1, Phase C's config change is a no-op (D12).
B→C is mandatory: without B, C is a guess dressed as a fix.
D→E is the discipline the last two full-run attempts skipped.

**Total forecast: ~$3.50.** The campaign has already burned more than that on
runs that produced no output files.

---

## 10. Architecture guardrails (every phase)

- **Dependency rule:** A1/A2 are Application-layer edits reading a value the
  RouterPort already returns. No new ports, **no new port methods** (fenced).
- **Config in config:** ceilings belong in `routes.yaml`, reached through
  `RouteResult` — that is the entire point of D12. Do not fix D11 by editing a
  new hardcoded number into `skeptic.py`.
- **No silent failure:** D13 is the plan taking its own no-silent-failure rule
  seriously. Do not add a second swallow while fixing the first.
- **Domain untouched:** `Finding`, `IRAC`, `Confidence` are not in scope.
- **Hermetic tests:** `tests/conftest.py` stays; no test may reach a provider.
- **One variable per paid run**, control comparison always named.

---

## 11. Definition of done (measurable — no prose verdicts)

1. `Outputs/full5_v3/OE_ΣΧΝ-ΥΠΔΙΚ_findings.json` exists from an all-5-lens run
   on `Inputs/OE_ΣΧΝ-ΥΠΔΙΚ.pdf` that completed without being stopped.
2. Every §10 row in Phase E's gate table is PASS with a cited number.
3. `skeptic_llm_error` < 10% of adversarial-gate calls, and ≥1 non-`supports`
   verdict — the critic is demonstrably on.
4. Parse failures < 5% of LLM calls.
5. Spend < $5, recorded from `flow.budget_state`.
6. All 5 lenses have at least one green single-lens smoke on record.
7. `docs/SMOKE_AUDIT_V3.md` committed with the before/after table and the logs
   pasted in.
8. Offline: pytest ≥ 532 passed, mypy/ruff/lint-imports clean.
9. Deliberative pipeline: one live run recorded — PASS, or OPEN with the reason.

Until 1–8 are all true, the campaign is not done. Item 9 may land as OPEN.
