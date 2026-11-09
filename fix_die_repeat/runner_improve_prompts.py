"""Prompt-improvement management for fix-die-repeat runner.

Drives the ``--improve-prompts`` mode: reads accumulated PR review
introspection data, copies (on-demand) the shipped prompt templates into
the user dotfolder, and asks Antigravity (``agy``) to edit the user copies
so future runs pick up the improvements. Nothing inside the installed
package is ever mutated.
"""

import difflib
import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import yaml
from jinja2 import Environment, TemplateSyntaxError, select_autoescape

from fix_die_repeat.agy_policy import AgentRole
from fix_die_repeat.config import (
    Settings,
    get_introspection_archive_file_path,
    get_introspection_file_path,
    get_user_templates_dir,
)
from fix_die_repeat.prompts import clear_prompt_cache, render_prompt
from fix_die_repeat.runner_introspection import _FileLock

_SUMMARY_OPEN = "<IMPROVE_PROMPTS_SUMMARY>"
_SUMMARY_CLOSE = "</IMPROVE_PROMPTS_SUMMARY>"

# Templates exposed to agy for editing. Kept intentionally narrow: the
# four top-level language-agnostic prompts that steer every run, plus the
# shared ``partials/`` fragments those prompts compose. Per-language
# partials under ``lang_checks/`` are deliberately excluded.
EDITABLE_TEMPLATES = (
    "fix_checks.j2",
    "local_review.j2",
    "resolve_review_issues.j2",
    "pr_threads_header.j2",
    "partials/_review_readonly_task.j2",
    "partials/_issue_classification.j2",
    "partials/_critical_checklist.j2",
    "partials/_language_checks.j2",
    "partials/_review_reporting_rules.j2",
    "partials/_review_output_contract.j2",
)

# Non-zero exit code returned when agy succeeds but leaves the introspection,
# archive, or user templates unparseable, so the user notices the rollback.
_ROLLBACK_EXIT_CODE = 1


@dataclass(frozen=True)
class _RunAgentResult:
    """Outcome of ``_run_agent_with_rollback``, with counts captured under the file lock."""

    returncode: int
    pending_before: int
    pending_after: int
    stdout: str


@dataclass(frozen=True)
class _RollbackContext:
    """Files and snapshots guarded during an ``--improve-prompts`` agent turn."""

    introspection_file: Path
    archive_file: Path
    template_paths: dict[str, Path]
    template_snapshot: dict[str, str]


