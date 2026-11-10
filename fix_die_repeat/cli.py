"""Command-line interface for fix-die-repeat."""

import logging
import traceback

import click
from rich.console import Console

from fix_die_repeat.config import (
    CliOptions,
    Paths,
    get_introspection_file_path,
    get_settings,
)
from fix_die_repeat.detection import (
    get_system_config_path,
    resolve_check_cmd,
    validate_check_cmd_or_exit,
)
from fix_die_repeat.runner import AgyRunner
from fix_die_repeat.runner_improve_prompts import ImprovePromptsManager
from fix_die_repeat.utils import is_running_in_dev_mode

console = Console()


_MAIN_HELP = (
    "Automated check, review, and fix loop using Antigravity (agy).\n"
    "\n"
    "\b\n"
    "Environment variables:\n"
    "  FDR_CHECK_CMD, FDR_MAX_ITERS, FDR_MODEL, FDR_AGY_BIN,\n"
    "  FDR_MODELS_FIX, FDR_MODELS_REVIEW, FDR_MODELS_INTROSPECT, FDR_MODELS_IMPROVE,\n"
    "  FDR_QUOTA_SOURCE (auto|codexbar|none), FDR_QUOTA_MAX_USED_PCT, FDR_QUOTA_MAX_WAIT_S,\n"
    "  FDR_DAILY_MAX_CALLS (default: 50, 0=unlimited),\n"
    "  FDR_AGENT_IDLE_TIMEOUT_S, FDR_AGENT_TOOL_TIMEOUT_S, FDR_AGENT_HARD_TIMEOUT_S,\n"
    "  FDR_MAX_PR_THREADS, FDR_ARCHIVE_ARTIFACTS, FDR_COMPACT_ARTIFACTS,\n"
    "  FDR_PR_REVIEW, FDR_PR_REVIEW_INTROSPECT,\n"
    "  FDR_CONTEXTUAL_REVIEW, FDR_FULL_CODEBASE_REVIEW,\n"
    "  FDR_PR_THREADS_INTROSPECT_ONLY, FDR_IMPROVE_PROMPTS,\n"
    "  FDR_PREFLIGHT, FDR_DEBUG, FDR_LANGUAGES,\n"
    "  FDR_HOME (base directory for state; defaults to ~/.fix-die-repeat),\n"
    "  FDR_NTFY_ENABLED (default: 1),\n"
    "  FDR_NTFY_URL (default: http://localhost:2586)\n"
    "\n"
    "\b\n"
    "Examples:\n"
    "  # Run with default settings\n"
    "  fix-die-repeat\n"
    "\b\n"
    "  # Use a custom check command\n"
    '  fix-die-repeat -c "make test"\n'
    "\b\n"
    "  # Verify agy CLI, model chains, and quota windows before running\n"
    "  fix-die-repeat --preflight\n"
    "\b\n"
    "  # Enable PR review mode\n"
    "  fix-die-repeat --pr-review\n"
    "\b\n"
    "  # PR review mode with prompt introspection\n"
    "  fix-die-repeat --pr-review-introspect\n"
    "\b\n"
    "  # Smart contextual review (uncommitted > branch > full codebase)\n"
    "  fix-die-repeat --contextual-review\n"
    "\b\n"
    "  # Audit the entire codebase (report-only, no fixes attempted)\n"
    "  fix-die-repeat --full-codebase-review\n"
    "\b\n"
    "  # Fetch and introspect unresolved PR review threads, then exit\n"
    "  fix-die-repeat --pr-threads-introspect-only\n"
    "\b\n"
    "  # Update user prompt templates from accumulated introspection data\n"
    "  fix-die-repeat --improve-prompts\n"
)


