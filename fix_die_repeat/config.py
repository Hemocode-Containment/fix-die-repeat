"""Configuration management for fix-die-repeat."""

import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import pydantic as pyd
from pydantic_settings import BaseSettings, SettingsConfigDict

from fix_die_repeat.utils import run_command


class Settings(BaseSettings):
    """Configuration settings for fix-die-repeat."""

    # Check configuration
    check_cmd: str | None = pyd.Field(
        default=None,
        alias="FDR_CHECK_CMD",
        description="Command to run checks",
    )

    # Iteration limits
    max_iters: int = pyd.Field(
        default=10,
        alias="FDR_MAX_ITERS",
        description="Maximum loop iterations",
    )

    # Antigravity CLI & Model configuration
    agy_bin: str = pyd.Field(
        default="agy",
        alias="FDR_AGY_BIN",
        description="Path or name of the Antigravity CLI executable",
    )

    model: str | None = pyd.Field(
        default=None,
        alias="FDR_MODEL",
        description="Primary agy model ID prepended to all role chains",
    )

    models_fix: str | None = pyd.Field(
        default=None,
        alias="FDR_MODELS_FIX",
        description="Comma-separated model fallback chain for fix turns",
    )

    models_review: str | None = pyd.Field(
        default=None,
        alias="FDR_MODELS_REVIEW",
        description="Comma-separated model fallback chain for review turns",
    )

    models_introspect: str | None = pyd.Field(
        default=None,
        alias="FDR_MODELS_INTROSPECT",
        description="Comma-separated model fallback chain for introspection turns",
    )

    models_improve: str | None = pyd.Field(
        default=None,
        alias="FDR_MODELS_IMPROVE",
        description="Comma-separated model fallback chain for improve-prompts turns",
    )

    preflight: bool = pyd.Field(
        default=False,
        alias="FDR_PREFLIGHT",
        description="Run agy version, model inventory, quota snapshot, and smoke test, then exit",
    )

    # Quota & Operational Safeguards
    quota_source: str = pyd.Field(
        default="auto",
        alias="FDR_QUOTA_SOURCE",
        description="Quota source for reset-window ordering: auto, codexbar, or none",
    )

    quota_max_used_pct: float = pyd.Field(
        default=95.0,
        alias="FDR_QUOTA_MAX_USED_PCT",
        description="Max window usedPercent before a quota pool is skipped",
    )

    quota_max_wait_s: int = pyd.Field(
        default=900,
        alias="FDR_QUOTA_MAX_WAIT_S",
        description="Max seconds to sleep waiting for a window reset when all models are exhausted",
    )

    daily_max_calls: int = pyd.Field(
        default=50,
        alias="FDR_DAILY_MAX_CALLS",
        description="Maximum agy invocations per UTC day (0 for unlimited)",
    )

    # PR review configuration
    max_pr_threads: int = pyd.Field(
        default=5,
        alias="FDR_MAX_PR_THREADS",
        description="Maximum PR threads to process per iteration",
    )

    # Artifact management
    archive_artifacts: bool = pyd.Field(
        default=False,
        alias="FDR_ARCHIVE_ARTIFACTS",
        description="Archive existing artifacts to a timestamped folder",
    )

    compact_artifacts: bool = pyd.Field(
        default=True,
        alias="FDR_COMPACT_ARTIFACTS",
        description="Automatically compact large artifacts",
    )

    # PR review mode
    pr_review: bool = pyd.Field(
        default=False,
        alias="FDR_PR_REVIEW",
        description="Enable PR review mode",
    )

    # PR review introspection
    pr_review_introspect: bool = pyd.Field(
        default=False,
        alias="FDR_PR_REVIEW_INTROSPECT",
        description="Enable PR review mode with prompt introspection",
    )

    # Full-codebase review mode (report-only, no fixes)
    full_codebase_review: bool = pyd.Field(
        default=False,
        alias="FDR_FULL_CODEBASE_REVIEW",
        description="Audit the entire codebase instead of a diff; never applies fixes",
    )

    # Contextual review mode (report-only, auto-scoped)
    contextual_review: bool = pyd.Field(
        default=False,
        alias="FDR_CONTEXTUAL_REVIEW",
        description="Smart contextual review: uncommitted changes, branch diff, or full codebase",
    )

    # PR threads introspect-only mode
    pr_threads_introspect_only: bool = pyd.Field(
        default=False,
        alias="FDR_PR_THREADS_INTROSPECT_ONLY",
        description="Fetch unresolved PR threads and run introspection without attempting fixes",
    )

    # Prompt-improvement mode (uses agy to update user-templates from introspection data)
    improve_prompts: bool = pyd.Field(
        default=False,
        alias="FDR_IMPROVE_PROMPTS",
        description="Run agy over accumulated introspection data to update user prompt templates",
    )

    # Debug mode
    debug: bool = pyd.Field(
        default=False,
        alias="FDR_DEBUG",
        description="Enable debug mode with timestamped session logs",
    )

    # Language detection
    languages: str | None = pyd.Field(
        default=None,
        alias="FDR_LANGUAGES",
        description="Comma-separated language override (e.g., 'python,rust'). "
        "Replaces auto-detection from diff.",
    )

    # Notification settings
    ntfy_enabled: bool = pyd.Field(
        default=True,
        alias="FDR_NTFY_ENABLED",
        description="Enable ntfy notifications",
    )

    ntfy_url: str = pyd.Field(
        default="http://localhost:2586",
        alias="FDR_NTFY_URL",
        description="ntfy server URL",
    )

    # Thresholds
    auto_attach_threshold: int = pyd.Field(
        default=200 * 1024,
        description="Size threshold in bytes for auto-attaching file contents",
    )

    compact_threshold_lines: int = pyd.Field(
        default=150,
        description="Line count threshold for artifact compaction",
    )

    emergency_threshold_lines: int = pyd.Field(
        default=200,
        description="Emergency compaction threshold",
    )

    large_file_lines: int = pyd.Field(
        default=2000,
        description="Line count threshold for large file warnings",
    )

    # Agent turn timeouts
    agent_idle_timeout_s: float = pyd.Field(
        default=120.0,
        alias="FDR_AGENT_IDLE_TIMEOUT_S",
        description="Max seconds without an agy event while no tool step is active",
    )

    agent_tool_timeout_s: float = pyd.Field(
        default=1200.0,
        alias="FDR_AGENT_TOOL_TIMEOUT_S",
        description="Max seconds an active agy tool step may run",
    )

    agent_hard_timeout_s: float = pyd.Field(
        default=3600.0,
        alias="FDR_AGENT_HARD_TIMEOUT_S",
        description="Absolute wall-clock cap on a single agy turn",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="FDR_",
        extra="allow",
        populate_by_name=True,
    )

    def validate_max_iters(self) -> None:
        """Validate max_iters is a positive integer."""
        if self.max_iters <= 0:
            message = (
                f"Invalid configuration: FDR_MAX_ITERS must be a positive integer "
                f"(got '{self.max_iters}')"
            )
            raise ValueError(message)


