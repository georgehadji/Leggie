# Post-DH-36 Review Follow-up Plan (DH-37 … DH-41)

**Date:** 2026-09-08
**Branch:** master
**Base commit:** `b2c3e31` (DH-34 `model_copy` hole closed)
**Origin:** adversarial review of `c9a1de0` + `3506efb`, run 2026-09-07 by a
fresh `ecc:python-reviewer` subagent against the full current files (not
hunks), with 188 affected tests, ruff, mypy and `lint-imports` re-run.
**Author context:** the reviewed commits are my own; DH-37 in particular
falsifies a claim made in `c9a1de0`'s commit message. Recorded here rather
than quietly patched.

---

## 0. Current state — what already works, do NOT re-touch

Verified by the review, independently or by running the code:

| Already correct | Evidence |
|---|---|
| DH-35 — `Event.event_type` is a real `EventType` at runtime; `StrEnum.__str__` keeps serialization byte-identical | `domain/models/__init__.py`, `test_models.py` |
| DH-10 — `run_off_loop` future-settling is sound: `future.done()` guards double-settle, cancellation propagates, `contextlib.suppress(RuntimeError)` covers a closed loop | `infrastructure/ingest/base.py:38-72` |
| DH-28 — law refs emit under `CitationScheme.UNKNOWN`, which is absent from `INDEX_CATEGORY_SCHEMES`, so they can never be disproven | `infrastructure/citation/__init__.py:60-65,140-154` |
| Single production construction site for `GreekCitationParser` | `infrastructure/container.py:219` (ADR-0003, D22 stays closed) |
| `Frozen[str]` has no bare-string footgun (a `str` is a `Sequence[str]`, but no field is typed `Frozen[str]`) | `domain/models/__init__.py` |
| Architecture contracts | `lint-imports` 2/2 kept |
| DH-34 `model_copy` hole | CLOSED in `b2c3e31` — do not re-open |

Nothing in this plan changes Domain models. `Citation` already carries both
`resolved` and `checked`; the whole DH-37 fix is a change in *which* of those
the infrastructure adapter sets, not in their shape.

---

## 1. Defect inventory

| # | Defect | Layer | Evidence | Severity | Risk class |
|---|---|---|---|---|---|
| DH-37 | Index coverage is treated as index *authority*. A scheme with a token number of hand-seeded entries counts as "covered", so any genuine citation outside that handful returns `checked=True, resolved=False` and CoVe hard-drops the entire finding. ΦΕΚ is the most-cited scheme in Greek bills. | Infrastructure (+ Application read-side) | `citation/__init__.py:167-189`; `container.py:196-207`; `cove_verifier.py:334-335`; packaged index `categories = {constitution:120, fek:3, celex:4, charter:54}` | **HIGH** | A |
| DH-38 | `run_off_loop` spawns an unbounded number of daemon threads — no cap, no back-pressure, unlike the bounded default executor it replaced. | Infrastructure | `ingest/base.py:59` | MEDIUM | B |
| DH-39 | `find_toc_span`'s DH-9 fallback loop is untested against a non-monotonic pre-body region (a rationale whose article numbers are out of order). | Infrastructure | `parse/toc.py:45-54` | MEDIUM | C |
| DH-40 | `supported_schemes()` omits `CitationScheme.UNKNOWN`, which `parse()` has emitted since DH-28. The port contract is "schemes this parser handles", so the list is simply incomplete. | Infrastructure | `citation/__init__.py:200-201` vs `:147-154`; port contract `ports/citation_parser.py:35-37` | LOW | B |
| DH-41 | `test_timeout_no_longer_holds_the_process_open` leaves a ~0.6 s daemon thread burning after the assertion passes. | Tests | `test_ingest.py:330-356` | LOW | C |

### 1.1 DH-37 — the mechanism, stated exactly

`resolve()` currently reasons in one step:

```
index exists AND scheme is "covered"  ->  checked = True
                                          resolved = (identifier in index)
```

`_covers()` asks *"does the index hold any entries for this scheme?"*. That is
a **presence** test. Disproving a citation needs an **exhaustiveness** test:
"is this index a complete register of every valid identifier in this scheme?"

Nothing in Leggie is exhaustive for any scheme. `tools/build_citation_index.py`
hand-seeds three ΦΕΚ numbers and four CELEX numbers, described in its own
comment as "cited across the current test corpus". The remaining 174 entries
are `Σύνταγμα Άρθρο N` / `Χάρτης Άρθρο N` strings that `parse()` can never
emit, so they inflate `identifier_count` to 181 while contributing nothing.

Consequence chain, all in current code:

1. A lens cites a real, valid ΦΕΚ — say `ΦΕΚ Α 88/2024`, not one of the three.
2. `resolve()` → `checked=True, resolved=False`.
3. `cove_verifier.py:334` → `return True, ...` → **disproven**.
4. The whole finding is hard-dropped, not flagged, not downgraded.

DH-36 (`c9a1de0`) closed only the zero-entry half of this: ECLI and URL have no
`categories` key at all, so they became uncovered and stopped being
disprovable. FEK and CELEX have nonzero counts, so they stayed disprovable and
still are.

Worse, `c9a1de0` shipped a test that **asserts the surviving half is correct**:

