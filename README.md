# Fix. Die. Repeat.

[![CI](https://github.com/CTristan/fix-die-repeat/actions/workflows/ci.yml/badge.svg)](https://github.com/CTristan/fix-die-repeat/actions/workflows/ci.yml)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Fix. Die. Repeat.** automatically runs your project's checks, uses Google Antigravity (`agy`) to fix failures, reviews the result, and loops until everything passes.

> **What is Antigravity (`agy`)?** Google Antigravity is an agentic coding environment whose headless CLI (`agy -p`) reads files, edits code, runs commands, and returns schema-validated structured output from your terminal.

---

### Inspired by *Live. Die. Repeat.* aka *Edge of Tomorrow*

> *"I'm not going to give up on this. Not today, not tomorrow, not ever."*

A soldier relives the same day until the battle finally goes right. Fix. Die. Repeat. does the same thing to your repository, trapping your coding agent in a supervised loop until checks and code review both pass.

- **The "day"**: one full iteration of checks and review
- **The "death"**: a failing check or a critical review finding
- **The "reset"**: run `agy`, apply changes, and start the day again
- **The "escape"**: checks pass *and* the review reports `NO_ISSUES`

---

## What It Does

1. **Run your check command** (for example, `./scripts/ci.sh` or `pytest`)
2. **If checks fail** → `agy` analyzes the filtered failure log and applies a fix
3. **If checks pass** → `agy` reviews the diff in a read-only permission profile
4. **If review finds critical issues** → `agy` fixes them and the loop restarts
5. **Repeat** until the run survives both checks *and* review

## Quick Start

### Requirements

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Google Antigravity CLI (`agy`) on `PATH` and signed in (`fix-die-repeat --preflight` verifies your setup)
- Git
- (Optional) [CodexBar](https://github.com/steipete/CodexBar) (`codexbar`) on `PATH` for live reset-window-aware model ordering
- (Optional) GitHub CLI (`gh`) for [PR review mode](docs/guide.md#pr-review-mode)

### Install

```bash
uv tool install "fix-die-repeat @ git+https://github.com/CTristan/fix-die-repeat.git"
```

### Usage

```bash
# Verify agy CLI installation, model catalog, and structured-output health
fix-die-repeat --preflight

# Auto-detects your project's check command
fix-die-repeat

# Custom check command
fix-die-repeat -c "pytest -xvs"

# Override the model for all roles
fix-die-repeat -c "make test" -m claude-opus-4-6-thinking
```

On first run, `fix-die-repeat` [detects your project type](docs/guide.md#check-command-resolution) and asks you to confirm the check command. The choice is saved so subsequent runs use it automatically.

## Features

- **[Antigravity supervisor (`AgyRunner`)](docs/agy-runner.md)** — drives `agy -p` with `--output-format stream-json --json-schema <schema>`, per-role permission profiles, tool-aware stall detection, and post-run git invariant checks
- **[Reset-window-aware model routing](docs/agy-runner.md#reset-window-aware-model-selection-codexbar)** — combines per-role model chains (`fix`, `review`, `introspect`, `improve_prompts`), persistent cooldowns, and live `codexbar` quota windows to burn quota that resets soonest
- **[Auto-detection](docs/guide.md#check-command-resolution)** — finds your check command from project files (`Makefile`, `package.json`, `Cargo.toml`, `pyproject.toml`, and more)
- **[PR review mode](docs/guide.md#pr-review-mode)** — fetches and fixes GitHub PR review comments automatically
- **[PR review introspection](docs/guide.md#pr-review-with-introspection)** — analyzes PR reviews to identify prompt improvement opportunities
- **[Self-improving prompts](docs/guide.md#improve-prompts)** — `--improve-prompts` updates your user-owned templates in `<FDR_HOME>/templates/` from accumulated introspection data, with automatic Jinja2 validation and rollback
- **[Oscillation detection](docs/guide.md#oscillation-detection)** — warns when the same failure keeps repeating
- **[Context management](docs/guide.md#context-management)** — keeps prompts compact by attaching small files and summarizing large diffs
- **[Notifications](docs/guide.md#notifications-optional)** — optional [ntfy](https://ntfy.sh/) alerts when runs complete
- **[Debug mode](docs/guide.md#debug-mode)** — timestamped session logs and per-call NDJSON transcripts under `<fdr_dir>/agy/`

## Terms of Service Note

`fix-die-repeat` invokes the official `agy` CLI under your own interactive login for local, human-initiated development loops. Do not use a personal consumer Antigravity login on shared or unattended CI runners; see [Terms of Service and Acceptable Use](docs/agy-runner.md#terms-of-service-and-acceptable-use) for details.

For full CLI reference, environment variables, and detailed usage, see the **[User's Guide](docs/guide.md)** and **[Antigravity Runner Architecture](docs/agy-runner.md)**.

---

[Contributing](CONTRIBUTING.md) · [License (MIT)](LICENSE) · [Antigravity Runner Docs](docs/agy-runner.md)

> *"On your feet, maggot."*
