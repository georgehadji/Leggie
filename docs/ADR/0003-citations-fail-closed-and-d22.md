# 0003 — Citations fail closed; index ships as package data; D22

**Status:** Accepted · **Date:** 2026-08-10

## Context

`GreekCitationParser.resolve()` (`infrastructure/citation/__init__.py`)
checks `citation.identifier in self._resolution_index` — a boolean
set-membership test against an index passed in at construction time. If no
index is configured, resolution fails closed: every citation reports
"unverified," never "invalid." The 2026-07-09 audit and a skill file both
described this as a *design* gap ("no code path ever populates a resolution
index" — the audit's "D7").

That claim was wrong for the deterministic pipeline, verified against
source on 2026-08-10: `container.py:153-168` loads
`leggie/data/citation_index.json` (181 identifiers, shipped as package data)
and constructs `GreekCitationParser(resolution_index=resolution_index)`,
which the deterministic path's `CoVeVerifier` receives via
`container.get(CitationParserPort)`.

The real gap was narrower and different: the *deliberative* pipeline's
`cli_handlers.py` (pre-IMPL-1) constructed a second, bare
`GreekCitationParser()` — no index — for `DeliberativeFlow`. Every citation
in a `<stem>_deliberative.md` report therefore read "unverified," not
because verification failed, but because nothing was ever loaded to verify
against. Filed as **D22**.

## Decision

- Citation resolution fails closed by design: an identifier not present in
  the index is "unverified," never silently treated as valid. This stands —
  it was never actually broken for the deterministic path.
- The resolution index is package data (`leggie/data/citation_index.json`),
  loaded once by the composition root and injected into every consumer via
  `CitationParserPort` — not constructed ad hoc per call site. IMPL-1 fixed
  the one call site (deliberative) that violated this.
- D22 regression coverage:
  `tests/unit/infrastructure/test_container_bindings.py::test_citation_parser_port_carries_resolution_index`
  asserts the container-resolved parser actually resolves a known
  identifier — the container binding is what's under test, not just the
  parser class in isolation.

## Consequences

- Both `leggie analyze` (deterministic) and the deliberative pipeline now
  verify citations against the same 181-identifier index. Before this fix,
  the deliberative report's citation-verification section was silently
  vacuous — always "unverified," regardless of citation validity.
- The audit that originally raised "D7" was corrected in place
  (`ARCH-AUDIT-V2_2026-08-10.md`, top-of-file note) rather than left
  standing — an uncorrected audit becomes the next audit's bad input, which
  is exactly how the D7 error happened (a 2026-07-14 skill note was trusted
  without checking the container binding that post-dated it).
- Still open, not part of this ADR: `ParseDocumentHandler` (`leggie parse
  -o`) uses a separate, independently-drifted citation extractor
  (`DocumentParser.extract_citations()`) with different scheme coverage
  than `GreekCitationParser.parse()`. Kept as-is pending a decision on which
  parser is canonical for that command's JSON output shape (see
  `ARCHITECTURE_IMPLEMENTATION_PLAN_2026-08-10.md` §2.1 Group A footnote) —
  not resolved by this ADR.