```python
async def test_covered_scheme_still_disproves_a_genuine_miss(self):
    """No-regression: coverage must not turn the gate off entirely. ..."""
    ...
    assert resolved.checked is True
    assert resolved.resolved is False
```

That test must be deleted, not adjusted — its premise ("a ΦΕΚ miss is a real,
checked miss") is the defect. Leaving it would block DH-37 at the gate.

**Exposure note (honest scoping):** this is pre-existing, not a regression
introduced by `c9a1de0` — the same hard-drop existed before DH-36 and applied
to *more* schemes. What `c9a1de0` got wrong is the claim, in its message and
in `docs/ESCALATED_DEFECTS_PLAN.md` §11, that DH-36 is closed. §11 must be
corrected as part of this work.

---

## 2. Phase 1 — DH-37: separate *coverage* from *authority*

**Goal:** an index may CONFIRM a citation always, and may DISPROVE one only
where it explicitly declares itself exhaustive for that scheme. No scheme
declares that today, so after this change no citation is ever disproven by the
deterministic parser — wrongness is left to CoVe's LLM cross-check, which is
where a judgement call belongs.

### 2.1 Domain — no change

`Citation.resolved` / `.checked` already express exactly the three states
needed. `CitationScheme` unchanged. Confirms non-negotiable #2 of
leggie-change-control (Domain frozen during infrastructure remediation).

### 2.2 Infrastructure — `GreekCitationParser`

Replace the `covered_schemes` constructor parameter (added one commit ago,
single production caller) with `authoritative_schemes`, and collapse
`resolve()` to a three-branch decision:

```python
def __init__(
    self,
    resolution_index: set[str] | None = None,
    authoritative_schemes: set[CitationScheme] | None = None,
) -> None:
    self._resolution_index = resolution_index or set()
    # DH-37: an index may CONFIRM whatever it holds, but may DISPROVE only a
    # scheme it is an exhaustive register for. ``None`` = the caller built
    # this index itself and asserts exhaustiveness (build_resolution_index);
    # the default empty set = confirm-only, which is what every packaged
    # index earns.
    self._authoritative_schemes = authoritative_schemes or set()

async def resolve(self, citation: Citation) -> Citation:
    if citation.identifier in self._resolution_index:
        # A hit is positive evidence regardless of exhaustiveness.
        resolved, checked = True, True
        evidence = "resolved against internal index"
    elif self._is_authoritative(citation.scheme):
        resolved, checked = False, True
        evidence = (
            f"not found in an index declared exhaustive for "
            f"'{citation.scheme.value}'"
        )
    else:
        # Fail closed: unverified is not invalid. A sparse index cannot tell
        # a fabricated ΦΕΚ from a real one it simply never seeded (DH-37).
        resolved, checked = False, False
        evidence = (
            "no resolution index configured — not independently verified"
            if not self._resolution_index
            else f"index is not exhaustive for scheme "
                 f"'{citation.scheme.value}' — not independently verified"
        )
    ...
```

Notes on the design, so the next reader does not "simplify" it back:

- **Why the hit branch no longer consults coverage.** A hit means the
  identifier is literally in the known-good list. `parse()` cannot emit the
  `Σύνταγμα Άρθρο N` / `Χάρτης Άρθρο N` strings, and no ECLI or URL is in the
  file, so a cross-scheme false hit is not reachable. Checking coverage first
  would only be able to *suppress* a true positive.
- **Why `None` still means "fully authoritative".** `build_resolution_index()`
  exists so a caller can construct an index from a known-good citation list it
  owns; for that caller exhaustiveness is true by construction. Keeping the
  escape hatch avoids a second constructor.
- **Why not delete disprove entirely.** The mechanism is correct and will be
  needed the day an index is built from a live register (EUR-Lex CELLAR, the
  `gov-et-laws` dataset). Deleting it would have to be rebuilt; ADR-0004
  already names CELLAR as the reintroduction trigger for a related deletion.

`INDEX_CATEGORY_SCHEMES` keeps its current job — mapping index category keys to
schemes — but is now only consulted for the authority declaration.

### 2.3 Infrastructure — `container.py`

Derive authority from an explicit declaration, never from a count:

```python
# DH-37: authority is DECLARED, never inferred from a count. An index that
# says nothing claims nothing, which is the safe answer for every index
# Leggie ships today.
declared = index_data.get("authoritative_schemes")
if isinstance(declared, list):
    authoritative = {
        scheme
        for name, scheme in INDEX_CATEGORY_SCHEMES.items()
        if name in declared
    }
else:
    authoritative = set()
```

Failure modes stay fail-open, matching DH-29's trust-boundary handling of the
same file: a missing key, a wrong type, an unknown scheme name, or an unreadable
file all yield an empty set. No log warning is needed for the *absent* case —
absent is the correct steady state and warning on it would be noise on every
run; a *malformed* declaration (present but not a list) does warn, matching the
existing `citation_index.malformed_shape` precedent.

### 2.4 Data + builder — `tools/build_citation_index.py`

- Emit `"authoritative_schemes": []` with an inline comment in the module
  docstring explaining that no seeded category is exhaustive, and that the key
  is the single switch that would let the resolver disprove.
- Fix two pre-existing inaccuracies noticed while reading it: the docstring
  claims "three resolver categories" and lists ECLI, but no `ECLI_REFS` exists
  and `categories` has no `ecli`/`url` key; and "Constituition" is misspelt in
  the emitted `description`.
- Regenerate `leggie/data/citation_index.json` by running the builder. Expect
  `identifier_count` to stay 181 and `categories` unchanged — only the new key
  and the corrected description differ. Diff the file before committing.

### 2.5 Application — `cove_verifier.py`

No code change. `_check_citations` already implements the correct rule
(`checked and not resolved` → disproven); it was fed a wrong `checked`. Add
one line to its docstring pointing at the DH-37 contract so the next reader
does not "fix" the gate at the wrong end.

### 2.6 Tests

| Action | Test |
|---|---|
| **DELETE** | `test_citation_parser.py::test_covered_scheme_still_disproves_a_genuine_miss` — its premise is the defect |
| **ADD** | `test_sparse_index_miss_is_unverified_not_disproven` — packaged-shape parser, `ΦΕΚ Α 999/2099` → `checked is False`, `resolved is False` |
| **ADD** | `test_declared_authoritative_scheme_disproves_a_miss` — parser built with `authoritative_schemes={FEK}` → `checked is True`, `resolved is False`. Keeps the mechanism alive and proves the switch works |
| **ADD** | `test_hit_resolves_even_when_scheme_is_not_authoritative` — `ΦΕΚ Α 137/2023` → `resolved is True` |
| **ADD** (`test_cove_verifier.py`) | end-to-end: a finding citing a real-but-unseeded ΦΕΚ **survives** CoVe's citation gate. This is the finding-loss regression in the terms that actually matter |
| **UPDATE** | `test_container_bindings.py::test_covered_schemes_come_from_the_index_categories` → rewrite as `test_authority_is_declared_not_inferred`: an index with `fek: 3` and no `authoritative_schemes` key yields an empty authority set |
| **ADD** (`test_container_bindings.py`) | the packaged index as shipped declares no authority — guards against a future builder change silently arming the gate |
| **KEEP** | `test_covered_scheme_hit_still_resolves`, and every ECLI/URL unverified test from DH-36 — all still pass unchanged |

### 2.7 Docs

- `docs/ESCALATED_DEFECTS_PLAN.md` §11: correct the DH-36 entry from closed to
  "half-closed; completed by DH-37", with the reason.
- `docs/ADR/`: add an ADR — *"A citation index may confirm always, disprove
  only where declared exhaustive"* — following the existing ADR-0003 (citations
  fail closed) which this refines. Number it after the current highest.
