# User's Guide

Full reference for [Fix. Die. Repeat.](../README.md) — CLI options, configuration, and detailed usage.

---

## Command Line Options

```
Usage: fix-die-repeat [OPTIONS]

Options:
  -c, --check-cmd TEXT            Command to run checks (default: auto-detected)
  -n, --max-iters INTEGER         Maximum loop iterations (default: 10)
  -m, --model TEXT                Override model selection (e.g., claude-opus-4-6-thinking)
  --max-pr-threads INTEGER        Maximum PR threads to process per iteration (default: 5)
  --archive-artifacts             Archive existing artifacts to a timestamped folder
  --no-compact                    Skip automatic compaction of large artifacts
  --pr-review                     Enable PR review mode
  --pr-review-introspect          Enable PR review mode with prompt introspection (implies --pr-review)
  --contextual-review             Smart contextual review (report-only). Reviews uncommitted
                                  changes, branch diff vs default branch, or full codebase
                                  if neither applies.
  --full-codebase-review          Audit the entire codebase instead of a diff. Report-only:
                                  never attempts fixes. Ignores --pr-review if also set.
  --pr-threads-introspect-only    Fetch the PR's unresolved review threads, run introspection
                                  on them, then exit. Does not run checks, local review, or
                                  attempt fixes.
  --improve-prompts               Ask agy to update the user-owned prompt templates under
                                  <FDR_HOME>/templates/ based on accumulated introspection data.
                                  Seeds user copies on first use; never mutates the package.
  --preflight                     Run Antigravity CLI, model catalog, and structured-output
                                  preflight checks, then exit.
  -d, --debug                     Enable debug mode (timestamped session logs and verbose logging)
  --version                       Show the version and exit.
  --help                          Show this message and exit.
```

## Environment Variables

All options can be set via `FDR_`-prefixed environment variables:

| Variable | Description | Default |
|-----------|-------------|----------|
| `FDR_CHECK_CMD` | Command to run checks | (auto-detected) |
| `FDR_MAX_ITERS` | Maximum loop iterations | `10` |
| `FDR_AGY_BIN` | Antigravity CLI binary name or path | `agy` |
| `FDR_MODEL` | Override model selection across all roles | (none) |
| `FDR_MODELS_FIX` | Ordered model fallback chain for `fix` | `claude-opus-4-6-thinking,claude-sonnet-4-6-thinking,gemini-3.1-pro-high,gemini-3-flash,gpt-oss-120b-medium` |
| `FDR_MODELS_REVIEW` | Ordered model fallback chain for `review` | `claude-opus-4-6-thinking,gemini-3.1-pro-high,claude-sonnet-4-6-thinking,gemini-3-flash,gpt-oss-120b-medium` |
| `FDR_MODELS_INTROSPECT` | Ordered model fallback chain for `introspect` | `gemini-3.1-pro-low,gemini-3-flash,claude-sonnet-4-6,gpt-oss-120b-medium` |
| `FDR_MODELS_IMPROVE_PROMPTS` | Ordered model fallback chain for `improve_prompts` | `claude-opus-4-6-thinking,gemini-3.1-pro-high,claude-sonnet-4-6-thinking` |
| `FDR_PREFLIGHT` | Run preflight checks and exit | `0` |
| `FDR_QUOTA_SOURCE` | Quota provider (`auto` for `codexbar`, or `none`) | `auto` |
| `FDR_QUOTA_MAX_USED_PCT` | Skip models with any quota window at or above this used % | `95.0` |
| `FDR_QUOTA_COOLDOWN_MINUTES` | Cooldown duration after a capacity error | `30` |
| `FDR_DAILY_MAX_CALLS` | Rolling 24-hour cap on `agy` calls (`0` = unlimited) | `50` |
| `FDR_AGENT_IDLE_TIMEOUT_S` | Idle timeout when no tool is running (seconds) | `120` |
| `FDR_AGENT_TOOL_TIMEOUT_S` | Maximum runtime for an active tool step (seconds) | `1200` |
| `FDR_AGENT_HARD_TIMEOUT_S` | Hard wall-clock cap per `agy` turn (seconds) | `3600` |
| `FDR_MAX_PR_THREADS` | PR threads per iteration | `5` |
| `FDR_ARCHIVE_ARTIFACTS` | Archive existing artifacts | `0` |
| `FDR_COMPACT_ARTIFACTS` | Auto-compact large artifacts | `1` |
| `FDR_PR_REVIEW` | Enable PR review mode | `0` |
| `FDR_PR_REVIEW_INTROSPECT` | Enable PR review mode with prompt introspection | `0` |
| `FDR_CONTEXTUAL_REVIEW` | Smart contextual review (report-only) | `0` |
| `FDR_FULL_CODEBASE_REVIEW` | Audit the entire codebase (report-only) | `0` |
| `FDR_PR_THREADS_INTROSPECT_ONLY` | Fetch unresolved PR threads, introspect, then exit | `0` |
| `FDR_IMPROVE_PROMPTS` | Update user templates from introspection data, then exit | `0` |
| `FDR_HOME` | Base directory for central state (`~/.fix-die-repeat/`) | (unset) |
| `FDR_DEBUG` | Enable debug mode | `0` |
| `FDR_NTFY_ENABLED` | Enable ntfy notifications | `1` |
| `FDR_NTFY_URL` | ntfy server URL | `http://localhost:2586` |

