# 0002 — Composition root in infrastructure/container.py

**Status:** Accepted · **Date:** 2026-08-10 (IMPL-1)

## Context

Before IMPL-1, `application/cqrs/handlers/cli_handlers.py` and
`application/workflow/bill_analysis_flow.py` hand-constructed infrastructure
adapters directly (`GreekCitationParser()`, `ReasonerAdapter(...)`,
`IngestorFactory.ingest(...)`, `DocumentParser()`, ...) even though every one
of those types was already bound in `infrastructure/container.py`'s
`Container.configure_defaults()`. This was whitelisted as 13
`ignore_imports` entries in the layer contract rather than fixed — a
self-declared debt item (`ARCH-04`) with the fix already named but not done.

Two of those construction sites diverged in behavior: the deterministic
path's citation parser got the container's indexed
`GreekCitationParser(resolution_index=...)`; the deliberative path's did
not. Same class, two construction sites — this was D22, a live production
bug, not just a lint violation. See ADR-0003.

## Decision

`Container` (`infrastructure/container.py`) is the single composition root.
Application-layer code resolves dependencies through it — `container.get(SomePort)`
— rather than importing and constructing infrastructure classes.

Three resolution strategies, by what the dependency looks like:

1. **Port already exists** (the common case): resolve by the ABC type —
   `container.get(CitationParserPort)`, `container.get(ReasonerPort)`, etc.
2. **No port, single implementation, per-request constructor args**
   (`CheckpointStore(path)`, `GoldSet(path)`): stays a direct import. A
   zero-arg container factory cannot supply a runtime value like
   `--checkpoint-path`; a string-key binding wouldn't remove the import
   either, since the handler still constructs the instance itself. Promote
   to a real port only if a second implementation appears (e.g. an S3
   checkpoint store) — the trigger, not "someday."
3. **No port, needed only as a type annotation**: define a narrow
   `Protocol` in the application layer instead of importing the concrete
   infrastructure class, even under `TYPE_CHECKING`. `ContainerProtocol`
   (`cli_handlers.py`, declares only `get()`/`has_binding()`) mirrors the
   pre-existing `ServerLifecycle` Protocol in `deliberative_flow.py`.

Exceptions that a cross-layer caller is expected to catch belong to the
port module, not the infrastructure implementation — the LLM exception
hierarchy (`LLMError`, `LLMConfigurationError`, `LLMTimeoutError`,
`LLMRateLimitError`, `BudgetExceededError`) moved from
`infrastructure/llm/base.py` to `application/ports/llm.py`, mirroring
`ReasonerUnavailableError`'s existing home in `application/ports/reasoner.py`.

## Consequences

- All 13 whitelisted `ignore_imports` entries are drained to zero
  (`pyproject.toml`, `layer-dependencies` contract). The `ARCH-04` baseline
  comment block is gone — a baseline comment for an empty baseline is rot.
- `ingest_parse.py`'s lazy-fallback factories (`lazy_ingest_adapter`/
  `lazy_parse_adapter`) remain — they duplicate container bindings that
  already exist, but deleting the file requires making
  `BillAnalysisFlow`/`DeliberativeFlow`'s `ingester`/`parser` params
  non-optional, which touches ~57 test construction sites. Deferred, not
  forgotten — tracked as its own future phase, not part of IMPL-1's scope.
- D22 is fixed as a side effect of doing the refactor correctly: both
  citation-parser construction sites now resolve the same
  `container.get(CitationParserPort)`, so they can no longer drift.