- Update `.claude/skills/leggie-failure-archaeology/SKILL.md`'s defect ledger
  with DH-37, in the existing symptom → root cause → evidence → status format.

### 2.8 Gates for Phase 1

Class A (pipeline-behavior-changing: it changes which findings survive). Per
leggie-change-control §2 this requires the offline sweep **and** a live smoke
judged against `REMEDIATION_PLAN` §10.

**The live smoke is a user decision, not mine** — the standing rule is never to
run one unasked, and the last recorded attempt hit a 120 s ingest timeout.
Two acceptable dispositions, to be chosen explicitly:

1. Run a single-lens live smoke and record `findings_per_article` before/after.
   Expect it to rise or hold — never fall, since the change only removes drops.
2. Defer the smoke and record the deferral in the audit doc, on the argument
   that the change is strictly drop-removing and covered by the new
   `test_cove_verifier.py` end-to-end case.

Do not silently skip it. State which was chosen.

---

## 3. Phase 2 — DH-38: name the `run_off_loop` thread ceiling

**Recommended: comment, not a cap.** Leggie ingests exactly one file per run;
`BoundedIngestor` already bounds each call in wall-clock. A semaphore today
would be an abstraction guarding a load that does not exist, and the module
already carries a `ponytail:` comment about the abandoned-CPU ceiling — this is
the same category of honest limitation.

Add to the existing `ponytail:` block in `ingest/base.py`:

```
ponytail: also unbounded — one daemon thread per call, no cap and no
back-pressure, where the default executor was bounded. Safe while ingest is
one file per run. If ingest is ever batched or served concurrently, gate the
spawn on an asyncio.Semaphore sized from settings before adding threads.
```

No test. A comment is not behaviour, and inventing a cap to have something to
assert is exactly the over-engineering this codebase has been burned by.

**If a cap is wanted anyway** (say so explicitly): an `asyncio.Semaphore`
acquired inside `run_off_loop` before `Thread(...).start()`, sized from a new
`IngestSettings` field. Note it is per-event-loop, so it bounds concurrency
within a run, not across the process. That is a class-B change with its own
test asserting the (N+1)-th call waits.

---

## 4. Phase 3 — DH-39: test `find_toc_span` against a non-monotonic pre-body

Pure test addition (class C). Construct a document whose rationale section
walks article numbers out of order — e.g. markers followed by `Άρθρο 3`,
`Άρθρο 1`, `Άρθρο 2` — so the descent triggers *inside* the rationale rather
than at the body boundary, then the real body follows.

Assert the current, honest behaviour rather than an aspiration: either the span
is correct, or the parse-integrity gate refuses the document. What must **not**
happen is a silently wrong span that lets rationale text parse as articles —
that is the F0 phantom-articles failure this module exists to prevent.

