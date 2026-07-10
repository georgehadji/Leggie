---
name: leggie-build-and-env
description: >
  Recreate the Leggie development environment from scratch on Windows and fix
  environment problems. Load for install/import errors, venv setup, missing
  tools (pytest/ruff/mypy/lint-imports/pre-commit), Greek-text encoding or
  mojibake issues, CI-vs-local differences, or Docker questions. Leggie is
  developed and run on Windows; CI runs ubuntu — local Windows is the real
  target.
---

# Leggie Build and Environment

Target: Windows 11, Python 3.12+ (3.12.10 verified working 2026-07-10). Repo
root: `E:\Documents\Vibe-Coding\Leggie`. The project analyzes Greek text —
UTF-8 handling is a first-class concern (§4).

## 1. From scratch

```powershell
# PowerShell
py -3.12 --version                    # need 3.12+
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # PowerShell activation
pip install -e ".[dev]"               # runtime + pytest/ruff/mypy/pre-commit/hypothesis
pip install -e ".[lint]"              # adds import-linter
copy .env.example .env                # then edit: set your OpenRouter key
```

```bash
# Git Bash equivalents
source .venv/Scripts/activate
cp .env.example .env
```

Extras (pyproject): `dev` = pytest, pytest-asyncio, pytest-cov,
pytest-benchmark, hypothesis, coverage, ruff, mypy, pre-commit, types-PyYAML;
`lint` = import-linter + ruff + mypy; `eval` = pandas, scikit-learn,
matplotlib (only needed for extended eval analysis).

`.env` notes: get an OpenRouter key at openrouter.ai (single key covers all
providers). **Trap:** `.env.example` shows
`LEGGIE_BUDGET__MAX_TOKENS_PER_RUN=500000` — stale; code default is
20,000,000 and the 500k value historically throttled runs. Omit that line or
set 20000000.

## 2. Health check (all should pass)

```powershell
python -c "import leggie; print(leggie.__version__)"   # 0.1.0
leggie --version                                       # Leggie v0.1.0
python -m pytest tests/ -q                             # 361 passed (baseline 2026-07-10)
ruff check leggie/ tests/                              # clean
mypy leggie/ --ignore-missing-imports                  # clean (strict mode via pyproject)
lint-imports                                           # layer contract OK
pre-commit install                                     # one-time hook setup
leggie parse Inputs/OE_ΣΧΝ-ΥΠΔΙΚ.pdf | Select-Object -First 5   # free, no API key needed
```

Verified tool versions 2026-07-10: ruff 0.15.20, pre-commit 4.5.1,
Python 3.12.10. Pytest config (pyproject): `asyncio_mode=auto` (async tests
need no decorator), testpaths=tests.

## 3. Tooling invocations

| Tool | Command | Notes |
|---|---|---|
| Tests | `python -m pytest tests/ -q` | full suite is fast (no LLM calls in tests) |
| One file | `python -m pytest tests/unit/application/test_skeptic.py -v` | |
| Coverage | `python -m pytest tests/ --cov=leggie --cov-report=term-missing` | pyproject sets `fail_under = 80` |
| Lint | `ruff check leggie/ tests/` | ignore list is frozen debt — never widen (leggie-change-control) |
| Types | `mypy leggie/ --ignore-missing-imports` | strict=true in pyproject |
| Layers | `lint-imports` | interfaces→infrastructure→application→domain→config |
| Hooks | `pre-commit run --all-files` | ruff --fix, ruff-format, mypy --strict, bandit |

## 4. Windows traps (the money section)

1. **Greek text on console**: legacy codepage (cp1252/cp737) can't encode
   Greek → mojibake or `UnicodeEncodeError`. The CLI protects itself
   (`_force_utf8_console()` in `leggie/interfaces/cli/__init__.py` reconfigures
   stdout/stderr to UTF-8). For YOUR scripts: start with
   `sys.stdout.reconfigure(encoding="utf-8")`, or set `PYTHONUTF8=1`
   (`$env:PYTHONUTF8="1"` / `set PYTHONUTF8=1`), or `chcp 65001`.
2. **File I/O**: repo code passes `encoding="utf-8"` explicitly on writes
   (see flow auto-save). Never call `open()` on Greek-content files without
   `encoding="utf-8"` — Windows default is locale codepage.
3. **Greek filenames**: the sample bill is `Inputs/OE_ΣΧΝ-ΥΠΔΙΚ.pdf` — Greek
   letters in the path. PowerShell and Git Bash both handle it; quote the path
   if a shell chokes, and prefer tab-completion.
4. **PowerShell vs Git Bash**: activation differs
   (`.\.venv\Scripts\Activate.ps1` vs `source .venv/Scripts/activate`); log
   capture differs (`2>&1 | Tee-Object run.log` vs `2>&1 | tee run.log`).
5. **`temp_dir` setting default is `/tmp/leggie_ingest`** (POSIX path) — if
   ingest temp files ever matter on Windows, override
   `LEGGIE_INGEST__TEMP_DIR` to a real Windows path.
6. **lxml/pdfplumber**: binary wheels exist for 3.12 on Windows; if pip tries
   to build from source, upgrade pip (`python -m pip install -U pip`).

## 5. CI vs local

CI (`.github/workflows/ci.yml`): ubuntu, Python 3.12, `pip install -e ".[dev]"`,
then ruff + mypy + pytest. CI does **NOT** run: import-linter, the coverage
gate, bandit, or any live smoke/eval. Those are local responsibilities
(pre-commit covers bandit+mypy+ruff on commit). CI runs only on
push/PR to `master`.

## 6. Docker (optional)

`Dockerfile`: two-stage python:3.12-slim build; installs deps, copies
`leggie/ config/ tests/`, `ENTRYPOINT ["leggie"]`. Use when you need a clean
Linux runtime check: `docker build -t leggie . && docker run leggie --help`.
Not the primary dev path — Windows-native is.

## When NOT to use this skill

- Running analyses / artifact locations → **leggie-run-and-operate**
- Config values and env var catalog → **leggie-config-and-flags**
- Test-writing patterns → **leggie-validation-and-qa**
- A run misbehaving after env is healthy → **leggie-debugging-playbook**

## Provenance and maintenance

Dated 2026-07-10. Re-verify:
- Toolchain: `python --version; ruff --version; pre-commit --version; lint-imports --help`
- Test baseline: `python -m pytest tests/ -q`
- Extras: `grep -A12 "optional-dependencies" pyproject.toml`
- UTF-8 guard: `grep -n "_force_utf8_console" leggie/interfaces/cli/__init__.py`
- Stale env example: `grep MAX_TOKENS .env.example` vs `grep max_tokens_per_run leggie/config/settings.py`
