# Antigravity Runner Architecture (`AgyRunner`)

`fix-die-repeat` drives the official Google Antigravity CLI (`agy`) in headless print mode (`agy -p`) to diagnose check failures, review diffs, introspect pull-request review threads, and refine user-owned prompt templates. The Python supervisor in `fix_die_repeat/agy_client.py`, `fix_die_repeat/agy_policy.py`, and `fix_die_repeat/health.py` owns process groups, permission profiles, JSON schema validation, quota-aware model ordering, and state-file materialization.

## Overview

Every agent turn executes as a fresh, ephemeral `agy -p` subprocess in its own process group (`start_new_session=True`):

```bash
agy --output-format stream-json \
    --model <model> \
    --disable-slash-commands \
    --no-chrome \
    --print-timeout 3600 \
    --settings <role_permissions_json> \
    --add-dir <allowed_fdr_dir> \
    --json-schema <role_schema_json> \
    -p "<prompt>"
```

Placing `-p "<prompt>"` at the end of `argv` ensures `-p` consumes the prompt string rather than a preceding flag.

## Terms of Service and Acceptable Use

`fix-die-repeat` is a local developer-loop supervisor around the official `agy` binary and enforces strict operational boundaries so usage remains compliant with the Google Antigravity Terms of Service:

- **Official binary and native authentication**: `AgyClient` invokes the installed `agy` binary directly and relies on the user's interactive `agy` login. It never extracts, inspects, or proxies OAuth tokens, cookies, or language-server credentials.
- **Single-user personal usage**: `fix-die-repeat` never rotates across multiple accounts to pool or evade rate limits.
- **Zero-token local checks**: Local check execution (`./scripts/ci.sh`, `pytest`), git diff inspection, and `gh` PR thread queries run in Python before invoking `agy`. If checks pass and no diff requires review, `agy` is not called.
- **Bounded execution**: Runs are bounded by `--max-iters` (default `10`), oscillation detection, a single-instance per-repo lock (`<fdr_dir>/runner.lock`), and a UTC calendar day call budget (`FDR_DAILY_MAX_CALLS`, default `50`).
- **Shared or multi-user CI Runners**: Do not run `fix-die-repeat` with a personal consumer login on shared, unattended, or multi-user CI runners. For unattended organization pipelines, use an API-key-backed runner (`GEMINI_API_KEY`, Vertex AI, or `ANTHROPIC_API_KEY`) or an enterprise plan that explicitly authorizes unattended CI usage.

## Role Permission Profiles and Post-Run Verification

Each agent role receives a dedicated `--settings` JSON object (`build_permission_settings` in `fix_die_repeat/agy_policy.py`) and post-run supervisor verification (`fix_die_repeat/agy_client.py`):

| Role | Allowed tools and paths | Denied tools and paths | Post-run supervisor check |
| --- | --- | --- | --- |
| `review` | Read-only repository inspection (`read_file`, `grep_search`, `find_by_name`, `list_dir`) + `--add-dir <fdr_dir>` | `write_file(/)`, `edit_file(/)`, `command(*)`, `mcp(*)` | `git status --porcelain -z` and `git rev-parse HEAD` must match the pre-turn snapshot. |
| `introspect` | Read-only repository and thread-data inspection | `write_file(/)`, `edit_file(/)`, `command(*)`, `mcp(*)` | `git status --porcelain -z` and `git rev-parse HEAD` must match the pre-turn snapshot. |
| `improve_prompts` | `read_file(<FDR_HOME>/templates/*)`, `read_file(<FDR_HOME>/introspection.yaml)`, `write_file(<FDR_HOME>/templates/*)`, `edit_file(<FDR_HOME>/templates/*)` | `write_file(<repo_root>/*)`, `edit_file(<repo_root>/*)`, `command(*)`, `mcp(*)` | Repository git snapshot must remain unchanged, and all user templates must parse as valid Jinja2; otherwise user templates roll back. |
| `fix` | Repository file edits and constrained verification commands (`read_file`, `write_file`, `edit_file`, `command(<verification_commands>*)`) + read-only access to `<fdr_dir>` | `write_file(<FDR_HOME>/*)`, `edit_file(<FDR_HOME>/*)`, `command(git commit*)`, `command(git push*)`, `command(git reset*)`, `command(git checkout*)`, `command(gh pr*)`, `command(*fix-die-repeat*)` | `git rev-parse HEAD`, staged index (`git diff --cached --name-only`), and `.agents/` must match the pre-turn snapshot. |

## Structured Output Contracts

All four roles pass `--json-schema <schema>` to `agy` and return validated JSON in the terminal `result.structured_output` event (`fix_die_repeat/agy_policy.py`):