If the test shows a wrong-but-accepted span, that is a new defect (DH-42) with
its own plan entry — do not fix it inside this phase and do not weaken the
assertion to make it pass.

---

## 5. Phase 4 — DH-40: complete `supported_schemes()`

```python
def supported_schemes(self) -> list[CitationScheme]:
    # Every scheme parse() can emit — including UNKNOWN, which carries the
    # DH-28 individual law references ("Ν. 4622/2019"). The port contract is
    # "schemes this parser handles", not "schemes the index covers".
    return [
        CitationScheme.FEK,
        CitationScheme.CELEX,
        CitationScheme.ECLI,
        CitationScheme.URL,
        CitationScheme.UNKNOWN,
    ]
```

Extend `test_citation_parser.py::test_supported_schemes` to assert `UNKNOWN` is
present and that the list matches the schemes `parse()` actually emits for a
fixture text containing one citation of each kind — that assertion keeps the two
in step automatically the next time a pattern is added.

Check before landing: no caller branches on the length or exact membership of
this list. Current call sites are the port ABC, the adapter, and six test
doubles; none are production consumers. Confirm with a fresh grep at
implementation time.

---

## 6. Phase 5 — DH-41: stop the test leaking a live daemon thread

`test_timeout_no_longer_holds_the_process_open` measures the right thing but
abandons a thread that keeps sleeping for ~0.6 s into subsequent tests.

Give the fake work a `threading.Event` to poll and set it once the timing
assertion is done:

```python
stop = threading.Event()

class SlowIngestor(Ingestor):
    async def ingest(self, source: Path | str) -> str:
        return await run_off_loop(lambda: (stop.wait(0.6), "done")[1])
...
assert elapsed < 0.4, f"process held open for {elapsed:.2f}s by abandoned ingest work"
stop.set()  # the daemon thread must not outlive the assertion it proved
```

`stop.wait(0.6)` blocks the same way `time.sleep(0.6)` did, so the timing proof
is unchanged; setting the event afterwards lets the abandoned worker exit
immediately instead of running into the next test.

While here, check whether the same leak exists in
`test_worker_runs_on_a_daemon_thread_off_the_shared_executor`; apply the same
treatment if so.

---

## 7. Execution order and dependencies

```
Phase 1 (DH-37) ──┬── independent of everything below
                  │   (delete the blocking test FIRST, or the suite fails mid-phase)
                  │
Phase 4 (DH-40) ──┘   touches the same file; land after Phase 1 to keep the
                      citation diff readable, or fold into it

Phase 2 (DH-38) ─── independent (comment only)
Phase 3 (DH-39) ─── independent (test only)
Phase 5 (DH-41) ─── independent (test only)
```

Phases 2, 3 and 5 can land as one small commit. Phase 1 is its own commit with
its own audit trail; Phase 4 may ride with it since it is the same file.

Suggested commit shape (subjects only, in project conventional style):

```
fix(DH-37): a citation index may confirm always, disprove only where declared exhaustive
fix(DH-40): supported_schemes() must list UNKNOWN, which parse() emits since DH-28
test(DH-39,DH-41): non-monotonic pre-body span; stop the timeout test leaking a daemon thread
docs(DH-38): name the unbounded-thread ceiling in run_off_loop
```

---

## 8. Architecture guardrails (apply to every phase)

1. **No Domain edits.** `Citation`, `CitationScheme`, `Finding` untouched.
2. **No new port methods.** `CitationParserPort` keeps its three methods; all
   DH-37 behaviour rides on adapter constructor arguments.
3. **Dependency rule.** Everything lands in `infrastructure/` and `tests/`; the
   Application layer gets a docstring line only. `lint-imports` must stay 2/2.
4. **No silent failure.** Every degradation path in `container.py` logs, except
   the absent-declaration case, which is the correct steady state.
5. **Fail closed on citations.** Unverified is never reported as resolved.
   ADR-0003 stands; DH-37 refines it.
6. **Do not widen the ruff ignore list.** Four files (`skeptic.py`,
   `test_orchestrator.py`, `test_skeptic.py`, `test_openrouter_adapter.py`)
   currently fail `ruff format --check` as pre-existing debt — do not fix them
   in these commits and do not let them mask a new one.
7. **`max_cost_per_run` stays at $5.**
8. **No `config/routes.yaml` edits** without asking first.

---

## 9. Definition of done — measurable

| # | Criterion | How measured |
|---|---|---|
| 1 | A real, unseeded ΦΕΚ citation is never disproven by the deterministic parser | `test_sparse_index_miss_is_unverified_not_disproven` passes |
| 2 | A finding citing a real, unseeded ΦΕΚ survives CoVe's citation gate | new `test_cove_verifier.py` end-to-end case passes |
| 3 | The disprove mechanism still works when authority is declared | `test_declared_authoritative_scheme_disproves_a_miss` passes |
| 4 | The packaged index as shipped declares zero authority | `test_container_bindings.py` assertion on `leggie/data/citation_index.json` |
| 5 | `test_covered_scheme_still_disproves_a_genuine_miss` no longer exists | `grep -c` returns 0 |
| 6 | `supported_schemes()` equals the set `parse()` can emit | `test_supported_schemes` derives its expectation from a fixture |
| 7 | Suite green and grown | ≥ 882 passed (876 today + ~6 new), 1 skipped, 0 failed |
| 8 | Types, lint, layers | mypy clean · `ruff check` clean · `ruff format --check` still exactly the 4 pre-existing files · `lint-imports` 2/2 |
| 9 | No lingering worker after the ingest timing test | `threading.active_count()` back to baseline at test teardown |
| 10 | DH-36's status corrected in the plan of record | `docs/ESCALATED_DEFECTS_PLAN.md` §11 says half-closed, completed by DH-37 |
| 11 | Live smoke run or deferral explicitly recorded | a named disposition in the landing audit doc (§2.8) |

