# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Added pure-Python Antigravity runner (`AgyRunner`, `AgyClient`, `agy_policy`, `health`) that supervises `agy -p --output-format stream-json` with `--json-schema` enforcement, per-role `--settings` permission profiles, process-group reaping, and tool-aware stall detection. See [`docs/agy-runner.md`](docs/agy-runner.md).
- Added reset-window-aware model ordering via `codexbar usage --provider antigravity --json --no-color`, ranking eligible models in each role's chain by earliest 5-hour quota reset (`resetsAt`) and persisting capacity cooldowns in `<FDR_HOME>/quota_state.json`.
- Added `--preflight` (`FDR_PREFLIGHT`) and cached version/model preflight checks in `<FDR_HOME>/agy_state.json`.
- Added single-instance per-repository lock (`<fdr_dir>/runner.lock`) with PID and start-time identity verification, plus rolling 24-hour call budgeting (`FDR_DAILY_MAX_CALLS`).
- Added post-run git working-tree, staged-index, and `HEAD` invariant checks for `review`, `introspect`, `improve_prompts`, and `fix` turns.
- Added `.agents/` workspace customizations (`AGENTS.md`, `rules/code-quality.md`, and skills `fdr-check`, `roadmap`, `technology-selection`, `decision-records`, and `writing-voice`).

### Changed

- Replaced `PiRunner` and all Pi CLI/SDK invocations with `AgyRunner` (`run_agent`, `run_agent_safe`, `run_agent_review`, `before_agent_call`, `run_preflight`).
- Moved state-file writes (`review_current.md`, `.resolved_threads`, `.introspection_result.yaml`, and `introspection.yaml` status updates) from agent file tools to Python supervisor materialization of validated `result.structured_output` payloads.

### Removed

- Removed the Node.js `priv/pi-bridge/` sidecar, `fix_die_repeat/pi_bridge.py`, `fix_die_repeat/bridge_install.py`, `docs/pi-bridge.md`, and all Node.js 20 runtime/CI dependencies.
- Removed legacy `--test-model` / `FDR_TEST_MODEL` and `FDR_PI_*` settings in favor of `--preflight` / `FDR_PREFLIGHT`, `FDR_AGENT_*`, `FDR_MODELS_*`, and `FDR_QUOTA_*`.
