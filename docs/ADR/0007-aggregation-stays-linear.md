# 0007 — Aggregation stays a linear method; Chain-of-Responsibility is the extraction trigger

**Status:** Accepted (deferred) · **Date:** 2026-08-10 (IMPL-4)

## Context

`BlackboardAggregator.aggregate()` hardcodes 4 rounds in sequence: dedup →
rerank → Skeptic → CoVe. The 2026-08-10 audit proposed extracting a
`list[AggregationStage]` iterated in order, calling it a Strategy
extraction.

Verified against source before building: the 4 rounds are not uniform, and
a shared abstraction over them would cost more than it saves.

- Round 1 (dedup) uses the Observer substrate — `_DedupObserver` subscribes
  to the board.
- Rounds 2–4 (rerank, Skeptic, CoVe) do not use Observer — they call
  services directly and post results for audit.
- Each round has a different early-exit condition and emits different
  `EventType`s with different payload shapes (`DEDUP_REMOVED` vs.
  `FINDING_REFUTED` with a `"stage"` discriminator vs.
  `CITATION_FAILED`/`CITATION_VERIFIED`).

A uniform stage interface would have to carry a union of all these
concerns, and every stage would ignore most of it — abstraction tax paid up
front against a need nobody has yet. No third party or new aggregation
round has been proposed. The audit's proposed name ("Strategy") was also
imprecise even for the case where extraction is warranted: the rounds are
sequential filters over `list[Finding]` where each link may narrow or
short-circuit the set, which is Chain of Responsibility, not Strategy
(interchangeable algorithms over the same input, which these are not).

## Decision

Defer the extraction. The 100-line linear method is readable top-to-bottom
today, and each round's early-exit is visible exactly where it happens —
a Strategy/Chain list would hide that.

**Extraction trigger, recorded so it doesn't need re-deriving:** extract
when a *third* party needs to add a round, or when an existing round needs
to be conditionally skipped at runtime. If and when that happens, the
correct shape is **Chain of Responsibility** over `list[Finding]` (each
link returns a possibly-narrowed list or short-circuits to `[]`) — not a
Strategy list.

## Consequences

- No `AggregationStage` Protocol exists in this codebase. Do not add one
  speculatively; the trigger above is the gate.
- If the trigger fires, `blackboard_aggregator.py:75-177` is the file to
  restructure — each round's current early-exit and `EventType` emission
  become that round's Chain link.
