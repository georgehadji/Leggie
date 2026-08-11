# 0005 — Deliberative pipeline deliberately skips Skeptic/CoVe

**Status:** Accepted (retroactive) · **Date:** 2026-08-10

## Context

Leggie has two analysis pipelines: the deterministic `leggie analyze` path
(`BillAnalysisFlow` → lenses → `BlackboardAggregator`'s 4-round pipeline:
dedup → rerank → Skeptic → CoVe) and the deliberative path
(`DeliberativeFlow` → external Reasoner service → synthesis + citations).
Only the deterministic path runs Skeptic (adversarial claim-checking) and
CoVe (citation/quote verification against the bill's own text) as
aggregation rounds.

## Decision

The deliberative pipeline's output is explicitly not findings-grade in the
same sense as the deterministic path's: it is a single external reasoning
service's synthesis, not a blackboard of per-lens findings that benefit
from adversarial refutation and verbatim-quote verification. Running
Skeptic/CoVe against a single synthesis document would not perform the same
function they perform against multiple independent lens findings — there is
nothing to cross-check between findings when there is only one.

The deliberative path still gets citation resolution (via
`CitationParserPort`, see ADR-0003 / D22) — that is orthogonal to
Skeptic/CoVe's finding-level verification and applies regardless of which
pipeline produced the citations.

## Consequences

- Deliberative reports carry a synthesis + citation-resolution status, not
  a Skeptic-refutation or CoVe-verified-quote status. This is by design, not
  an oversight — do not "fix" it by force-running
  `BlackboardAggregator`'s rounds against deliberative output without first
  reconsidering what those rounds would be checking.
- If a future requirement needs adversarial verification of deliberative
  output specifically, that is new design work (what does "refute a
  synthesis" mean operationally?), not a wiring gap to close.
