# Contributing to Fix. Die. Repeat.

Thanks for your interest in contributing! This guide covers development setup, running tests, and the project architecture.

---

## Dev Install

```bash
uv sync --all-extras
```

This installs the project in editable mode along with all dev dependencies (`pytest`, `ruff`, `mypy`, etc.) into a managed virtual environment. Use `uv run <command>` to invoke tools within it.

### Running the Local Dev Version

After running `uv sync --all-extras`, prefix commands with `uv run`:

```bash
# Run the local editable version
uv run fix-die-repeat

# Verify Antigravity CLI preflight
uv run fix-die-repeat --preflight

# Run with options
uv run fix-die-repeat -c "pytest" --debug
```

When running from an editable install, you'll see a cyan indicator confirming you're in dev mode:

```
⚡ Running in DEV mode (editable install)
```

---

## Running Checks

Run the full CI verification suite (Ruff NEVER-IGNORE policy check, Ruff lint, Mypy type check, Pytest with coverage, and Ruff format check):

```bash
# Check-only mode (CI-friendly)
./scripts/ci.sh --check-only

# Auto-fix mode
./scripts/ci.sh
```

Individual commands:

```bash
# Run all unit tests
uv run pytest

# Lint and format
uv run ruff check fix_die_repeat tests
uv run ruff format fix_die_repeat tests

# Type check
uv run mypy fix_die_repeat tests
```

---

## Architecture

```
fix_die_repeat/
├── cli.py                       # CLI — uses Click
├── config.py                    # Settings and paths — uses Pydantic
├── agy_client.py                # Subprocess supervisor for `agy -p --output-format stream-json`
├── agy_policy.py                # Role schemas, permission profiles, codexbar quota ranking, preflight
├── health.py                    # Process-group reaping and single-instance RunnerLock
├── detection.py                 # Check command resolution and auto-detection
├── messages.py                  # Constants and message generators for user-facing text
├── prompts.py                   # Jinja template renderer for prompts (ChoiceLoader: user-dir wins)
├── runner.py                    # Main loop (AgyRunner) — orchestrates check/fix/review
├── runner_improve_prompts.py    # --improve-prompts mode: updates user templates with rollback
├── runner_introspection.py      # PR review introspection pass
├── runner_pr.py                 # PR review mode
├── runner_review.py             # Local + contextual + full-codebase review
├── runner_artifacts.py          # Attach/compact logs and diffs
├── utils.py                     # Utilities — logging, git, file operations
└── templates/                   # Shipped default prompt templates (Jinja2)
```

For details on how `AgyRunner` supervises `agy -p`, enforces role permissions, validates JSON schemas, and orders models via `codexbar`, see [`docs/agy-runner.md`](docs/agy-runner.md).

### User-owned prompt templates

`prompts.py` resolves Jinja templates from `<FDR_HOME>/templates/` first, then falls through to the package copies. The `--improve-prompts` mode seeds the four top-level language-agnostic templates (`fix_checks.j2`, `local_review.j2`, `resolve_review_issues.j2`, `pr_threads_header.j2`) plus the shared review fragments under `partials/` (`_issue_classification.j2`, `_critical_checklist.j2`, `_language_checks.j2`, `_review_reporting_rules.j2`, `_review_output_contract.j2`, `_review_readonly_task.j2`) into that directory on demand and asks `agy` to update them in place. After the turn completes, Python validates that all templates parse as valid Jinja2 and updates `introspection.yaml` (`status: pending` → `status: reviewed`).

---

## State Files

All runtime state is stored outside the target repo, under `~/.fix-die-repeat/repos/<basename>-<8charhash>/` (override via `FDR_HOME`). Nothing is written inside the repo and `.gitignore` is never modified.

Global files directly under `<FDR_HOME>/`:

| File | Purpose |
|------|----------|
| `introspection.yaml` | Appended log of PR review introspection documents |
| `introspection-archive.yaml` | Archive of older reviewed entries after compaction |
| `templates/` | User-owned prompt template overrides |
| `quota_state.json` | Persistent model cooldowns and rolling UTC daily call timestamps |
| `agy_state.json` | Cached `agy --version` and `agy models` preflight receipt |

Per-repository files under `<FDR_HOME>/repos/<slug>/`:

| File | Purpose |
|------|----------|
| `runner.lock` | Single-instance PID-identity lock for the repository |
| `review.md` | Historical review entries, preserved across runs |
| `review_current.md` | Current issues to fix (materialized by Python from `REVIEW_SCHEMA`) |
| `build_history.md` | `git diff --stat` for changes made each iteration |
| `checks.log` | Full output from check command |
| `checks_filtered.log` | Error-relevant lines extracted from `checks.log` |
| `.checks_hashes` | History of check output hashes (for oscillation detection) |
| `agy.log` | All `agy` invocations and their output summaries |
| `agy/` | Per-call raw NDJSON event transcripts (`<timestamp>-<iter>-<role>-<model>.ndjson`) |
| `fdr.log` | Internal logging |
| `session.log` | Combined output for current run (timestamped if debug mode) |
| `changes.diff` | Git diff of all changes (used for review phase) |
| `run_timestamps.md` | Start/end timestamps of each run |
| `.start_sha` | Git commit SHA before any changes (for rollback) |
| `.pr_threads_cache` | Cached PR threads markdown |
| `.pr_threads_hash` | Cache key for PR threads (`owner/repo/number`) |
| `.pr_thread_ids_in_scope` | Thread IDs from the original PR fetch |
| `.resolved_threads` | Thread IDs materialized from `FIX_SCHEMA.resolved_thread_ids` |