@click.command(help=_MAIN_HELP)
@click.option(
    "-c",
    "--check-cmd",
    help="Command to run checks (default: auto-detected)",
    envvar="FDR_CHECK_CMD",
)
@click.option(
    "-n",
    "--max-iters",
    type=int,
    help="Maximum loop iterations (default: 10)",
    envvar="FDR_MAX_ITERS",
)
@click.option(
    "-m",
    "--model",
    help="Primary agy model ID prepended to role chains (e.g., claude-sonnet-5-5-high)",
    envvar="FDR_MODEL",
)
@click.option(
    "--max-pr-threads",
    type=int,
    help="Maximum PR threads to process per iteration (default: 5)",
    envvar="FDR_MAX_PR_THREADS",
)
@click.option(
    "--archive-artifacts",
    is_flag=True,
    help="Archive existing artifacts to a timestamped folder",
    envvar="FDR_ARCHIVE_ARTIFACTS",
)
@click.option(
    "--no-compact",
    is_flag=True,
    help="Skip automatic compaction of large artifacts",
)
@click.option(
    "--pr-review",
    is_flag=True,
    help="Enable PR review mode",
    envvar="FDR_PR_REVIEW",
)
@click.option(
    "--pr-review-introspect",
    is_flag=True,
    help="Enable PR review mode with prompt introspection (implies --pr-review)",
    envvar="FDR_PR_REVIEW_INTROSPECT",
)
@click.option(
    "--contextual-review",
    is_flag=True,
    help=(
        "Smart contextual review (report-only). Reviews uncommitted changes, "
        "branch diff vs default branch, or full codebase if neither applies. "
        "Standalone mode — mutually exclusive with --full-codebase-review, "
        "--pr-threads-introspect-only, and --improve-prompts."
    ),
    envvar="FDR_CONTEXTUAL_REVIEW",
)
@click.option(
    "--full-codebase-review",
    is_flag=True,
    help=(
        "Audit the entire codebase instead of a diff. Report-only: "
        "never attempts fixes. Ignores --pr-review if also set. "
        "Standalone mode — mutually exclusive with --contextual-review, "
        "--pr-threads-introspect-only, and --improve-prompts."
    ),
    envvar="FDR_FULL_CODEBASE_REVIEW",
)
@click.option(
    "--pr-threads-introspect-only",
    is_flag=True,
    help=(
        "Fetch the PR's unresolved review threads, run introspection on them, "
        "then exit. Does not run checks, local review, or attempt fixes. "
        "Standalone mode — mutually exclusive with --contextual-review, "
        "--full-codebase-review, and --improve-prompts."
    ),
    envvar="FDR_PR_THREADS_INTROSPECT_ONLY",
)
@click.option(
    "--improve-prompts",
    is_flag=True,
    help=(
        "Read accumulated introspection data and have agy update the user-owned "
        "prompt templates under <FDR_HOME>/templates/. Seeds copies of the shipped "
        "templates on first use; never mutates the package. Runs once and exits. "
        "Standalone mode — mutually exclusive with --contextual-review, "
        "--full-codebase-review, and --pr-threads-introspect-only."
    ),
    envvar="FDR_IMPROVE_PROMPTS",
)
@click.option(
    "--preflight",
    is_flag=True,
    help="Run agy version, model inventory, quota snapshot, and smoke test, then exit",
    envvar="FDR_PREFLIGHT",
)
@click.option(
    "-d",
    "--debug",
    is_flag=True,
    help="Enable debug mode (timestamped session logs and verbose logging)",
    envvar="FDR_DEBUG",
)
@click.version_option()
def main(**kwargs: str | int | bool | None) -> None:
    """Run the automated check, review, and fix loop."""
    debug = bool(kwargs.get("debug", False))
    exit_code = _run_main_with_error_handling(kwargs, debug=debug)
    raise SystemExit(exit_code)


