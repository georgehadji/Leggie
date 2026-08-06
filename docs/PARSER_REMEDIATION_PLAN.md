# Parser Remediation Plan

**Created:** 2026-07-28
**Trigger:** `docs/IMPROVEMENT_RESEARCH.md` — every live run analyzed the table
of contents, not the bill.
**Scope:** fix the parse layer, make "we analyzed the wrong text" a detectable
failure, then re-establish a trustworthy baseline.

**Architecture authority:** `docs/ARCHITECTURE.md`, the import-linter contract
in `pyproject.toml`, and the guardrails in `REMEDIATION_PLAN_V3.md` §10.

---

## 1. Root causes — measured, not inferred

All three reproduce against `Inputs/OE_ΣΧΝ-ΥΠΔΙΚ.pdf` through the exact call
`bill_analysis_flow._do_parse` makes.

| # | Defect | Mechanism | Damage |
|---|---|---|---|
| **P-1** | Stop-list rejects legitimate headings | `_STOP_PATTERN` matches «της Οδηγίας» in `Άρθρο 2 Αντικείμενο (άρθρο 1 της Οδηγίας (ΕΕ) 2024/1069)` — the standard drafting form for an EU-transposition bill | **13 articles lost**: 2,3,4,5,7,8,9,10,13,19,38,60,77 |
| **P-2** | Heading regex crosses newlines | `^\s*Άρθρο\s+(\d+…)\s*[—–\-]?\s*(.*?)$` — `\s` matches `\n`, so `Άρθρο 5\n(άρθρα 6, 7… της Οδηγίας…)` absorbs the *next* line into the heading, then P-1 rejects it | compounds P-1; corrupts titles |
| **P-3** | TOC parsed as articles | TOC lines are line-anchored `Άρθρο N <title>` and match identically to real headings | **43 duplicate IDs**, 50 sub-200-char records |

Secondary: the monotonic guard (`delta > 50`) silently `continue`s — 160
surviving candidates become 121 articles, so ~39 are dropped with no record.

**Net:** 121 records for a 91-article bill; 78 distinct IDs; `--articles 1-10`
resolves to `['1','6']`, both TOC fragments.

### The decisive evidence for P-1

The three cross-reference tests that justify `_STOP_PATTERN`
(`test_rejects_directive_ref`, `test_rejects_law_ref`, `test_rejects_code_ref`)
use *in-body, lowercase* references such as `Κατά το άρθρο 14 της Οδηγίας…`.
Those never match `^\s*Άρθρο` — wrong case, not line-anchored. The line-anchor
already rejects them.

**Verified:** disabling the stop-list entirely leaves **all 19 parse tests
passing**. It has no demonstrated benefit on the test corpus and costs 13
articles on the real one. This is a guard that only ever fired on false
positives.

---

## 2. Architectural constraints this plan honours

From the import-linter contract (`pyproject.toml`), layers are strictly:

```
leggie.interfaces → leggie.infrastructure → leggie.application → leggie.domain → leggie.config
```

| Constraint | How the plan respects it |
|---|---|
| **Dependency rule** | Parsing stays in `infrastructure/parse/`; it may import Domain (inward), never Application. The integrity report is a Domain value object, so both Infrastructure (producer) and Application (consumer) may depend on it. |
| **Ports & Adapters** | `ParsePort` is the Application-side contract. §5 adds **one** optional method rather than a new port; §4 explains why the alternative was rejected. |
| **No silent failure** (D13 lineage) | Every candidate the parser discards is *counted and reported*, never dropped invisibly. This is the campaign's hardest-won discipline and the reason P-1/P-3 went unseen. |
| **Config in config** | Thresholds (TOC run-length, min body length, monotonic delta) move to `config/settings.py`, not magic numbers — the D12/D21 lesson. |
| **Many small files** | `parse/__init__.py` is 217 lines and this work roughly doubles it. §3 splits the package *before* changing behaviour. |
| **Immutability** | New domain types are frozen Pydantic models, consistent with `Document`/`Article`. |
| **Hermetic tests** | All parser work is offline. No test may reach a provider (`tests/conftest.py` stays). |
| **One variable per paid run** | §8 re-baselines with exactly one change from `full5_v5`: the parsed document. |

---

## 3. Phase 0 — Characterize before changing (offline, free)

