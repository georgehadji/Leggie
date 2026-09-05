# Escalated Defects Plan (DH-9, DH-10, DH-28, DH-34, DH-35 + DH-36)

**Date:** 2026-09-05  **Branch:** master @ `70a35e0`  **Author:** research pass
over the five `[REQUIRES HUMAN REVIEW]` items left open by
`docs/DEFECT_HUNT_PLAN.md`, per leggie-change-control §3 non-negotiable #8
("plans before code" — each of these was escalated *because* it needed one).

**Status: ALL SIX PHASES IMPLEMENTED, 2026-09-05.** Every mechanism and every
proposed fix below was verified by running it, not by reading the code, before
being written; the sections below are the plan as approved, with a §12
implementation record of what actually landed and where reality differed.

Gates at completion: `pytest` **875 passed, 1 skipped, 0 failed** (baseline
851; the two `xfail(strict=True)` tripwires are gone because both were fixed),
`mypy --ignore-missing-imports` clean (116 files), `ruff check` clean,
`ruff format --check` clean on every touched file, `lint-imports` 2 kept /
0 broken. DH-2's `pytest-randomly` order-dependent flake remains open by
design and is unrelated.

**One new defect was found while researching DH-28 and is filed here as
DH-36** (new ID, per house style; does not renumber anything). It is the
highest-yield item in this document and it is *not* latent — it fires on the
next live run.

---

## 0. Current state (what already works — do not re-touch)

- HEAD `70a35e0`; working tree clean apart from three pre-existing untracked
  items that predate the defect-hunt campaign (`.claude/claudex/`,
  `CLAUDE.md`, `implementation_audit_report_2026-08-11.md`).
- Offline gates green at HEAD: `pytest` 851+ passed, `mypy
  --ignore-missing-imports` clean (116 files), `ruff check` clean,
  `lint-imports --debug --verbose` 2 kept / 0 broken.
- The parse-integrity gate works and is the reason DH-9 is survivable:
  `ParseIntegrityReport.is_clean` goes False on duplicate IDs and
  `bill_analysis_flow._do_parse` (`bill_analysis_flow.py:524`) aborts by
  default. Do not weaken it.
- `Citation.checked` (`domain/models/__init__.py:167-172`) already carries the
  correct contract in its own docstring — "`resolved=False` here must never be
  read as 'disproven', only as 'unverified'". DH-36 is a violation of that
  contract, not a gap in it. The contract itself is right; leave it alone.
- The reference-bill characterization is the safety net for anything touching
  the parser: `tests/fixtures/parse/oe_sxn_ypdik.txt` → 91 articles, zero
  duplicates, zero missing, `is_clean=True`, `find_toc_span == (267, 13860)`.
  Any parser change must reproduce those exact numbers.

---

## 1. Defect inventory (ranked by yield impact)