Criterion 7's number is a floor, not a target — if the implementation needs more
tests, the floor moves up, never down.

---

## 10. Known risks

| Risk | Mitigation |
|---|---|
| Removing the disprove path lets a genuinely fabricated ΦΕΚ through | It was never a fabrication detector — with 3 seeded numbers it disproved ~every real citation too. The LLM cross-check remains the wrongness judge. Real detection needs a live register (ADR-0004, CELLAR). |
| Report language now reads "unverified" for more citations | Already true for ECLI/URL since DH-36; consistent with ADR-0003's fail-closed stance. Cosmetic only if it appears misleading — raise separately, do not fix by re-arming the gate. |
| `authoritative_schemes` is added and never populated | That is the intended steady state. Criterion 4 pins it; the switch exists for the day a live register lands. |
| The DH-39 test uncovers a real span bug | Log it as DH-42 with its own plan; do not widen Phase 3's scope. |

---

## 11. Implementation record (2026-09-08)

| Phase | Landed | Files | Deviation from the plan |
|---|---|---|---|
| 1 — DH-37 | YES | `infrastructure/citation/__init__.py` (`authoritative_schemes` ctor arg, `_is_authoritative()`, rewritten `resolve()`), `infrastructure/container.py` (reads the declaration), `tools/build_citation_index.py` + regenerated `leggie/data/citation_index.json`, `application/services/cove_verifier.py` (docstring pointer) | **One test the plan did not anticipate.** `test_empty_covered_set_checks_nothing` asserted that an empty coverage set makes even a *hit* unverified. Under DH-37 a hit confirms regardless of exhaustiveness, so it was rewritten as `test_empty_authority_still_confirms_a_hit_but_never_disproves` — now pinning both halves. `test_resolve_with_index` and `test_citation_in_a_scheme_the_index_never_covered_is_not_dropped` also needed the new kwarg and evidence wording. |
| 2 — DH-38 | YES (comment) | `infrastructure/ingest/base.py` | None. No cap added, as recommended — the `ponytail:` block now names the unbounded-thread ceiling and the semaphore upgrade path. |
| 3 — DH-39 | YES | `tests/unit/infrastructure/test_parse.py` | None, and the test earned its keep: the non-monotonic rationale **does** mis-span. Measured — `find_toc_span` ends the span at the rationale's out-of-order "Άρθρο 1", yielding 5 articles with 2 duplicate ids and `is_clean=False`. Second branch of the invariant: refused, not silently wrong, so **not** DH-42. The measured outcome is recorded in the test docstring. |
| 4 — DH-40 | YES | `infrastructure/citation/__init__.py` | None. `test_supported_schemes_covers_everything_parse_emits` derives its expectation from `parse()` so the two cannot drift again. |
| 5 — DH-41 | YES | `tests/unit/infrastructure/test_ingest.py` | None. `threading.Event.wait(0.6)` replaces `time.sleep(0.6)`, released in a `finally` — timing proof unchanged. The sibling daemon-thread test returns immediately and never leaked. |
| Docs | YES | `docs/ESCALATED_DEFECTS_PLAN.md` §11a (DH-36 corrected to HALF), `docs/ADR/0008-index-confirms-always-disproves-only-when-declared.md`, `.claude/skills/leggie-failure-archaeology/SKILL.md` §17 | None. |

### 11.1 Definition-of-done status

| # | Criterion | Result |
|---|---|---|
| 1 | Unseeded ΦΕΚ never disproven | PASS |
| 2 | Finding citing an unseeded ΦΕΚ survives CoVe | PASS — `test_real_but_unseeded_fek_citation_survives_the_gate` |
| 3 | Declared authority still disproves | PASS |
| 4 | Packaged index declares zero authority | PASS — asserted against the real data file |
| 5 | `test_covered_scheme_still_disproves_a_genuine_miss` gone | PASS — 0 matches |
| 6 | `supported_schemes()` matches what `parse()` emits | PASS |
| 7 | Suite green and grown (floor 882) | PASS — **883 passed, 1 skipped**, up from 876 |
| 8 | Types, lint, layers | PASS — mypy clean (116 files), `ruff check` clean, `lint-imports` 2/2. `ruff format --check` names 5 files, all pre-existing debt in files this work did not touch (`skeptic.py`, `test_orchestrator.py`, `test_skeptic.py`, `test_openrouter_adapter.py`, `refresh_model_prices.py`) |
| 9 | No lingering worker after the ingest timing test | PASS |
| 10 | DH-36 status corrected | PASS — §11a |
| 11 | Live smoke run or deferral recorded | **RUN — no regression; DH-37 not exercised by that bill, discharged instead by §11.3** |