Refactoring a parser with no ground truth is how P-1 survived. Lock behaviour
down first.

1. **Commit a real fixture.** Extract the ingested text of
   `OE_ΣΧΝ-ΥΠΔΙΚ.pdf` once to
   `tests/fixtures/parse/oe_sxn_ypdik.txt`. Greek legislative text is public
   record; keep it whole so it stays representative.
   Add a trimmed `toc_and_first_articles.txt` (TOC block + articles 1–10) as the
   fast fixture for unit tests.
2. **Write characterization tests** asserting *today's wrong* behaviour
   (121 records, 43 duplicate IDs, 13 missing, `1-10` → `['1','6']`), each
   marked `xfail(strict=True)` with the defect ID. As each defect is fixed the
   xfail flips to a pass and the marker is deleted — the suite proves the fix
   rather than merely tolerating it.
3. **Establish expected truth.** Assert the bill has exactly **91** articles
   numbered 1..91 contiguously, derived from the document's own structure.

**Gate:** fixture committed; characterization tests fail exactly where the
defects are and nowhere else.

---

## 4. Phase 1 — Split the parse package (pure refactor, zero behaviour change)

`leggie/infrastructure/parse/__init__.py` currently holds patterns,
preprocessing, article extraction, paragraph extraction, and citations in one
file. Target layout:

```
leggie/infrastructure/parse/
  __init__.py      # DocumentParser facade + re-exports; public API unchanged
  patterns.py      # ARTICLE_HEADING, PARAGRAPH_*, citation regexes
  preprocess.py    # newline repair, normalisation
  toc.py           # NEW — table-of-contents detection
  articles.py      # article segmentation + candidate filtering
  structure.py     # paragraphs / subparagraphs
  citations.py     # citation extraction
  integrity.py     # NEW — validation, produces the domain report
```

Rules for this phase:

- `from leggie.infrastructure.parse import DocumentParser, ARTICLE_HEADING,
  ParseError, …` must keep working — `test_parse.py` and my probe scripts import
  these directly.
- **No behaviour change.** The 19 existing tests and the Phase 0
  characterization tests must produce byte-identical parse output.

**Gate:** full sweep green; characterization tests still fail in exactly the
same places (proving the refactor moved nothing).

**Rollback:** this phase is independently revertable; nothing depends on it.

---

## 5. Phase 2 — Fix the three defects, one at a time

One defect per commit, each with a test that is **falsified** (reverted-fix →
test fails) before it is trusted. That discipline is what made the D21 fix
credible; a test that passes both with and without the fix proves nothing.

### 5.1 P-2 first — heading must not cross lines

Ordering matters: P-2 corrupts the heading text that P-1's rule inspects, so
fixing P-1 against corrupted input would encode the corruption.

- Constrain the heading to a single line: replace `\s*` separators with
  `[ \t]*`, and bound the title with `[^\n]*`.
- Test: `Άρθρο 5\n(άρθρα 6, 7 … )` yields id `5` with the parenthetical as
  *body*, not title.

### 5.2 P-1 — stop the false rejections

Evidence says the stop-list is inert on legitimate input and harmful on real
input. Preferred fix, in order:

1. **Strip parenthesised spans from the heading line before applying the
   stop-list.** `Άρθρο 8 Εγγυοδοσία … (άρθρο 10 της Οδηγίας …)` → tests
   `Άρθρο 8 Εγγυοδοσία …`, which no longer trips the pattern. This preserves
   the guard's stated intent while removing its observed failure mode.
2. If (1) still misfires, **delete `_STOP_PATTERN`** and rely on line-anchoring
   plus the number-shape constraint. §1 shows the test suite does not need it.

Either way, **replace the three misleading tests** with cases that genuinely
exercise the rule: a line-anchored, capital-Ά cross-reference. If no such case
can be constructed from real Greek drafting, that is itself the argument for
option (2), and should be recorded as such.

- Test: all 13 previously-missing articles are present; no article is lost.

### 5.3 P-3 — exclude the table of contents

Detect the TOC as a *structural region*, not by keyword:

- A maximal run of ≥ `toc_min_run` consecutive heading matches whose inter-match
  gap is < `toc_max_body_chars` (i.e. headings with no body between them).