@dataclass(frozen=True)
class CliOptions:
    """CLI override options for Settings.

    Groups all CLI-provided overrides into a single object to avoid
    function parameter count violations (PLR0913) in internal logic.
    """

    check_cmd: str | None = None
    max_iters: int | None = None
    model: str | None = None
    max_pr_threads: int | None = None
    archive_artifacts: bool | None = None
    no_compact: bool = False
    pr_review: bool = False
    pr_review_introspect: bool = False
    full_codebase_review: bool = False
    contextual_review: bool = False
    pr_threads_introspect_only: bool = False
    improve_prompts: bool = False
    preflight: bool = False
    debug: bool = False


def get_settings(options: CliOptions | None = None) -> Settings:
    """Create Settings instance from command line args and environment.

    Args:
        options: CLI override options grouped into a dataclass

    Returns:
        Settings instance

    """
    settings = Settings()

    if options is not None:
        _apply_cli_options(settings, options)

    settings.validate_max_iters()

    return settings


def _apply_cli_options(settings: Settings, options: CliOptions) -> None:
    """Apply CLI options to Settings instance.

    Extracted from get_settings to reduce cyclomatic complexity (C901).

    Args:
        settings: Settings instance to modify
        options: CLI options to apply

    """
    _apply_command_options(settings, options)
    _apply_boolean_flags(settings, options)


