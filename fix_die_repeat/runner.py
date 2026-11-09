"""Main runner for fix-die-repeat."""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Self

import yaml

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence
    from types import TracebackType

from fix_die_repeat.agy_client import (
    AgyClient,
    AgyTurnRequest,
    AgyTurnResult,
    capture_git_snapshot,
    verify_fix_git_state,
    verify_readonly_git_state,
)
from fix_die_repeat.agy_policy import (
    ROLE_SCHEMAS,
    AgentRole,
    AgyPolicyError,
    PermissionProfileContext,
    QuotaStateStore,
    RankedModelSelection,
    RankingContext,
    build_permission_settings,
    ensure_agy_ready,
    fetch_codexbar_snapshot,
    is_quota_error,
    rank_models_for_role,
    resolve_role_chain,
)
from fix_die_repeat.config import (
    Paths,
    Settings,
    get_user_templates_dir,
)
from fix_die_repeat.config import (
    get_introspection_file_path as _get_introspection_file_path,
)
from fix_die_repeat.health import RunnerLock
from fix_die_repeat.lang import filter_supported_languages, resolve_languages
from fix_die_repeat.messages import (
    git_checkout_instructions,
    git_diff_instructions,
    oscillation_warning,
)
from fix_die_repeat.prompts import render_prompt
from fix_die_repeat.runner_artifacts import ArtifactManager
from fix_die_repeat.runner_improve_prompts import ImprovePromptsManager
from fix_die_repeat.runner_introspection import (
    IntrospectionManager,
    IntrospectionPrInfo,
)
from fix_die_repeat.runner_pr import PrInfo as ReviewPrInfo
from fix_die_repeat.runner_pr import PrReviewManager
from fix_die_repeat.runner_review import ReviewManager
from fix_die_repeat.utils import (
    configure_logger,
    detect_large_files,
    format_duration,
    get_changed_files,
    get_file_line_count,
    get_file_size,
    get_git_revision_hash,
    play_completion_sound,
    run_command,
    send_ntfy_notification,
)


def _embed_attached_files(
    message: str,
    file_args: Iterable[str | Path],
    project_root: Path,
) -> str:
    """Prepend attached file contents to ``message`` for ``agy`` prompts.

    Missing or unreadable files surface as an inline note so the agent sees
    the failure instead of silently losing context.
    """
    chunks: list[str] = []
    for raw_ref in file_args:
        ref = str(raw_ref).removeprefix("@")
        path = Path(ref)
        if not path.is_absolute():
            path = (project_root / ref).resolve()
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as err:
            chunks.append(f"--- Attached (unavailable): {ref} — {err} ---\n")
            continue
        rel = _safe_relative(path, project_root)
        chunks.append(f"--- Attached: {rel} ---\n{content}\n")
    if not chunks:
        return message
    return "\n".join(chunks) + "\n" + message


def _safe_relative(path: Path, project_root: Path) -> str:
    try:
        return str(path.relative_to(project_root))
    except ValueError:
        return str(path)


