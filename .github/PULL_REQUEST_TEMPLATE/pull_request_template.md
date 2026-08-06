## Summary

<!-- What does this PR do? -->

## Related issue(s)

<!-- Closes #... -->

## Change type

- [ ] Bug fix
- [ ] Feature
- [ ] Refactor / wiring
- [ ] Docs / tests

## Test evidence

<!-- What did you run to verify? Include command + result. -->

- [ ] `pytest tests/ -q --cov=leggie --cov-fail-under=85`
- [ ] `ruff check leggie/ tests/`
- [ ] `mypy leggie/ --ignore-missing-imports`
- [ ] `lint-imports`
- [ ] `bandit -c pyproject.toml -r leggie/`

## Architecture guardrails

- [ ] No edits to `domain/models/`
- [ ] No new methods on existing ports
- [ ] `$5` budget cap not raised
- [ ] Ruff ignore list not expanded

## Checklist

- [ ] Tests added/updated for this change
- [ ] CHANGELOG.md updated (if release-relevant)
