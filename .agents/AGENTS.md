# fix-die-repeat Agent Instructions

@[README](../README.md)
@[CONTRIBUTING](../CONTRIBUTING.md)

## Quick Commands

- Run full CI in check-only mode: `./scripts/ci.sh --check-only`
- Run full CI with auto-fix: `./scripts/ci.sh`
- Run tests: `uv run pytest`
- Run lint: `uv run ruff check .`
- Run typecheck: `uv run mypy fix_die_repeat tests`
- Run rule validation: `uv run scripts/validate_ruff_rules.py`

## Non-Negotiable Invariants

- Never ignore `C901`, `PLR0913`, `PLR2004`, or `PLC0415` in `pyproject.toml` or via inline `# noqa`.
- Never use `--no-verify` on `git commit`.
- All Antigravity invocations go through `AgyClient` (`fix_die_repeat/agy_client.py`) using the official `agy -p` CLI with `--output-format stream-json --json-schema <schema>` and role-scoped `--settings` permissions (`fix_die_repeat/agy_policy.py`).
- Python owns all state files in `<FDR_HOME>` (`review_current.md`, `review.md`, `.resolved_threads`, `.introspection_result.yaml`, `introspection.yaml`, `quota_state.json`, `agy_state.json`, `runner.lock`). Never instruct `agy` to write state files in `<FDR_HOME>` directly.
