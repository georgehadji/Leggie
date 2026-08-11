# Architecture Implementation Plan — 2026-08-10

Source: `ARCH-AUDIT-V2_2026-08-10.md` (re-audit). Covers IMPL-1 … IMPL-6.
Companion to `docs/ARCHITECTURE_REMEDIATION_PLAN.md` (Phases 0–1, already
landed as ARCH-01/02/03). This plan is Phases 2–6.

Every code path named below was read on 2026-08-10 at `91f5947`. Line numbers
are from that commit.

---

## §0 — Corrections to the audit (read this first)

Verifying the plan against source turned up two things the audit got wrong.
Both change what gets built.

### 0.1 D7 (citation resolution) is CLOSED for the deterministic pipeline — audit was wrong

The audit listed D7 as Primary Risk #5, claiming "no code path ever populates
a resolution index." False. Verified:

- `container.py:153-168` loads `leggie/data/citation_index.json` and passes it
  to `GreekCitationParser(resolution_index=resolution_index)`.
- That file exists and holds **181 identifiers**.
- `cli_handlers.py:63-70` `_resolve_cove_from_container()` pulls
  `CitationParserPort` from the container into `CoVeVerifier`.

So `leggie analyze` (deterministic path) resolves citations against a real
index. The audit's Primary Risk #5 and its roadmap entry are withdrawn. The
error came from reading `citation/__init__.py`'s fail-closed branch and the
architecture-contract skill's 2026-07-14 note without checking the container
binding — the binding post-dates the note.

### 0.2 A real bug found in its place — the deliberative path gets an unindexed parser

`cli_handlers.py:219`:

```python
citation_parser=GreekCitationParser(),      # ← no resolution_index
```

`DeliberativeFlow` is handed a bare parser while its deterministic sibling
gets the 181-identifier one from the container. Same class, two construction
sites, divergent behavior: every citation in `<stem>_deliberative.md` reports
"unverified" — not because verification failed, but because nothing was ever
loaded to verify against.

This is not cosmetic and it is not separate from IMPL-1. Lines 208-219
hand-construct `ReasonerAdapter`, `ReasonerServerManager`, **and**
`GreekCitationParser` — all three are already registered in the container
(`container.py:167`, `:229`, `:243`). The composition-root refactor fixes a
live correctness defect, not just a lint contract. **This raises IMPL-1 from
"debt paydown" to "bug fix" and it goes first.**

Add to the defect ledger as **D22**.

### 0.3 One audit claim confirmed sharper than stated