Regenerated index diff, for the record: `identifier_count` 181 unchanged,
`categories` unchanged, only the corrected "Constitution" spelling, a new
`build_date`, and `"authoritative_schemes": []`.

### 11.2 Live smoke, single lens (2026-09-08)

Run at the user's explicit request, discharging the class-A obligation in
§2.8. `leggie analyze Inputs/OE_ΣΧΝ-ΥΠΔΙΚ.pdf --lenses constitutional`,
exit 0. Log: `smoke_dh37.log` (521 lines, scratchpad — not committed).

Phase 4a first, free: 91 articles, `is_clean=true`, 0 duplicates, 0 missing,
0 rejected, 89 s wall clock. Note the ingest cap is a hardcoded
`timeout_s=120.0` in `ingest/bounded.py` with no env override — 31 s of
headroom on the reference bill, and the 2026-09-07 attempt (§13 of
`ESCALATED_DEFECTS_PLAN.md`) died exactly there.

| §10 gate | Measured | Verdict |
|---|---|---|
| Parse failures < 5% of LLM calls | 0 of 200 | PASS |
| Non-neutral skeptic verdicts present | 19 refutes / 8 supports / 0 neutral | PASS |
| No mass `skeptic_llm_error` | 0 | PASS |
| CoVe drop/revise on valid input | 8 `cove_result`, 6 dropped | PASS |
| Spend under the $5 cap | **$0.58** | PASS |
| Findings proportional to article count | 2 findings, **0.02/article** | **FAIL — DH-42** |

Event census: 200 `llm.call` (128 `google/gemini-2.5-flash`, 72
`x-ai/grok-4.5`), 72 `cascade`, 27 `skeptic_verdict`, 8 `cove_result`,
6 `cove_quote_fail`.

**DH-37 was NOT exercised by this run, and the smoke therefore does not
positively prove the fix.** Citation-gate drops: 0. Citation evidence strings
in the log: 0. `citations` on both surviving findings: `null`. Not one citation
reached `resolve()`, so the changed code path never executed. This is the same
wall the 2026-09-07 DH-36 attempt hit and recorded in §13 — the reference
fixture does not emit the citations these defects are about.

What the run *does* establish, and all it establishes:

1. **No regression.** Exit 0, no degraded events, no budget trip, zero parse
   failures, spend an order of magnitude under the cap.
2. **Every drop is attributable and none is the citation gate.** All 6 CoVe
   drops are `cove_quote_fail` — a fabricated `verbatim_quote`, which is the
   correct drop.
3. **The offline end-to-end case (criterion 2) remains the only positive
   evidence for DH-37.** Discharging it live needs a fixture whose findings
   actually carry ΦΕΚ citations; a synthetic bill built for that purpose is
   the cheaper route than waiting for the reference bill to produce one.

---

### 11.3 DH-37 discharged on a real document (2026-09-08)

§11.2 left DH-37 live-unproven for one reason: the reference bill's findings
carry no citations, so `resolve()` never ran. The fix was for a code path no
available document exercised. Waiting for the reference bill to eventually
produce a ΦΕΚ citation was never a plan.

`Inputs/DH37_citation_probe.txt` removes the excuse — a small Greek bill whose
text forces every citation shape `parse()` emits, including the DH-37 case: a
real, well-formed ΦΕΚ that is simply not one of the three hand-seeded
identifiers. Measured against the **container-built parser and the packaged
index**, i.e. the objects the CLI actually uses:

| Citation | Result | Meaning |
|---|---|---|
| `ΦΕΚ Α 88/2024` (unseeded) | `checked=False, resolved=False` | **unverified — DH-37 working.** Before the fix: `checked=True, resolved=False`, the pair CoVe hard-drops on |
| `ΦΕΚ Α 137/2023` (seeded) | `checked=True, resolved=True` | confirming still works |
| `CELEX 32016R0679` (seeded) | `checked=True, resolved=True` | confirming still works |
| `Ν. 4624/2019`, `Ν. 4270/2014` | `checked=False` | DH-28 law refs, never disprovable |

`tests/integration/test_dh37_citation_probe.py` pins this, including a
whole-document sweep asserting **no citation anywhere in a real bill comes back
disproven** — the assertion that would have caught DH-36's surviving half. No
LLM, no network, so it runs in CI at zero cost.

**Honest limit:** this proves the citation gate end-to-end on real document text
through real wiring. It does not prove a *live LLM run* keeps a finding that
cites an unseeded ΦΕΚ.

### 11.4 The live probe run, and why it still did not discharge DH-37

```
leggie analyze Inputs/DH37_citation_probe.txt --lenses constitutional
```

Run 2026-09-08, $0.016, exit 0. Census: 8 `llm.call`, 4 `skeptic_verdict`,
1 `cascade`. Findings surviving: **0**.

**All 4 findings were refuted by the skeptic, so none reached CoVe and the
citation gate never ran.** The blocker is no longer the fixture — the probe did
its job and produced findings carrying ΦΕΚ citations. It is DH-42's skeptic
half: the first gate in the chain refuses everything, so nothing downstream can
be validated live at all.

