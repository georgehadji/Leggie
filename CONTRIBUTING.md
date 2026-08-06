# Contributing to Leggie

Thanks for your interest in contributing! This project follows the
[rules distilled from its production-readiness work](docs/PRODUCTION_READINESS_PLAN.md)
and we ask contributors to respect them.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev,lint]"
```

## Running checks

The full gate set (all must pass before a PR merges):

```bash
pytest tests/ -q --cov=leggie --cov-fail-under=85   # 0 outbound connections
ruff check leggie/ tests/                            # lint
mypy leggie/ --ignore-missing-imports                # types
lint-imports                                         # architecture layers
bandit -c pyproject.toml -r leggie/                  # security
```

## Architecture guardrails (binding)

1. **Dependency rule** — Interfaces → Infrastructure → Application → Domain → Config.
   `lint-imports` must pass on every commit.
2. **Domain models are frozen** — do not edit `leggie/domain/models/`.
3. **No new methods on existing ports** — add new adapters / decorators / new ports.
4. **The `$5` budget cap is never raised.**
5. **The ruff ignore list only ever shrinks.**
6. **No silent failure** — new degradation paths emit `DEGRADED` or a logged warning.
7. **Structured output only** — new LLM interactions validate against a Pydantic schema.

## Making changes

1. Fork / branch from `master`.
2. Make focused changes with tests.
3. Run the full gate set above.
4. Open a PR with a clear description of the change and its test evidence.
