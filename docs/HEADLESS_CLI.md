# Headless CLI — driving Leggie from an external agent

Contract for invoking Leggie non-interactively from another program. Verified
end-to-end on 2026-08-01; regression-locked by
`tests/unit/interfaces/test_headless_cli_contract.py`.

---

## Invocation

Two equivalent forms. Both work from **any** working directory:

```bash
leggie --json parse /path/to/bill.pdf
```

```bash
python -m leggie --json parse /path/to/bill.pdf
```

Prefer `python -m leggie` when you cannot guarantee the console script is on
`PATH` (virtualenv not activated, container without an entrypoint shim).

Leggie never prompts. There is no `input()`, no `getpass`, no confirmation
step anywhere in the codebase — a bare `leggie` with no subcommand prints help
and exits 0 rather than blocking.

---

## Output contract

Pass `--json` for every programmatic invocation.

**stdout carries exactly one JSON document and nothing else.** Informational
lines ("Parsed document written to …", the legal disclaimer, next-step hints)
are routed to a presenter that suppresses them in `--json` mode, so they can
never appear beside the payload. Diagnostics go to stderr; logs are controlled
separately with `--log-level`.

Success envelopes vary per command:

| Command | stdout shape |
|---|---|
| `parse` | the parsed document object (`title`, `articles`, `citations`, `integrity`) |
| `preview` | the preview object |
| `analyze` | `{"ok": true, "report": <string\|null>, "disclaimer": "..."}` |
| `eval` | `{"ok": true, "results": [...], "results_path": "..."}` |
| `replay` | the replay summary object |
| `--version` | `{"version": "x.y.z"}` |

`analyze` always emits an envelope, including the no-findings case — you will
never receive an empty stdout on success.

Failure is uniform across every command:

```json
{
  "ok": false,
  "error": "Unsupported format: .xyz",
  "error_type": "UnsupportedFormatError",
  "exit_code": 2
}
```

Branch on `error_type` when you need the specific cause; branch on the exit
code when you only need the class of failure.

---

## Exit codes

| Code | Name | Meaning | Agent should |
|---|---|---|---|
| 0 | OK | Success | proceed |
| 1 | UNKNOWN | Unclassified error | escalate; do not retry blindly |
| 2 | CONFIG_ERROR | Bad input path, unsupported format, missing/invalid API key, bad settings | **fail fast** — retrying cannot help |
| 3 | BUDGET_EXCEEDED | Per-run cost ceiling reached | stop; raise budget deliberately or reduce scope |
| 4 | DEGRADED_PARSE | Document failed parse-integrity checks | re-submit with `--allow-degraded-parse` only if you accept the risk |
| 5 | PROVIDER_UNAVAILABLE | LLM/network/ingest backend failure | **retry with backoff** — genuinely transient |
| 6 | INTERRUPTED | SIGINT/SIGTERM; checkpoint flushed | resume from checkpoint |

The 2-vs-5 split is the one that matters most for automation: a nonexistent
input file is a permanent caller-side mistake (2), not a transient outage (5).
Conflating them puts an agent into an infinite retry loop against a typo.

---

## Flags that matter headlessly

| Flag | Effect |
|---|---|
| `--json` | machine-readable stdout, structured errors. Use always. |
| `--quiet` / `-q` | suppress informational lines in human mode |
| `--log-level {DEBUG,INFO,WARNING,ERROR,CRITICAL}` | log verbosity, independent of stdout payload |
| `--output` / `-o` | write the artifact to a path; stdout still carries the payload |

---

## Cost and safety controls

Leggie calls a paid API. Before automating it:

- `LEGGIE_BUDGET__MAX_COST_PER_RUN` (default `5.0` USD) is the governor. Exceeding it exits **3**.
- `--checkpoint <path>` persists spend across runs so a crash-restart does not double-spend.
- `analyze` is the only command that costs money at scale; `parse` and `preview` are cheap or free.
- Start with `--lenses constitutional` and `--articles 1-3` to smoke-test a new integration before a full run.

Two defaults deliberately favour predictability: `LEGGIE_REASONER__ENABLED`
and `LEGGIE_REASONER__AUTOSTART` are both **false**, so Leggie will not spawn
an external service behind your back. The deliberative pipeline is strictly
opt-in via `--pipeline deliberative`.

---

## Reference example

```python
import json, subprocess, sys

RETRYABLE = {5}
FATAL = {2, 3, 4}

def analyze(bill_path: str) -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "leggie", "--json", "analyze", bill_path],
        capture_output=True, text=True, encoding="utf-8", timeout=3600,
    )
    payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
    if proc.returncode == 0:
        return payload
    if proc.returncode in RETRYABLE:
        raise TransientError(payload.get("error"))   # retry with backoff
    raise FatalError(payload.get("error"))           # do not retry
```

Set an explicit `timeout`. A full 5-lens run on a large bill has historically
taken 35–45 minutes; see `docs/PRODUCTION_READINESS_PLAN.md` §4b for the
throughput work that targets this.

---

## Known limits

Honest statement of what is not yet proven, so an integrator is not surprised:

- The **full 5-lens pipeline has never completed a recorded live run** (`docs/SMOKE_AUDIT.md`). Single-lens is proven.
- **Analysis quality is unmeasured** — the gold set is 2 synthetic bills and the last recorded eval scored F1 = 0.
- **Citation verification is not effective yet**: the resolution index holds 2 identifiers, so most citations resolve as unverified.

Output is automated analysis, **not legal advice**.