| # | Defect | Layer | Evidence | Severity |
|---|--------|-------|----------|----------|
| **DH-36** | `GreekCitationParser.resolve()` treats "the index is non-empty" as "the index is authoritative for **every** scheme". The packaged index covers almost none of the schemes `parse()` actually emits, so real, valid citations come back `checked=True, resolved=False` — the exact condition `CoVeVerifier._check_citations` (`cove_verifier.py:334`) reads as **positively disproven** and hard-drops the whole finding (`cove_verifier.py:278-287`). | Infrastructure (`citation/__init__.py`) + composition root | Measured, not reasoned — see §1a. `data/citation_index.json` holds 181 identifiers: 120 `Σύνταγμα Άρθρο N` + 54 `Χάρτης Άρθρο N` (shapes `parse()` **can never emit**), 3 ΦΕΚ, 4 CELEX, **0 ECLI, 0 URL**. Probe run against the real packaged index: a valid `ΦΕΚ Α 100/2021`, a valid `CELEX:32011L0083`, any `ECLI:…`, and any `https://www.et.gr/…` all return `checked=True, resolved=False`; `_check_citations` on a `Finding` citing them returns `disproven=True, note='ΦΕΚ Α 100/2021 (not found in index)'` → finding dropped. | **CRITICAL** |
| DH-28 | `LAW_REF_PATTERN` (`citation/__init__.py:43`) has been dead since the first MVP commit `68484e7`; `parse()` never invokes it, so "Ν. 4622/2019"-shaped references — the most common cross-reference in Greek amending text — are never extracted at all. | Infrastructure (`citation/__init__.py`) | `LAW_REF_PATTERN.finditer()` matches the text correctly; `GreekCitationParser().parse()` on the same text returns zero citations. Wiring it in **naively** was proven harmful by DH-28's own R7 evidence — for the same reason as DH-36. | MEDIUM (blocked by DH-36) |
| DH-10 | `BoundedIngestor`'s `timeout_s` cap frees the awaiting coroutine but does not bound wall-clock time: the abandoned `asyncio.to_thread` worker is joined at interpreter shutdown, so **the process still waits for the full duration of the work the timeout claimed to abort**. | Infrastructure (`ingest/`) | Measured: a 0.2 s `wait_for` over 3.0 s of blocking work raises `TimeoutError` at 0.20 s, and `asyncio.run()` then returns at **3.03 s** — the cap bought nothing. (`asyncio.Runner` calls `loop.shutdown_default_executor()`, which joins the pool; `concurrent.futures.thread._python_exit` joins any other `ThreadPoolExecutor` too.) | MEDIUM |
| DH-35 | `Event.model_config` is the only one of the file's 16 models carrying `"use_enum_values": True`, so `event_type` is a plain `str` at runtime despite its `EventType` annotation. | Domain (`models/__init__.py:365`) | `isinstance(event.event_type, EventType)` is `False`; `.value` raises `AttributeError`. Proven fix equivalence in §5. | LOW (active), MEDIUM (landmine) |
| DH-34 | `frozen=True` blocks reassignment only; `list`/`dict` fields stay fully mutable, and `model_copy(update=…)` (`deep=False` at all 4 production call sites) aliases those same list objects between a finding and its revision. Narrows leggie-architecture-contract Invariant #3 from a guarantee to a convention. | Domain (`models/__init__.py`) | `revised.evidence is finding.evidence` → `True`; appending to one changes the other. Repo-wide grep: **zero** production in-place mutations, zero list-concat, zero slice-assignment — latent, not active. | MEDIUM (structural) |
| DH-9 | `find_toc_span` excises exactly one pre-body heading-dense region. A bill with an `ΑΙΤΙΟΛΟΓΙΚΗ ΕΚΘΕΣΗ` walking through "Άρθρο 1, 2, …" before the enacting text gets the rationale parsed as the body, and the real body then duplicates it. | Infrastructure (`parse/toc.py`) | R2's constructed variants: 3 articles → 6; 20 → 40. Not reproducible on the reference bill (no rationale section). Bounded today by the parse-integrity gate — the document becomes *unanalyzable*, not silently wrong. | LOW-MEDIUM |

### 1a. DH-36 — the measurement

Probe (hermetic, no network, no LLM; real packaged index, real
`GreekCitationParser`, real `CoVeVerifier._check_citations`):

```
index size: 181
FEK not in index (real, valid)   -> checked=True  resolved=False  => HARD-DROPPED
FEK that IS in index             -> checked=True  resolved=True   => resolved OK
CELEX in index                   -> checked=True  resolved=True   => resolved OK
CELEX not in index               -> checked=True  resolved=False  => HARD-DROPPED
ECLI (index has zero ECLI)       -> checked=True  resolved=False  => HARD-DROPPED
URL  (index has zero URLs)       -> checked=True  resolved=False  => HARD-DROPPED
law ref                          -> parse() extracted NOTHING          (DH-28)

CoVe _check_citations -> disproven=True  note='ΦΕΚ Α 100/2021 (not found in index)'
```

**Why no recorded run shows this.** The gate has never been live-exercised
against a meaningfully populated index. `data/citation_index.json` held **2**
identifiers when it landed (`02c3ac6`, 2026-07-11); the 181-identifier build
(`build_date` 2026-07-31) arrived in `4f41602` on **2026-08-07** — *after* the
last recorded live smoke (`full5_v3/v4/v5`, 2026-07-28). Those logs show
`cove_citation_fail: 0`, but they are WARNING-level (`cove_quote_fail`, also
INFO, is likewise 0 while the WARNING-level `cove_llm_error` does appear), so
they are not evidence of absence either way. **The production drop rate is
unmeasured.** What is measured is that the mechanism fires on the first
unindexed ΦΕΚ in a finding — and ΦΕΚ references are ubiquitous in Greek
statutory text.