- **`REVIEW_SCHEMA`**: Requires `verdict` (`"NO_ISSUES"` or `"ISSUES"`), `issues_markdown`, and `summary`. `AgyRunner` writes `"NO_ISSUES"` or the formatted Markdown report to `<fdr_dir>/review_current.md`.
- **`FIX_SCHEMA`**: Requires `outcome` (`"fixed"` or `"blocked"`), `summary`, `files_modified`, and `resolved_thread_ids`. `AgyRunner` writes `resolved_thread_ids` (one per line) to `<fdr_dir>/.resolved_threads`, or unlinks the file when no threads were resolved.
- **`INTROSPECT_SCHEMA`**: Requires `date`, `project`, `pr_number`, `pr_url`, `status` (`"pending"` or `"reviewed"`), and `threads` (each containing `id`, `title`, `category`, `outcome`, `summary`, `relevance`, and `lang_check_gap`). `AgyRunner` serializes the validated payload as YAML to `<fdr_dir>/.introspection_result.yaml`.
- **`IMPROVE_PROMPTS_SCHEMA`**: Requires `reviewed_thread_ids`, `templates_updated`, and `summary`. When Jinja2 validation passes and matching thread IDs are reported, `AgyRunner` updates matching `status: pending` entries in `<FDR_HOME>/introspection.yaml` to `status: reviewed`.

## Reset-Window-Aware Model Selection (`codexbar`)

`fix-die-repeat` selects models per role using a three-layer policy in `fix_die_repeat/agy_policy.py`:

1. **Per-role ordered chains**:
   - `FDR_MODELS_FIX`: `claude-opus-4-6-thinking,claude-sonnet-4-6-thinking,gemini-3.1-pro-high,gemini-3-flash,gpt-oss-120b-medium`
   - `FDR_MODELS_REVIEW`: `claude-opus-4-6-thinking,gemini-3.1-pro-high,claude-sonnet-4-6-thinking,gemini-3-flash,gpt-oss-120b-medium`
   - `FDR_MODELS_INTROSPECT`: `gemini-3.1-pro-low,gemini-3-flash,claude-sonnet-4-6,gpt-oss-120b-medium`
   - `FDR_MODELS_IMPROVE_PROMPTS`: `claude-opus-4-6-thinking,gemini-3.1-pro-high,claude-sonnet-4-6-thinking`
   - Passing `-m` / `FDR_MODEL` overrides all roles with a single-element chain.
2. **Persistent learned cooldowns (`<FDR_HOME>/quota_state.json`)**:
   - Capacity errors (`429`, `503`, `RESOURCE_EXHAUSTED`, `overloaded`, `quota exceeded`, and `rate limit`) mark the model on cooldown (`reset_at` when parsed from `agy` output, or `now + FDR_QUOTA_COOLDOWN_MINUTES` if configured, otherwise escalating through 5m, 15m, and 60m step delays) and immediately advance to the next candidate in the role's chain.
   - Daily call counts are recorded in the same locked state file to enforce `FDR_DAILY_MAX_CALLS` across each UTC calendar day.
3. **Live `codexbar` quota snapshot (`FDR_QUOTA_SOURCE=auto`)**:
   - When `codexbar` is on `PATH`, `fetch_codexbar_snapshot` runs `codexbar usage --provider antigravity --json --no-color` (3-second timeout).
   - Each model maps to its 5-hour (`windowMinutes == 300`) and weekly (`windowMinutes == 10080`) quota windows:
     - `claude-*` and `gpt-oss-*` check `antigravity-quota-summary-3p-5h` and `antigravity-quota-summary-3p-weekly`.
     - `gemini-*` checks `antigravity-quota-summary-gemini-5h`, `antigravity-quota-summary-gemini-weekly`, and `antigravity-quota-summary-gemini-argon-wtu-weekly`.
   - A model is eligible only when every mapped window has `usedPercent < FDR_QUOTA_MAX_USED_PCT` (default `95.0`) and the model is not in cooldown.
   - Eligible models are ordered by ascending `resetsAt` of their 5-hour window (`windowMinutes == 300`), preserving the role's configured chain order when two models share the same reset timestamp. This burns quota that resets soonest before touching fresher pools.

## Subprocess Supervision and Observability

`AgyClient.run_turn` reads the `stream-json` NDJSON output line by line using a background reader thread and queue:

- **Tool-aware stall detection**: If no event arrives for `FDR_AGENT_IDLE_TIMEOUT_S` (`120s`) while no tool is running, `AgyClient` reaps the process group (`SIGTERM` followed by `SIGKILL`) and reports a stall. While a `tool` step is `ACTIVE`, the idle timer yields to `FDR_AGENT_TOOL_TIMEOUT_S` (`1200s`). Total wall-clock runtime is bounded by `FDR_AGENT_HARD_TIMEOUT_S` (`3600s`).
- **Live tool logging**: Active `tool` steps log `agy: <tool_name> <tool_summary>` at `INFO` level.
- **Per-call transcripts**: Every invocation writes its raw NDJSON stream to `<fdr_dir>/agy/<timestamp>-<iter>-<role>-<model>.ndjson` and appends a human-readable summary to `<fdr_dir>/agy.log`.
