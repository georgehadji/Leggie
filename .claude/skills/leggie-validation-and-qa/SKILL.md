---
name: leggie-validation-and-qa
description: >
  What counts as EVIDENCE in Leggie, tiered from unit tests up to live-smoke
  numbers and eval deltas. Load when writing tests, judging whether a change
  is proven, adding gold-set labels, or defining acceptance criteria. Covers
  the fake-MVP doctrine (green tests are never sufficient), the acceptance
  thresholds, the golden inventory, and the project's test-writing patterns
  (async auto mode, FakeLLM adapters, malformed-payload regression fixtures).
---

# Leggie Validation and QA

## 1. The doctrine anchor

The MVP shipped with **199 green tests and was a fake product**: no LLM was
ever called, lenses were regex stubs, and `leggie eval` scored EMPTY findings
lists — precision 0, RDI −1 for every bill, and nobody noticed until a human
read the output (`docs/FIX_PLAN.md` Part 1). The stub-era `eval_results.json`
is still checked in as the fossil. Consequence, non-negotiable: **green tests
are necessary, never sufficient.** Behavior-changing claims require measured
runtime evidence.

## 2. Evidence tiers (what each change class must show)

| Tier | Evidence | Command | Required for |
|---|---|---|---|
| 1 | full test suite green | `python -m pytest tests/ -q` → baseline **361 passed** (measured 2026-07-10) | every change |
| 2 | mypy strict clean on touched modules + ruff + import-linter | `mypy leggie/ --ignore-missing-imports && ruff check leggie/ tests/ && lint-imports` | every code change |
| 3 | live smoke with MEASURED numbers | procedure in **leggie-run-and-operate** §3, measurement via **leggie-diagnostics-and-tooling** | class-A (pipeline-behavior) changes |
| 4 | eval delta vs gold set | `leggie eval --gold-set tests/eval/gold_set_sample.json` before/after | quality claims ("improves recall/precision") |

Change classes: **leggie-change-control** §1.

## 3. Acceptance thresholds (never weaken to pass)

From `docs/REMEDIATION_PLAN.md` §10 — the current definition of done:

- findings roughly **proportional to article count** (not ~1)
- **<5%** parse-failure rate across LLM calls
- skeptic produces **some non-neutral verdicts** (parse not blocking it)
- CoVe drop/revise observed **only on invalid inputs** (fabricated quotes)
- full pytest green; mypy clean; **no new ports**
- spend **< $5**/run

Thresholds change only through change control with rationale — never inline
to make a run pass. Output quality is judged by these numbers, never by eye.

## 4. Golden inventory (certified artifacts)

| Artifact | Role | Notes |
|---|---|---|
| `tests/eval/gold_set_sample.json` | gold labels: 2 bills × 3 labels each | schema per label: `article_id, finding_type, description, severity, citation_text` (nullable); finding_types match the `FindingType` enum |
| `Inputs/OE_ΣΧΝ-ΥΠΔΙΚ.pdf` | canonical live-smoke bill | Greek filename — quote the path |
| `tests/unit/infrastructure/test_phase1_structured_output.py` | regression fixtures built from REAL malformed LLM payloads captured in the smoke log | the pattern to copy when a new drift alias appears |
| `Outputs/OE_ΣΧΝ-ΥΠΔΙΚ_findings.json` (checked in) | FOSSIL of the 1-survivor pathology | historical exhibit, not a target |
| `eval_results.json` (checked in, gitignored for future runs) | FOSSIL of stub-era all-zero eval | regenerate, don't trust |
| `analysis_report.md`, `e2e_test_results.json` | historical exhibits | stub-era; reference only |

## 5. How to write tests here (verified patterns)

- **Layout mirrors the package**: `tests/unit/{domain,application,infrastructure}/`,
  plus `tests/integration/test_e2e_pipeline.py` and root-level
  `tests/unit/test_{cli,config,observability}.py`.
- **Async**: pyproject sets `asyncio_mode = "auto"` — write
  `async def test_...` directly, no `@pytest.mark.asyncio` needed.
- **Fakes over mocks**: the house pattern is small `FakeLLM`-style classes
  implementing the port surface (e.g. `class FakeLLM` in
  `tests/unit/application/test_skeptic.py:17`; also in test_cove_verifier,
  test_di, test_port_contracts, test_verbalized_sampling). Copy that pattern;
  avoid MagicMock for ports.
- **Regression fixtures from reality**: when a new LLM failure mode appears,
  capture the actual malformed payload into the phase1 test file and assert it
  now parses/repairs.
- **AAA structure** and descriptive behavior names
  (`test_fallback_to_json_object_on_400`-style).
- **Coverage**: `python -m pytest tests/ --cov=leggie --cov-report=term-missing`;
  pyproject `fail_under = 80`. (Current exact % UNVERIFIED here — run it.)
- **hypothesis** is installed as a dev dep; property-based tests are not yet
  an established pattern in the suite (candidate, not doctrine).

## 6. Extending the gold set

Add entries per bill_id to a gold-set JSON following
`tests/eval/gold_set_sample.json`: `article_id` (bill article), `finding_type`
(one of the FindingType enum values), `description` (specific, testable),
`severity`, `citation_text` (ΦΕΚ/CELEX/Ν. format or null — see
**greek-legal-domain-reference** §2). Source labels from Επιστημονική Υπηρεσία
Βουλής reports (the expert baseline). Bigger gold set = prerequisite for every
research-frontier claim.

## 7. CI reality

CI (ubuntu) runs ruff + mypy + pytest only. It does NOT enforce coverage,
import-linter, bandit, live smoke, or eval. Tier 3/4 evidence is always
produced locally on Windows. Do not cite "CI is green" as tier-3 evidence.

## When NOT to use this skill

- Measurement script mechanics → **leggie-diagnostics-and-tooling**
- Gates and commit workflow → **leggie-change-control**
- Designing an experiment / proving a mechanism → **leggie-research-methodology**
- Env/test-runner problems → **leggie-build-and-env**

## Provenance and maintenance

Dated 2026-07-10. Re-verify:
- Baseline: `python -m pytest tests/ -q` (was 361 passed)
- Async mode: `grep -n asyncio_mode pyproject.toml`
- Fake pattern: `grep -rn "class Fake" tests/ | head`
- Gold set size: `python -c "import json;d=json.load(open('tests/eval/gold_set_sample.json',encoding='utf-8'));print({k:len(v) for k,v in d.items()})"`
- Thresholds: `grep -n "Definition of done" -A10 docs/REMEDIATION_PLAN.md`