This sits directly in front of leggie-remediation-campaign's stated next
step (the 91-article run) and is a candidate contributor to the campaign's
open "yield still low" symptom. **Fix DH-36 before spending money on that
run.**

---

## 2. Phase 1 — DH-36: make `resolve()` scheme-coverage-aware

**Layer:** Infrastructure. **Risk class:** A (pipeline-behavior — it changes
which findings survive CoVe).

The index is not authoritative for a scheme it has no entries for. Today
`checked` answers "is there an index?"; it must answer "did we actually check
*this* citation against something that could have contained it?".

**Changes**

1. `leggie/infrastructure/citation/__init__.py` —
   `GreekCitationParser.__init__(self, resolution_index=None, covered_schemes: set[CitationScheme] | None = None)`.
   In `resolve()`:
   `checked = bool(self._resolution_index) and citation.scheme in self._covered_schemes`.
   Uncovered → `checked=False`, `resolution_evidence="index has no entries for
   scheme <x> — not independently verified"`. Constructor-only; **no new port
   method** (non-negotiable #3), no Domain change, no CoVe change.
2. `leggie/infrastructure/container.py` — derive `covered_schemes` from the
   index file's own `categories` object (`{"constitution": 120, "fek": 3,
   "celex": 4, "charter": 54}`), mapping `fek→FEK`, `celex→CELEX`,
   `ecli→ECLI`, `url→URL`; `constitution`/`charter` map to nothing because
   `parse()` never emits those shapes. Missing/older-shape `categories` →
   empty set → everything reports unverified. **Fail open, never fail closed
   on a guess** — the same rule `resolve()`'s own no-index branch already
   follows.

**Why not the alternatives.** Making `CoVeVerifier._check_citations`
scheme-aware puts index-coverage knowledge in the Application layer, which
has no business knowing what a packaged data file contains — and R4 already
closed that file this campaign. Populating the index with every real ΦΕΚ is
not a fix, it is an unbounded data-acquisition project.

**Tests** (`tests/unit/infrastructure/test_citation_parser.py`,
`tests/unit/application/test_cove_verifier.py`):
proof — a valid ΦΕΚ absent from a covered index still yields `checked=True,
resolved=False` (the covered case must keep working); an ECLI against an
index whose categories declare no ECLI yields `checked=False` and CoVe does
**not** drop; boundary — empty index, `categories` absent, `categories`
present but empty; regression — the 3 indexed ΦΕΚ and 4 indexed CELEX still
resolve `True`.

---

## 3. Phase 2 — DH-28: wire `LAW_REF_PATTERN` (only after Phase 1)

**Layer:** Infrastructure. **Risk class:** A.

Phase 1 is what makes this safe: an emitted law reference lands in a scheme
the index never covers, so it can only ever be `checked=False` — visible as
"unverified", never disprovable, never a hard drop.

**Changes** — in `parse()`, add a `LAW_REF_PATTERN` loop emitting
`Citation(scheme=CitationScheme.UNKNOWN, identifier=f"Ν. {num}/{year}", …)`.
`UNKNOWN` already exists (`CitationScheme` member, `models/__init__.py:84`),
so **no Domain change and no hook-blocked edit**. Widen the pattern's
initial `Ν` to `[Νν]`: the lowercase form ("του ν. 4999/2022") is the common
one in real amending text and the current pattern misses it — the repo's own
`patterns.py:18` stop-list already had to account for exactly that spelling.

**Deliberately not done here:** a dedicated `CitationScheme.LAW` member. That
is a Domain change (hook-blocked, its own class-A plan) and buys nothing until
there is a law-reference index to resolve against. `UNKNOWN` is the honest
label for "extracted, and this parser cannot resolve it".

**Drop condition:** if the added noise in reports is not wanted, Phase 2 can
be skipped entirely without affecting Phase 1. Phase 1 is the fix that matters.

**Tests:** proof — `parse()` extracts "Ν. 4622/2019" and "του ν. 4270/2014";
boundary — no double-extraction where a ΦΕΚ and a law ref share a sentence,
no match on bare "ν" without digits; regression — full CoVe path over a
finding citing a law reference is **not** dropped.

---

## 4. Phase 3 — DH-10: stop an abandoned ingest from holding the process open

**Layer:** Infrastructure. **Risk class:** A (resource lifecycle).

Leggie is a single-run CLI — `grep` confirms no long-lived host process
(`uvicorn`/`asgi` in the repo belongs to the *external* Reasoner backend,
spawned as a subprocess by `ReasonerServerManager`). So the shared-executor
starvation R2 described is the *smaller* half of the problem. The measured
harm is that `timeout_s` does not bound the run at all.

**Change** — one helper in `leggie/infrastructure/ingest/base.py`:

```python
async def run_off_loop(fn: Callable[[], str]) -> str:
    """Run blocking work on a daemon thread.

    ponytail: a daemon thread keeps burning CPU until it finishes on its own;
    what this buys is that the interpreter no longer joins it at shutdown, so
    BoundedIngestor's timeout actually bounds the run's wall-clock time.
    Upgrade path if Leggie ever grows a long-lived process: ProcessPoolExecutor,
    whose workers can genuinely be terminated.
    """