- Take the earliest such run; treat its span as TOC and exclude it from article
  extraction. Record the excluded span in the integrity report.

Thresholds live in `config/settings.py` (`ParserSettings`), not inline
constants.

- Tests: TOC block yields zero articles; the real articles that share those
  numbers appear exactly once; total distinct IDs == total records.

### 5.4 Make the monotonic guard auditable

Keep the guard, but every `continue` appends a `RejectedCandidate(num, reason,
offset)` to the report instead of vanishing. Reasons: `cross_reference`,
`monotonic_jump`, `toc_region`, `duplicate_id`.

**Gate for Phase 2:** parsing the real fixture yields **91 articles, ids 1..91,
no duplicates, no gaps**, and `--articles 1-10` selects 10.

---

## 6. Phase 3 — Integrity as a first-class, typed result

### Domain (`leggie/domain/models/parse_integrity.py`)

```python
class RejectedCandidate(BaseModel):   # frozen
    number: str
    reason: RejectionReason           # StrEnum
    offset: int

class ParseIntegrityReport(BaseModel):  # frozen
    articles_parsed: int
    distinct_ids: int
    duplicate_ids: tuple[str, ...]
    missing_numbers: tuple[int, ...]
    empty_or_heading_only: tuple[str, ...]
    toc_span: tuple[int, int] | None
    rejected: tuple[RejectedCandidate, ...]

    @property
    def is_contiguous(self) -> bool: ...
    @property
    def is_clean(self) -> bool: ...     # the gate predicate
```

**Why Domain:** it is a structural statement about a `Document`, which is a
Domain entity. Infrastructure produces it, Application consumes it — both are
allowed to depend inward. Putting it in Application would force Infrastructure
to import Application and break the layer contract.

### Port (`leggie/application/ports/parse.py`)

Add one optional method rather than changing `parse`'s signature:

```python
def parse_with_integrity(self, text, title="", source_format="txt") \
        -> tuple[Document, ParseIntegrityReport]: ...
```

Default implementation delegates to `parse` and returns an empty report, so
existing `ParsePort` implementations and every current caller keep working.

**Alternative considered and rejected:** attaching the report to `Document`.
That pollutes a Domain entity with parser diagnostics and would ripple through
every `Document` construction site and serialization (including checkpoints).

---

## 7. Phase 4 — Enforce it where the damage happened (Application)

### 7.1 Parse-integrity gate

In `bill_analysis_flow._do_parse`, obtain the report and act on it *before*
`WorkflowState.PLANNING`:

- `is_clean` → proceed.
- otherwise → emit a `DEGRADED` event carrying the report, and **abort by
  default**. A malformed parse must not silently proceed to paid analysis.
- `--allow-degraded-parse` opts into proceeding, logging exactly what is wrong.

Fail-closed is the correct default here: the failure mode being fixed is *paid
runs against the wrong text*.

### 7.2 Selection strictness — the check that would have caught this on day one

`_filter_document` currently raises only when a selection matches **nothing**.
`1-10` matching 2 of 10 passed silently.

- Have `_parse_article_selection` return matched ids **and** the requested
  numeric set.
- For an explicit range, if `matched < requested`, raise with both lists.
- `--articles 1-10` on today's parse must fail loudly with:
  *"selection '1-10' requested 10 articles, matched 2 (['1','6'])"*.

### 7.3 Unify the two parse surfaces (P3 from the research doc)

`leggie parse` emits 91 clean ID-only records; `leggie analyze` builds 121 with
duplicates. Route both through `parse_with_integrity`, and have `leggie parse`
print the integrity summary. The document a user previews must be the document
that gets analyzed.

**Gate:** a test proving `leggie parse` and the analyze flow produce identical
article ids for the same input.

---

## 8. Phase 5 — Re-baseline (paid, ~$0.10, one variable)

Only after Phases 0–4 are green.

1. Re-run the 10-article 5-lens subset under `PYTHONPATH`, per the D20 rule —
   `*_route_resolved` lines must appear or the run measured nothing.
2. Confirm from the log that **10 real articles** were analyzed
   (`lens_route_resolved` × 5 lenses × 10 articles = 50).
3. Record as **`full5_v6`**, explicitly labelled *the first run against correct
   input*.