That reframes DH-42 from "yield is disappointing" to **"the verification chain
cannot currently be exercised end to end"**. On the 91-article bill the skeptic
refuted 19 of 27 (70%); on this probe, 4 of 4 (100%).

The run did confirm one thing directly. The four `refutes` verdicts carried
adjustments of **+0.5, +0.5, −0.5, −0.2** — the sign is arbitrary, exactly as
§12.0's `_bounded_adjustment` fix assumed. That is live evidence for a change
that had only log-line evidence before.

---

## 12. DH-42 — yield collapse in the verification chain

**Severity:** HIGH · **Status:** CoVe half FIXED, skeptic half OPEN (policy
decision) · **Class:** A · **Found:** 2026-09-08, by the §11.2 smoke.

### 12.0 Resolution (2026-09-08)

**Mechanism 2 — fabricated quotes — was not fabrication at all.** `_normalize`
folded only case and whitespace, then demanded an exact substring. A model asked
to quote verbatim *retypes* the sentence rather than copying bytes, so it renders
the source's typography its own way. Measured in the reference bill's 143,335
characters of paragraph text: **89 × `’` (U+2019), 11 × `–` (U+2013), 4,265
final sigmas** — every one a way for a byte-strict gate to reject a genuine
quote. Zero-width and soft hyphens survive PDF extraction and are invisible in
both strings, so they could only ever cause false misses.

`_normalize` now applies NFC, folds typographic punctuation to ASCII, folds
final sigma, and strips invisibles. It is shared by `validate_quote` **and all
five lenses' evidence check**, so one fix repairs both the lens verdict
("supports" vs "quote-not-verified-as-substring") and the CoVe hard-drop.

This does not loosen the gate in the sense §12 fenced: a fabricated sentence
still fails. `tests/unit/application/test_quote_normalization.py` pins both
halves — real quotes survive typographic variation, invented ones (wrong
ministry, wrong paragraph number, transposed article) are still rejected.

**Deliberate limit, recorded not hidden:** an all-caps quote of accented text
still misses, because Greek uppercase drops diacritics by convention and
lowercasing cannot restore them. Fixing that means folding accents, which is the
same knob that would let `πότε` match `ποτέ` in every comparison and cost the
gate its ability to catch a fabrication differing by one accented word. Not
worth it for the narrow case. `TestKnownLimitations` states this in the code.

**Observability, the reason this took a whole extra investigation:** the smoke
logged `cove_quote_fail` with the finding UUID and nothing else, so there was no
way to tell a fabricated quote from one the normalizer mishandled. It now logs
the quote itself and the source length.

**Mechanism 1 — skeptic over-refutation — is a POLICY question, not a bug, and
is deliberately left open.** `review()` hard-drops on a single `refutes` string
from one adversarial LLM call: no confidence weighting, no severity threshold,
no second opinion (`skeptic.py`, `_review_one`). That is what removed 19 of 27.
Changing it is exactly the "raise yield by loosening a gate" this section fences,
so it needs an explicit decision rather than a quiet edit.

One real bug *was* found and fixed while reading that path: the critic's
`confidence_adjustment` arrived unbounded and unsigned — the smoke logged
`verdict=refutes adjustment=0.50`, a *positive* half-point on a refutation.
A "supports" that lowers confidence, or a "refutes" that raises it, is the model
contradicting itself, and taking the number at face value lets that
contradiction move the score. `_bounded_adjustment` now forces the sign to match
the verdict.

**Scope was narrowed twice during implementation, both times away from my first
instinct.** The first version zeroed every `neutral` adjustment and capped
magnitudes at 0.25. Three ordering tests failed and were right to: a gate can
legitimately return `neutral` while still docking confidence — the
deterministic gates do exactly that — and capping magnitudes *shrinks
penalties*, meaning more findings survive. That is the "raise yield by loosening
a gate" move this section fences, arrived at by accident. The shipped version
constrains the sign only, leaves `neutral` untouched, and caps nothing.

**Measured 2026-09-08 (§11.4):** the probe run produced 4 findings and the
skeptic refuted all 4, so CoVe ran zero times and the quote fix could not be
observed either way. Mechanism 2's fix remains proven only offline.

**This makes mechanism 1 the priority, and raises its severity.** Refute rates
now measured: 19/27 (70%) on the reference bill, 4/4 (100%) on the probe. A
first gate that refuses everything means no downstream stage — CoVe, the
citation gate, rerank — can be exercised live at all. DH-37's live proof is
blocked behind it, and so is any future verification work.

### 12.1 Resolution of the skeptic half (2026-09-09) — it was not a policy question

§12.0 filed this as a policy decision with four options. Reading the evidence
first — as this section demanded — showed that was wrong. **It was a
prompt/code mismatch.**

All 23 refutations from the two smoke runs were decoded and classified:

| Kind | Count | Example |
|---|---|---|
| **Falsification** — the finding cites a provision that says something else | ~11 | *"βασίζεται σε εσφαλμένη ερμηνεία του άρθρου 77 παρ. 2"*, with the critic quoting the real text back |
| **Calibration** — the observation stands, the conclusion overreaches | ~12 | *"κάνει λογικό άλμα στο συμπέρασμα"*, *"υπερβάλλει στη σοβαρότητα"* |