```

Then the four ingestors swap `await asyncio.to_thread(_extract)` →
`await run_off_loop(_extract)` (`ingest/__init__.py:73, 110, 139, 153`).
`BoundedIngestor` itself is unchanged — it already emits the DEGRADED event
and logs the refusal (non-negotiable #6).

**Why not `ProcessPoolExecutor`** (R2's own suggestion): it is the only option
that truly *terminates* the work, but it costs process spawn per ingest on
Windows and forces the currently-closure-based `_extract` functions to become
picklable module-level callables — a real refactor of all four ingestors, for
a threat model (a long-lived multi-tenant process) this project does not have.
Named here as the upgrade trigger, not built now.

**Tests** (`tests/unit/infrastructure/test_ingest.py`): proof — a slow
synthetic ingestor times out *and* the surrounding `asyncio.run` returns
promptly (the existing
`TestTimeoutDoesNotActuallyStopWork::test_wrapped_ingestor_keeps_running_past_the_timeout`
stays valid — the thread does still run; what changes is that it no longer
blocks exit); boundary — `timeout_s=0` bypass path unchanged, exception
propagation from the worker unchanged; regression — all four real ingestors
still return correct text.

---

## 5. Phase 4 — DH-35: drop `use_enum_values` from `Event`

**Layer:** Domain. **Risk class:** A by policy (Domain), but the smallest
diff in this document: **one line deleted**.

`leggie/domain/models/__init__.py:365`:
`{"frozen": True, "use_enum_values": True}` → `{"frozen": True}`.
Requires clearing the `guard_pretooluse.py` Domain block — that guard firing
is the checklist working, and this plan doc is what it is asking for.

**Proven equivalence** (both variants constructed and compared side by side on
pydantic 2.13.5):

| probe | current | after fix |
|---|---|---|
| `type(event_type)` | `str` | `EType` |
| `isinstance(…, EventType)` | **False** | **True** |
| `str(event_type)` | `'degraded'` | `'degraded'` |
| `== EventType.DEGRADED` | True | True |
| `getattr(…, "value", …)` | `'degraded'` | `'degraded'` |
| `model_dump_json()` | `{"event_type":"degraded",…}` | identical |
| `model_dump(mode="json")` | `{'event_type': 'degraded',…}` | identical |
| dict-key lookup | `'handler'` | `'handler'` |

Every one of the 12 consumer sites is `==`, `in`, `str(…)`, a dict key, or an
explicit conversion, so **no consumer changes behaviour**. Checkpoint
serialization is `model_dump(mode="json")` (`bill_analysis_flow.py:669`) —
byte-identical, so existing checkpoint files stay loadable.

**Leave the two compensating shims in place** —
`persistence/__init__.py:69` (`getattr(…, "value", …)`) and
`sqlite_event_store.py:103-105` (`isinstance(str) → EventType(…)`). Both stay
correct after the fix and both still guard the genuinely-string path of events
rehydrated from the database. Removing them is unrelated cleanup.

**Tests** (`tests/unit/domain/test_models.py`): proof — flip
`TestEventTypeRuntimeRepresentation::test_event_type_is_plain_str_at_runtime_not_the_enum_member`
and `…::test_value_attribute_access_raises_despite_the_eventtype_annotation`
to assert the *fixed* behaviour; boundary — `Event(event_type="degraded")`
(string input) still validates to the enum member, `model_dump_json` output
unchanged; regression — full persistence round-trip through
`SqliteEventStore`.

---

## 6. Phase 5 — DH-34: make the collection fields genuinely immutable

**Layer:** Domain. **Risk class:** A.

The obvious fix — retype the fields `tuple[Evidence, ...]` — costs ~30
construction sites in `leggie/` and ~26 in `tests/`, because pydantic's
`@dataclass_transform` makes mypy check every `evidence=[…]` literal against
the declared type. There is a cheaper fix with a *stronger* guarantee.

**Change** — annotate `collections.abc.Sequence[...]` and add one after-
validator that returns a tuple:

```python
evidence: Sequence[Evidence] = Field(default_factory=tuple)

