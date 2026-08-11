# 0006 — Concurrency is per-run by design; cross-run governor deferred

**Status:** Accepted (WONTFIX for now) · **Date:** 2026-08-10 (IMPL-3)

## Context

Every concurrency limiter in Leggie is instance-scoped:
`orchestrator.py:66,218`, `skeptic.py:191`, `cove_verifier.py:152`,
`bill_overview.py:41` each construct their own `asyncio.Semaphore`, sized
from `LEGGIE_LLM__MAX_CONCURRENCY` (default 5), per `BillAnalysisFlow`
instance. Two concurrent `leggie analyze` invocations each independently
spend up to that ceiling — the limiter is per-run, not per-host.

Leggie is a single-user Windows CLI, not a service
(`leggie-windows-only`). Two separate OS processes cannot share an
`asyncio.Semaphore` in memory — a process-wide semaphore fixes nothing
across two `leggie` invocations, which is the actual failure mode a
cross-run governor would need to address. What already exists and partly
covers the risk: `RateLimiter(max_rate=...)` bounds requests/second inside
one process, and OpenRouter enforces its own server-side limits
server-side. The uncovered case is narrow: two simultaneous CLI runs on one
host racing the same rate budget and the same `$5` `max_cost_per_run` cap.

## Decision

**No governor built.** Asked directly whether concurrent `leggie analyze`
invocations are an actual usage pattern: they are not. Single interactive
runs (including the 91-article batch run, which is one process) are the
real usage today. Building a cross-process governor for a scenario that
doesn't occur is solving a problem nobody has.

**Fallback design, recorded so it doesn't need re-deriving if this
changes:** an advisory file lock (`msvcrt.locking` on a lockfile under the
checkpoint directory, acquired around LLM dispatch, sized by a new
`LEGGIE_LLM__GLOBAL_MAX_CONCURRENCY`) is the Windows-appropriate mechanism —
cross-process on Windows, no daemon, no Redis. Redis/a task queue is
correct only once Leggie stops being a single-host CLI.

## Consequences

- **Reopen trigger**: concurrent `leggie analyze` becomes a real pattern —
  batch usage, CI running multiple analyses at once, or a scripted
  multi-bill sweep. Until then, this stays closed.
- If reopened, build the file-lock design above first; escalate to
  Redis/queue only when Leggie stops being a single-host CLI, not before.