def _build_cli_options(kwargs: dict[str, str | int | bool | None]) -> CliOptions:
    """Build CliOptions from Click's keyword arguments."""
    check_cmd = kwargs.get("check_cmd")
    max_iters = kwargs.get("max_iters")
    model = kwargs.get("model")
    max_pr_threads = kwargs.get("max_pr_threads")
    archive_flag = kwargs.get("archive_artifacts")

    return CliOptions(
        check_cmd=str(check_cmd) if check_cmd is not None else None,
        max_iters=int(max_iters) if max_iters is not None else None,
        model=str(model) if model is not None else None,
        max_pr_threads=int(max_pr_threads) if max_pr_threads is not None else None,
        archive_artifacts=bool(archive_flag) if archive_flag else None,
        no_compact=bool(kwargs.get("no_compact", False)),
        pr_review=bool(kwargs.get("pr_review", False)),
        pr_review_introspect=bool(kwargs.get("pr_review_introspect", False)),
        full_codebase_review=bool(kwargs.get("full_codebase_review", False)),
        contextual_review=bool(kwargs.get("contextual_review", False)),
        pr_threads_introspect_only=bool(kwargs.get("pr_threads_introspect_only", False)),
        improve_prompts=bool(kwargs.get("improve_prompts", False)),
        preflight=bool(kwargs.get("preflight", False)),
        debug=bool(kwargs.get("debug", False)),
    )


def _run_main_with_error_handling(
    kwargs: dict[str, str | int | bool | None],
    *,
    debug: bool,
) -> int:
    """Run the main application with error handling."""
    try:
        options = _build_cli_options(kwargs)
        return _run_main(options)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        return 1
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        return 130
    except Exception as e:
        console.print(f"[red]Unexpected error: {e}[/red]")
        if debug:
            console.print(traceback.format_exc())
        return 1


_STANDALONE_MODE_FLAGS: tuple[tuple[str, str], ...] = (
    ("pr_threads_introspect_only", "--pr-threads-introspect-only"),
    ("improve_prompts", "--improve-prompts"),
    ("contextual_review", "--contextual-review"),
    ("full_codebase_review", "--full-codebase-review"),
)


def _validate_standalone_modes_mutually_exclusive(settings: object) -> None:
    """Reject combinations of standalone-mode flags."""
    active = [cli_flag for attr, cli_flag in _STANDALONE_MODE_FLAGS if getattr(settings, attr)]
    if len(active) > 1:
        joined = ", ".join(active)
        msg = (
            f"The following flags are mutually exclusive: {joined}. "
            "Pick one — each runs a one-shot mode and exits."
        )
        raise ValueError(msg)


def _run_main(options: CliOptions) -> int:
    """Run main application logic using CliOptions."""
    if is_running_in_dev_mode():
        console.print("[cyan]⚡ Running in DEV mode (editable install)[/cyan]")

    settings = get_settings(options)

    _validate_standalone_modes_mutually_exclusive(settings)

    if settings.improve_prompts and not ImprovePromptsManager.has_pending_work(
        logging.getLogger("fix-die-repeat.cli")
    ):
        console.print(
            f"[cyan][ImprovePrompts][/cyan] No pending introspection entries at "
            f"{get_introspection_file_path(create=False)}; nothing to do."
        )
        return 0

    paths = Paths()

    needs_check_cmd = not (
        settings.full_codebase_review
        or settings.pr_threads_introspect_only
        or settings.contextual_review
        or settings.improve_prompts
        or settings.preflight
    )

    if needs_check_cmd:
        if settings.check_cmd is None:
            settings.check_cmd = resolve_check_cmd(
                cli_check_cmd=options.check_cmd,
                project_config_path=paths.config_file,
                system_config_path=get_system_config_path(),
                project_root=str(paths.project_root),
            )

        if settings.check_cmd is None:
            console.print(
                "[red]Error:[/red] Unable to determine a check command to run.\n"
                "Please specify one via the [bold]--check-cmd[/bold] option or the "
                "[bold]FDR_CHECK_CMD[/bold] environment variable."
            )
            raise SystemExit(1)

        validate_check_cmd_or_exit(settings.check_cmd)

    with AgyRunner(settings, paths) as runner:
        return runner.run()


if __name__ == "__main__":
    main()