class AgyRunner:
    """Runner that orchestrates the fix-die-repeat loop using Antigravity (``agy``)."""

    _runner_lock: RunnerLock | None = None
    _quota_store: QuotaStateStore | None = None
    _client: AgyClient | None = None
    _available_models: frozenset[str] = frozenset()

    def __init__(self, settings: Settings, paths: Paths) -> None:
        """Initialize the runner.

        Args:
            settings: Configuration settings
            paths: Path management

        """
        self.settings = settings
        self.paths = paths
        self.project_root = paths.project_root
        self.iteration = 0
        self.agent_invocation_count = 0
        self.script_start_time: float = 0.0
        self.start_sha = ""
        self.pr_review_no_progress_count = 0
        self.last_review_current_hash = ""
        self.last_git_state = ""
        self.consecutive_toolless_attempts = 0
        self._success_complete = False

        # Ensure central state dir exists before creating logger
        self.paths.ensure_fdr_dir()

        # Determine session log path
        if self.settings.debug:
            session_timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
            self.session_log = self.paths.fdr_dir / f"session_{session_timestamp}.log"
        else:
            self.session_log = self.paths.fdr_dir / "session.log"

        # Initialize logger
        self.logger = configure_logger(
            fdr_log=self.paths.fdr_log,
            session_log=self.session_log,
            debug=self.settings.debug,
        )

        # Initialize manager classes
        self.artifact_manager = ArtifactManager(self.settings, self.paths, self.logger)
        self.review_manager = ReviewManager(
            self.settings, self.paths, self.project_root, self.logger
        )
        self.pr_manager = PrReviewManager(self.settings, self.paths, self.project_root, self.logger)
        self.introspection_manager = IntrospectionManager(
            self.settings, self.paths, self.project_root, self.logger
        )
        self.improve_prompts_manager = ImprovePromptsManager(self.settings, self.logger)

        self._runner_lock: RunnerLock | None = None
        self._quota_store: QuotaStateStore | None = None
        self._client: AgyClient | None = None
        self._available_models: frozenset[str] = frozenset()

    def _get_artifact_manager(self) -> ArtifactManager | None:
        """Return the artifact manager when initialized."""
        manager = self.__dict__.get("artifact_manager")
        return manager if manager is not None else None

    def _get_review_manager(self) -> ReviewManager | None:
        """Return the review manager when initialized."""
        manager = self.__dict__.get("review_manager")
        return manager if manager is not None else None

    def _get_pr_manager(self) -> PrReviewManager | None:
        """Return the PR review manager when initialized."""
        manager = self.__dict__.get("pr_manager")
        return manager if manager is not None else None

    def _get_introspection_manager(self) -> IntrospectionManager | None:
        """Return the introspection manager when initialized."""
        manager = self.__dict__.get("introspection_manager")
        return manager if manager is not None else None

    def _get_improve_prompts_manager(self) -> ImprovePromptsManager | None:
        """Return the improve-prompts manager when initialized."""
        manager = self.__dict__.get("improve_prompts_manager")
        return manager if manager is not None else None

    def __enter__(self) -> Self:
        """Acquire the per-repository runner lock and initialize the ``agy`` client."""
        lock = RunnerLock(self.paths.runner_lock_file, logger=self.logger)
        lock.__enter__()
        self._runner_lock = lock
        self._quota_store = QuotaStateStore(self.paths.quota_state_file)
        self._client = AgyClient(
            executable=self.settings.agy_bin,
            logger=self.logger,
            runner_lock=lock,
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Release the per-repository runner lock."""
        if self._runner_lock is not None:
            try:
                self._runner_lock.__exit__(exc_type, exc, tb)
            finally:
                self._runner_lock = None
        self._client = None

    def _get_quota_store(self) -> QuotaStateStore:
        """Return or lazily construct the quota state store."""
        store = self.__dict__.get("_quota_store")
        if store is not None:
            return store
        quota_file = getattr(self.paths, "quota_state_file", None)
        if not isinstance(quota_file, Path):
            quota_file = self.paths.fdr_dir / "quota_state.json"
        store = QuotaStateStore(quota_file)
        self._quota_store = store
        return store

    def _get_client(self) -> AgyClient:
        """Return or lazily construct the ``agy`` client."""
        client = self.__dict__.get("_client")
        if client is not None:
            return client
        agy_bin = getattr(self.settings, "agy_bin", "agy")
        if not isinstance(agy_bin, str) or not agy_bin.strip():
            agy_bin = "agy"
        lock = self.__dict__.get("_runner_lock")
        client = AgyClient(
            executable=agy_bin,
            logger=self.logger,
            runner_lock=lock if isinstance(lock, RunnerLock) else None,
        )
        self._client = client
        return client

    def before_agent_call(self) -> None:
        """Add optional delay between sequential ``agy`` calls."""
        count = getattr(self, "agent_invocation_count", 0)
        delay = getattr(self.settings, "agent_sequential_delay_seconds", 0)
        if count > 0 and isinstance(delay, (int, float)) and delay > 0:
            time.sleep(delay)
        self.agent_invocation_count = count + 1

    def _role_chain(self, role: AgentRole) -> tuple[str, ...]:
        """Resolve the configured model chain for ``role``."""
        primary = getattr(self.settings, "model", None)
        primary_str = primary if isinstance(primary, str) else None
        attr_map = {
            AgentRole.FIX: "models_fix",
            AgentRole.REVIEW: "models_review",
            AgentRole.INTROSPECT: "models_introspect",
            AgentRole.IMPROVE_PROMPTS: "models_improve_prompts",
        }
        raw_override = getattr(self.settings, attr_map[role], None)
        chain_override = raw_override if isinstance(raw_override, str) else None
        return resolve_role_chain(
            role,
            primary_override=primary_str,
            role_chain_override=chain_override,
        )

    def _rank_models_for_role(self, role: AgentRole) -> RankedModelSelection:
        """Rank the configured chain for ``role`` using cooldowns and ``codexbar``."""
        chain = self._role_chain(role)
        quota_source = getattr(self.settings, "quota_source", "auto")
        source_str = quota_source if isinstance(quota_source, str) else "auto"
        snapshot = fetch_codexbar_snapshot(quota_source=source_str, logger=self.logger)
        max_used_raw = getattr(self.settings, "quota_max_used_pct", 95.0)
        max_used = float(max_used_raw) if isinstance(max_used_raw, (int, float)) else 95.0
        avail: frozenset[str] = getattr(self, "_available_models", frozenset())
        context = RankingContext(
            quota_state=self._get_quota_store(),
            snapshot=snapshot,
            available_models=avail if isinstance(avail, frozenset) else frozenset(),
            max_used_pct=max_used,
        )
        return rank_models_for_role(chain, context)

    def run_preflight(self) -> None:
        """Verify ``agy`` binary, model inventory, and optional ``--preflight`` check."""
        all_models: list[str] = []
        for role in AgentRole:
            for model in self._role_chain(role):
                if model not in all_models:
                    all_models.append(model)

        try:
            result = ensure_agy_ready(
                agy_bin=self.settings.agy_bin,
                state_file=self.paths.agy_state_file,
                required_models=all_models,
                force_smoke=bool(self.settings.preflight),
                quota_source=self.settings.quota_source,
            )
        except AgyPolicyError as err:
            self.logger.error("Antigravity preflight failed: %s", err)
            sys.exit(1)

        self._available_models = result.available_models

        if not self.settings.preflight:
            return

        self.logger.info(
            "Preflight OK: agy %s (%s) — %d models available.",
            result.version,
            result.executable,
            len(result.available_models),
        )
        for role in AgentRole:
            selection = self._rank_models_for_role(role)
            self.logger.info(
                "  role=%s ranked=%s skipped=%s",
                role.value,
                list(selection.eligible_models),
                selection.skipped_reasons,
            )
        if result.quota_snapshot and result.quota_snapshot.windows:
            for win in result.quota_snapshot.windows.values():
                self.logger.info(
                    "  quota %s (%s): %.1f%% used, resets %s",
                    win.window_id,
                    win.title,
                    win.used_percent,
                    win.resets_at_iso,
                )
        sys.exit(0)

    def run_agent_turn(
        self,
        role: AgentRole | str,
        prompt: str,
        *,
        attachments: Sequence[str | Path] = (),
        model: str | None = None,
    ) -> AgyTurnResult:
        """Execute a single ``agy`` turn with git verification and output materialization."""
        self.before_agent_call()
        role_enum = AgentRole(role)
        chosen_model = model or self._role_chain(role_enum)[0]
        quota_store = self._get_quota_store()

        max_calls_raw = getattr(self.settings, "daily_max_calls", 0)
        max_calls = max_calls_raw if isinstance(max_calls_raw, int) else 0
        try:
            quota_store.check_daily_cap(max_calls)
        except AgyPolicyError as err:
            self.logger.error("%s", err)
            cap_res = AgyTurnResult(
                returncode=1,
                response_text="",
                stderr=str(err),
                model=chosen_model,
            )
            self._log_agent_invocation(role_enum, chosen_model, cap_res)
            return cap_res

        full_prompt = _embed_attached_files(prompt, attachments, self.paths.project_root)
        fdr_home = get_user_templates_dir().parent
        perm_ctx = PermissionProfileContext(
            project_root=self.paths.project_root,
            fdr_dir=self.paths.fdr_dir,
            fdr_home=fdr_home,
        )
        settings_obj = build_permission_settings(role_enum, perm_ctx)
        add_dirs = (
            (fdr_home, fdr_home / "templates")
            if role_enum == AgentRole.IMPROVE_PROMPTS
            else (self.paths.fdr_dir,)
        )

        transcript_dir = getattr(self.paths, "agy_transcripts_dir", None)
        transcript_path: Path | None = None
        if isinstance(transcript_dir, Path):
            call_num = getattr(self, "agent_invocation_count", 1)
            iter_num = getattr(self, "iteration", 0)
            transcript_path = (
                transcript_dir / f"iter{iter_num}_call{call_num}_{role_enum.value}.jsonl"
            )

        request = AgyTurnRequest(
            role=role_enum,
            prompt=full_prompt,
            model=chosen_model,
            schema=ROLE_SCHEMAS[role_enum],
            settings=settings_obj,
            working_dir=self.paths.project_root,
            add_dirs=add_dirs,
            transcript_path=transcript_path,
            idle_timeout_s=self._timeout_setting("agent_idle_timeout_s", 120.0),
            tool_timeout_s=self._timeout_setting("agent_tool_timeout_s", 1200.0),
            hard_timeout_s=self._timeout_setting("agent_hard_timeout_s", 3600.0),
        )

        before_git = capture_git_snapshot(self.paths.project_root)
        quota_store.increment_daily_calls(chosen_model)
        result = self._get_client().run_turn(request)

        if result.returncode == 0 and not self._verify_turn_git_state(role_enum, before_git):
            result = replace(
                result,
                returncode=1,
                stderr="Post-run git invariant verification failed.",
            )

        if result.returncode == 0:
            result = self._materialize_role_output(role_enum, result)

        self._log_agent_invocation(role_enum, chosen_model, result)
        return result

    def _timeout_setting(self, attr_name: str, default: float) -> float:
        raw = getattr(self.settings, attr_name, default)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0:
            return float(raw)
        return default

    def _verify_turn_git_state(self, role: AgentRole, before_git: object) -> bool:
        """Verify git working-tree, index, and ``HEAD`` invariants after a turn."""
        if not hasattr(before_git, "is_git_repo"):
            return True
        if role in (AgentRole.REVIEW, AgentRole.INTROSPECT, AgentRole.IMPROVE_PROMPTS):
            return verify_readonly_git_state(
                self.paths.project_root,
                before_git,  # type: ignore[arg-type]
                self.logger,
            )
        return verify_fix_git_state(
            self.paths.project_root,
            before_git,  # type: ignore[arg-type]
            self.logger,
        )

    def _materialize_role_output(
        self,
        role: AgentRole,
        result: AgyTurnResult,
    ) -> AgyTurnResult:
        """Write supervisor-verified ``structured_output`` to ``<FDR_HOME>`` files."""
        structured = result.structured_output
        if not isinstance(structured, dict):
            return result

        if role == AgentRole.REVIEW:
            verdict = str(structured.get("verdict") or "").strip()
            issues_md = str(structured.get("issues_markdown") or "").strip()
            if verdict == "NO_ISSUES" or not issues_md or issues_md == "NO_ISSUES":
                self.paths.review_current_file.write_text("NO_ISSUES", encoding="utf-8")
            else:
                self.paths.review_current_file.write_text(f"{issues_md}\n", encoding="utf-8")
            return result

        if role == AgentRole.INTROSPECT:
            yaml_text = yaml.safe_dump(structured, default_flow_style=False, sort_keys=False)
            self.paths.introspection_result_file.write_text(yaml_text, encoding="utf-8")
            return result

        if role == AgentRole.FIX:
            resolved_ids = structured.get("resolved_thread_ids")
            if isinstance(resolved_ids, list):
                clean_ids = [
                    str(tid).strip()
                    for tid in resolved_ids
                    if isinstance(tid, str) and str(tid).strip()
                ]
                if clean_ids:
                    self.paths.pr_resolved_threads_file.write_text(
                        "\n".join(clean_ids) + "\n",
                        encoding="utf-8",
                    )
            return result

        if role == AgentRole.IMPROVE_PROMPTS:
            self._mark_introspection_entries_reviewed()
            summary = str(structured.get("summary") or "").strip()
            if summary and "<IMPROVE_PROMPTS_SUMMARY>" not in result.response_text:
                wrapped = f"<IMPROVE_PROMPTS_SUMMARY>\n{summary}\n</IMPROVE_PROMPTS_SUMMARY>\n"
                return replace(result, response_text=wrapped)

        return result

    @staticmethod
    def _mark_introspection_entries_reviewed() -> None:
        """Transition ``status: pending`` entries in ``introspection.yaml`` to ``reviewed``."""
        introspection_file = _get_introspection_file_path(create=False)
        if not introspection_file.exists():
            return
        try:
            content = introspection_file.read_text(encoding="utf-8")
            docs = list(yaml.safe_load_all(content))
        except (OSError, yaml.YAMLError):
            return
        changed = False
        updated_docs: list[dict[str, object]] = []
        for doc in docs:
            if isinstance(doc, dict):
                entry = doc
                if entry.get("status") == "pending":
                    entry = {**entry, "status": "reviewed"}
                    changed = True
                updated_docs.append(entry)
        if changed:
            rendered = "\n---\n".join(
                yaml.safe_dump(doc, default_flow_style=False, sort_keys=False).strip()
                for doc in updated_docs
            )
            introspection_file.write_text(f"{rendered}\n", encoding="utf-8")

    def run_agent(
        self,
        role: AgentRole | str,
        prompt: str,
        *,
        attachments: Sequence[str | Path] = (),
        model: str | None = None,
    ) -> tuple[int, str, str]:
        """Run a single ``agy`` turn and return ``(returncode, response_text, stderr)``."""
        result = self.run_agent_turn(
            role,
            prompt,
            attachments=attachments,
            model=model,
        )
        return (result.returncode, result.response_text, result.stderr)

    def run_agent_safe(
        self,
        role: AgentRole | str,
        prompt: str,
        *,
        attachments: Sequence[str | Path] = (),
    ) -> tuple[int, str, str]:
        """Run ``agy`` across the ranked model chain with quota fallback and single retry."""
        role_enum = AgentRole(role)
        selection = self._rank_models_for_role(role_enum)
        models_to_try = selection.eligible_models

        if not models_to_try and selection.earliest_reset_epoch is not None:
            max_wait_raw = getattr(self.settings, "quota_max_wait_s", 0)
            max_wait = max_wait_raw if isinstance(max_wait_raw, int) else 0
            wait_s = max(0.0, selection.earliest_reset_epoch - time.time())
            if 0 < wait_s <= max_wait:
                self.logger.info(
                    "All models for role '%s' in quota cooldown; sleeping %.0fs until reset...",
                    role_enum.value,
                    wait_s,
                )
                time.sleep(wait_s)
                selection = self._rank_models_for_role(role_enum)
                models_to_try = selection.eligible_models

        if not models_to_try:
            models_to_try = self._role_chain(role_enum)

        quota_store = self._get_quota_store()
        last_tuple: tuple[int, str, str] = (1, "", "No models configured")

        for candidate_model in models_to_try:
            returncode, stdout, stderr = self.run_agent(
                role_enum,
                prompt,
                attachments=attachments,
                model=candidate_model,
            )
            last_tuple = (returncode, stdout, stderr)
            if returncode == 0:
                quota_store.clear_cooldown(candidate_model)
                return last_tuple

            combined_err = f"{stderr}\n{self._read_agy_log_tail()}"
            if "429" in combined_err.lower() and "long context" in combined_err.lower():
                self.logger.info(
                    "Detected long context rate limit (429). Forcing emergency compaction...",
                )
                self.emergency_compact()
                self.logger.info("Emergency compaction complete. Retrying...")

            if is_quota_error(combined_err):
                quota_store.record_quota_cooldown(candidate_model, reason=combined_err)
                self.logger.warning(
                    "Model %s hit quota/capacity limit; advancing to next model in '%s' chain.",
                    candidate_model,
                    role_enum.value,
                )
                continue

            self.logger.info(
                "agy failed (exit %s) on %s. Retrying once...",
                returncode,
                candidate_model,
            )
            return self.run_agent(
                role_enum,
                prompt,
                attachments=attachments,
                model=candidate_model,
            )

        return last_tuple

    def _read_agy_log_tail(self) -> str:
        log_path = getattr(self.paths, "agy_log", None)
        if isinstance(log_path, Path) and log_path.exists():
            try:
                return log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
            except OSError:
                return ""
        return ""

    def _log_agent_invocation(
        self,
        role: AgentRole,
        model: str,
        result: AgyTurnResult,
    ) -> None:
        """Append a one-call audit trail to ``paths.agy_log`` and log failures."""
        log_path = getattr(self.paths, "agy_log", None)
        if isinstance(log_path, Path):
            with log_path.open("a", encoding="utf-8") as f:
                f.write(f"Role: {role.value} | Model: {model}\n")
                f.write(f"Exit code: {result.returncode}\n")
                if result.usage.total_tokens:
                    f.write(
                        f"Tokens: input={result.usage.input_tokens} "
                        f"output={result.usage.output_tokens} "
                        f"thinking={result.usage.thinking_tokens} "
                        f"total={result.usage.total_tokens}\n"
                    )
                if result.response_text:
                    f.write(f"STDOUT:\n{result.response_text}\n")
                if result.stderr:
                    f.write(f"STDERR:\n{result.stderr}\n")
                if result.structured_output is not None:
                    f.write(f"STRUCTURED_OUTPUT:\n{json.dumps(result.structured_output)}\n")
                f.write("\n")
        if result.returncode != 0:
            self.logger.error("agy exited with code %s (model=%s)", result.returncode, model)
            if isinstance(log_path, Path):
                self.logger.error("agy output logged to: %s", log_path)

    def run_checks(self) -> tuple[int, str]:
        """Run the check command.

        Returns:
            Tuple of (exit_code, output)

        """
        check_cmd = self.settings.check_cmd
        if check_cmd is None:
            msg = (
                "Settings.check_cmd is None. This likely indicates AgyRunner was "
                "constructed or used without running the CLI validation that sets "
                "a check command."
            )
            raise RuntimeError(msg)
        self.logger.info("[Step 1] Running %s (output: checks.log)...", check_cmd)

        start_time = time.time()
        returncode, stdout, stderr = run_command(
            check_cmd,
            cwd=self.paths.project_root,
        )

        # Write output to checks log
        output = f"{stdout}{stderr}"
        self.paths.checks_log.write_text(output)

        end_time = time.time()
        duration = int(end_time - start_time)
        self.logger.info("[Step 1] run_checks duration: %s", format_duration(duration))

        return (returncode, output)

    def setup_run(self) -> None:
        """Initialize run environment."""
        self.script_start_time = time.time()

        # Setup paths
        self.paths.ensure_fdr_dir()

        # Archive artifacts if requested
        if self.settings.archive_artifacts:
            archive_timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
            archive_dir = self.paths.fdr_dir / "archive" / archive_timestamp
            self.logger.info("Archiving existing artifacts to %s", archive_dir)
            archive_dir.mkdir(parents=True, exist_ok=True)
            for file_path in self.paths.fdr_dir.glob("*"):
                if file_path.is_file() and file_path.name != "runner.lock":
                    file_path.rename(archive_dir / file_path.name)

        # Initialize logs
        self.paths.agy_log.write_text("")
        self.session_log.write_text("")
        self.paths.checks_hash_file.write_text("")

        # Reset cumulative tracking files for introspection (fresh for each run)
        self.paths.cumulative_in_scope_threads_file.unlink(missing_ok=True)
        self.paths.cumulative_resolved_threads_file.unlink(missing_ok=True)
        self.paths.cumulative_pr_threads_content_file.unlink(missing_ok=True)
        self.logger.info("Logging full session output to: %s", self.session_log)

        # Record starting commit SHA
        returncode, stdout, _ = run_command(
            "git rev-parse HEAD",
            cwd=self.paths.project_root,
            check=False,
        )
        if returncode == 0:
            self.start_sha = stdout.strip()
            self.paths.start_sha_file.write_text(self.start_sha)
            self.logger.info("Git checkpoint: %s", self.start_sha)

        # Verify agy CLI and models (and exit early if --preflight was passed)
        self.run_preflight()

        # Compact large artifacts from previous runs
        self.check_and_compact_artifacts()

    def run_fix_attempt(
        self,
        fix_attempt: int,
        changed_files: list[str],
        context_mode: str,
        large_context_list: str,
        large_file_warning: str,
    ) -> int:
        """Run a single fix attempt.

        Args:
            fix_attempt: Current attempt number
            changed_files: List of changed files
            context_mode: Either "push" or "pull"
            large_context_list: Formatted list of large files for pull mode
            large_file_warning: Warning message for large files

        Returns:
            Exit code from agy command

        """
        # Check for oscillation (delegates to artifact_manager)
        artifact_manager = self._get_artifact_manager()
        if artifact_manager:
            oscillation_warn = artifact_manager.check_oscillation(self.iteration)
        else:
            oscillation_warn = self.check_oscillation()

        self.logger.info(
            "[Step 2A] Checks failed (fix attempt %s/%s). Running agy to fix errors...",
            fix_attempt,
            self.settings.max_iters,
        )

        # Filter checks log (delegates to artifact_manager)
        if artifact_manager:
            artifact_manager.filter_checks_log()
        else:
            self.filter_checks_log()

        # Build attachments list
        attachments: list[str] = [str(self.paths.checks_filtered_log)]

        # Add historical context
        if self.paths.review_file.exists():
            attachments.append(str(self.paths.review_file))
        if self.paths.build_history_file.exists():
            attachments.append(str(self.paths.build_history_file))

        # Attach changed files in push mode
        if context_mode == "push":
            attachments.extend(changed_files)

        # Detect languages for language-specific checks
        languages = resolve_languages(changed_files, self.settings.languages)
        languages = filter_supported_languages(languages)

        prompt = render_prompt(
            "fix_checks.j2",
            check_cmd=self.settings.check_cmd,
            oscillation_warning=oscillation_warn,
            include_review_history=self.paths.review_file.exists(),
            include_build_history=self.paths.build_history_file.exists(),
            context_mode=context_mode,
            large_context_list=large_context_list,
            large_file_warning=large_file_warning,
            languages=sorted(languages),
            **self.paths.template_context(),
        )

        self.logger.info("Running agy to fix errors (attempt %s)...", fix_attempt)
        agy_returncode, _, _ = self.run_agent_safe(
            AgentRole.FIX,
            prompt,
            attachments=attachments,
        )

        if agy_returncode != 0:
            self.logger.info("agy could not produce a fix on attempt %s.", fix_attempt)

        # Check if changes were made
        _git_returncode, stdout, _ = run_command(
            "git status --porcelain",
            cwd=self.paths.project_root,
            check=False,
        )
        if not stdout.strip():
            self.logger.error(
                "agy reported success but NO files were modified. This suggests "
                "edit operations failed or no fix was applied.",
            )
            with self.paths.build_history_file.open("a") as f:
                f.write(
                    f"## Iteration {self.iteration} fix attempt {fix_attempt}: "
                    "FAILED to apply fixes (no files changed)\n\n",
                )
        else:
            # Record history
            _git_returncode, stat_output, _ = run_command(
                "git diff --stat",
                cwd=self.paths.project_root,
                check=False,
            )
            with self.paths.build_history_file.open("a") as f:
                f.write(f"## Iteration {self.iteration} fix attempt {fix_attempt}\n")
                f.write(f"{stat_output}\n\n")

        return agy_returncode

    def prepare_fix_context(self) -> tuple[list[str], str, str, str]:
        """Prepare context for fix attempts.

        Returns:
            Tuple of (changed_files, context_mode, large_context_list, large_file_warning)

        """
        # Get changed files
        changed_files = get_changed_files(self.paths.project_root)

        if not changed_files:
            self.logger.info("No changed files found.")
        else:
            self.logger.info("Found %s changed file(s)", len(changed_files))

        # Check for large files
        large_file_warning = ""
        if changed_files:
            large_file_warning = detect_large_files(
                changed_files,
                self.paths.project_root,
                self.settings.large_file_lines,
            )

        # Calculate context size
        changed_size = 0
        for filepath in changed_files:
            changed_size += get_file_size(self.paths.project_root / filepath)

        context_mode = "push"
        large_context_list = ""

        if changed_size > self.settings.auto_attach_threshold:
            context_mode = "pull"
            self.logger.info(
                "Context size (%s bytes) exceeds threshold (%s). Switching to PULL mode.",
                changed_size,
                self.settings.auto_attach_threshold,
            )
            large_context_lines = [
                "The following files have changed but are too large to pre-load "
                f"automatically ({changed_size} bytes total). You MUST use the "
                "'read' tool to inspect the ones relevant to the error:",
            ]
            large_context_lines.extend(f"- {filepath}" for filepath in changed_files)
            large_context_list = "\n".join(large_context_lines)
        else:
            self.logger.info(
                "Context size (%s bytes) is within limits. Pushing file contents to prompt.",
                changed_size,
            )

        return changed_files, context_mode, large_context_list, large_file_warning

    def run_fix_loop(self) -> int:
        """Run the inner fix loop until checks pass or max attempts reached.

        Returns:
            Exit code (0 for success, non-zero for failure)

        """
        # Step 1: Run checks
        checks_status, _ = self.run_checks()

        # Step 2: Inner fix loop - if checks failed, keep fixing
        fix_attempt = 0
        while checks_status != 0:
            fix_attempt += 1

            if fix_attempt > self.settings.max_iters:
                self.logger.error(
                    "Maximum fix attempts (%s) exhausted. Could not resolve check failures.",
                    self.settings.max_iters,
                )
                if self.start_sha:
                    self.logger.error(git_diff_instructions(self.start_sha))
                    self.logger.error(git_checkout_instructions(self.start_sha))
                return 1

            # Prepare context
            changed_files, context_mode, large_context_list, large_file_warning = (
                self.prepare_fix_context()
            )

            # Run fix attempt
            self.run_fix_attempt(
                fix_attempt,
                changed_files,
                context_mode,
                large_context_list,
                large_file_warning,
            )

            # Re-run checks
            self.logger.info(
                "[Step 2A] Re-running %s after fix attempt %s...",
                self.settings.check_cmd,
                fix_attempt,
            )
            checks_status, _ = self.run_checks()

        self.logger.info("[Step 2B] Checks passed. Proceeding to review.")
        return 0

    def run_review_phase(self, _changed_files: list[str]) -> None:
        """Run the review phase.

        Args:
            _changed_files: List of changed files (unused, kept for compatibility)

        """
        # Step 3: Prepare review artifacts
        self.logger.info("[Step 3] Preparing review artifacts...")
        self.paths.review_current_file.unlink(missing_ok=True)

        # Step 3.5: Check PR threads if enabled
        pr_manager = self._get_pr_manager()
        if pr_manager:
            pr_manager.iteration = self.iteration

        if self.settings.pr_review and pr_manager:
            pr_manager.fetch_pr_threads()

        # Step 4: Collect files for review
        if (
            not self.paths.review_current_file.exists()
            or not self.paths.review_current_file.read_text()
        ):
            # Skip local review if we have PR threads to process
            self.review_manager.run_local_review(
                self.iteration,
                self.start_sha,
                self.run_agent_safe,
            )
        else:
            self.logger.info(
                "[Step 4] Using PR threads from %s for review.",
                self.paths.review_current_file,
            )
            self.logger.info("[Step 5] Skipping local file review generation.")

        # Step 6: Process review results
        self.process_review_results()

    def _run_standalone_mode(self) -> int | None:
        """Check for standalone modes that short-circuit the main loop.

        Returns:
            Exit code if a standalone mode was run, None to continue to the main loop.

        """
        if self.settings.pr_threads_introspect_only:
            return self.run_pr_threads_introspect_only()
        if self.settings.improve_prompts:
            return self.run_improve_prompts_once()
        if self.settings.contextual_review:
            return self.run_contextual_review_once()
        if self.settings.full_codebase_review:
            return self.run_full_codebase_review_once()
        return None

    def run(self) -> int:
        """Run the main fix-die-repeat loop.

        Returns:
            Exit code (0 for success, non-zero for failure)

        """
        self.setup_run()

        # Standalone modes short-circuit the fix-die-repeat loop.
        standalone_result = self._run_standalone_mode()
        if standalone_result is not None:
            return standalone_result

        while True:
            self.iteration += 1
            self.logger.info(
                "===== Iteration %s of %s =====",
                self.iteration,
                self.settings.max_iters,
            )

            self.artifact_manager.check_and_compact_artifacts()

            if self.iteration > self.settings.max_iters:
                return self._handle_max_iterations_exceeded()

            exit_code = self.run_fix_loop()
            if exit_code != 0:
                return self._handle_fix_loop_failure(exit_code)

            changed_files = get_changed_files(self.paths.project_root)
            self.run_review_phase(changed_files)

            if self._success_complete:
                self._run_post_run_introspection()
                return self.complete_success()

        return 0

    def run_full_codebase_review_once(self) -> int:
        """Run a single report-only full-codebase review pass.

        This mode never attempts fixes, so looping is pointless — any second
        pass would re-discover the same issues. Runs agy exactly once over the
        whole tracked codebase, overwrites review.md with the findings, and
        prints them to the terminal so the user can see them directly.

        Returns:
            Exit code 0 on completion (success regardless of findings).

        """
        self.iteration = 1
        self.logger.info("Full-codebase review (single pass, report-only).")
        self.artifact_manager.check_and_compact_artifacts()

        self.paths.review_current_file.unlink(missing_ok=True)
        self.review_manager.run_full_codebase_review(self.iteration, self.run_agent_safe)

        if not self.paths.review_current_file.exists():
            self.logger.error(
                "%s was not created by agy. Treating as no issues.",
                self.paths.review_current_file,
            )
            self.paths.review_current_file.write_text("NO_ISSUES")
            self.paths.review_file.write_text("NO_ISSUES\n")

        review_content = (
            self.paths.review_file.read_text() if self.paths.review_file.exists() else ""
        )

        if self.review_manager.has_no_review_issues(review_content):
            self.logger.info("[Step 6B] No critical issues found.")
        else:
            critical_count = review_content.count("[CRITICAL]")
            nit_count = review_content.count("[NIT]")
            self.logger.info(
                "[Step 6B] Full-codebase review complete: %s [CRITICAL], %s [NIT] finding(s).",
                critical_count,
                nit_count,
            )
            # Write raw findings to stdout (no log timestamp) for easy copy/paste.
            sys.stdout.write("===== Full-codebase review findings =====\n")
            sys.stdout.write(review_content)
            if not review_content.endswith("\n"):
                sys.stdout.write("\n")
            sys.stdout.write("==========================================\n")
            sys.stdout.flush()
            self.logger.info("Findings saved to %s", self.paths.review_file)

        self._success_complete = True
        self.paths.review_current_file.unlink(missing_ok=True)
        self.paths.start_sha_file.unlink(missing_ok=True)

        duration = int(time.time() - self.script_start_time)
        play_completion_sound()
        if self.settings.ntfy_enabled:
            send_ntfy_notification(
                exit_code=0,
                duration_str=format_duration(duration),
                repo_name=self.paths.project_root.name,
                ntfy_url=self.settings.ntfy_url,
                logger=self.logger,
            )
        return 0

    def run_contextual_review_once(self) -> int:
        """Run a single report-only contextual review pass.

        Auto-scopes to uncommitted changes, branch diff, or full codebase.
        Never attempts fixes — runs agy exactly once over the scoped files,
        overwrites review.md with the findings, and prints them to the
        terminal.

        Returns:
            Exit code 0 on completion (success regardless of findings).

        """
        self.iteration = 1
        self.logger.info("Contextual review (single pass, report-only).")
        self.artifact_manager.check_and_compact_artifacts()

        self.paths.review_current_file.unlink(missing_ok=True)
        self.review_manager.run_contextual_review(self.iteration, self.run_agent_safe)

        if not self.paths.review_current_file.exists():
            self.logger.error(
                "%s was not created by agy. Treating as no issues.",
                self.paths.review_current_file,
            )
            self.paths.review_current_file.write_text("NO_ISSUES")
            self.paths.review_file.write_text("NO_ISSUES\n")

        review_content = (
            self.paths.review_file.read_text() if self.paths.review_file.exists() else ""
        )

        if self.review_manager.has_no_review_issues(review_content):
            self.logger.info("[Step 6B] No critical issues found.")
        else:
            critical_count = review_content.count("[CRITICAL]")
            nit_count = review_content.count("[NIT]")
            self.logger.info(
                "[Step 6B] Contextual review complete: %s [CRITICAL], %s [NIT] finding(s).",
                critical_count,
                nit_count,
            )
            # Write raw findings to stdout (no log timestamp) for easy copy/paste.
            sys.stdout.write("===== Contextual review findings =====\n")
            sys.stdout.write(review_content)
            if not review_content.endswith("\n"):
                sys.stdout.write("\n")
            sys.stdout.write("=======================================\n")
            sys.stdout.flush()
            self.logger.info("Findings saved to %s", self.paths.review_file)

        self._success_complete = True
        self.paths.review_current_file.unlink(missing_ok=True)
        self.paths.start_sha_file.unlink(missing_ok=True)

        duration = int(time.time() - self.script_start_time)
        play_completion_sound()
        if self.settings.ntfy_enabled:
            send_ntfy_notification(
                exit_code=0,
                duration_str=format_duration(duration),
                repo_name=self.paths.project_root.name,
                ntfy_url=self.settings.ntfy_url,
                logger=self.logger,
            )
        return 0

    def run_pr_threads_introspect_only(self) -> int:
        """Fetch unresolved PR threads and run introspection without attempting fixes.

        Returns:
            Exit code (0 on success, non-zero on failure)

        """
        self.logger.info("PR threads introspect-only mode: no fix loop, no review.")
        pr_manager = self._get_pr_manager()
        introspection_manager = self._get_introspection_manager()
        if pr_manager is None or introspection_manager is None:
            self.logger.error("Required managers not initialized; cannot run introspect-only mode.")
            return 1

        fetched = self._fetch_unresolved_pr_threads(pr_manager)
        if fetched is None:
            return 1

        pr_info, unresolved = fetched
        if not unresolved:
            self.logger.info("No unresolved PR threads found. Nothing to introspect.")
            return 0

        self.logger.info(
            "Found %s unresolved thread(s). Writing introspection inputs...",
            len(unresolved),
        )
        self._write_introspect_only_inputs(pr_manager, pr_info, unresolved)

        # Clear any stale diff from a prior run so introspect-only doesn't
        # leak unrelated changes into the introspection YAML.
        try:
            if self.paths.diff_file.exists():
                self.paths.diff_file.unlink()
        except OSError:
            self.logger.exception(
                "Failed to clear stale diff file before introspection-only run: %s",
                self.paths.diff_file,
            )
            return 1

        try:
            introspection_manager.run_introspection(
                self.iteration,
                "",
                self.run_agent_safe,
                introspect_only=True,
            )
        except Exception:
            self.logger.exception("Introspection failed")
            return 1

        return 0

    def run_improve_prompts_once(self) -> int:
        """Run the prompt-improvement mode once and exit.

        Reads ``<FDR_HOME>/introspection.yaml``, seeds user template copies
        under ``<FDR_HOME>/templates/`` if missing, and asks agy to update
        them based on pending introspection entries. Does not run checks,
        review, or the fix loop.

        Returns:
            Exit code (0 on success, non-zero on failure).

        """
        self.logger.info("Improve-prompts mode: no fix loop, no review.")
        manager = self._get_improve_prompts_manager()
        if manager is None:
            self.logger.error(
                "Improve-prompts manager not initialized; cannot run --improve-prompts.",
            )
            return 1
        return manager.run_improve_prompts(self.run_agent_safe)

    def _fetch_unresolved_pr_threads(
        self, pr_manager: PrReviewManager
    ) -> tuple[ReviewPrInfo, list[dict]] | None:
        """Fetch unresolved PR threads for the current branch.

        Returns:
            Tuple of (pr_info, unresolved_threads) on success, None on failure.

        """
        branch = pr_manager.get_branch_name()
        if not branch:
            self.logger.error("Not on a git branch. Cannot fetch PR threads.")
            return None

        returncode, _, _ = run_command("gh auth status", cwd=self.paths.project_root)
        if returncode != 0:
            self.logger.error("GitHub CLI not authenticated. Cannot fetch PR threads.")
            return None

        pr_info = pr_manager.get_pr_info(branch)
        if not pr_info:
            self.logger.error("No open PR found for branch %s.", branch)
            return None

        threads = pr_manager.fetch_pr_threads_gql(
            pr_info.repo_owner, pr_info.repo_name, pr_info.number
        )
        if threads is None:
            self.logger.error("Failed to fetch PR threads.")
            return None

        unresolved = [t for t in threads if not t.get("isResolved")]
        return pr_info, unresolved

    def _write_introspect_only_inputs(
        self,
        pr_manager: PrReviewManager,
        pr_info: ReviewPrInfo,
        unresolved: list[dict],
    ) -> None:
        """Persist the cumulative files that IntrospectionManager reads."""
        formatted = pr_manager.format_pr_threads(unresolved, pr_info.number, pr_info.url)
        self.paths.cumulative_pr_threads_content_file.write_text(formatted)

        thread_ids = [str(t.get("id") or "") for t in unresolved if t.get("id")]
        self.paths.cumulative_in_scope_threads_file.write_text("\n".join(thread_ids) + "\n")
        # Ensure resolved file is empty so all threads register as not-attempted.
        self.paths.cumulative_resolved_threads_file.write_text("")

    def emergency_compact(self) -> None:
        """Force emergency truncation of artifacts."""
        artifact_manager = self._get_artifact_manager()
        if artifact_manager:
            artifact_manager.emergency_compact()
        else:
            for f in [self.paths.review_file, self.paths.build_history_file]:
                if f.exists():
                    lines = f.read_text().splitlines()[-100:]
                    f.write_text("\n".join(lines))

    def check_compaction_needed(self) -> tuple[bool, bool]:
        """Check if artifacts need compaction.

        Returns:
            Tuple of (needs_emergency, needs_compact)

        """
        artifact_manager = self._get_artifact_manager()
        if artifact_manager:
            return artifact_manager.check_compaction_needed()
        needs_compact = False
        needs_emergency = False

        for f in [self.paths.review_file, self.paths.build_history_file]:
            if f.exists():
                line_count = get_file_line_count(f)
                if line_count > self.settings.emergency_threshold_lines:
                    needs_emergency = True
                    break
                if line_count > self.settings.compact_threshold_lines:
                    needs_compact = True

        return needs_emergency, needs_compact

    def perform_emergency_compaction(self) -> None:
        """Perform emergency compaction (truncate to 100 lines)."""
        artifact_manager = self._get_artifact_manager()
        if artifact_manager:
            artifact_manager.perform_emergency_compaction()
        else:
            self.logger.info(
                "Emergency: artifacts exceed %s lines. Truncating to last 100 lines...",
                self.settings.emergency_threshold_lines,
            )
            for f in [self.paths.review_file, self.paths.build_history_file]:
                if f.exists():
                    lines = f.read_text().splitlines()[-100:]
                    f.write_text("\n".join(lines))

    def perform_regular_compaction(self) -> None:
        """Perform regular compaction (truncate to 50 lines)."""
        artifact_manager = self._get_artifact_manager()
        if artifact_manager:
            artifact_manager.perform_regular_compaction()
        else:
            self.logger.info(
                "Artifacts exceed %s lines. Compacting artifacts...",
                self.settings.compact_threshold_lines,
            )

            for f in [self.paths.review_file, self.paths.build_history_file]:
                if f.exists():
                    before = get_file_line_count(f)
                    lines = f.read_text().splitlines()[-50:]
                    f.write_text("\n".join(lines))
                    after = get_file_line_count(f)
                    self.logger.info("Compacted %s from %s to %s lines", f.name, before, after)

    def check_oscillation(self) -> str | None:
        """Check for oscillation by tracking check output hashes.

        Returns:
            Warning message if oscillation detected, None otherwise

        """
        artifact_manager = self._get_artifact_manager()
        if artifact_manager:
            return artifact_manager.check_oscillation(self.iteration)
        current_hash = get_git_revision_hash(self.paths.checks_log)

        if self.paths.checks_hash_file.exists():
            hashes = self.paths.checks_hash_file.read_text().strip().split("\n")
            for entry in reversed(hashes):
                if entry.startswith(f"{current_hash}:"):
                    prev_iter = int(entry.split(":")[-1])
                    self.logger.info(
                        "Detected oscillation: iteration %s matches iteration %s",
                        self.iteration,
                        prev_iter,
                    )
                    warning = oscillation_warning(prev_iter)
                    with self.paths.checks_hash_file.open("a") as f:
                        f.write(f"{current_hash}:{self.iteration}\n")
                    return warning

        with self.paths.checks_hash_file.open("a") as f:
            f.write(f"{current_hash}:{self.iteration}\n")

        return None

    def check_and_compact_artifacts(self) -> bool:
        """Check and compact large persistent artifacts.

        Returns:
            True if compaction was performed, False otherwise

        """
        artifact_manager = self._get_artifact_manager()
        if artifact_manager:
            return artifact_manager.check_and_compact_artifacts()
        if not self.settings.compact_artifacts:
            return False

        needs_emergency, needs_compact = self.check_compaction_needed()

        if needs_emergency:
            self.perform_emergency_compaction()
            return True

        if needs_compact:
            self.perform_regular_compaction()
            return True

        return False

    def filter_checks_log(self) -> None:
        """Filter checks.log to extract the most useful failure information."""
        artifact_manager = self._get_artifact_manager()
        if artifact_manager:
            artifact_manager.filter_checks_log()
        else:
            if not self.paths.checks_log.exists():
                return

            max_lines = 300
            context_lines = 3
            tail_lines = 80

            total_lines = get_file_line_count(self.paths.checks_log)

            if total_lines <= max_lines:
                self.paths.checks_filtered_log.write_text(self.paths.checks_log.read_text())
                return

            error_pattern = re.compile(
                r"(error[:\[ ]|ERROR[:\[ ]|fatal|FATAL|FAILED|panic|exception|"
                r"undefined reference|cannot find|no such file|not found|segfault|"
                r"abort|compilation failed|build failed|assert)",
                re.IGNORECASE,
            )

            lines = self.paths.checks_log.read_text().splitlines()
            filtered_lines = [
                f"=== FILTERED CHECK OUTPUT (full log: {self.paths.checks_log}, "
                f"{total_lines} lines) ===",
                "",
                "--- Error/failure lines with context ---",
            ]

            seen_indices = set()
            for i, line in enumerate(lines):
                if error_pattern.search(line):
                    start = max(0, i - context_lines)
                    end = min(len(lines), i + context_lines + 1)
                    for j in range(start, end):
                        if j not in seen_indices:
                            filtered_lines.append(lines[j])
                            seen_indices.add(j)

            filtered_lines.extend(["", f"--- Last {tail_lines} lines ---"])
            filtered_lines.extend(lines[-tail_lines:])

            if len(filtered_lines) > max_lines:
                filtered_lines = filtered_lines[: max_lines - tail_lines] + lines[-tail_lines:]

            self.paths.checks_filtered_log.write_text("\n".join(filtered_lines))

    def generate_diff(self) -> str:
        """Generate git diff for review.

        Returns:
            Diff content as string

        """
        review_manager = self._get_review_manager()
        if review_manager:
            return review_manager.generate_diff(self.start_sha)
        diff_content = ""
        if self.start_sha:
            _returncode, diff_output, _ = run_command(
                f"git diff {self.start_sha}",
                cwd=self.paths.project_root,
            )
            diff_content += diff_output
        else:
            _returncode, diff_output, _ = run_command(
                "git diff HEAD",
                cwd=self.paths.project_root,
            )
            diff_content += diff_output
        return diff_content

    def add_untracked_files_diff(self, diff_content: str) -> str:
        """Add pseudo-diff for untracked files.

        Args:
            diff_content: Existing diff content

        Returns:
            Diff content with untracked files added

        """
        review_manager = self._get_review_manager()
        if review_manager:
            return review_manager.add_untracked_files_diff(diff_content)
        returncode, new_files, _ = run_command(
            "git ls-files --others --exclude-standard",
            cwd=self.paths.project_root,
            check=False,
        )
        if returncode != 0:
            return diff_content

        for new_file in new_files.strip().split("\n"):
            if not new_file or not (self.paths.project_root / new_file).is_file():
                continue
            diff_content += self.create_pseudo_diff(new_file)

        return diff_content

    def create_pseudo_diff(self, filepath: str) -> str:
        """Create pseudo-diff for a single untracked file.

        Args:
            filepath: Path to the untracked file

        Returns:
            Pseudo-diff content

        """
        review_manager = self._get_review_manager()
        if review_manager:
            return review_manager.create_pseudo_diff(filepath)
        file_path = self.paths.project_root / filepath
        pseudo_diff = (
            f"diff --git a/{filepath} b/{filepath}\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            f"+++ b/{filepath}\n"
        )

        try:
            returncode, file_type, _ = run_command(["file", str(file_path)], check=False)
            if returncode == 0 and "text" in file_type.lower():
                with file_path.open(encoding="utf-8", errors="replace") as file_handle:
                    for line in file_handle:
                        pseudo_diff += f"+{line}"
            else:
                pseudo_diff += f"Binary file {filepath} differs\n"
        except OSError:
            pass

        return pseudo_diff + "\n"

    def has_no_review_issues(self, review_content: str) -> bool:
        """Check if review content indicates no issues.

        Args:
            review_content: Review file content

        Returns:
            True if no issues found

        """
        review_manager = self._get_review_manager()
        if review_manager:
            return review_manager.has_no_review_issues(review_content)
        stripped = review_content.strip()

        if stripped == "NO_ISSUES":
            return True

        if not stripped:
            self.logger.warning(
                "review_current.md is empty — expected 'NO_ISSUES' marker. "
                "Treating as no issues, but this may indicate a problem.",
            )
            return True

        if "no critical issues found" in stripped.lower():
            content_lines = [
                line for line in review_content.splitlines() if line and not line.startswith("#")
            ]
            return len(content_lines) <= 1

        return False

    def fetch_pr_threads_gql(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
    ) -> list[dict] | None:
        """Fetch PR threads via GraphQL.

        Args:
            repo_owner: Repository owner
            repo_name: Repository name
            pr_number: PR number

        Returns:
            Thread data list or None on failure

        """
        pr_manager = self._get_pr_manager()
        if pr_manager:
            return pr_manager.fetch_pr_threads_gql(repo_owner, repo_name, pr_number)

        query = """
            query($owner: String!, $repo: String!, $number: Int!) {
                repository(owner: $owner, name: $repo) {
                    pullRequest(number: $number) {
                        reviewThreads(first: 100) {
                            nodes {
                                isResolved
                                id
                                path
                                line
                                comments(last: 10) {
                                    nodes {
                                        author { login }
                                        body
                                        createdAt
                                    }
                                }
                            }
                        }
                    }
                }
            }
        """

        query_single_line = " ".join(line.strip() for line in query.splitlines() if line.strip())
        cmd = [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={query_single_line}",
            "-F",
            f"owner={repo_owner}",
            "-F",
            f"repo={repo_name}",
            "-F",
            f"number={pr_number}",
        ]

        returncode, gql_result, _ = run_command(cmd, cwd=self.paths.project_root)
        if returncode != 0:
            return None

        try:
            data = json.loads(gql_result)
            return data["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
        except (json.JSONDecodeError, KeyError):
            self.logger.exception("Failed to parse PR thread data")
            return None

    def fetch_pr_threads(self) -> None:
        """Fetch PR threads for PR review mode."""
        pr_manager = self._get_pr_manager()
        if pr_manager:
            pr_manager.fetch_pr_threads()
            return

        self._fetch_pr_threads_fallback()

    def _fetch_pr_threads_fallback(self) -> None:
        """Fetch PR threads when the PR manager is unavailable (tests)."""
        self.logger.info("[Step 3.5] Checking for unresolved PR threads...")

        branch = self.get_branch_name()
        if not branch:
            self.logger.error("Not on a git branch. Skipping PR review.")
            return

        self.logger.info("Fetching PR info for branch: %s", branch)

        if not self._ensure_gh_authenticated():
            return

        pr_info = self.get_pr_info(branch)
        if not pr_info:
            self.logger.info(
                "No open PR found for %s or error fetching PR. Skipping PR review.",
                branch,
            )
            return

        normalized_info = self._normalize_pr_info(pr_info)
        self.logger.info(
            "Found PR #%s (%s). Checking for cached threads...",
            normalized_info.number,
            normalized_info.url,
        )

        cache_key = self._build_pr_cache_key(normalized_info)
        if self.check_pr_threads_cache(cache_key):
            return

        self.logger.info("Cache miss or invalid. Fetching fresh threads...")

        unresolved_threads = self._fetch_unresolved_threads(
            normalized_info.repo_owner,
            normalized_info.repo_name,
            normalized_info.number,
        )
        if unresolved_threads is None:
            return

        limited_threads = self._limit_unresolved_threads(unresolved_threads)
        self._write_unresolved_threads(
            limited_threads,
            normalized_info.number,
            normalized_info.url,
        )

    def _ensure_gh_authenticated(self) -> bool:
        """Ensure the GitHub CLI is authenticated before PR review."""
        returncode, _, _ = run_command("gh auth status", cwd=self.paths.project_root)
        if returncode != 0:
            self.logger.error("GitHub CLI not authenticated. Skipping PR review.")
            return False
        return True

    def _normalize_pr_info(self, pr_info: dict | ReviewPrInfo) -> ReviewPrInfo:
        """Normalize PR info into a ReviewPrInfo instance."""
        if isinstance(pr_info, ReviewPrInfo):
            return pr_info

        return ReviewPrInfo(
            number=pr_info["number"],
            url=pr_info["url"],
            repo_owner=pr_info["repo_owner"],
            repo_name=pr_info["repo_name"],
        )

    @staticmethod
    def _build_pr_cache_key(pr_info: ReviewPrInfo) -> str:
        """Build cache key for PR threads."""
        return f"{pr_info.repo_owner}/{pr_info.repo_name}/{pr_info.number}"

    def _fetch_unresolved_threads(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
    ) -> list[dict] | None:
        """Fetch unresolved PR threads from GitHub."""
        threads = self.fetch_pr_threads_gql(repo_owner, repo_name, pr_number)
        if threads is None:
            return None

        return [thread for thread in threads if not thread.get("isResolved", True)]

    def _limit_unresolved_threads(self, unresolved_threads: list[dict]) -> list[dict]:
        """Limit unresolved PR threads based on settings.max_pr_threads."""
        total_unresolved = len(unresolved_threads)
        max_threads = self.settings.max_pr_threads

        if not isinstance(max_threads, int) or max_threads <= 0 or total_unresolved <= max_threads:
            return unresolved_threads

        ordered_threads = sorted(
            unresolved_threads,
            key=self._thread_sort_key,
            reverse=True,
        )
        skipped_threads = ordered_threads[max_threads:]
        skipped_ids = [str(thread.get("id")) for thread in skipped_threads if thread.get("id")]

        self.logger.warning(
            "Skipping %s unresolved thread(s) due to max_pr_threads limit (%s); skipped IDs: %s",
            len(skipped_ids),
            max_threads,
            skipped_ids,
        )
        self.logger.info(
            "Limited to %s of %s threads",
            max_threads,
            total_unresolved,
        )

        return ordered_threads[:max_threads]

    @staticmethod
    def _thread_sort_key(thread: dict) -> tuple[str, str]:
        """Sort threads by latest comment timestamp and ID."""
        comments = thread.get("comments", {})
        nodes = comments.get("nodes", [])
        timestamps = [
            str(node.get("createdAt") or "")
            for node in nodes
            if isinstance(node, dict) and node.get("createdAt")
        ]
        latest_timestamp = max(timestamps) if timestamps else ""
        return (latest_timestamp, str(thread.get("id") or ""))

    def _write_unresolved_threads(
        self,
        unresolved_threads: list[dict],
        pr_number: int,
        pr_url: str,
    ) -> None:
        """Write unresolved thread data to review files."""
        if unresolved_threads:
            content = self.format_pr_threads(unresolved_threads, pr_number, pr_url)
            self.paths.review_current_file.write_text(content)
            self.logger.info(
                "Found %s unresolved threads. Added to review queue.",
                len(unresolved_threads),
            )

            thread_ids = [str(thread["id"]) for thread in unresolved_threads if thread.get("id")]
            if thread_ids:
                self.paths.pr_thread_ids_file.write_text("\n".join(thread_ids) + "\n")
            return

        self.logger.info("No unresolved threads found.")
        self.paths.review_current_file.write_text("")
        self.paths.pr_thread_ids_file.unlink(missing_ok=True)

    def get_branch_name(self) -> str | None:
        """Get the current git branch name.

        Returns:
            Branch name or None if not on a branch

        """
        pr_manager = self._get_pr_manager()
        if pr_manager:
            return pr_manager.get_branch_name()
        returncode, branch, _ = run_command(
            "git branch --show-current",
            cwd=self.paths.project_root,
        )
        if returncode != 0 or not branch.strip():
            return None
        return branch.strip()

    def get_pr_info(self, branch: str) -> dict | None:
        """Get PR information for a branch.

        Args:
            branch: Branch name

        Returns:
            PR data dict or None if not found

        """
        pr_manager = self._get_pr_manager()
        if pr_manager:
            pr_info = pr_manager.get_pr_info(branch)
            if pr_info:
                return {
                    "number": pr_info.number,
                    "url": pr_info.url,
                    "repo_owner": pr_info.repo_owner,
                    "repo_name": pr_info.repo_name,
                }
            return None
        returncode, pr_json, _ = run_command(
            f"gh pr view {branch} --json number,url,headRepository,headRepositoryOwner",
            cwd=self.paths.project_root,
        )
        if returncode != 0:
            return None

        pr_data = json.loads(pr_json)
        return {
            "number": pr_data.get("number"),
            "url": pr_data.get("url"),
            "repo_owner": pr_data["headRepositoryOwner"]["login"],
            "repo_name": pr_data["headRepository"]["name"],
        }

    def check_pr_threads_cache(self, cache_key: str) -> bool:
        """Check if cached PR threads are valid and use them.

        Args:
            cache_key: Cache key to validate

        Returns:
            True if cache was used, False otherwise

        """
        pr_manager = self._get_pr_manager()
        if pr_manager:
            return pr_manager.check_pr_threads_cache(cache_key)
        has_valid_cache = (
            self.paths.pr_threads_cache.exists()
            and self.paths.pr_threads_hash_file.exists()
            and self.paths.pr_threads_hash_file.read_text() == cache_key
        )
        if not has_valid_cache:
            return False

        if not self.paths.pr_thread_ids_file.exists():
            self.logger.warning(
                "PR thread cache exists but in-scope ID file is missing. Refetching threads.",
            )
            return False

        in_scope_ids = [
            thread_id.strip()
            for thread_id in self.paths.pr_thread_ids_file.read_text().splitlines()
            if thread_id.strip()
        ]
        if not in_scope_ids:
            self.logger.warning(
                "PR thread cache exists but in-scope ID file is empty. Refetching threads.",
            )
            return False

        self.logger.info("Using cached PR threads (unchanged)...")
        cached_content = self.paths.pr_threads_cache.read_text()
        self.paths.review_current_file.write_text(cached_content)
        self.logger.info("Found %s unresolved threads from cache.", len(in_scope_ids))
        return True

    def _run_post_run_introspection(self) -> None:
        """Run post-run introspection if enabled.

        Introspection is a non-blocking operation. Errors are logged but do not
        affect the main flow.

        """
        if self.settings.pr_review_introspect:
            introspection_manager = self._get_introspection_manager()
            if not introspection_manager:
                return
            try:
                introspection_manager.run_introspection(
                    self.iteration,
                    self.start_sha,
                    self.run_agent_safe,
                )
            except Exception:
                self.logger.exception(
                    "Introspection step failed (non-blocking)",
                )

    def _handle_max_iterations_exceeded(self) -> int:
        """Handle the case when maximum iterations is exceeded.

        Returns:
            Exit code 1 (failure)

        """
        self.logger.error(
            "Maximum iterations (%s) exceeded. Could not resolve all issues.",
            self.settings.max_iters,
        )
        if self.start_sha:
            self.logger.error(git_diff_instructions(self.start_sha))
            self.logger.error(git_checkout_instructions(self.start_sha))
        self._run_post_run_introspection()
        return 1

    def _handle_fix_loop_failure(self, exit_code: int) -> int:
        """Handle fix loop failure.

        Args:
            exit_code: Exit code from the fix loop

        Returns:
            The provided exit code

        """
        self._run_post_run_introspection()
        return exit_code

    def complete_success(self) -> int:
        """Complete the run successfully.

        Returns:
            Exit code 0 for success

        """
        timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self.paths.review_file.open("a") as f:
            f.write(f"### Iteration {self.iteration} - Resolution ({timestamp})\n")
            f.write("- No issues found.\n\n")

        self.paths.review_current_file.unlink(missing_ok=True)
        self.paths.start_sha_file.unlink(missing_ok=True)

        end_time = int(time.time())
        duration = int(end_time - self.script_start_time)
        self.logger.info(
            "[Step 7] Done! All checks passed and no review issues found. "
            "%s retained. Session log: %s",
            self.paths.review_file,
            self.session_log,
        )

        play_completion_sound()

        if self.settings.ntfy_enabled:
            send_ntfy_notification(
                exit_code=0,
                duration_str=format_duration(duration),
                repo_name=self.paths.project_root.name,
                ntfy_url=self.settings.ntfy_url,
                logger=self.logger,
            )

        return 0

    def run_review_fix_attempt(self, fix_attempt: int, max_fix_attempts: int) -> bool:
        """Run a single review fix attempt.

        Args:
            fix_attempt: Current attempt number
            max_fix_attempts: Maximum attempts

        Returns:
            True if fix was successful, False to retry

        """
        self.logger.info(
            "[Step 6A] agy fix attempt %s of %s...",
            fix_attempt,
            max_fix_attempts,
        )

        attachments: list[str] = [str(self.paths.review_current_file)]

        # Attach recent history
        if self.paths.review_file.exists():
            lines = self.paths.review_file.read_text().splitlines()[-50:]
            self.paths.review_recent_file.write_text("\n".join(lines))
            attachments.append(str(self.paths.review_recent_file))

        # Build fix prompt
        fix_prompt = render_prompt(
            "resolve_review_issues.j2",
            **self.paths.template_context(),
        )

        returncode, _, _ = self.run_agent_safe(
            AgentRole.FIX,
            fix_prompt,
            attachments=attachments,
        )

        if returncode != 0:
            self.logger.info("agy fix failed on attempt %s.", fix_attempt)

        # Record resolution attempt
        timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self.paths.review_file.open("a") as f:
            f.write(
                f"### Iteration {self.iteration} - Resolution ({timestamp})\n"
                f"- Fixes applied for {self.paths.review_current_file} "
                f"(attempt {fix_attempt}); verification pending.\n\n",
            )

        # Check if changes were made
        returncode, stdout, _ = run_command(
            "git status --porcelain",
            cwd=self.paths.project_root,
            check=False,
        )
        if not stdout.strip():
            self.consecutive_toolless_attempts += 1
            self.logger.error(
                "agy reported success on attempt %s but NO files were modified. "
                "This suggests edit operations failed or no changes were applied.",
                fix_attempt,
            )
            with self.paths.build_history_file.open("a") as f:
                f.write(
                    f"## Iteration {self.iteration} fix attempt {fix_attempt}: "
                    "FAILED to apply fixes (no files changed)\n\n",
                )
            return False

        self.consecutive_toolless_attempts = 0
        # Record history
        returncode, stat_output, _ = run_command(
            "git diff --stat",
            cwd=self.paths.project_root,
            check=False,
        )
        with self.paths.build_history_file.open("a") as f:
            f.write(f"## Iteration {self.iteration} Review Fixes (attempt {fix_attempt})\n")
            f.write(f"{stat_output}\n\n")

        # Check if PR threads were resolved
        if self.settings.pr_review:
            self.resolve_pr_threads()

        return True

    def process_review_results(self) -> None:
        """Process review results and fix issues if needed."""
        self.logger.info("[Step 6] Processing review results...")

        # CRITICAL: Check for prohibited ruff ignores (NEVER-IGNORE policy)
        self.review_manager.check_prohibited_ruff_ignores()

        if not self.paths.review_current_file.exists():
            self.logger.error(
                "%s was not created by agy. This is unexpected.",
                self.paths.review_current_file,
            )
            sys.exit(1)

        review_content = self.paths.review_current_file.read_text()

        if self.review_manager.has_no_review_issues(review_content):
            self.logger.info(
                "[Step 6B] No issues found in %s.",
                self.paths.review_current_file,
            )
            self._success_complete = True
            return

        # Issues found - fix them
        self.logger.info(
            "[Step 6A] Issues found in %s. Running agy to fix them...",
            self.paths.review_current_file,
        )

        fix_attempt = 1
        max_fix_attempts = 3

        while fix_attempt <= max_fix_attempts:
            success = self.run_review_fix_attempt(fix_attempt, max_fix_attempts)
            if success:
                break
            if fix_attempt < max_fix_attempts:
                self.logger.info("Retrying fix (attempt %s)...", fix_attempt + 1)
                fix_attempt += 1
            else:
                fix_attempt += 1

    def format_pr_threads(self, threads: list, pr_number: int, pr_url: str) -> str:
        """Format PR threads for display.

        Args:
            threads: List of thread data
            pr_number: PR number
            pr_url: PR URL

        Returns:
            Formatted thread content

        """
        pr_manager = self._get_pr_manager()
        if pr_manager:
            return pr_manager.format_pr_threads(threads, pr_number, pr_url)
        threads_output = [
            f"# PR Review Threads for #{pr_number} ({pr_url})",
            f"Unresolved threads: {len(threads)}",
            "",
        ]
        for i, thread in enumerate(threads, 1):
            threads_output.append(f"--- Thread #{i} ---")
            threads_output.append(f"ID: {thread['id']}")
            threads_output.append(f"File: {thread.get('path', 'N/A')}")
            if thread.get("line"):
                threads_output.append(f"Line: {thread['line']}")
            for comment in thread["comments"]["nodes"]:
                author = comment["author"]["login"] if comment.get("author") else "unknown"
                body = str(comment.get("body") or "")
                comment_lines = body.splitlines() or [""]
                threads_output.append(f"[{author}]: {comment_lines[0]}")
                threads_output.extend(f"    {line}" for line in comment_lines[1:])
            threads_output.append("")
        return "\n".join(threads_output)

    def resolve_pr_threads(self) -> None:
        """Resolve PR threads that were fixed."""
        pr_manager = self._get_pr_manager()
        if pr_manager:
            pr_manager.resolve_pr_threads()
            return

        if not self.paths.pr_resolved_threads_file.exists():
            self.logger.info("No threads were reported as resolved. Continuing to next iteration.")
            return

        resolved_ids = self.paths.pr_resolved_threads_file.read_text().strip().split("\n")
        resolved_ids = [thread_id for thread_id in resolved_ids if thread_id]

        if not resolved_ids:
            self.logger.info("No threads were reported as resolved. Continuing to next iteration.")
            return

        self.logger.info("Model reported %s resolved thread(s).", len(resolved_ids))

        in_scope_ids = []
        if self.paths.pr_thread_ids_file.exists():
            in_scope_ids = self.paths.pr_thread_ids_file.read_text().strip().split("\n")

        safe_resolved_ids = set(resolved_ids) & set(in_scope_ids)

        if len(safe_resolved_ids) < len(resolved_ids):
            unsafe_ids = set(resolved_ids) - set(in_scope_ids)
            self.logger.warning(
                "Skipping %s thread(s) that were not in scope: %s",
                len(unsafe_ids),
                list(unsafe_ids),
            )

        if safe_resolved_ids:
            mutation = """
                mutation($threadId: ID!) {
                    resolveReviewThread(input: {threadId: $threadId}) {
                        thread {
                            id
                        }
                    }
                }
            """
            mutation_single_line = " ".join(
                line.strip() for line in mutation.splitlines() if line.strip()
            )

            resolved_count = 0
            for thread_id in safe_resolved_ids:
                self.logger.info(
                    "Resolving PR thread %s via gh GraphQL",
                    thread_id,
                )
                returncode, _gql_result, _ = run_command(
                    [
                        "gh",
                        "api",
                        "graphql",
                        "-f",
                        f"query={mutation_single_line}",
                        "-F",
                        f"threadId={thread_id}",
                    ],
                    cwd=self.paths.project_root,
                )

                if returncode == 0:
                    resolved_count += 1
                else:
                    self.logger.warning(
                        "Failed to resolve thread %s (exit code: %s)",
                        thread_id,
                        returncode,
                    )

            self.logger.info(
                "Successfully resolved %s of %s thread(s).",
                resolved_count,
                len(safe_resolved_ids),
            )
            self.paths.pr_threads_hash_file.unlink(missing_ok=True)
            self.fetch_pr_threads()

            if (
                not self.paths.review_current_file.exists()
                or not self.paths.review_current_file.read_text().strip()
            ):
                self.logger.info(
                    "All PR threads have been resolved! "
                    "Running final local diff review to catch any remaining issues.",
                )
        else:
            self.logger.info(
                "No in-scope threads were reported as resolved. Continuing to next iteration.",
            )

        self.paths.pr_resolved_threads_file.unlink(missing_ok=True)

    def append_review_entry(self, iteration: int) -> None:
        """Append review entry to review file.

        Args:
            iteration: Current iteration number

        """
        review_manager = self._get_review_manager()
        if review_manager:
            review_manager.append_review_entry(iteration)
        else:
            timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            with self.paths.review_file.open("a") as f:
                f.write(f"## Iteration {iteration} - Review ({timestamp})\n")
                if (
                    self.paths.review_current_file.exists()
                    and self.paths.review_current_file.read_text()
                ):
                    f.write(self.paths.review_current_file.read_text())
                else:
                    f.write("_No issues found._\n")
                f.write("\n")

    def build_review_prompt(self, diff_size: int, attachments: list[Path]) -> str:
        """Build review prompt based on diff size.

        Args:
            diff_size: Size of the diff in bytes
            attachments: List to append diff file path to if within threshold

        Returns:
            Review prompt prefix

        """
        review_manager = self._get_review_manager()
        if review_manager:
            return review_manager.build_review_prompt(diff_size, attachments)
        if diff_size > self.settings.auto_attach_threshold:
            return (
                f"The changes are too large to attach automatically ({diff_size} bytes). "
                f"You MUST use the 'read_file' tool to inspect '{self.paths.diff_file}'.\n"
            )
        if diff_size == 0:
            return (
                "No diff is available for this review (the diff could not be computed or "
                "is empty), and no changed-file list is attached in this review mode. "
                "Do not assume any changes are available to inspect. Return verdict "
                "NO_ISSUES.\n"
            )
        attachments.append(self.paths.diff_file)
        return (
            f"I have attached '{self.paths.diff_file}' which contains the changes "
            "made in this session.\n"
        )

    def run_agent_review(
        self,
        diff_size: int,
        run_agent_callback: Callable[..., tuple[int, str, str]],
    ) -> None:
        """Run agy to review changes.

        Args:
            diff_size: Size of the diff in bytes
            run_agent_callback: Function to run agy (from AgyRunner)

        """
        review_manager = self._get_review_manager()
        if review_manager:
            review_manager.run_agent_review(diff_size, run_agent_callback)
            return

        self.logger.info("[Step 5] Running agy to review files...")

        attachments: list[Path] = []
        review_prompt_prefix = self.build_review_prompt(diff_size, attachments)

        if self.paths.review_file.exists():
            attachments.append(self.paths.review_file)

        changed_files = get_changed_files(self.paths.project_root)
        languages = resolve_languages(changed_files, self.settings.languages)
        languages = filter_supported_languages(languages)

        review_prompt = render_prompt(
            "local_review.j2",
            review_prompt_prefix=review_prompt_prefix,
            languages=sorted(languages),
            **self.paths.template_context(),
        )

        returncode, _, _ = run_agent_callback(
            AgentRole.REVIEW,
            review_prompt,
            attachments=attachments,
        )

        if returncode != 0:
            self.logger.info("agy review failed. Treating as no issues found.")
            self.paths.review_current_file.write_text("NO_ISSUES")

    def collect_introspection_data(
        self,
        iteration: int,
        start_sha: str,
        pr_info: IntrospectionPrInfo | None = None,
    ) -> None:
        """Collect input data for introspection analysis.

        Args:
            iteration: Current iteration number
            start_sha: Starting git commit SHA
            pr_info: PR information (or None to fetch from get_pr_info)

        """
        introspection_manager = self._get_introspection_manager()
        if not introspection_manager:
            return

        if pr_info is None:
            branch = self.get_branch_name()
            if branch is None:
                return
            pr_data = self.get_pr_info(branch)
            if pr_data is None:
                return
            pr_info = IntrospectionPrInfo(
                number=pr_data["number"],
                url=pr_data["url"],
            )

        introspection_manager.collect_introspection_data(iteration, start_sha, pr_info)

    def run_introspection(
        self,
        _iteration: int | None = None,
        _start_sha: str | None = None,
        _run_agent_callback: Callable[..., tuple[int, str, str]] | None = None,
    ) -> None:
        """Run prompt introspection analysis.

        Args:
            _iteration: Current iteration number (unused)
            _start_sha: Starting git commit SHA (unused)
            _run_agent_callback: Function to run agy (unused)

        """
        self._run_post_run_introspection()


def get_introspection_file_path() -> Path:
    """Return the global introspection YAML path."""
    return _get_introspection_file_path()
