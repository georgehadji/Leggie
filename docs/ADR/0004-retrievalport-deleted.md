# 0004 — RetrievalPort deleted; CELLAR is the reintroduction trigger

**Status:** Accepted · **Date:** 2026-08-10 (IMPL-2)

## Context

`RetrievalPort` (`application/ports/retrieval.py`) +
`SimpleRetrievalAdapter` (`infrastructure/retrieval_adapter.py`) + a
container binding had zero call sites anywhere in the codebase — no lens,
no flow, no CLI command called `.search()` / `.get_document()` /
`.corpus_stats()`. The adapter was a file-glob stub reading `corpus/*.md`
and scoring every match a flat `0.5`; the `corpus/` directory it read from
was empty, with no fixture data.

Before deleting, this was researched rather than assumed: is there a real,
currently-unmet need this port could fill? Finding: yes, narrowly — CoVe
verification (`cove_verifier.py`) only checks citation-identifier
*membership* against `citation_index.json` (see ADR-0003), it never fetches
or compares against actual ΦΕΚ/CELEX/ECLI document text. That is a genuine
gap. But the stub could not fill it: the real need is a networked fetcher
against EUR-Lex/ET.gr, and wiring an empty-corpus file-glob stub into CoVe
to "use" it would be inventing a consumer to justify existing dead code,
not meeting the actual need.

## Decision

Delete `RetrievalPort`, `RetrievalResult`, `SimpleRetrievalAdapter`, the
container binding, the `ports/__init__.py` re-export, and their dedicated
tests. Pure subtraction — recoverable from git history.

`RetrievalSettings` (`config/settings.py`) is left in place: it names a
HuggingFace embedding-model catalog for a future dense/hybrid retriever, a
different concern from the deleted port/adapter pair and never consumed by
either (confirmed: `SimpleRetrievalAdapter.__init__` never read it). Not
this deletion's scope.

## Consequences

- ~90 lines removed; import-linter dependency count drops accordingly.
- **Reintroduction trigger, recorded so it doesn't need re-deriving**: the
  EUR-Lex CELLAR integration is what would give retrieval a real reason to
  exist — a SPARQL client against a real corpus, sized to feed CoVe
  substantive-claim verification. When that requirement is real, rebuild
  from it. Do not resurrect this stub via `git revert` — the stub's shape
  was guesswork, not a spec.
- Coverage note: deleting ~90 lines of untested stub code nudges the
  coverage percentage up arithmetically. This is not progress toward the
  85% v1.0 exit criterion and should not be counted as such
  (`ARCHITECTURE_IMPLEMENTATION_PLAN_2026-08-10.md` §3).