def _apply_command_options(settings: Settings, options: CliOptions) -> None:
    """Apply command-related CLI options.

    Args:
        settings: Settings instance to modify
        options: CLI options to apply

    """
    if options.check_cmd is not None:
        settings.check_cmd = options.check_cmd
    if options.max_iters is not None:
        settings.max_iters = options.max_iters
    if options.model is not None:
        settings.model = options.model
    if options.max_pr_threads is not None:
        settings.max_pr_threads = options.max_pr_threads


_BOOLEAN_FLAG_NAMES: tuple[str, ...] = (
    "pr_review",
    "full_codebase_review",
    "contextual_review",
    "pr_threads_introspect_only",
    "improve_prompts",
    "preflight",
    "debug",
)


def _apply_boolean_flags(settings: Settings, options: CliOptions) -> None:
    """Apply boolean flag CLI options.

    Args:
        settings: Settings instance to modify
        options: CLI options to apply

    """
    if options.archive_artifacts is not None:
        settings.archive_artifacts = options.archive_artifacts
    if options.no_compact:
        settings.compact_artifacts = False
    for flag_name in _BOOLEAN_FLAG_NAMES:
        if getattr(options, flag_name):
            setattr(settings, flag_name, True)
    if options.pr_review_introspect:
        settings.pr_review_introspect = True
        settings.pr_review = True


def _central_root() -> Path:
    """Return the central fix-die-repeat state directory.

    Defaults to ``~/.fix-die-repeat``; overridable via the ``FDR_HOME`` env var.
    """
    override = os.environ.get("FDR_HOME")
    if override:
        return Path(override).expanduser().resolve(strict=False)
    return (Path.home() / ".fix-die-repeat").resolve(strict=False)


def _repo_slug(project_root: Path) -> str:
    """Return a stable, semi-readable per-repo slug.

    Format: ``<basename>-<8charhash>``. Hash input is the git ``origin`` remote
    URL when available, else the absolute path of the project root.
    """
    git_path = shutil.which("git")
    hash_input: str | None = None
    if git_path:
        returncode, stdout, _ = run_command(
            [git_path, "-C", str(project_root), "remote", "get-url", "origin"],
            check=False,
        )
        if returncode == 0 and stdout.strip():
            hash_input = stdout.strip()
    if hash_input is None:
        hash_input = str(project_root.resolve())
    digest = hashlib.sha1(hash_input.encode(), usedforsecurity=False).hexdigest()[:8]
    return f"{project_root.name}-{digest}"


