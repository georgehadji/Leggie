# 0001 — Clean/Hexagonal layering, six-layer order

**Status:** Accepted (retroactive) · **Date:** 2026-08-10

## Context

Leggie is a Clean/Hexagonal architecture: `interfaces → infrastructure →
application → domain → observability → config`, enforced by import-linter's
`layers` contract (`pyproject.toml`, `layer-dependencies`). This was the
de-facto design from early on but was never written down as a decision —
only recoverable from the contract itself and commit history (ARCH-01/02/03,
2026-08-05).

`observability` sits below `domain` as its own layer, not inside
`infrastructure`, because it only ever imports `config` — it was filed in
the wrong package originally and moved out (ARCH-03).

## Decision

Six layers, dependencies point inward only:

1. `interfaces` — CLI entry point, thin, dispatches via mediator/handlers
2. `infrastructure` — adapters implementing ports (LLM, ingest, parse,
   persistence, reasoner, etc.)
3. `application` — ports, workflow orchestration, CQRS handlers, agents
4. `domain` — frozen Pydantic models, zero outward imports
5. `observability` — logging/tracing, cross-cutting but not "real"
   infrastructure
6. `config` — settings, the only thing everything may depend on

A second contract (`domain-purity`) forbids `domain` from importing
`observability` even though the layers contract would permit it — a domain
model has no legitimate reason to log anything itself.

## Consequences

- `unmatched_ignore_imports_alerting = "error"` means any whitelisted
  exception to the layer contract must stay accurate — a stale entry
  (already-fixed import) fails the build until pruned. This is what kept
  IMPL-1's 13-entry baseline honest instead of accumulating silently.
- Namespace packages under `application/{agents,cqrs,services,workflow}`
  and `infrastructure/llm/adapters` need `__init__.py` for grimp (the
  import-linter graph tool) to see them at all — their absence let the
  contract pass vacuously over 44 of 118 modules until 2026-08-05 (ARCH-01).