`RateLimiter` is config-driven, not the hardcoded `5.0` the audit cited from
`openrouter.py:49` (that's the constructor default). `container.py:186-189`
passes `settings.llm.max_rate_per_second`. D9 closed, as stated — but for a
better reason than given.

---

## §1 Sequencing

Dependency-ordered. Each phase lands as its own commit behind the full gate set.

| # | Item | Why this position |
|---|---|---|
| 1 | **IMPL-1** composition root | Fixes D22 (live bug). Every later phase edits these same call sites — doing it first means editing them once. |
| 2 | **IMPL-5** lockfile + SBOM | Independent of all code changes. Cheap, HIGH severity, no merge risk against 1. |
| 3 | **IMPL-2** RetrievalPort | Deletion. Trivially safe once 1 has settled the container's shape. |
| 4 | **IMPL-3** concurrency governor | Touches the container (new binding) — wants 1 done. |
| 5 | **IMPL-4** aggregation Strategy | Lowest value. See §2.5 — recommend deferring. |
| 6 | **IMPL-6** ADRs + docs | Records decisions 1–5 actually made, so it must be last. |

---

## §2 Per-item plans

### 2.1 IMPL-1 — Composition-root injection (drains 13 `ignore_imports`, fixes D22)

**Severity:** HIGH · **Paradigm:** Dependency Injection, constructor-injected
· **Pattern:** Composition Root (already exists at `container.py`; the fix is
to *use* it, not to build anything new)

All 13 whitelisted violations are one defect wearing 13 hats: **a caller
hand-constructs something the container already binds.** Not 13 problems — one
pattern, 13 sites. The fix per site is mechanical; what varies is what key the
application layer is allowed to ask for.

#### Group A — port already exists, just resolve it (7 sites, zero new abstractions)

| Site | Now | Becomes |
|---|---|---|
| `cli_handlers.py:204,219` | `GreekCitationParser()` | `container.get(CitationParserPort)` ← **fixes D22** |
| `cli_handlers.py:205,208` | `ReasonerAdapter(...)` | `container.get(ReasonerPort)` |
| `cli_handlers.py:84,87` | `IngestorFactory.ingest(...)` | `container.get(IngestPort)` |
| `cli_handlers.py:85,88` | `DocumentParser()` | `container.get(ParsePort)` |
| `ingest_parse.py:15,21` | `lazy_ingest_adapter()` / `lazy_parse_adapter()` | handler passes resolved ports into the flow |

`ingest_parse.py` is dead weight: it duplicates container bindings that
already exist (`container.py:182-183`), and both flows already accept
`ingester=` / `parser=` injection (`bill_analysis_flow.py:105-106`,
`deliberative_flow.py:60-61`) — they only fall back to the lazy factories when
nothing is passed. **Delete the file**; have the handlers pass the resolved
ports. Two `ignore_imports` entries drain with it.

**One snag, flagged not glossed:** `ParseDocumentHandler` calls
`parser.extract_citations(text)` (`cli_handlers.py:92`). `parse_with_integrity`
*is* on `ParsePort` (`ports/parse.py:19`) so that call is fine, but
`extract_citations` is not — and invariant #4 forbids adding it. It doesn't
need adding: `CitationParserPort.parse(text)` is that function. Resolve the
citation parser from the container and call it. **Pre-check before editing:**
confirm return shapes match (`list[Citation]` vs whatever the JSON output
block at `cli_handlers.py:107` expects) — if they differ, convert at the
handler, do not widen either port.

#### Group B — no port exists; use a string key (3 sites)

`CheckpointStore`, `EvalScorer`/`GoldSet`, `SqliteEventStore` are
infrastructure types with no port and no second implementation.

Do **not** invent `CheckpointPort` / `EvalPort` for a single implementation
each — that is the "interface with one implementation" anti-pattern the audit
already flags elsewhere, and YAGNI. The container already supports string keys
(`register_instance(port_type: type | str, ...)`, `get(port_type: type | str)`).

- Widen `Container.register()` to accept `type | str` (currently `type` only,
  `container.py:61`) — one-line signature change, mirrors `register_instance`.
- Register under `"checkpoint_store"`, `"eval_scorer"`, `"gold_set"`.
- Handlers resolve by string.
- Promote to a real port **only if** a second implementation appears (e.g. an
  S3 checkpoint store). Note that trigger in the ADR.

`SqliteEventStore` at `cli_handlers.py:356` needs a look first — the store is
already resolved from the container three lines later (`:359`), so the import
is probably an `isinstance` narrowing. If so, delete the import and narrow on
`EventBusPort` behavior instead (duck-typed `hasattr`, or a
`@runtime_checkable` Protocol in the application layer if the check is load-
bearing).

#### Group C — the type-only imports (3 sites)

- `from leggie.infrastructure.container import Container` (`:33`, under
  `TYPE_CHECKING`) — grimp counts it. Define `ContainerProtocol` in
  `leggie/application/ports/` with just `get()` / `has_binding()`, annotate
  handlers against that. The application layer stops naming an infrastructure
  class even in a type position.
- `from leggie.infrastructure.llm.base import LLMConfigurationError` (`:44`) —
  an exception crossing a layer boundary inward. Move the exception class to
  `leggie/application/ports/llm.py`, have infrastructure import and raise it.
  Exceptions that callers are expected to catch belong to the interface, not
  the implementation.
- `ReasonerServerManager` (`:206,213`) — has no port, but the application layer
  already defines a `ServerLifecycle` Protocol for exactly this
  (`deliberative_flow.py`). Register the manager under `ServerLifecycle` in the
  container and resolve against the Protocol.

#### Execution order within IMPL-1

Do Group A first and commit — it carries the D22 fix and needs no new types.
Then B, then C. Drain the corresponding `ignore_imports` lines **in the same
commit as each fix**: `unmatched_ignore_imports_alerting="error"` means a
stale entry fails the build, which is the mechanism keeping this honest. Do
not batch the drains at the end.

**Done when:** `ignore_imports = []` and the `layers` contract passes with
zero exceptions. Delete the whole `ARCH-04` comment block (`pyproject.toml:134-147`)
at that point — a baseline comment for an empty baseline is rot.

**Test:** existing `tests/unit/test_architecture_contract.py` guards contract
visibility. Add one case asserting the deliberative path's citation parser
carries a non-empty `resolution_index` — that is the D22 regression test, and
it must fail against current `master` before the fix lands.

---

### 2.2 IMPL-5 — Lockfile + SBOM

**Severity:** HIGH · **Paradigm:** declarative build reproducibility · **Pattern:** n/a (tooling)

The Dockerfile says "Install runtime dependencies only from lockfile"
(`Dockerfile:12`) and copies `requirements.txt`. That file is 16 lines, 9
loose `>=` bounds, no transitive pins, no hashes — despite a header claiming
`pip-compile` produced it. Every image build resolves against whatever is
latest-compatible that day.

CI already runs `pip-audit --strict` (`ci.yml:57-60`), so vulnerability
scanning exists — what is missing is pinning and provenance.

1. Generate a real lockfile:
   `pip-compile --resolver=backtracking --generate-hashes --output-file=requirements.txt`
   Expect 40–100+ pinned, hashed lines replacing the current 9.
2. Add a CI drift check — regenerate and `git diff --exit-code requirements.txt`.
   Fails the build if someone edits `pyproject.toml` without recompiling.
   Put it in the existing `lint` job; no new job.
3. `pip install --require-hashes` in the Dockerfile builder stage, so the
   hashes are actually enforced rather than decorative.
4. SBOM: add `cyclonedx-py` to the `release.yml` job, emit
   `sbom.cyclonedx.json` as a release artifact. Release-only — an SBOM per PR
   is noise.

**Risk:** hash-pinning can surface a transitive conflict that loose bounds
were papering over. If it does, that is a real finding, not a reason to revert
— fix the conflict.

**Done when:** a clean `docker build` twice a week apart installs byte-identical
dependencies, and `requirements.txt`'s header stops lying.

---

### 2.3 IMPL-2 — RetrievalPort dead abstraction

**Severity:** MEDIUM · **Recommendation: delete, do not wire**

`RetrievalPort` + `SimpleRetrievalAdapter` + the `container.py:200-202`
binding have **zero call sites** outside themselves. No lens, no flow, no CLI
command calls `.search()` / `.get_document()` / `.corpus_stats()`. The adapter
is a file-glob stub that reads `corpus/*.md` and scores everything `0.5`.

Two options; take the first:

- **Delete** `ports/retrieval.py`, `infrastructure/retrieval_adapter.py`, the
  container binding, and the `ports/__init__.py` re-export. ~90 lines gone.
  Nothing imports it, so the diff is a pure subtraction.
- Wire it into a real call site. **Do not do this now** — there is no consumer
  asking for retrieval, and inventing one to justify an existing abstraction is
  backwards.

Deletion is safe because it is recoverable: git history keeps it, and the
EUR-Lex CELLAR integration that would genuinely need retrieval will want a
SPARQL client against a real corpus, not this file-glob stub. Rebuilding
against the real requirement beats preserving a placeholder shaped by
guesswork.

**Record in the ADR:** deleted 2026-08-xx, zero callers; the CELLAR trigger is
when to reintroduce, and reintroduction starts from the requirement, not from
`git revert`.

---

### 2.4 IMPL-3 — Cross-run concurrency governor

**Severity:** MEDIUM · **Paradigm:** bounded resource acquisition · **Pattern:** semaphore hierarchy (per-run, already present) + a process-wide ceiling

Every semaphore today is instance-scoped: `orchestrator.py:66,218`,
`skeptic.py:191`, `cove_verifier.py:152`, `bill_overview.py:41`. Each is sized
from `LEGGIE_LLM__MAX_CONCURRENCY` (default 5) **per `BillAnalysisFlow`
instance**. Two concurrent `leggie analyze` invocations each spend up to that
ceiling, blind to each other.

**Scope this correctly, because the obvious version overshoots.** Leggie is a
Windows CLI (see `leggie-windows-only`), not a service. Two separate processes
cannot share an `asyncio.Semaphore` — a process-wide semaphore fixes nothing
across two `leggie` invocations, which is the actual complaint.

What already exists and partly covers this: `RateLimiter(max_rate=...)`
bounds requests/second inside a process, and OpenRouter enforces its own
server-side limits. The uncovered case is narrow: two simultaneous CLI runs on
one host racing the same rate budget and the same `$5` cost cap.

Recommended, in order of laziness:

1. **Do nothing yet, and say so.** Single interactive runs are the actual usage
   pattern. The 91-article run is one process. If nobody is running concurrent
   analyses, this is a solution without a problem.
2. If concurrent runs become real: a **file-lock advisory governor** —
   `msvcrt.locking` on a lockfile under the checkpoint dir, acquired around LLM
   dispatch, sized by a new `LEGGIE_LLM__GLOBAL_MAX_CONCURRENCY`. Cross-process
   on Windows, no daemon, no Redis. Mark it
   `# ponytail: advisory file lock, per-host only — Redis if multi-host appears`.
3. Redis/queue only when Leggie stops being a CLI.

**Decision required from the operator before building anything here:** are
concurrent `leggie analyze` runs a thing you actually do? If no, close IMPL-3
as WONTFIX with that reason recorded, which is a legitimate outcome and
cheaper than option 2.

---

### 2.5 IMPL-4 — Aggregation Strategy extraction

**Severity:** LOW-MEDIUM · **Recommendation: defer, with the trigger written down**

`BlackboardAggregator.aggregate()` (`blackboard_aggregator.py:75-177`)
hardcodes 4 rounds: dedup → rerank → skeptic → CoVe. The audit proposed
extracting a `list[AggregationStage]` iterated in order.

Honest assessment: the rounds are **not** uniform, and pretending they are
costs more than it saves.

- Round 1 uses the Observer substrate (`_DedupObserver` subscribes to the board).
- Rounds 2–4 do not — they call services directly and post results for audit.
- Each round has a different early-exit and emits different `EventType`s with
  different payload shapes (`DEDUP_REMOVED` vs `FINDING_REFUTED` with a
  `"stage"` discriminator vs `CITATION_FAILED`/`CITATION_VERIFIED`).

A uniform `AggregationStage` Protocol would have to carry a union of those
concerns, and every stage would ignore most of it. That is abstraction tax
paid up front against a need nobody has yet: **no new aggregation round has
been proposed.**

**Defer with a written trigger:** extract the Strategy when a *third* party
needs to add a round, or when a round needs to be conditionally skipped at
runtime. Until then the 100-line linear method is readable top-to-bottom and
each round's early-exit is visible where it happens — which a Strategy list
would hide.

If it is built anyway, the correct shape is a Chain of Responsibility over
`list[Finding]` (each link may return fewer findings or short-circuit to `[]`),
**not** a Strategy — the rounds are sequential filters, not interchangeable
algorithms. Naming it Strategy in the audit was imprecise.

---

### 2.6 IMPL-6 — ADRs + docs

**Severity:** process · Currently `in_progress` (the re-audit half is done)

No `docs/ADR/` directory exists. Every judgment call in this codebase is
currently recoverable only from commit messages and skill files.

Create `docs/ADR/` with Nygard-format records. Minimum set, each one a
decision this plan actually makes:

| ADR | Decision |
|---|---|
| 0001 | Clean/Hexagonal layering + the 6-layer order (retroactive; cite `pyproject.toml` contract) |
| 0002 | Composition root in `infrastructure/container.py`; string keys for port-less infra types, promotion trigger = second implementation |
| 0003 | Citations fail closed; index ships as package data (`leggie/data/citation_index.json`) — and D22, why one path missed it |
| 0004 | `RetrievalPort` deleted 2026-08-xx; CELLAR is the reintroduction trigger and it starts from requirements |
| 0005 | Deliberative pipeline deliberately skips Skeptic/CoVe (Decision B) — its output is not findings-grade |
| 0006 | Concurrency is per-run by design; cross-run governor deferred (record the §2.4 outcome) |
| 0007 | Aggregation stays a linear method; Chain-of-Responsibility trigger recorded (§2.5) |

Doc updates in the same phase:

- **README port count**: says 7, source has 11. Stale since `ReasonerPort` landed.
- **`ARCH-AUDIT-V2_2026-08-10.md`**: apply §0.1 (withdraw D7 risk) and §0.2
  (add D22). An audit left uncorrected becomes the next audit's bad input —
  which is exactly how the D7 error propagated here.
- **`leggie-failure-archaeology` skill**: add D22.
- **`leggie-architecture-contract` skill**: D7 row → CLOSED with the container
  evidence; note the `ignore_imports` count going to zero.

---

## §3 Gates

Per phase, before commit — the standing set from `leggie-change-control`:

```bash
ruff check leggie/ tests/
mypy leggie/ --ignore-missing-imports
mypy tests/ --ignore-missing-imports
lint-imports --debug
bandit -c pyproject.toml -r leggie/
pytest tests/ --cov=leggie
```

Baseline to hold or beat: **734 passed, 1 skipped, 82.70%** coverage, ruff
clean, import-linter 2 contracts kept / 0 broken, bandit 0 issues.

Standing constraints, unchanged and not negotiable during this work:

- Do not widen the ruff ignore list (`E501`, `ARG002` are frozen debt).
- No blanket `# type: ignore` to silence an error — fix the type.
- Tests stay hermetic (`tests/conftest.py` blanks credentials, blocks
  non-localhost sockets).
- `$5` `max_cost_per_run` cap is never raised to make a run pass.
- Domain models stay frozen; no new methods on existing ports (invariant #4).
- Coverage gate is declared in `pyproject.toml` only — never re-add
  `--cov-fail-under` to a workflow (`tests/unit/test_ci_gate_contract.py`
  enforces this).

**Coverage note:** the v1.0 exit criterion is 85%, current is 82.70%. IMPL-2
deletes ~90 lines of untested stub code, which will nudge the percentage up
without writing a test. Do not count that as progress toward 85 — it is
arithmetic, not coverage. The gap closes with real tests or not at all.

---

## §4 Explicitly not building

Recorded so the next reader does not re-derive these:

- **No `CheckpointPort` / `EvalPort`** — one implementation each. String keys
  until a second appears (§2.1 Group B).
- **No `AggregationStage` Protocol** — no consumer (§2.5).
- **No Redis / task queue / distributed anything** — Leggie is a single-user
  Windows CLI. The prior audit's long-term target state stays deferred.
- **No retrieval implementation** — deleting, not building (§2.3).
- **No new ports at all** in this plan. Every fix uses a port that already
  exists, a Protocol the application layer already defines, or a string key.

---

## §5 Risk and rollback

| Phase | Risk | Rollback |
|---|---|---|
| IMPL-1 | Highest — touches every handler. Behavior change is intended (D22). | Per-group commits; `git revert` a group without losing the others. |
| IMPL-5 | Hash-pinning surfaces a latent transitive conflict. | Keep the old `requirements.txt` in the same commit's history; conflict is a finding to fix, not to revert. |
| IMPL-2 | Deleting something later wanted. | Pure subtraction, recoverable from history — but reintroduce from requirements, not `git revert`. |
| IMPL-3 | Likely WONTFIX. | n/a |
| IMPL-4 | Deferred. | n/a |
| IMPL-6 | Docs only. | n/a |

The one genuinely irreversible-in-shape action is **IMPL-2's deletion**.
Everything else is additive or mechanical. Confirm before deleting.

---

## §6 Task mapping

| Task | This plan |
|---|---|
| #21 IMPL-1 | §2.1 — expand into 3 sub-tasks (Groups A/B/C) |
| #22 IMPL-2 | §2.3 — delete |
| #23 IMPL-3 | §2.4 — decision needed first; likely WONTFIX |
| #24 IMPL-4 | §2.5 — defer, record trigger |
| #25 IMPL-5 | §2.2 |
| #26 IMPL-6 | §2.6 — re-audit done, ADRs + doc corrections remain |
| new | **D22** — deliberative citation parser has no resolution index (fixed inside §2.1 Group A) |