@field_validator("evidence", "counter_evidence", mode="after")
@classmethod
def _freeze(cls, v: Sequence[Evidence]) -> Sequence[Evidence]:
    return tuple(v)
```

Verified on pydantic 2.13.5:

- `F(evidence=[Ev(...), Ev(...)])` still accepted → **zero construction-site
  ripple**; runtime type is `tuple`.
- `.append()` raises `AttributeError` at runtime **and** `Sequence` has no
  `append` in mypy's view, so it is a *static* error too — this is the part
  `tuple[...]` and `model_copy(deep=True)` both fail to give.
- `model_copy(update=…)` still shares the object, but sharing a tuple cannot
  corrupt anything — the DH-34 mechanism is dead either way.
- `model_dump_json()` / `model_validate_json()` round-trip unchanged.

Apply to: `Finding.evidence`, `Finding.counter_evidence`,
`Document.articles`, `Article.paragraphs`, `Paragraph.subparagraphs`,
`Plan.lens_tasks`, `BillOverview.articles`. Grep confirms zero production
in-place mutation, zero list-concatenation and zero slice-assignment on any of
them, and the only list-typed helper signatures
(`compute_title_only_ids`, `_extract_preamble`, `decompose`) are called with
locally-built lists, not with model fields — so nothing else needs widening.

**`Event.data: dict[str, Any]` stays a `dict`.** There is no stdlib frozen
mapping pydantic can validate to, nothing in `leggie/` mutates it, and
inventing one is exactly the abstraction this repo's own change control tells
you not to add. Record it as accepted residual risk in the landing audit.

**Tripwire:** `TestFrozenModelsShallowImmutability::test_in_place_mutation_should_be_rejected_but_is_not`
is `xfail(strict=True)`. When this phase lands it will XPASS and **fail the
suite** — that is by design. Remove the marker and invert the assertion as
part of this phase; do not delete the test.

---

## 7. Phase 6 — DH-9: anchor body detection on the last pre-body marker

**Layer:** Infrastructure. **Risk class:** A (this is the F0 phantom-articles
file — leggie-failure-archaeology #2, commit `703651e`).

`find_toc_span` stops at the first ascending-run break after the
`ΠΙΝΑΚΑΣ ΠΕΡΙΕΧΟΜΕΝΩΝ` marker. With TOC → rationale → body, that break is the
*rationale's* restart, not the body's.

**Change** — in `patterns.py`, extend the marker set to the other standard
pre-body sections of a Greek bill (`ΑΙΤΙΟΛΟΓΙΚΗ ΕΚΘΕΣΗ`,
`ΑΝΑΛΥΣΗ ΣΥΝΕΠΕΙΩΝ ΡΥΘΜΙΣΗΣ`) as a sibling of `_TOC_MARKER`; in `toc.py`,
anchor the scan on the **last** such marker in the text rather than the first,
then apply the existing first-descent rule unchanged.

**Why this and not "keep scanning for the last restart".** Taking the last
descent anywhere in the document would re-create F0 exactly: one line-anchored
in-body cross-reference (`Άρθρο 5 του ν. …` at line start, after Άρθρο 91)
would truncate the whole body. Marker-anchored keeps `toc.py`'s own stated
rule — "never excise on a guess."

**Proven safe against the reference bill:** `oe_sxn_ypdik.txt` contains
`ΠΙΝΑΚΑΣ ΠΕΡΙΕΧΟΜΕΝΩΝ` exactly once and **zero** occurrences of `ΑΙΤΙΟΛΟΓΙΚΗ`,
`ΕΚΘΕΣΗ` or `ΑΝΑΛΥΣΗ ΣΥΝΕΠΕΙΩΝ`, so "last marker" ≡ "first marker" and the
parse is bit-identical: `find_toc_span == (267, 13860)`, 91 articles, no
duplicates, no gaps, `is_clean=True`.

**Explicitly still not fixed:** DH-9 variant (a) — a rationale walkthrough with
*no* marker of any kind. Nothing distinguishes it from the body without
guessing, and the parse-integrity gate already refuses such a document. That
is the correct outcome; do not paper over it.

**Tests:** flip
`test_parse_characterization.py::TestExplanatoryMemorandumDoubleRestart` from
`xfail(strict=True)` to passing for variant (b); keep variant (a) as a
documented `xfail`; keep
`TestParseIntegrity::test_toc_plus_rationale_section_is_flagged_not_clean` as
the safety-net regression; re-run the full reference-bill characterization and
assert the exact numbers above.

---

## 8. Execution order & dependencies

```
Phase 1 (DH-36) ──► Phase 2 (DH-28)          [2 is unsafe before 1]
      │
      └──► live smoke re-forecast (yield may change materially)

