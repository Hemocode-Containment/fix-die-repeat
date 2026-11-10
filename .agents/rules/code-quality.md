---
trigger: always_on
description: Strict code quality, Ruff NEVER-IGNORE policy, and test discipline for fix-die-repeat
---

# Code Quality & Verification Rules

1. **Ruff NEVER-IGNORE Policy (`scripts/validate_ruff_rules.py`)**:
   - Never ignore `C901` (extract helper functions), `PLR0913` (group >5 parameters into dataclasses), `PLR2004` (promote magic numbers to module-level constants), or `PLC0415` (keep imports at module top-level).
2. **Type Safety**:
   - All functions and methods in `fix_die_repeat/` and `tests/` must have complete type annotations that pass `uv run mypy fix_die_repeat tests`.
3. **Verification Gate**:
   - Before completing any code change, run `./scripts/ci.sh --check-only` and ensure Ruff, Mypy, and Pytest (`>=80%` coverage) pass cleanly.
4. **Git Hygiene**:
   - Never pass `--no-verify` to `git commit`.