**Every prior number becomes historical.** `SMOKE_AUDIT_V3.md` §2/§3/§3a/§3b
must be re-headed as *measured against TOC fragments*, not silently updated.
Retaining them is the D20 precedent: retract the interpretation, keep the
evidence.

**Expect the numbers to move, possibly unfavourably.** Real articles are longer
and denser than TOC lines, so parse-failure rate and cost per article may rise.
That is a truer baseline, not a regression.

Then re-derive the cost model: measure cost/article on `full5_v6`, forecast 91,
and only then revisit the N=91 decision and the $5 cap. The current ~$5.01
forecast extrapolates from 2-record runs and should be struck.

---

## 9. Phase 6 — The gold set (the real unlock)

`tests/eval/gold_set_sample.json` holds **2** labelled findings. Precision,
recall, F1 and RDI over n=2 cannot distinguish a working analyzer from one
reading a table of contents — which is precisely what happened.

- Target **50–100 labelled findings across ≥2 bills**, including *negative*
  articles where the correct output is no finding.
- Label schema: article id, lens, issue, severity, and a `is_real_defect`
  boolean, so precision is measurable rather than inferred.
- Needs a Greek-legal reviewer; this is the one phase that is not purely
  engineering. Flagging it as the critical-path dependency for any quality
  claim.

Until this exists, **no statement about analytical quality is measurable** —
only plumbing is. That should be stated plainly wherever quality is discussed.

---

## 10. Phase 7 — Replace yield with correctness

Once §9 lands:

- Demote `findings/article` to a sanity floor; it rewards plausible noise
  identically to real defects.
- Promote precision/recall against the gold set to the §10 gate.
- Re-characterize the skeptic and CoVe on real input. Their entire observed
  behaviour — the verdict spread, the retired D17 diversity row, the persistent
  `verified=0/3` — was measured on TOC-derived findings and is unsafe to
  generalize.

---

## 11. Execution order and dependencies

```
0  Characterize + fixture            free ─┐
1  Package split (no behaviour)      free  │ offline, must land in order
2  P-2 → P-1 → P-3 → audit guard     free  │
3  Integrity model + port            free  │
4  Flow gate + selection strictness  free ─┘
                                        │
5  Re-baseline full5_v6              ~$0.10   ← first trustworthy numbers
                                        │
6  Gold set (needs a legal reviewer)  free, slow
                                        │
7  Precision/recall replaces yield    free
```

Phases 0–4 are free, offline, and fully testable. **Nothing paid should run
until Phase 4 is green** — the last three paid runs measured a table of
contents, and repeating that is the only real waste available here.

---

## 12. Definition of done

| # | Criterion | Measurement |
|---|---|---|
| 1 | Real bill parses to 91 articles, ids 1..91 | fixture test |
| 2 | Zero duplicate ids, zero gaps, zero heading-only bodies | integrity report `is_clean` |
| 3 | TOC contributes no articles | fixture test on the TOC block |
| 4 | `--articles 1-10` selects 10 or fails loudly | flow test |
| 5 | Malformed parse aborts before paid analysis | flow test |
| 6 | `leggie parse` == analyze-flow article ids | parity test |
| 7 | Every discarded candidate is attributable | report `rejected` non-lossy |
| 8 | `full5_v6` log shows 50 lens calls over 10 real articles | live log |
| 9 | Offline sweep green | pytest / ruff / mypy / import-linter |
| 10 | Audit doc re-headed, prior numbers retracted not overwritten | doc review |

---

## 13. Risks

| Risk | Mitigation |
|---|---|
| TOC heuristic misfires on a bill with no TOC, or eats real articles | Threshold-driven and config-tunable; report records the excluded span; parity test on a TOC-less fixture |
| Removing the stop-list readmits genuine cross-references | Phase 0 characterization plus a purpose-built line-anchored cross-ref test; fall back to paren-stripping (5.2 option 1) |
| 91 is itself inferred from a broken parse | Derive expected count from the document's own numbering and confirm against the PDF by hand once — do not bootstrap ground truth from the parser under test |
| Re-baseline numbers look worse | Expected (§8). Longer real text is harder than TOC lines. Report honestly rather than tuning to preserve a flattering number |
| Scope creep into lens/prompt tuning | Out of scope. This plan ends at *the right text reaches the lenses, and quality is measurable* |