Phase 3 (DH-10)   ─┐
Phase 4 (DH-35)   ─┼── independent of each other and of 1/2; any order
Phase 5 (DH-34)   ─┤   (4 before 5: both touch domain/models/__init__.py,
Phase 6 (DH-9)    ─┘    and 4 is one line while 5 is a shape change)
```

Phases 4 and 5 both need the Domain guardrail cleared
(`.claude/hooks/guardrails.yaml`); do them in one authorized window.

**Recommended stopping point if only one thing gets done: Phase 1.** It is the
only item here that is currently destroying output on every run, and it is the
one that stands between the project and a meaningful 91-article smoke.

---

## 9. Architecture guardrails (apply to every phase)

- Dependency rule unchanged; `lint-imports --debug --verbose` must stay
  2 kept / 0 broken.
- **No new port methods** (non-negotiable #3). Phase 1 adds a *constructor*
  parameter to an adapter, not a method to `CitationParserPort`.
- **No silent failure** (#6). Every new degrade path logs or emits
  `EventType.DEGRADED`. Phase 1's "scheme not covered" path is an
  `resolution_evidence` string on a returned `Citation`, already surfaced in
  CoVe's note — plus a `log.debug` at most; do not add per-citation warnings
  that would flood a 91-article run.
- **Domain edits only in Phases 4/5**, each with the guard cleared explicitly
  and re-armed afterwards.
- **$5 `max_cost_per_run` is never raised** to make a smoke finish (#4).
- **Ruff ignore list is never widened** (#5).
- Unit tests stay hermetic (`tests/conftest.py`) — no phase may introduce a
  test that reaches OpenRouter.

---

## 10. Definition of done (measurable)

1. `pytest tests/ -q` ≥ 851 passed, 0 failed; `mypy --ignore-missing-imports`
   clean; `ruff check` clean; `lint-imports` 2 kept / 0 broken — after **each**
   phase, not just at the end.
2. Reference-bill characterization reproduces exactly: 91 articles, 0
   duplicates, 0 missing, `is_clean=True`, `find_toc_span == (267, 13860)`.
3. Phase 1: the §1a probe re-run shows `checked=False` for every ECLI/URL
   citation and for every ΦΕΚ/CELEX **only if** `categories` declares no
   coverage; the 3 indexed ΦΕΚ and 4 indexed CELEX still resolve `True`;
   `_check_citations` returns `disproven=False` for all of them.
4. Phase 3: a `BoundedIngestor(timeout_s=0.2)` over 3 s of blocking work makes
   the enclosing `asyncio.run()` return in **< 0.5 s** (measured 3.03 s today).
5. Phase 4: `isinstance(Event(...).event_type, EventType)` is `True` and
   `Event.model_dump_json()` output is byte-identical to the pre-fix output.
6. Phase 5: `finding.evidence.append(...)` raises at runtime **and** fails
   mypy; the `xfail(strict=True)` marker is removed, not the test.
7. Class-A phases are covered by a landing audit doc recording the measured
   before/after numbers (template: leggie-docs-and-writing §3), including the
   accepted residual risks named above (`Event.data` still a mutable dict;
   DH-9 variant (a) still unfixed; DH-10 worker still burns CPU until done).
8. A live smoke is **not** required to land Phases 3-6, but Phase 1 changes
   which findings survive CoVe and therefore needs the class-A live-smoke
   evidence per leggie-change-control §2 before it is called done.

---

## 11. Implementation record (2026-09-05)

| Phase | Landed | Files | Deviation from the plan |
|---|---|---|---|
| 1 — DH-36 | YES | `infrastructure/citation/__init__.py` (`covered_schemes` ctor arg, `_covers()`, `INDEX_CATEGORY_SCHEMES`), `infrastructure/container.py` (derives coverage from the index's `categories`) | None. `covered_schemes=None` deliberately keeps the old "caller asserts full coverage" semantics for callers that built their own index; only the composition root, which loads a packaged file it did not build, passes an explicit set. |
| 2 — DH-28 | YES | `infrastructure/citation/__init__.py` (`LAW_REF_PATTERN` wired into `parse()`, widened to `\b[Νν]`) | None. |
| 3 — DH-10 | YES | `infrastructure/ingest/base.py` (`run_off_loop`, `_hand_back`), `infrastructure/ingest/__init__.py` (4 call sites) | Two additions found while implementing: (a) settling the future by **argument**, not closure — `except … as exc` unbinds `exc` at block exit, so a lambda capturing it raised `NameError` on the loop and left the future pending forever (caught by the new exception test, which hung); (b) `_hand_back` suppresses `RuntimeError` so an abandoned worker completing after its loop closed does not raise inside a daemon thread at exit. |
| 4 — DH-35 | YES | `domain/models/__init__.py` (`Event.model_config`) | None. The two compensating shims (`persistence/__init__.py:69`, `sqlite_event_store.py:103-105`) were left in place as planned. |
| 5 — DH-34 | YES | `domain/models/__init__.py` (`Frozen[T]` alias + 8 fields) | Implemented as `type Frozen[T] = Annotated[Sequence[T], AfterValidator(tuple)]` rather than a per-class `field_validator`: same guarantee, one line per field, no methods. Ripple was **2 test assertions** (`== []` → `== ()`), not the ~56 construction sites `tuple[...]` would have cost. `ArticleOverview.key_provisions` was added to the list during implementation — same gap, missed in the plan. |
| 6 — DH-9 | YES | `infrastructure/parse/patterns.py` (`_PRE_BODY_MARKER`), `infrastructure/parse/toc.py` (`find_toc_span`) | **The plan's design was unsafe as written.** Anchoring on the last marker unconditionally regresses F0: a memorandum that is pure prose (no per-article headings) yields no ascending run, so no break is found, the TOC excision is abandoned and every TOC line returns as a phantom article. Implemented as last-marker-first **with fallback to earlier markers**, and `test_prose_only_rationale_falls_back_to_the_toc_marker` guards it. |

Reference-bill characterization re-run after Phase 6, bit-identical to §0:
`find_toc_span == (267, 13860)`, 91 articles, 0 duplicates, 0 missing,
`is_clean=True`.

**Still open, by design:** DH-9 variant (a) (a rationale walkthrough with no
marker line at all — the parse-integrity gate refuses such a document, which
is the correct outcome); `Event.data` remains a mutable dict (§6); the
abandoned ingest thread still burns CPU until it finishes (§4); DH-2's
unpinned `pytest-randomly` seed.

**Not yet discharged:** Phase 1 is a class-A change that alters which findings
survive CoVe, so per leggie-change-control §2 it needs live-smoke evidence
before it is called done (DoD item 8). No live run has been made.

## 12. Provenance

All measurements in this document were produced on 2026-09-05 against HEAD
`70a35e0` with pydantic 2.13.5, using three hermetic probe scripts (no network,
no LLM calls, no cost): the citation-gate probe (§1a), the ingest thread-leak
timing probe (§1 DH-10 row), and the domain-shape probe (§5, §6). Re-run them
before trusting any number here if the tree has moved.
