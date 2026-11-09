---
name: fdr-check
description: Run the fix-die-repeat quality gate (Ruff NEVER-IGNORE validation, Ruff lint/format, Mypy typecheck, and Pytest with coverage). Use before finishing any code or test change in this repository.
---

# fix-die-repeat Quality Gate (`fdr-check`)

Run the repository's single verification gate before concluding any implementation or refactoring task:

```bash
./scripts/ci.sh --check-only
```

## What It Checks

1. `uv run scripts/validate_ruff_rules.py` — verifies that `C901`, `PLR0913`, `PLR2004`, and `PLC0415` are never ignored in `pyproject.toml`.
2. `uv run ruff check .` — lints `fix_die_repeat`, `tests`, and `scripts` with `ALL` rules enabled.
3. `uv run mypy fix_die_repeat tests` — runs static type checking across source and tests.
4. `uv run pytest` — runs the unit test suite and enforces `>=80%` coverage on `fix_die_repeat`.
5. `uv run ruff format --check .` — checks formatting consistency.

## Fixing Failures

- For auto-fixable lint or formatting issues, run `./scripts/ci.sh`.
- For `C901` (complexity), extract focused helper functions or methods.
- For `PLR0913` (too many arguments), bundle related parameters into a frozen `@dataclass`.
- For `PLR2004` (magic value comparison), extract a named module-level constant.
- For `PLC0415` (import outside top-level), move the import to the top of the module.
