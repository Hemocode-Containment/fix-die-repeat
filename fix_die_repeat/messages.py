"""Constants and message generators for user-facing messages."""

from collections.abc import Iterable


def git_diff_instructions(start_sha: str) -> str:
    """Return git diff/checkout instructions for showing changes.

    Args:
        start_sha: Starting git commit SHA

    Returns:
        Instruction string

    """
    return f"To see all changes made: git diff {start_sha}"


def git_checkout_instructions(start_sha: str) -> str:
    """Return git checkout instructions for rolling back changes.

    Args:
        start_sha: Starting git commit SHA

    Returns:
        Instruction string

    """
    return f"To revert all changes:   git checkout {start_sha} -- ."


def oscillation_warning(prev_iter: int) -> str:
    """Return warning for detected oscillation (identical check output).

    Args:
        prev_iter: Previous iteration number that matched

    Returns:
        Warning message

    """
    return (
        f"WARNING: Check output is IDENTICAL to iteration {prev_iter}. "
        "You are going in CIRCLES. Your previous approach did NOT work — "
        "you MUST try a fundamentally DIFFERENT strategy."
    )


def large_file_warning_intro() -> str:
    """Return the introduction for large file warnings."""
    return (
        "CRITICAL WARNING: The following files are >2000 lines and "
        "will be TRUNCATED by the 'read' tool:"
    )


def large_file_warning_item(filepath: str, line_count: int) -> str:
    """Return a single large file warning item.

    Args:
        filepath: Path to the large file
        line_count: Number of lines in the file

    Returns:
        Warning item string

    """
    return f"- {filepath} ({line_count} lines)"


def large_file_warning_critical() -> str:
    """Return the critical warning about flying blind with large files."""
    return (
        "[CRITICAL]: You CANNOT see the bottom of these files. If errors "
        "occur there, you are flying blind."
    )


def large_file_warning_recommendations() -> str:
    """Return recommendations for fixing large files."""
    return (
        "STRONGLY RECOMMENDED: Split these files into smaller files or "
        "modules to bring them under the 2000-line limit.\n"
        "  - If the file contains tests at the bottom, move them to a "
        "separate test file (e.g., tests.rs, test_file.py, file.test.js).\n"
        "  - If it is a large logic file, extract cohesive functionality "
        "into separate source files or subfolders."
    )


def build_large_file_warning(files: Iterable[tuple[str, int]]) -> str:
    """Build complete large file warning from file list.

    Args:
        files: Iterable of (filepath, line_count) tuples

    Returns:
        Complete warning message with intro, items, and recommendations

    """
    if not files:
        return ""

    parts = [large_file_warning_intro()]
    parts.extend(large_file_warning_item(fp, lc) for fp, lc in files)
    parts.extend(["", large_file_warning_critical(), large_file_warning_recommendations()])

    return "\n".join(parts)


def model_recommendations_header() -> str:
    """Return the header for model recommendations."""
    return "RECOMMENDATION: Try a different model:"


def model_recommendation_items() -> str:
    """Return the list of recommended models."""
    return (
        "  - claude-sonnet-5-5-high (recommended for code editing)\n"
        "  - claude-opus-5-5-high (highest reasoning capacity)\n"
        "  - gemini-4-argon-high (separate Gemini Argon quota pool)"
    )


def model_recommendations_full() -> str:
    """Return complete model recommendations message."""
    return f"{model_recommendations_header()}\n{model_recommendation_items()}"


def pr_threads_unsafe_count_warning(unsafe_count: int, unsafe_ids: list[str]) -> str:
    """Return warning about PR threads not in scope.

    Args:
        unsafe_count: Number of unsafe threads
        unsafe_ids: List of unsafe thread IDs

    Returns:
        Warning message

    """
    ids_str = ", ".join(unsafe_ids)
    return f"WARNING: Model reported {unsafe_count} thread(s) NOT in scope: {ids_str}"


def pr_threads_safe_only_message(safe_count: int) -> str:
    """Return message about only resolving safe threads.

    Args:
        safe_count: Number of safe/in-scope threads

    Returns:
        Informational message

    """
    return f"Only resolving the {safe_count} in-scope thread(s)."


def auto_detect_found_message(command: str, reason: str) -> str:
    """Return message for auto-detected check command.

    Args:
        command: Auto-detected command
        reason: Reason for detection

    Returns:
        Auto-detect found message

    """
    return f"🔍 Auto-detected check command: {command}\n   ({reason})"


def auto_detect_confirm_prompt() -> str:
    """Return prompt for confirming auto-detected command.

    Returns:
        Confirm prompt string

    """
    return "Use this command?"


def no_detection_prompt_message() -> str:
    """Return prompt when no auto-detection match found.

    Returns:
        Prompt message asking for check command

    """
    return (
        "No check command detected for this project.\n\n"
        "What command should fix-die-repeat run to check your project?\n"
        "Examples: pytest, npm test, cargo test, make test, ./scripts/ci.sh\n\n"
        "Check command"
    )


def no_tty_error_message(
    project_config_path: str | None = None,
    system_config_path: str | None = None,
) -> str:
    """Return error message when no TTY is available.

    Args:
        project_config_path: Absolute path to this project's config file.
        system_config_path: Absolute path to the global config file.

    Returns:
        Error message with setup instructions

    """
    project_line = project_config_path or "<FDR_HOME>/repos/<slug>/config"
    global_line = system_config_path or "<FDR_HOME>/config"
    return (
        "Error: No check command configured.\n\n"
        "fix-die-repeat needs a check command to run. Provide one via:\n"
        '  • CLI flag:          fix-die-repeat -c "pytest"\n'
        '  • Environment var:   FDR_CHECK_CMD="pytest" fix-die-repeat\n'
        f'  • Project config:    echo \'check_cmd = "pytest"\' > "{project_line}"\n'
        f'  • Global config:     echo \'check_cmd = "pytest"\' > "{global_line}"'
    )


def global_config_fallthrough_warning(command: str) -> str:
    """Return warning when system config command not found.

    Args:
        command: Command from system config that wasn't found

    Returns:
        Warning message

    """
    msg = f"Global check command '{command}' not found"
    msg += " in this project. Falling back to auto-detection..."
    return f"⚠ {msg}"


def check_cmd_not_found_error(command: str) -> str:
    """Return error when check command is not executable.

    Args:
        command: Command that wasn't found

    Returns:
        Error message

    """
    return f"Check command '{command}' not found. Is it installed and on your PATH?"


def check_cmd_persisted_message(config_path: str) -> str:
    """Return message when check command is persisted to config.

    Args:
        config_path: Path to config file where command was saved

    Returns:
        Success message

    """
    return f"✓ Check command saved to {config_path}"