class Paths:
    """Path management for fix-die-repeat."""

    def __init__(self, project_root: Path | None = None) -> None:
        """Initialize paths.

        Args:
            project_root: Project root directory (defaults to git root or cwd)

        """
        self.project_root = project_root or self._find_project_root()
        self.fdr_home = _central_root()
        self.fdr_dir = self.fdr_home / "repos" / _repo_slug(self.project_root)
        self.agy_state_file = self.fdr_home / "agy_state.json"
        self.quota_state_file = self.fdr_home / "quota_state.json"
        self.runner_lock_file = self.fdr_dir / "runner.lock"
        self.config_file = self.fdr_dir / "config"
        self.review_file = self.fdr_dir / "review.md"
        self.review_current_file = self.fdr_dir / "review_current.md"
        self.review_recent_file = self.fdr_dir / "review_recent.md"
        self.build_history_file = self.fdr_dir / "build_history.md"
        self.checks_log = self.fdr_dir / "checks.log"
        self.checks_filtered_log = self.fdr_dir / "checks_filtered.log"
        self.checks_hash_file = self.fdr_dir / ".checks_hashes"
        self.agy_log = self.fdr_dir / "agy.log"
        self.agy_transcripts_dir = self.fdr_dir / "agy"
        self.fdr_log = self.fdr_dir / "fdr.log"
        self.pr_threads_cache = self.fdr_dir / ".pr_threads_cache"
        self.pr_threads_hash_file = self.fdr_dir / ".pr_threads_hash"
        self.start_sha_file = self.fdr_dir / ".start_sha"
        self.pr_thread_ids_file = self.fdr_dir / ".pr_thread_ids_in_scope"
        self.pr_resolved_threads_file = self.fdr_dir / ".resolved_threads"
        # Cumulative tracking for introspection (persists across iterations)
        self.cumulative_in_scope_threads_file = self.fdr_dir / ".cumulative_in_scope_threads"
        self.cumulative_resolved_threads_file = self.fdr_dir / ".cumulative_resolved_threads"
        self.cumulative_pr_threads_content_file = self.fdr_dir / ".cumulative_pr_threads_content"
        self.diff_file = self.fdr_dir / "changes.diff"
        self.run_timestamps_file = self.fdr_dir / "run_timestamps.md"
        self.introspection_data_file = self.fdr_dir / ".introspection_data.yaml"
        self.introspection_result_file = self.fdr_dir / ".introspection_result.yaml"

    @staticmethod
    def _find_project_root() -> Path:
        """Find project root directory.

        Returns:
            Path to project root

        """
        git_path = shutil.which("git")
        if git_path:
            returncode, stdout, _ = run_command(
                [git_path, "rev-parse", "--show-toplevel"],
                check=False,
            )
            if returncode == 0 and stdout.strip():
                return Path(stdout.strip())

        return Path.cwd()

    def ensure_fdr_dir(self) -> None:
        """Ensure the central per-repo state directory exists."""
        self.fdr_dir.mkdir(parents=True, exist_ok=True)

    def template_context(self) -> dict[str, str]:
        """Return absolute path strings for every path referenced in prompts.

        Spread into ``render_prompt(...)`` calls so templates can use
        ``{{ review_current_path }}`` etc. without hardcoding ``.fix-die-repeat``.
        """
        return {
            "fdr_dir_path": str(self.fdr_dir),
            "review_history_path": str(self.review_file),
            "review_current_path": str(self.review_current_file),
            "build_history_path": str(self.build_history_file),
            "checks_log_path": str(self.checks_log),
            "checks_filtered_log_path": str(self.checks_filtered_log),
            "diff_file_path": str(self.diff_file),
            "resolved_threads_path": str(self.pr_resolved_threads_file),
            "config_file_path": str(self.config_file),
        }


def get_introspection_file_path(*, create: bool = True) -> Path:
    """Return the global introspection file path (``<FDR_HOME>/introspection.yaml``).

    Args:
        create: When ``True`` (default), ensure ``<FDR_HOME>/`` exists. Pass
            ``False`` for read-only/no-op callers that only need the path and
            don't want to materialize the dotfolder as a side effect.

    """
    central = _central_root()
    if create:
        central.mkdir(parents=True, exist_ok=True)
    return central / "introspection.yaml"


def get_introspection_archive_file_path() -> Path:
    """Return the introspection archive path (``<FDR_HOME>/introspection-archive.yaml``)."""
    return _central_root() / "introspection-archive.yaml"


def get_user_templates_dir() -> Path:
    """Return the user-editable templates directory (``<FDR_HOME>/templates/``)."""
    return _central_root() / "templates"
