# 0008 — A citation index may confirm always, but disprove only where declared exhaustive

**Status:** Accepted · **Date:** 2026-09-08 · **Refines:** ADR-0003

## Context

`CoVeVerifier._check_citations` treats a resolved citation as **positively
disproven** when the parser reports `checked=True, resolved=False`, and
hard-drops the entire finding carrying it. That rule is correct: the parser is
the only component that can say a lookup actually happened, so `checked` is a
claim of authority, not a claim of effort.

The adapter got that claim wrong twice.

**Originally** `resolve()` set `checked=True` whenever *any* index was
configured. A real, valid ECLI or et.gr URL against an index holding zero of
either came back disproven. Filed as DH-36.

**DH-36's fix** gated on the index's declared `categories` counts: a scheme
was checkable if the index held *any* entries for it. That closed the
zero-count half and left the rest open. `tools/build_citation_index.py`
hand-seeds three ΦΕΚ numbers and four CELEX numbers — described in its own
comment as "cited across the current test corpus". Nonzero, therefore
"covered", therefore disprovable. Every genuine gazette reference outside
those three still hard-dropped its finding, and ΦΕΚ is the most-cited scheme
in Greek bills.

The error was conceptual, not clerical: **presence is not exhaustiveness.**
Confirming a citation needs only a hit. Disproving one needs the index to be a
*complete register* of the scheme, so that absence carries information. Of the
181 identifiers Leggie ships, 174 are `Σύνταγμα Άρθρο N` / `Χάρτης Άρθρο N`
strings that `parse()` can never emit, and the remaining 7 are a hand-picked
sample. Nothing here is a register of anything.

DH-36 also shipped a test asserting the surviving half was correct, which
would have blocked the fix at the gate.

## Decision

Authority to disprove is **declared by the index**, never inferred from its
contents.

1. `GreekCitationParser` takes `authoritative_schemes: set[CitationScheme] |
   None`. A **miss** is reported `checked=True` only for a scheme in that set.
2. A **hit** always reports `resolved=True, checked=True`, whatever the set
   says. The identifier is literally on the known-good list; exhaustiveness is
   irrelevant to a positive.
3. `None` keeps the pre-existing "the caller built this index and asserts it
   is complete" semantics, used by `build_resolution_index` callers. The
   composition root, which loads a packaged file it did not build, always
   passes an explicit set.
4. `container.py` reads the set from the index file's own
   `authoritative_schemes` list. Absent, malformed, or naming an unknown
   scheme → empty set. Absence is the correct steady state and does not warn;
   a malformed declaration does (DH-29's trust-boundary precedent).
5. The packaged index ships `"authoritative_schemes": []`. Today the
   deterministic parser therefore never disproves anything.

## Consequences

- **A fabricated ΦΕΚ is no longer caught deterministically.** It never really
  was: with three seeded numbers the gate disproved essentially every genuine
  citation it saw as well. Judging a citation *wrong* moves to CoVe's LLM
  cross-check, which is where a judgement call belongs. Detection returns when
  an index is built from a live register.
- **More citations read "unverified" in reports.** Consistent with ADR-0003's
  fail-closed stance, and already true of ECLI and URL since DH-36. Unverified
  is not invalid.
- **The mechanism is kept, not deleted.** ADR-0004 already names EUR-Lex
  CELLAR as the trigger for reintroducing retrieval; the same trigger arms this
  switch. Rebuilding a deleted mechanism would cost more than a dormant flag.
- **The switch is dangerous by construction**, so it is guarded on the data
  side: `test_packaged_index_as_shipped_declares_no_authority` fails if a
  future builder change ever arms it on a seeded list.

## Alternatives rejected

- **Infer authority from a count threshold** ("authoritative above N entries").
  Same category error with a magic number attached; 10,000 gazette numbers is
  still not every gazette number.
- **Delete the disprove path entirely.** Simpler today, but it would have to be
  rebuilt the day a live register lands, and the deletion would erase the
  reasoning above with it.
- **Fix the gate in `cove_verifier.py` instead.** The gate's rule is right. The
  wrong value was arriving from the adapter, and moving the check would leave
  the adapter free to lie to its next caller.