---

## Check Command Resolution

`fix-die-repeat` automatically finds your project's check command using this priority chain:

1. **CLI flag / env var** (`-c` / `FDR_CHECK_CMD`)
2. **Project config** (`~/.fix-die-repeat/repos/<slug>/config`)
3. **System config** (`~/.fix-die-repeat/config`)
4. **Auto-detect** from project files
5. **Interactive prompt**
6. **Error** (non-interactive with no config)

### Auto-Detection

| File Present | Detected Command |
|--------------|-----------------|
| `scripts/ci.sh` | `./scripts/ci.sh` |
| `Makefile` with `test` target | `make test` |
| `Makefile` with `check` target | `make check` |
| `package.json` with `scripts.test` | `npm test` |
| `Cargo.toml` | `cargo test` |
| `pyproject.toml` with `[tool.pytest]` | `uv run pytest` |
| `pyproject.toml` (no pytest) | `uv run python -m pytest` |
| `go.mod` | `go test ./...` |
| `build.gradle` / `build.gradle.kts` | `./gradlew test` |
| `pom.xml` | `mvn test` |
| `mix.exs` | `mix test` |
| `Gemfile` | `bundle exec rake test` |

### Config Files

**Project config** (`~/.fix-die-repeat/repos/<slug>/config`) — per-project, stored in a central directory so no files are written inside the repo. Override the base directory with `FDR_HOME` if you want state somewhere other than `~/.fix-die-repeat/`.
```toml
check_cmd = "uv run pytest"
```

**System config** (`~/.fix-die-repeat/config`) — shared across projects:
```toml
check_cmd = "pytest"
```

---

## How It Works

### The Time Loop

1. **Checkpoint**: Acquire `<fdr_dir>/runner.lock` and record the starting commit SHA (for rollback).
2. **Check Phase**: Run your check command.
   - Exit code **0** → proceed to review.
   - Non-zero → enter Fix Phase.
3. **Fix Phase**: Filter check output to error-relevant lines, invoke `agy` (`AgentRole.FIX`) to fix them, verify git invariants, and re-run checks.
4. **Review Phase**: Generate a git diff and invoke `agy` (`AgentRole.REVIEW`) in a read-only permission profile. If `agy` returns `ISSUES_FOUND`, `AgyRunner` writes `<fdr_dir>/review_current.md`, invokes `agy` (`AgentRole.FIX`) to resolve the findings, and loops back. If `NO_ISSUES` → exit `0`.

### Context Management

- Files under 200KB are referenced in the prompt for `agy` to inspect
- Larger changesets are listed with line counts so `agy` can selectively call `read_file`
- Historical artifacts are compacted when they exceed 150–200 lines