The critic's own system prompt listed four grounds for refutation — *εσφαλμένη
επίκληση νόμου, μη υπαρκτό άρθρο, λογικό άλμα στο συμπέρασμα, υπερβολή στη
σοβαρότητα* — then said "if you find a clear error, `refutes`". `review()`
executes every `refutes` as deletion. So a disagreement about **tone** carried
the same penalty as citing a nonexistent article: roughly half the deletions
were real observations that were merely overstated.

**Fix:** the prompt now separates the two. Falsification still refutes and still
deletes, and the prompt demands the critic show what the provision actually
says. Overstatement returns `neutral` with a negative `confidence_adjustment`,
which `review()` folds into the score — the finding survives at lower rank with
the objection recorded. No schema change: `verdict` + `confidence_adjustment` +
`reason` already expressed this. Yesterday's `_bounded_adjustment` decision to
leave `neutral` sign-and-magnitude untouched is what makes it work, and is now
load-bearing rather than incidental.

This is not the fenced "loosen a gate" move. Nothing that the gate could
previously prove false now survives; only objections the critic itself framed as
matters of degree stopped being executions.

### 12.2 Measured — three live runs on the probe, one variable at a time

`leggie analyze Inputs/DH37_citation_probe.txt --lenses constitutional`

| Run | Change | refutes | `cove_result` | `cove_quote_fail` | **findings** | cost |
|---|---|---|---|---|---|---|
| v1 | before DH-42 | **4/4** | 0 | — | **0** | $0.016 |
| v2 | skeptic prompt split | 1/4 | 3 | 2 | 1 | $0.017 |
| v3 | + quote-gate fixes | 1/4 | 3 | **1** | **2** | $0.018 |

Cost is flat across all three; this bought findings, not tokens. CoVe went from
never executing to running on every survivor — the chain can now be exercised
end to end, which was the deeper problem behind §11.4.

**v2 → v3 came from the new logging, not from guessing.** With the quote text
recorded, both v2 failures were visibly real quotes retyped imperfectly: one
wrapped in guillemets the source does not carry, one joining the source's line
break without a space (*"χωρίς τηνπροηγούμενη"*). `validate_quote` now strips
enclosing quotation marks, and `_normalize` discards whitespace rather than
collapsing it — spacing is forgiven, content is not.

**The one remaining `cove_quote_fail` is the gate working correctly.** The model
returned two quotes joined by *«…» και «…»* — a compound excerpt, not the single
verbatim quote the contract asks for. Accepting it would mean accepting
arbitrary constructed text. If it proves common, the fix belongs in the lens
prompt, not the matcher.

**Still not discharged: DH-37's live proof.** Findings now reach CoVe, but
neither surviving finding carried a ΦΕΚ citation in its evidence, so
`resolve()` still did not run on the disprovable path. The offline proof
(§11.3) remains the evidence for DH-37.

**Not caused by DH-37.** DH-37 only ever *removes* drops, and this run had
zero citation-gate drops to remove — the losses are entirely upstream of the
code this plan touched. Filed separately so it is not mistaken for fallout.

**Symptom:** a single-lens run over 91 articles yields 2 findings
(0.02/article) against the v5 baseline of 0.14/article (`SMOKE_AUDIT.md`,
2026-07-11). The `findings_stats.py` health note puts 0.01 in pathological
territory; this sits just above it.

**Evidence — the funnel, measured:**

| Stage | Count | Loss |
|---|---|---|
| Findings reaching the skeptic | 27 | — |
| After the skeptic (`supports`) | 8 | **19 refuted, 70%** |
| Reaching CoVe | 8 | — |
| Surviving CoVe | 2 | **6 dropped, 75%** |
| **End to end** | **2 of 27** | **93%** |

Against v5, which recorded 19 verdicts as 9 refutes / 9 supports / 1 neutral:
the refute rate rose from **47% to 70%**, and CoVe then removed three quarters
of what the skeptic passed. The raw lens yield is *not* the problem — 27
findings from 91 articles is healthy; the verification chain is eating them.

**Two candidate mechanisms, neither confirmed:**

1. **Skeptic over-refutation.** 19/27 refuted by the `adversarial_critic`
   route. Needs per-verdict inspection against the source: are these genuine
   refutations or an over-eager critic?
2. **Fabricated quotes at the lens stage.** All 6 CoVe drops are
   `cove_quote_fail` — the lens model returned a `verbatim_quote` absent from
   the source. CoVe is behaving correctly; the *lens* is producing unusable
   evidence. 128 of the 200 calls ran on `google/gemini-2.5-flash`, which is
   the cheapest tier and the obvious suspect for quote fabrication.

Mechanism 2 is the more actionable and the more testable: a quote either
appears in the source or it does not, so it needs no judgement call.

**Do NOT fix by loosening a gate.** Both stages are doing what they were built
to do; the historical pathology this project already suffered was the opposite
(299 findings, 68% INFO filler). Raising yield by weakening the skeptic or the
quote check would recreate it. The fix belongs upstream — at whatever is
producing refutable claims and fabricated quotes.

**First step when this is picked up:** dump the 6 `cove_quote_fail` findings
with their claimed quotes beside the real article text, and the 19 refutations
with the critic's stated reason. One ablation per run
(leggie-research-methodology). Do not change two variables at once.