class ImprovePromptsManager:
    """Orchestrates the ``--improve-prompts`` one-shot mode."""

    def __init__(
        self,
        settings: Settings,
        logger: logging.Logger,
    ) -> None:
        """Initialize the manager.

        Args:
            settings: Configuration settings.
            logger: Logger instance for output.

        """
        self.settings = settings
        self.logger = logger

    @classmethod
    def has_pending_work(cls, logger: logging.Logger) -> bool:
        """Return True if ``<FDR_HOME>/introspection.yaml`` has pending entries.

        Exposed as a classmethod so callers (e.g. the CLI) can decide whether
        --improve-prompts needs to materialize per-repo state before invoking
        the manager — matching the ``create=False`` no-op contract.
        """
        introspection_file = get_introspection_file_path(create=False)
        return cls._file_has_pending_entries(introspection_file, logger)

    def run_improve_prompts(
        self,
        run_agent_callback: Callable[..., tuple[int, str, str]],
    ) -> int:
        """Run the prompt-improvement mode.

        Args:
            run_agent_callback: Function to invoke agy (from ``AgyRunner``).

        Returns:
            Exit code (0 on success, non-zero on failure).

        """
        # Avoid creating <FDR_HOME>/ for no-op runs; the file's presence implies
        # the parent already exists, and we exit early below when it doesn't.
        introspection_file = get_introspection_file_path(create=False)

        if not self._has_pending_entries(introspection_file):
            self.logger.info(
                "[ImprovePrompts] No pending introspection entries at %s; nothing to do.",
                introspection_file,
            )
            return 0

        templates_dir = get_user_templates_dir()
        templates_dir.mkdir(parents=True, exist_ok=True)
        template_paths = self._ensure_user_templates(templates_dir)
        template_snapshot = self._snapshot_templates(template_paths)

        archive_file = get_introspection_archive_file_path()
        prompt = render_prompt(
            "improve_prompts.j2",
            introspection_file_path=str(introspection_file),
            archive_file_path=str(archive_file),
            templates_dir=str(templates_dir),
            template_paths={name: str(path) for name, path in template_paths.items()},
        )

        self.logger.info(
            "[ImprovePrompts] Asking agy to review introspection data and update user templates...",
        )
        rollback_ctx = _RollbackContext(
            introspection_file=introspection_file,
            archive_file=archive_file,
            template_paths=template_paths,
            template_snapshot=template_snapshot,
        )
        result = self._run_agent_with_rollback(
            context=rollback_ctx,
            run_agent_callback=run_agent_callback,
            prompt=prompt,
        )

        # Refresh the Jinja environment so the edits land on the next render,
        # even if the same process goes on to use templates (tests do this).
        clear_prompt_cache()

        if result.returncode != 0:
            self.logger.warning(
                "[ImprovePrompts] agy exited with code %s; "
                "user templates may be partially updated.",
                result.returncode,
            )
            return result.returncode

        changes = self._compute_template_changes(template_paths, template_snapshot)
        entries_consumed = max(0, result.pending_before - result.pending_after)
        self._emit_summary(changes, entries_consumed, result.stdout)

        self.logger.info(
            "[ImprovePrompts] Done. User templates at %s now take precedence.",
            templates_dir,
        )
        return 0

    def _run_agent_with_rollback(
        self,
        *,
        context: _RollbackContext,
        run_agent_callback: Callable[..., tuple[int, str, str]],
        prompt: str,
    ) -> _RunAgentResult:
        """Hold an exclusive lock on the introspection file for the agy call.

        Acquires ``_FileLock`` on ``introspection.yaml`` so a concurrent FDR
        process appending introspection entries serializes with us. Takes a
        backup of both the main and archive files before invoking agy, and
        rolls back if agy leaves either file or any edited template
        unparseable. Returns the before/after pending counts captured inside
        the lock so the summary reflects this invocation's exact state.
        """
        introspection_file = context.introspection_file
        archive_file = context.archive_file
        try:
            lock_handle = introspection_file.open("r+")
        except OSError:
            self.logger.exception(
                "[ImprovePrompts] Failed to open introspection file %s for locking",
                introspection_file,
            )
            return _RunAgentResult(_ROLLBACK_EXIT_CODE, 0, 0, "")

        with lock_handle, _FileLock(lock_handle):
            pending_before = self._count_pending_entries(introspection_file)
            main_backup = self._create_backup(introspection_file)
            archive_existed = archive_file.exists()
            archive_backup = self._create_backup(archive_file) if archive_existed else None

            try:
                returncode, stdout, _stderr = run_agent_callback(
                    AgentRole.IMPROVE_PROMPTS,
                    prompt,
                )
                if self._needs_rollback(context, archive_existed=archive_existed):
                    self._perform_rollback(
                        context,
                        main_backup=main_backup,
                        archive_backup=archive_backup,
                        archive_existed=archive_existed,
                    )
                    if returncode == 0:
                        returncode = _ROLLBACK_EXIT_CODE
                    pending_after = pending_before
                else:
                    pending_after = self._count_pending_entries(introspection_file)
                return _RunAgentResult(returncode, pending_before, pending_after, stdout)
            finally:
                if main_backup is not None and main_backup.exists():
                    main_backup.unlink()
                if archive_backup is not None and archive_backup.exists():
                    archive_backup.unlink()

    def _needs_rollback(
        self,
        context: _RollbackContext,
        *,
        archive_existed: bool,
    ) -> bool:
        """Return True if YAML state files or Jinja2 templates were left invalid."""
        introspection_exists = context.introspection_file.exists()
        archive_exists = context.archive_file.exists()
        introspection_invalid = not introspection_exists or not self._is_valid_yaml(
            context.introspection_file
        )
        archive_invalid = (archive_existed and not archive_exists) or (
            archive_exists and not self._is_valid_yaml(context.archive_file)
        )
        templates_invalid = not self._are_templates_valid_jinja(context.template_paths)
        return introspection_invalid or archive_invalid or templates_invalid

    def _perform_rollback(
        self,
        context: _RollbackContext,
        *,
        main_backup: Path | None,
        archive_backup: Path | None,
        archive_existed: bool,
    ) -> None:
        """Restore YAML files and user templates after a failed validation check."""
        self.logger.error(
            "[ImprovePrompts] agy left %s, %s, or templates invalid; rolling back.",
            context.introspection_file,
            context.archive_file,
        )
        self._restore_backup(context.introspection_file, main_backup)
        self._restore_archive(
            context.archive_file,
            archive_backup,
            archive_existed_before=archive_existed,
        )
        self._restore_templates(context.template_paths, context.template_snapshot)

    def _are_templates_valid_jinja(self, template_paths: dict[str, Path]) -> bool:
        """Verify that every editable user template compiles as valid Jinja2 syntax."""
        env = Environment(autoescape=select_autoescape(enabled_extensions=("html", "htm")))
        for name, path in template_paths.items():
            if not path.exists():
                self.logger.warning("[ImprovePrompts] Template %s was deleted", path)
                return False
            try:
                source = path.read_text(encoding="utf-8")
                env.parse(source, name=name)
            except (OSError, TemplateSyntaxError) as exc:
                self.logger.warning(
                    "[ImprovePrompts] Template %s failed Jinja2 syntax validation: %s",
                    name,
                    exc,
                )
                return False
        return True

    @staticmethod
    def _restore_templates(
        template_paths: dict[str, Path],
        snapshot: dict[str, str],
    ) -> None:
        """Restore user templates from their pre-turn snapshot."""
        for name, path in template_paths.items():
            if name in snapshot:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(snapshot[name], encoding="utf-8")

    @staticmethod
    def _create_backup(source: Path) -> Path | None:
        """Copy ``source`` to a sibling ``.bak`` path and return it, or None if absent."""
        if not source.exists():
            return None
        backup = source.with_suffix(source.suffix + ".bak")
        shutil.copy2(source, backup)
        return backup

    def _is_valid_yaml(self, path: Path) -> bool:
        """Return True if ``path`` is absent, empty, or parses as YAML documents."""
        if not path.exists():
            return True
        try:
            content = path.read_text()
        except OSError as exc:
            self.logger.warning("[ImprovePrompts] Failed to read %s: %s", path, exc)
            return False
        if not content.strip():
            return True
        try:
            list(yaml.safe_load_all(content))
        except yaml.YAMLError as exc:
            self.logger.warning("[ImprovePrompts] %s is not valid YAML: %s", path, exc)
            return False
        return True

    @staticmethod
    def _restore_backup(target: Path, backup: Path | None) -> None:
        """Overwrite ``target`` with ``backup`` if ``backup`` exists."""
        if backup is not None and backup.exists():
            shutil.copy2(backup, target)

    def _restore_archive(
        self,
        archive_file: Path,
        archive_backup: Path | None,
        *,
        archive_existed_before: bool,
    ) -> None:
        """Restore the archive file to its pre-agy state.

        If the archive existed before agy ran, restore from its backup. If it
        didn't exist before, delete any file agy created so we don't leave a
        half-formed archive behind.
        """
        if archive_existed_before:
            self._restore_backup(archive_file, archive_backup)
        elif archive_file.exists():
            archive_file.unlink()

    def _has_pending_entries(self, introspection_file: Path) -> bool:
        """Return True if ``introspection_file`` has at least one ``status: pending`` document."""
        return self._file_has_pending_entries(introspection_file, self.logger)

    @staticmethod
    def _file_has_pending_entries(introspection_file: Path, logger: logging.Logger) -> bool:
        """Shared YAML-parsing core for ``has_pending_work`` / ``_has_pending_entries``."""
        if not introspection_file.exists():
            return False
        try:
            content = introspection_file.read_text()
        except OSError as exc:
            logger.warning(
                "[ImprovePrompts] Failed to read %s: %s",
                introspection_file,
                exc,
            )
            return False
        if not content.strip():
            return False
        try:
            documents = list(yaml.safe_load_all(content))
        except yaml.YAMLError as exc:
            logger.warning(
                "[ImprovePrompts] %s is not valid YAML: %s",
                introspection_file,
                exc,
            )
            return False
        return any(isinstance(doc, dict) and doc.get("status") == "pending" for doc in documents)

    @staticmethod
    def _snapshot_templates(template_paths: dict[str, Path]) -> dict[str, str]:
        """Read each template's current content so we can diff post-agy."""
        snapshot: dict[str, str] = {}
        for name, path in template_paths.items():
            try:
                snapshot[name] = path.read_text() if path.exists() else ""
            except OSError:
                snapshot[name] = ""
        return snapshot

    def _count_pending_entries(self, introspection_file: Path) -> int:
        """Count ``status: pending`` documents in the introspection YAML."""
        if not introspection_file.exists():
            return 0
        try:
            content = introspection_file.read_text()
        except OSError:
            return 0
        if not content.strip():
            return 0
        try:
            documents = list(yaml.safe_load_all(content))
        except yaml.YAMLError:
            return 0
        return sum(
            1 for doc in documents if isinstance(doc, dict) and doc.get("status") == "pending"
        )

    @staticmethod
    def _compute_template_changes(
        template_paths: dict[str, Path],
        snapshot: dict[str, str],
    ) -> list[tuple[str, int, int]]:
        """Return (name, added, removed) for every template agy modified.

        Uses unified-diff counts so an in-place rewrite that preserves line
        count still surfaces as non-zero edits (e.g. ``+7 -7``), unlike a
        bare net delta which would collapse to ``0``.

        ``splitlines(keepends=True)`` preserves line terminators so that
        newline-only edits (CRLF/LF normalization or a flipped trailing
        newline) report as non-zero +/- counts instead of a misleading
        ``+0 -0`` after the ``pre != post`` guard has already passed.
        """
        changes: list[tuple[str, int, int]] = []
        for name, path in template_paths.items():
            pre = snapshot.get(name, "")
            try:
                post = path.read_text() if path.exists() else ""
            except OSError:
                continue
            if pre == post:
                continue
            added = 0
            removed = 0
            for line in difflib.unified_diff(
                pre.splitlines(keepends=True),
                post.splitlines(keepends=True),
                lineterm="",
            ):
                if line.startswith(("+++ ", "--- ")):
                    continue
                if line.startswith("+"):
                    added += 1
                elif line.startswith("-"):
                    removed += 1
            changes.append((name, added, removed))
        return changes

    @staticmethod
    def _extract_agent_summary(stdout: str) -> list[str] | None:
        """Return the non-empty lines inside agy's summary markers, or ``None``.

        ``None`` signals either that ``stdout`` was empty or that the marker
        contract was violated (open missing, close missing, or close before
        open) so callers can warn once rather than dump raw stdout.
        """
        if not stdout:
            return None
        open_idx = stdout.find(_SUMMARY_OPEN)
        if open_idx == -1:
            return None
        inner_start = open_idx + len(_SUMMARY_OPEN)
        close_idx = stdout.find(_SUMMARY_CLOSE, inner_start)
        if close_idx == -1:
            return None
        block = stdout[inner_start:close_idx]
        return [line.strip() for line in block.splitlines() if line.strip()]

    def _emit_summary(
        self,
        changes: list[tuple[str, int, int]],
        entries_consumed: int,
        agent_stdout: str,
    ) -> None:
        """Log a human-readable summary of template edits, counts, and agy's rationale."""
        entries_word = "entry" if entries_consumed == 1 else "entries"
        if not changes:
            self.logger.info(
                "[ImprovePrompts] Summary: no template edits made; %d introspection %s consumed.",
                entries_consumed,
                entries_word,
            )
            self._emit_agent_rationale(agent_stdout)
            return
        templates_word = "template" if len(changes) == 1 else "templates"
        self.logger.info(
            "[ImprovePrompts] Summary: %d %s modified, %d introspection %s consumed.",
            len(changes),
            templates_word,
            entries_consumed,
            entries_word,
        )
        for name, added, removed in changes:
            self.logger.info(
                "[ImprovePrompts]   %s (+%d -%d lines)",
                name,
                added,
                removed,
            )
        self._emit_agent_rationale(agent_stdout)

    def _emit_agent_rationale(self, agent_stdout: str) -> None:
        """Echo agy's marked summary block, or warn once if markers are absent.

        Empty/whitespace-only stdout is treated as "no rationale provided" and
        skipped silently — the marker-contract WARNING is reserved for cases
        where agy emitted output but failed to wrap it in the expected markers.
        """
        if not agent_stdout or not agent_stdout.strip():
            return
        rationale = self._extract_agent_summary(agent_stdout)
        if rationale is None:
            self.logger.warning(
                "[ImprovePrompts] agy did not wrap its summary in the expected markers; "
                "skipping rationale echo. See agy.log for the raw output.",
            )
            return
        for line in rationale:
            self.logger.info("[ImprovePrompts]   agy: %s", line)

    def _ensure_user_templates(self, templates_dir: Path) -> dict[str, Path]:
        """Ensure the editable templates exist under ``templates_dir``.

        For each template in ``EDITABLE_TEMPLATES``, copy the shipped
        package version into ``templates_dir`` if the user doesn't already
        have their own. Returns a mapping of template filename to the
        absolute user path (for all entries, whether freshly seeded or
        pre-existing).
        """
        package_templates = resources.files("fix_die_repeat").joinpath("templates")
        ensured: dict[str, Path] = {}
        for name in EDITABLE_TEMPLATES:
            target = templates_dir / name
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                # Traversable.joinpath('a/b') works for filesystem packages but
                # is not portable to zip/wheel resources; chain the components
                # so this keeps working after packaging.
                source = package_templates
                for part in name.split("/"):
                    source = source.joinpath(part)
                with resources.as_file(source) as source_path:
                    shutil.copyfile(source_path, target)
                self.logger.info(
                    "[ImprovePrompts] Seeded %s from shipped defaults.",
                    target,
                )
            ensured[name] = target
        return ensured