### Oscillation Detection

Check output hashes are tracked across iterations. If the same output repeats, the tool warns the agent to change tactics.

---

## PR Review Mode

Process GitHub PR review comments automatically:

```bash
fix-die-repeat --pr-review
```

This will:
- Fetch unresolved threads from the current branch's PR
- Have `agy` analyze and fix the issues
- Materialize `resolved_thread_ids` from `FIX_SCHEMA` to `.resolved_threads` and resolve only threads in the in-scope intersection
- Repeat until all threads are resolved

Requirements: you must be on a branch with an open PR, and `gh auth status` must succeed.

### Limiting PR Threads

```bash
fix-die-repeat --pr-review --max-pr-threads 3
```

### PR Review with Introspection

```bash
fix-die-repeat --pr-review-introspect
```

Implies `--pr-review` and additionally runs prompt introspection (`AgentRole.INTROSPECT`) on the processed threads, serializing schema-validated YAML entries to `~/.fix-die-repeat/introspection.yaml`.

### PR Threads Introspect-Only

```bash
fix-die-repeat --pr-threads-introspect-only
```

Fetches the PR's unresolved review threads, runs introspection on them, and exits without running checks or modifying code.

### Improve Prompts

```bash
fix-die-repeat --improve-prompts
```

Reads `~/.fix-die-repeat/introspection.yaml` and runs `agy` (`AgentRole.IMPROVE_PROMPTS`) to update the user-owned prompt templates under `~/.fix-die-repeat/templates/`. After the turn, `fix-die-repeat` validates that every template parses as valid Jinja2 (rolling back on syntax error) and marks pending introspection entries `reviewed`.

---

## Review-Only Modes

These report-only modes run under the read-only `review` permission profile and verify that the git working tree and `HEAD` remain unchanged.

### Contextual Review

```bash
fix-die-repeat --contextual-review
```

Smart scope detection:
- **Uncommitted:** reviews staged, unstaged, and untracked changes
- **Branch:** reviews the diff between the current branch and the default branch
- **Full:** falls back to full-codebase review when neither applies

### Full-Codebase Review

```bash
fix-die-repeat --full-codebase-review
```

Audits the entire codebase instead of a diff. Takes precedence over `--pr-review` if both are set.

---

## Debug Mode

```bash
fix-die-repeat --debug
```

Creates timestamped session logs in `~/.fix-die-repeat/repos/<slug>/`, stores raw NDJSON event streams under `<fdr_dir>/agy/`, and enables verbose console output.

---

## Notifications (Optional)

If you have an [ntfy](https://ntfy.sh/) server running, `fix-die-repeat` can notify you when runs complete.

```bash
# Disable notifications explicitly
export FDR_NTFY_ENABLED=0

# Point to your ntfy server (default: http://localhost:2586)
export FDR_NTFY_URL="http://your-server:2586"
```

---

## Exit Codes

| Code | Meaning |
|-------|----------|
| 0 | Escaped the loop — checks pass and review reports no issues |
| 1 | Max iterations exceeded, preflight failure, or unexpected error |
| 130 | Interrupted by user (`Ctrl+C`) |

On failure, the tool logs instructions for viewing or reverting changes:

```bash
git diff <start-sha>
git checkout <start-sha> -- .
```

---

## Troubleshooting

### `agy` not found or preflight failed

Ensure the Google Antigravity CLI (`agy`) is installed on `PATH` (or set `FDR_AGY_BIN=/path/to/agy`) and run `fix-die-repeat --preflight` to verify model catalog access and structured output.

### Another `fix-die-repeat` run is already active

`fix-die-repeat` acquires `<fdr_dir>/runner.lock` with PID and start-time identity verification. Wait for the active run to finish; if the previous process exited abnormally, the next run automatically reclaims the stale lock.

### Daily agent call budget reached

By default, `FDR_DAILY_MAX_CALLS=50` caps rolling 24-hour `agy` invocations across runs. Increase `FDR_DAILY_MAX_CALLS` or set `FDR_DAILY_MAX_CALLS=0` to disable the cap.
