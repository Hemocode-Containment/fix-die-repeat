"""Introspection management for fix-die-repeat runner.

This module handles PR review introspection, which analyzes real-world PR
feedback to improve future prompts.
"""

import json
import logging
import os
import sys
import types
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

import yaml

# Platform-specific file locking imports
if sys.platform == "win32":  # pragma: no cover
    import msvcrt
else:  # pragma: no cover
    import fcntl

from fix_die_repeat.agy_policy import AgentRole
from fix_die_repeat.config import Paths, Settings, get_introspection_file_path
from fix_die_repeat.prompts import render_prompt
from fix_die_repeat.utils import run_command

if TYPE_CHECKING:
    from typing import Self


@runtime_checkable
class _FileHandle(Protocol):
    """Protocol for objects that support file descriptor access.

    Used for type-checking the file lock context manager.
    """

    def fileno(self) -> int:
        """Return the file descriptor for the file handle."""
        ...

    def seek(self, offset: int, whence: int = 0) -> int:
        """Move to a new file position."""
        ...


class _FileLock:
    """Context manager for cross-platform file locking.

    Provides exclusive file locking to prevent concurrent writes from
    corrupting shared files like the global introspection.yaml.

    Uses fcntl on Unix and msvcrt on Windows.
    """

    def __init__(self, file_handle: _FileHandle) -> None:
        """Initialize the file lock.

        Args:
            file_handle: Open file handle to lock

        """
        self.file_handle = file_handle

    def __enter__(self) -> "Self":
        """Acquire the lock."""
        if sys.platform == "win32":  # pragma: no cover
            # Windows: use msvcrt.locking
            # Seek to start of file because msvcrt.locking locks a region
            # starting from the current file position. We lock a large region
            # (0xFFFF bytes) so all processes contend for the same range even
            # as the file grows.
            self.file_handle.seek(0)
            lock_length = 0xFFFF
            msvcrt.locking(self.file_handle.fileno(), msvcrt.LK_LOCK, lock_length)
        else:  # pragma: no cover
            # Unix: use fcntl.flock with LOCK_EX
            fcntl.flock(self.file_handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        """Release the lock."""
        if sys.platform == "win32":  # pragma: no cover
            # Windows: use msvcrt.locking with LK_UNLCK
            # Seek to start of file because msvcrt.locking locks a region
            # starting from the current file position. We must unlock the
            # same 0xFFFF-byte region that was locked in __enter__.
            self.file_handle.seek(0)
            lock_length = 0xFFFF
            msvcrt.locking(self.file_handle.fileno(), msvcrt.LK_UNLCK, lock_length)
        else:  # pragma: no cover
            # Unix: use fcntl.flock with LOCK_UN
            fcntl.flock(self.file_handle.fileno(), fcntl.LOCK_UN)


STATUS_PENDING = "pending"
STATUS_REVIEWED = "reviewed"
ALLOWED_STATUSES = {STATUS_PENDING, STATUS_REVIEWED}

OUTCOME_FIXED = "fixed"
OUTCOME_WONT_FIX = "wont-fix"
OUTCOME_NOT_ATTEMPTED = "not-attempted"
ALLOWED_OUTCOMES = {OUTCOME_FIXED, OUTCOME_WONT_FIX, OUTCOME_NOT_ATTEMPTED}

THREAD_REQUIRED_FIELDS = (
    "id",
    "title",
    "category",
    "outcome",
    "summary",
    "relevance",
    "lang_check_gap",
)
TOP_LEVEL_REQUIRED_FIELDS = {
    "date": (str,),
    "project": (str,),
    "pr_number": (int, str),
    "pr_url": (str,),
    "status": (str,),
    "threads": (list,),
}


@dataclass(frozen=True)
class IntrospectionYamlParams:
    """Parameters for building introspection YAML.

    Groups related parameters for _build_introspection_yaml to avoid
    violating PLR0913 (too many arguments).
    """

    pr_number: int | str
    pr_url: str
    in_scope_ids: list[str]
    resolved_set: set[str]
    pr_threads_content: str
    diff_content: str
    introspect_only: bool = False


@dataclass
class IntrospectionPrInfo:
    """PR information from GitHub (minimal for introspection)."""

    number: int
    url: str


class IntrospectionManager:
    """Manages PR review introspection for the fix-die-repeat runner.

    Handles:
    - Collecting PR thread data and agent outcomes
    - Running introspection analysis via agy
    - Appending results to global introspection file
    """

    def __init__(
        self,
        settings: Settings,
        paths: Paths,
        project_root: Path,
        logger: logging.Logger,
    ) -> None:
        """Initialize the introspection manager.

        Args:
            settings: Configuration settings
            paths: Path management
            project_root: Project root directory
            logger: Logger instance for output

        """
        self.settings = settings
        self.paths = paths
        self.project_root = project_root
        self.logger = logger

    def run_introspection(
        self,
        iteration: int,
        start_sha: str,
        run_agent_callback: Callable[..., tuple[int, str, str]],
        *,
        introspect_only: bool = False,
    ) -> None:
        """Run prompt introspection analysis after PR review completion.

        This is a post-run step that:
        1. Collects PR thread data and agent outcomes
        2. Calls agy with the introspection prompt template
        3. Appends the result to the global introspection file

        This step does NOT block or fail the overall run. Any errors are
        logged as warnings and the main run result is preserved.

        Args:
            iteration: Current iteration number
            start_sha: Starting git commit SHA
            run_agent_callback: Function to run agy (from AgyRunner)
            introspect_only: If True, skip fix-loop assumptions and mark every
                in-scope thread with outcome "not-attempted"

        """
        self.logger.info("[Introspection] Running PR review introspection...")

        # Validate prerequisites and get PR info
        pr_info = self._validate_prerequisites_for_introspection()
        if pr_info is None:
            return

        try:
            # Collect input data
            self.collect_introspection_data(
                iteration, start_sha, pr_info, introspect_only=introspect_only
            )
            if not self.paths.introspection_data_file.exists():
                self.logger.warning(
                    "[Introspection] Failed to collect introspection data, skipping",
                )
                return

            # Render prompt and invoke agy
            prompt = render_prompt(
                "introspect_pr_review.j2",
                run_date=datetime.now(tz=UTC).strftime("%Y-%m-%d"),
                project_name=self.project_root.name,
                pr_number=pr_info.number,
                pr_url=pr_info.url,
                output_path=str(self.paths.introspection_result_file),
            )

            # Run agy and validate result
            self.logger.info(
                "[Introspection] Calling agy to analyze PR threads...",
            )
            returncode, _stdout, _stderr = run_agent_callback(
                AgentRole.INTROSPECT,
                prompt,
                attachments=[str(self.paths.introspection_data_file)],
            )

            if returncode != 0:
                self.logger.warning(
                    "[Introspection] agy call failed (exit %s), skipping introspection",
                    returncode,
                )
                return

            # Validate result file and content
            result_content = self._validate_agent_result_file()
            if result_content is None:
                return

            if not self._validate_introspection_result(result_content):
                return

            # Append to global introspection file with file locking for atomicity
            global_introspection_file = get_introspection_file_path()
            separator = "\n---\n"

            with global_introspection_file.open("a+") as f, _FileLock(f):
                f.seek(0, os.SEEK_END)
                is_empty = f.tell() == 0
                if not is_empty:
                    f.write(separator)
                f.write(result_content)

            self.logger.info(
                "[Introspection] Appended analysis to %s",
                global_introspection_file,
            )

            # Result file consumed; safe to remove. Any earlier return (agy
            # failure, validation failure, exception) leaves it on disk so
            # the analysis can be recovered manually.
            self.paths.introspection_result_file.unlink(missing_ok=True)

        except Exception:
            self.logger.exception(
                "[Introspection] Unexpected error during introspection (non-blocking)",
            )
        finally:
            # Always clean up the input data file; it has no value post-run.
            self.paths.introspection_data_file.unlink(missing_ok=True)

    def _validate_prerequisites_for_introspection(self) -> IntrospectionPrInfo | None:
        """Validate introspection prerequisites and get PR info.

        Returns:
            IntrospectionPrInfo if valid, None if introspection should skip

        """
        # Skip if no PR threads were processed (check cumulative file)
        if not self.paths.cumulative_in_scope_threads_file.exists():
            self.logger.info(
                "[Introspection] No PR threads were processed, skipping introspection",
            )
            return None

        branch = self._get_current_branch()
        if branch is None:
            return None

        pr_json = self._get_pr_info_json(branch)
        if pr_json is None:
            return None

        return self._parse_pr_info(pr_json)

    def _get_current_branch(self) -> str | None:
        """Get the current branch name for introspection checks."""
        returncode, branch, _ = run_command(
            "git branch --show-current",
            cwd=self.project_root,
        )
        if returncode != 0 or not branch.strip():
            self.logger.warning(
                "[Introspection] Not on a git branch, skipping introspection",
            )
            return None
        return branch.strip()

    def _get_pr_info_json(self, branch: str) -> str | None:
        """Fetch PR info JSON string for a branch."""
        returncode, pr_json, _ = run_command(
            f"gh pr view {branch} --json number,url",
            cwd=self.project_root,
        )
        if returncode != 0:
            self.logger.warning(
                "[Introspection] No PR info available, skipping introspection",
            )
            return None
        return pr_json

    def _parse_pr_info(self, pr_json: str) -> IntrospectionPrInfo | None:
        """Parse PR info JSON into an IntrospectionPrInfo object."""
        try:
            pr_data = json.loads(pr_json)
        except json.JSONDecodeError:
            self.logger.warning(
                "[Introspection] Failed to parse PR info (invalid JSON)",
            )
            return None

        if not isinstance(pr_data, dict):
            self.logger.warning(
                "[Introspection] PR info payload is not an object, skipping introspection",
            )
            return None

        pr_number = pr_data.get("number")
        pr_url = pr_data.get("url")

        if not isinstance(pr_number, int) or not isinstance(pr_url, str) or not pr_url:
            self.logger.warning(
                "[Introspection] PR info missing valid 'number' or 'url', skipping introspection",
            )
            return None

        return IntrospectionPrInfo(
            number=pr_number,
            url=pr_url,
        )

    def _validate_agent_result_file(self) -> str | None:
        """Validate that a valid introspection result file was produced.

        Returns:
            Result content if valid, None otherwise

        """
        # Check if result file was created
        if not self.paths.introspection_result_file.exists():
            self.logger.warning(
                "[Introspection] agy did not produce result file, skipping",
            )
            return None

        # Read and validate result
        result_content = self.paths.introspection_result_file.read_text()
        normalized_content = self._normalize_result_content(result_content)
        if not normalized_content:
            self.logger.warning(
                "[Introspection] Result file is empty after normalization, skipping append",
            )
            return None

        return normalized_content

    def _validate_introspection_result(self, result_content: str) -> bool:
        """Validate introspection YAML structure before append.

        Args:
            result_content: Normalized YAML content

        Returns:
            True if YAML is valid and structurally sound, False otherwise

        """
        try:
            parsed_content = yaml.safe_load(result_content)
        except yaml.YAMLError as exc:
            self.logger.warning(
                "[Introspection] Result is not valid YAML: %s",
                exc,
            )
            self.logger.debug(
                "[Introspection] Invalid YAML content:\n%s",
                result_content,
            )
            return False

        if not self.validate_introspection_payload(parsed_content):
            self.logger.warning(
                "[Introspection] Result YAML failed structural validation, skipping append",
            )
            self.logger.debug(
                "[Introspection] Invalid YAML content:\n%s",
                result_content,
            )
            return False

        return True

    def validate_introspection_payload(self, payload: object) -> bool:
        """Validate the parsed introspection YAML payload.

        Args:
            payload: Parsed YAML data

        Returns:
            True if payload structure is valid, False otherwise

        """
        payload_mapping = self._coerce_payload_mapping(payload)
        if payload_mapping is None:
            return False

        if not self._validate_top_level_fields(payload_mapping):
            return False

        if not self._validate_status_value(payload_mapping.get("status")):
            return False

        threads_value = payload_mapping.get("threads")
        return self._validate_threads_value(threads_value)

    def _coerce_payload_mapping(self, payload: object) -> dict[str, object] | None:
        """Ensure the payload is a mapping."""
        if not isinstance(payload, dict):
            self.logger.warning(
                "[Introspection] Result YAML root is not a mapping, skipping append",
            )
            return None
        return cast("dict[str, object]", payload)

    def _validate_top_level_fields(self, payload: dict[str, object]) -> bool:
        """Validate required top-level fields."""
        for key, expected_types in TOP_LEVEL_REQUIRED_FIELDS.items():
            if not self._validate_required_field(payload, key, expected_types):
                return False
        return True

    def _validate_required_field(
        self,
        payload: dict[str, object],
        key: str,
        expected_types: tuple[type, ...],
    ) -> bool:
        """Validate that a required field exists and has expected type."""
        if key not in payload:
            self.logger.warning(
                "[Introspection] Result YAML missing top-level key '%s'",
                key,
            )
            return False
        value = payload.get(key)
        if not isinstance(value, expected_types):
            self.logger.warning(
                "[Introspection] Result YAML key '%s' has unexpected type %s",
                key,
                type(value).__name__,
            )
            return False
        if isinstance(value, str) and not value.strip():
            self.logger.warning(
                "[Introspection] Result YAML key '%s' is empty",
                key,
            )
            return False
        return True

    def _validate_status_value(self, status_value: object) -> bool:
        """Validate status value if present."""
        if isinstance(status_value, str) and status_value not in ALLOWED_STATUSES:
            self.logger.warning(
                "[Introspection] Result YAML has unsupported status '%s'",
                status_value,
            )
            return False
        return True

    def _validate_threads_value(self, threads_value: object) -> bool:
        """Validate the thread list and its entries."""
        if not isinstance(threads_value, list):
            self.logger.warning(
                "[Introspection] Result YAML 'threads' is not a list",
            )
            return False

        for index, thread in enumerate(threads_value, start=1):
            if not self._validate_thread_entry(thread, index):
                return False

        return True

    def _validate_thread_entry(self, thread: object, index: int) -> bool:
        """Validate a single thread entry."""
        if not isinstance(thread, dict):
            self.logger.warning(
                "[Introspection] Result YAML thread #%s is not a mapping",
                index,
            )
            return False

        thread_mapping = cast("dict[str, object]", thread)
        missing_fields = [field for field in THREAD_REQUIRED_FIELDS if field not in thread_mapping]
        if missing_fields:
            self.logger.warning(
                "[Introspection] Result YAML thread #%s missing fields: %s",
                index,
                ", ".join(missing_fields),
            )
            return False

        if not self._validate_thread_field_values(thread_mapping, index):
            return False

        return self._validate_thread_outcome(thread_mapping, index)

    def _validate_thread_field_values(self, thread: dict[str, object], index: int) -> bool:
        """Validate required thread fields are non-empty strings."""
        for field in THREAD_REQUIRED_FIELDS:
            field_value = thread.get(field)
            if not isinstance(field_value, str) or not field_value.strip():
                self.logger.warning(
                    "[Introspection] Result YAML thread #%s has invalid '%s' value",
                    index,
                    field,
                )
                return False
        return True

    def _validate_thread_outcome(self, thread: dict[str, object], index: int) -> bool:
        """Validate thread outcome and reason requirements."""
        outcome_value = thread.get("outcome")
        if outcome_value not in ALLOWED_OUTCOMES:
            self.logger.warning(
                "[Introspection] Result YAML thread #%s has invalid outcome '%s'",
                index,
                outcome_value,
            )
            return False

        reason_value = thread.get("reason")
        if outcome_value == OUTCOME_WONT_FIX:
            if not isinstance(reason_value, str) or not reason_value.strip():
                self.logger.warning(
                    "[Introspection] Result YAML thread #%s missing 'reason' for wont-fix",
                    index,
                )
                return False
        elif reason_value is not None and not isinstance(reason_value, str):
            self.logger.warning(
                "[Introspection] Result YAML thread #%s has non-string 'reason'",
                index,
            )
            return False

        return True

    @staticmethod
    def _normalize_result_content(result_content: str) -> str:
        """Normalize YAML result content for safe multi-document appends.

        Removes leading/trailing YAML document markers and ensures the
        content ends with a single newline.

        Args:
            result_content: Raw YAML content from pi

        Returns:
            Normalized YAML content (empty string if nothing remains)

        """
        stripped = result_content.strip()
        if not stripped:
            return ""

        lines = stripped.splitlines()
        while lines and lines[0].strip() in ("---", "..."):
            lines.pop(0)
        while lines and lines[-1].strip() in ("---", "..."):
            lines.pop()

        normalized = "\n".join(lines).strip()
        if not normalized:
            return ""

        return f"{normalized}\n"

    def collect_introspection_data(
        self,
        _iteration: int,
        start_sha: str,
        pr_info: IntrospectionPrInfo,
        *,
        introspect_only: bool = False,
    ) -> None:
        """Collect input data for introspection analysis.

        Gathers PR thread comments, fix/won't-fix outcomes, and the diff
        of changes made by the agent during PR review mode.

        Writes the collected data to `self.paths.introspection_data_file`
        as YAML that the introspection LLM prompt can reference.

        Args:
            iteration: Current iteration number (unused, kept for compatibility)
            start_sha: Starting git commit SHA
            pr_info: PR information
            introspect_only: If True, every thread is recorded with outcome
                "not-attempted" instead of fixed/wont-fix

        """
        self.logger.info("Collecting introspection data...")

        # Collect thread IDs and outcomes
        in_scope_ids, resolved_set = self._collect_thread_ids()
        pr_number = pr_info.number
        pr_url = pr_info.url

        # Read cached data
        pr_threads_content = self._read_pr_threads_cache()
        diff_content = self._read_diff_content(start_sha)

        # Build and write YAML structure
        yaml_params = IntrospectionYamlParams(
            pr_number=pr_number,
            pr_url=pr_url,
            in_scope_ids=in_scope_ids,
            resolved_set=resolved_set,
            pr_threads_content=pr_threads_content,
            diff_content=diff_content,
            introspect_only=introspect_only,
        )
        yaml_content = self._build_introspection_yaml(yaml_params)
        self.paths.introspection_data_file.write_text(yaml_content)
        self.logger.info(
            "Collected introspection data to %s",
            self.paths.introspection_data_file,
        )

    def _collect_thread_ids(self) -> tuple[list[str], set[str]]:
        """Collect in-scope and resolved thread IDs.

        Reads from cumulative files that persist across iterations,
        not the per-iteration files that may be deleted.

        Returns:
            Tuple of (in_scope_ids list, resolved_set)

        """
        # Read in-scope thread IDs from cumulative file
        in_scope_ids = []
        if self.paths.cumulative_in_scope_threads_file.exists():
            cumulative_content = self.paths.cumulative_in_scope_threads_file.read_text().strip()
            in_scope_ids = [thread_id for thread_id in cumulative_content.split("\n") if thread_id]

        # Read resolved thread IDs from cumulative file
        resolved_ids = []
        if self.paths.cumulative_resolved_threads_file.exists():
            resolved_content = self.paths.cumulative_resolved_threads_file.read_text().strip()
            resolved_ids = [thread_id for thread_id in resolved_content.split("\n") if thread_id]

        return in_scope_ids, set(resolved_ids)

    def _read_pr_threads_cache(self) -> str:
        """Read PR threads cache content from cumulative file.

        Reads from the cumulative file that contains all PR threads
        fetched across all iterations, not the per-iteration cache
        that is overwritten.

        Returns:
            PR threads cache content or empty string

        """
        if self.paths.cumulative_pr_threads_content_file.exists():
            return self.paths.cumulative_pr_threads_content_file.read_text()
        return ""

    def _read_diff_content(self, start_sha: str) -> str:
        """Read diff content from file or git.

        Args:
            start_sha: Starting git commit SHA

        Returns:
            Diff content or empty string

        """
        # Try to read from diff file
        if self.paths.diff_file.exists():
            return self.paths.diff_file.read_text()

        # Fall back to git diff if start_sha is available
        if not start_sha:
            return ""

        returncode, stdout, _ = run_command(
            f"git diff {start_sha}",
            cwd=self.project_root,
            check=False,
        )
        return stdout if returncode == 0 else ""

    @staticmethod
    def _outcome_for_thread(thread_id: str, params: IntrospectionYamlParams) -> str:
        """Resolve the outcome label for a single in-scope thread."""
        if params.introspect_only:
            return OUTCOME_NOT_ATTEMPTED
        return OUTCOME_FIXED if thread_id in params.resolved_set else OUTCOME_WONT_FIX

    def _build_introspection_yaml(self, params: IntrospectionYamlParams) -> str:
        """Build YAML structure for introspection data.

        Args:
            params: IntrospectionYamlParams with all required data

        Returns:
            YAML content as string

        """
        lines = [
            "# Introspection input data for PR review",
        ]

        # Use yaml.safe_dump for proper escaping and quoting
        header_data = {
            "pr_number": params.pr_number,
            "pr_url": params.pr_url,
            "in_scope_thread_ids": [
                {
                    "id": thread_id,
                    "outcome": self._outcome_for_thread(thread_id, params),
                }
                for thread_id in params.in_scope_ids
            ],
        }
        lines.append(yaml.safe_dump(header_data, default_flow_style=False, sort_keys=False).strip())

        # Add PR threads content
        lines.extend(
            [
                "",
                "# PR threads (from cache)",
                "pr_threads: |",
            ]
        )
        lines.extend([f"  {line}" for line in params.pr_threads_content.splitlines()])

        # Add diff content
        lines.extend(
            [
                "",
                "# Diff of changes made by agent",
                "changes_diff: |",
            ]
        )
        lines.extend([f"  {line}" for line in params.diff_content.splitlines()])

        return "\n".join(lines)
