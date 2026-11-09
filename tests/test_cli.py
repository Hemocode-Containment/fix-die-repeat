"""Tests for CLI module."""

import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from fix_die_repeat import cli as cli_module
from fix_die_repeat import config, runner
from fix_die_repeat.cli import _build_cli_options, main

# Constants for CLI test values
TEST_MAX_ITERS = 5
TEST_MAX_PR_THREADS = 10
TEST_PARTIAL_MAX_ITERS = 3
KEYBOARD_INTERRUPT_EXIT_CODE = 130

type CliKwargs = dict[str, str | int | bool | None]


class TestCliMain:
    """Tests for CLI main command."""

    def test_cli_help(self) -> None:
        """Test that CLI help works."""
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "Automated check, review, and fix loop" in result.output
        assert "--check-cmd" in result.output
        assert "--max-iters" in result.output
        assert "--model" in result.output
        assert "--pr-review" in result.output
        assert "--preflight" in result.output

    def test_cli_version(self) -> None:
        """Test that CLI version works."""
        runner = CliRunner()
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0

    def test_cli_preflight_flag_in_help(self) -> None:
        """Test CLI accepts --preflight flag."""
        runner = CliRunner()
        result = runner.invoke(main, ["--preflight", "--help"])
        assert result.exit_code == 0
        assert "--preflight" in result.output

    def test_cli_with_max_iters(self) -> None:
        """Test CLI with max-iters option."""
        runner = CliRunner()
        # We can't fully run the CLI without pi, but we can test option parsing
        result = runner.invoke(main, ["--help"])
        assert "--max-iters" in result.output
        assert "Maximum loop iterations" in result.output

    def test_cli_with_model(self) -> None:
        """Test CLI with model option."""
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "--model" in result.output
        assert "Primary agy model ID" in result.output

    def test_cli_with_pr_review(self) -> None:
        """Test CLI with pr-review flag."""
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "--pr-review" in result.output
        assert "Enable PR review mode" in result.output

    def test_cli_with_pr_review_introspect(self) -> None:
        """Test CLI with pr-review-introspect flag."""
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "--pr-review-introspect" in result.output
        assert "Enable PR review mode with prompt introspection" in result.output

    def test_cli_with_contextual_review(self) -> None:
        """Test CLI help exposes --contextual-review."""
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "--contextual-review" in result.output
        assert "Smart contextual review" in result.output

    def test_cli_with_full_codebase_review(self) -> None:
        """Test CLI help exposes --full-codebase-review."""
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "--full-codebase-review" in result.output
        assert "Audit the entire codebase" in result.output

    def test_cli_with_pr_threads_introspect_only(self) -> None:
        """Test CLI help exposes --pr-threads-introspect-only."""
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "--pr-threads-introspect-only" in result.output
        assert "unresolved review threads" in result.output

    def test_cli_with_improve_prompts(self) -> None:
        """Test CLI help exposes --improve-prompts."""
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "--improve-prompts" in result.output
        assert "user-owned" in result.output

    @pytest.mark.parametrize(
        ("flags", "expected_fragments"),
        [
            (
                ["--improve-prompts", "--full-codebase-review"],
                ("--improve-prompts", "--full-codebase-review"),
            ),
            (
                ["--improve-prompts", "--contextual-review"],
                ("--improve-prompts", "--contextual-review"),
            ),
            (
                ["--pr-threads-introspect-only", "--full-codebase-review"],
                ("--pr-threads-introspect-only", "--full-codebase-review"),
            ),
            (
                ["--contextual-review", "--full-codebase-review"],
                ("--contextual-review", "--full-codebase-review"),
            ),
            (
                [
                    "--improve-prompts",
                    "--contextual-review",
                    "--full-codebase-review",
                ],
                ("--improve-prompts", "--contextual-review", "--full-codebase-review"),
            ),
        ],
    )
    def test_mutually_exclusive_standalone_flags_error(
        self,
        flags: list[str],
        expected_fragments: tuple[str, ...],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Combining standalone-mode flags must exit 1 with a clear error."""
        original_paths = config.Paths

        def mock_paths(_project_root: Path | None = None) -> config.Paths:
            return original_paths(project_root=tmp_path)

        monkeypatch.setattr(config, "Paths", mock_paths)
        # Guard: if validation ever fails silently, we don't want a real run.
        monkeypatch.setattr(runner.AgyRunner, "run", lambda _self: 0)

        cli_runner = CliRunner()
        result = cli_runner.invoke(main, flags, catch_exceptions=False)

        assert result.exit_code == 1
        for fragment in expected_fragments:
            assert fragment in result.output, (
                f"error output {result.output!r} should name {fragment}"
            )

    @pytest.mark.parametrize(
        "flag",
        [
            "--full-codebase-review",
            "--pr-threads-introspect-only",
            "--contextual-review",
            "--improve-prompts",
        ],
    )
    def test_standalone_modes_skip_check_cmd(
        self,
        flag: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Standalone modes must not require or resolve a check command."""
        original_paths = config.Paths

        def mock_paths(_project_root: Path | None = None) -> config.Paths:
            return original_paths(project_root=tmp_path)

        monkeypatch.setattr(config, "Paths", mock_paths)

        # Ensure no check command is available anywhere
        monkeypatch.delenv("FDR_CHECK_CMD", raising=False)

        resolve_called = False
        original_resolve = cli_module.resolve_check_cmd

        def spy_resolve(**kwargs: object) -> str | None:
            nonlocal resolve_called
            resolve_called = True
            return original_resolve(**kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(cli_module, "resolve_check_cmd", spy_resolve)
        monkeypatch.setattr(cli_module, "validate_check_cmd_or_exit", lambda _cmd: None)

        # Mock PiRunner.run so we don't actually run anything
        monkeypatch.setattr(runner.AgyRunner, "run", lambda _self: 0)

        cli_runner = CliRunner()
        result = cli_runner.invoke(main, [flag], catch_exceptions=False)

        assert result.exit_code == 0
        assert not resolve_called, f"resolve_check_cmd should not be called with {flag}"

    def test_improve_prompts_noop_does_not_materialize_per_repo_state(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A no-op --improve-prompts run must not create <FDR_HOME>/repos/..."""
        # Mirrors the path the autouse _isolated_fdr_home fixture assigns to FDR_HOME.
        fdr_home = tmp_path / "fdr_home"

        original_paths = config.Paths

        def mock_paths(_project_root: Path | None = None) -> config.Paths:
            return original_paths(project_root=tmp_path)

        monkeypatch.setattr(config, "Paths", mock_paths)
        monkeypatch.delenv("FDR_CHECK_CMD", raising=False)

        def fail_if_runner_used(_self: object) -> int:
            pytest.fail("AgyRunner.run must not be invoked for a no-op --improve-prompts")

        monkeypatch.setattr(runner.AgyRunner, "run", fail_if_runner_used)

        cli_runner = CliRunner()
        result = cli_runner.invoke(main, ["--improve-prompts"], catch_exceptions=False)

        assert result.exit_code == 0
        assert not (fdr_home / "repos").exists(), (
            "No-op --improve-prompts must not materialize per-repo state"
        )

    def test_cli_with_archive_artifacts(self) -> None:
        """Test CLI with archive-artifacts flag."""
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "--archive-artifacts" in result.output
        assert "Archive existing artifacts" in result.output

    def test_cli_with_no_compact(self) -> None:
        """Test CLI with no-compact flag."""
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "--no-compact" in result.output
        assert "Skip automatic compaction" in result.output

    def test_cli_with_debug(self) -> None:
        """Test CLI with debug flag."""
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "--debug" in result.output
        assert "Enable debug mode" in result.output

    def test_cli_env_vars_work(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that environment variables work correctly."""
        # Set environment variables
        monkeypatch.setenv("FDR_CHECK_CMD", "make test")
        monkeypatch.setenv("FDR_MAX_ITERS", "5")
        monkeypatch.setenv("FDR_MODEL", "test-model")
        monkeypatch.setenv("FDR_PR_REVIEW", "1")
        monkeypatch.setenv("FDR_DEBUG", "1")

        # Create settings - should pick up env vars
        settings = config.Settings()
        assert settings.check_cmd == "make test"
        assert settings.max_iters == TEST_MAX_ITERS
        assert settings.model == "test-model"
        assert settings.pr_review
        assert settings.debug

    def test_cli_options_documented(self) -> None:
        """Test that CLI options are properly documented."""
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "check-cmd" in result.output
        assert "max-iters" in result.output
        assert "model" in result.output
        assert "pr-review" in result.output
        assert "preflight" in result.output


class TestCliOptions:
    """Tests for CLI option handling."""

    def test_check_cmd_short_option(self) -> None:
        """Test short option -c for check-cmd."""
        runner = CliRunner()
        result = runner.invoke(main, ["-c", "make test", "--help"])
        assert result.exit_code == 0

    def test_check_cmd_long_option(self) -> None:
        """Test long option --check-cmd."""
        runner = CliRunner()
        result = runner.invoke(main, ["--check-cmd", "make test", "--help"])
        assert result.exit_code == 0

    def test_max_iters_short_option(self) -> None:
        """Test short option -n for max-iters."""
        runner = CliRunner()
        result = runner.invoke(main, ["-n", "5", "--help"])
        assert result.exit_code == 0

    def test_max_iters_long_option(self) -> None:
        """Test long option --max-iters."""
        runner = CliRunner()
        result = runner.invoke(main, ["--max-iters", "5", "--help"])
        assert result.exit_code == 0

    def test_model_short_option(self) -> None:
        """Test short option -m for model."""
        runner = CliRunner()
        result = runner.invoke(main, ["-m", "test-model", "--help"])
        assert result.exit_code == 0

    def test_model_long_option(self) -> None:
        """Test long option --model."""
        runner = CliRunner()
        result = runner.invoke(main, ["--model", "test-model", "--help"])
        assert result.exit_code == 0

    def test_debug_short_option(self) -> None:
        """Test short option -d for debug."""
        runner = CliRunner()
        result = runner.invoke(main, ["-d", "--help"])
        assert result.exit_code == 0

    def test_debug_long_option(self) -> None:
        """Test long option --debug."""
        runner = CliRunner()
        result = runner.invoke(main, ["--debug", "--help"])
        assert result.exit_code == 0

    def test_multiple_options(self) -> None:
        """Test that multiple options can be combined."""
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "-c",
                "pytest",
                "-n",
                "5",
                "-m",
                "test-model",
                "--pr-review",
                "--debug",
                "--help",
            ],
        )
        assert result.exit_code == 0


class TestBuildCliOptions:
    """Tests for _build_cli_options function."""

    def test_all_options_set(self) -> None:
        """Test building CliOptions with all options set."""
        kwargs: CliKwargs = {
            "check_cmd": "pytest",
            "max_iters": TEST_MAX_ITERS,
            "model": "test-model",
            "max_pr_threads": TEST_MAX_PR_THREADS,
            "archive_artifacts": True,
            "no_compact": True,
            "pr_review": True,
            "pr_review_introspect": True,
            "preflight": True,
            "debug": True,
        }

        options = _build_cli_options(kwargs)

        assert options.check_cmd == "pytest"
        assert options.max_iters == TEST_MAX_ITERS
        assert options.model == "test-model"
        assert options.max_pr_threads == TEST_MAX_PR_THREADS
        assert options.archive_artifacts is True  # True when flag is set
        assert options.no_compact is True
        assert options.pr_review is True
        assert options.pr_review_introspect is True
        assert options.preflight is True
        assert options.debug is True

    def test_archive_artifacts_flag_true(self) -> None:
        """Test archive_artifacts when flag is True (uncovered line 107-108)."""
        kwargs: CliKwargs = {"archive_artifacts": True}

        options = _build_cli_options(kwargs)

        assert options.archive_artifacts is True

    def test_archive_artifacts_flag_false(self) -> None:
        """Test archive_artifacts when flag is False (not set)."""
        kwargs: CliKwargs = {"archive_artifacts": False}

        options = _build_cli_options(kwargs)

        assert options.archive_artifacts is None

    def test_no_options_set(self) -> None:
        """Test building CliOptions with no options set (all defaults)."""
        kwargs: CliKwargs = {}

        options = _build_cli_options(kwargs)

        assert options.check_cmd is None
        assert options.max_iters is None
        assert options.model is None
        assert options.max_pr_threads is None
        assert options.archive_artifacts is None
        assert options.no_compact is False
        assert options.pr_review is False
        assert options.pr_review_introspect is False
        assert options.full_codebase_review is False
        assert options.pr_threads_introspect_only is False
        assert options.preflight is False
        assert options.debug is False

    def test_contextual_review_propagates_to_settings(self) -> None:
        """--contextual-review propagates into Settings."""
        kwargs: CliKwargs = {"contextual_review": True}
        options = _build_cli_options(kwargs)
        settings = config.get_settings(options)
        assert options.contextual_review is True
        assert settings.contextual_review is True

    def test_full_codebase_review_propagates_to_settings(self) -> None:
        """--full-codebase-review propagates into Settings."""
        kwargs: CliKwargs = {"full_codebase_review": True}
        options = _build_cli_options(kwargs)
        settings = config.get_settings(options)
        assert options.full_codebase_review is True
        assert settings.full_codebase_review is True

    def test_pr_threads_introspect_only_propagates_to_settings(self) -> None:
        """--pr-threads-introspect-only propagates into Settings."""
        kwargs: CliKwargs = {"pr_threads_introspect_only": True}
        options = _build_cli_options(kwargs)
        settings = config.get_settings(options)
        assert options.pr_threads_introspect_only is True
        assert settings.pr_threads_introspect_only is True

    def test_pr_review_introspect_implies_pr_review(self) -> None:
        """Test that --pr-review-introspect flag implies --pr-review."""
        kwargs: CliKwargs = {
            "pr_review": False,
            "pr_review_introspect": True,
        }

        options = _build_cli_options(kwargs)
        settings = config.get_settings(options)

        # The flag should be set in options
        assert options.pr_review_introspect is True

        # And settings should have both flags set
        assert settings.pr_review_introspect is True
        assert settings.pr_review is True, (
            "pr_review should be True when pr_review_introspect is True"
        )

    def test_pr_review_without_introspect(self) -> None:
        """Test that --pr-review can be used without --pr-review-introspect."""
        kwargs: CliKwargs = {
            "pr_review": True,
            "pr_review_introspect": False,
        }

        options = _build_cli_options(kwargs)
        settings = config.get_settings(options)

        assert options.pr_review is True
        assert options.pr_review_introspect is False
        assert settings.pr_review is True
        assert settings.pr_review_introspect is False

    def test_partial_options(self) -> None:
        """Test building CliOptions with partial options set."""
        kwargs: CliKwargs = {
            "check_cmd": "make test",
            "max_iters": TEST_PARTIAL_MAX_ITERS,
            "no_compact": True,
        }

        options = _build_cli_options(kwargs)

        assert options.check_cmd == "make test"
        assert options.max_iters == TEST_PARTIAL_MAX_ITERS
        assert options.model is None
        assert options.max_pr_threads is None
        assert options.archive_artifacts is None
        assert options.no_compact is True
        assert options.pr_review is False
        assert options.preflight is False
        assert options.debug is False

    def test_type_conversions(self) -> None:
        """Test that type conversions work correctly."""
        kwargs: CliKwargs = {
            "check_cmd": 123,  # Wrong type, should be converted to string
            "max_iters": "5",  # Wrong type, should be converted to int
            "max_pr_threads": TEST_MAX_PR_THREADS,  # Already int, should stay int
        }

        options = _build_cli_options(kwargs)

        assert options.check_cmd == "123"
        assert options.max_iters == TEST_MAX_ITERS
        assert options.max_pr_threads == TEST_MAX_PR_THREADS


class TestCliExceptions:
    """Tests for CLI exception handling."""

    def test_keyboard_interrupt(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test CLI handles KeyboardInterrupt gracefully."""
        original_paths = config.Paths

        def mock_paths(_project_root: Path | None = None) -> config.Paths:
            return original_paths(project_root=tmp_path)

        monkeypatch.setattr(config, "Paths", mock_paths)

        # Monkeypatch PiRunner.run to raise KeyboardInterrupt
        def interrupting_run(_self: object) -> int:
            raise KeyboardInterrupt

        monkeypatch.setattr(runner.AgyRunner, "run", interrupting_run)

        cli_runner = CliRunner()
        result = cli_runner.invoke(main, catch_exceptions=False)

        # Should exit with code 130 (standard for KeyboardInterrupt)
        assert result.exit_code == KEYBOARD_INTERRUPT_EXIT_CODE
        assert "Interrupted" in result.output

    def test_generic_exception(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test CLI handles generic exceptions."""
        original_paths = config.Paths

        def mock_paths(_project_root: Path | None = None) -> config.Paths:
            return original_paths(project_root=tmp_path)

        monkeypatch.setattr(config, "Paths", mock_paths)

        # Monkeypatch PiRunner.run to raise a generic exception
        def failing_run(_self: object) -> int:
            raise RuntimeError

        monkeypatch.setattr(runner.AgyRunner, "run", failing_run)

        cli_runner = CliRunner()
        result = cli_runner.invoke(main, catch_exceptions=False)

        # Should exit with code 1 for generic errors
        assert result.exit_code == 1
        assert "Unexpected error" in result.output

    def test_debug_mode_shows_traceback(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that debug mode shows traceback for exceptions."""
        original_paths = config.Paths

        def mock_paths(_project_root: Path | None = None) -> config.Paths:
            return original_paths(project_root=tmp_path)

        monkeypatch.setattr(config, "Paths", mock_paths)

        # Monkeypatch PiRunner.run to raise a generic exception
        def failing_run(_self: object) -> int:
            raise RuntimeError

        monkeypatch.setattr(runner.AgyRunner, "run", failing_run)

        cli_runner = CliRunner()
        result = cli_runner.invoke(main, ["--debug"], catch_exceptions=False)

        # Should show traceback in debug mode
        assert result.exit_code == 1
        assert "RuntimeError" in result.output


class TestCliResolution:
    """Tests for CLI check command resolution integration."""

    def test_cli_with_check_cmd_skips_resolution(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that providing -c skips the resolution chain."""
        # Mock validate_check_cmd_or_exit to skip validation
        monkeypatch.setattr(cli_module, "validate_check_cmd_or_exit", lambda _cmd: None)

        original_paths = config.Paths

        def mock_paths(_project_root: Path | None = None) -> config.Paths:
            return original_paths(project_root=tmp_path)

        monkeypatch.setattr(config, "Paths", mock_paths)

        # Mock PiRunner.run to capture the settings.check_cmd value
        captured_check_cmd = []

        def capturing_run(self: object) -> int:
            settings = getattr(self, "settings", None)
            if settings is not None:
                captured_check_cmd.append(settings.check_cmd)
            return 0

        monkeypatch.setattr(runner.AgyRunner, "run", capturing_run)

        cli_runner = CliRunner()
        result = cli_runner.invoke(main, ["-c", "pytest"], catch_exceptions=False)

        assert result.exit_code == 0
        assert captured_check_cmd == ["pytest"]

    def test_cli_without_check_cmd_calls_resolution(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that CLI calls resolution when no check_cmd is provided."""
        # Resolve full path to git to avoid S607 (partial path security warning)
        git_path = shutil.which("git")
        if git_path is None:
            pytest.skip("git is not available; skipping integration test that requires git")

        # Initialize a git repo in tmp_path so it's isolated from actual project
        subprocess.run([git_path, "init"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(
            [git_path, "config", "user.email", "test@example.com"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [git_path, "config", "user.name", "Test User"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )

        original_paths = config.Paths

        def mock_paths(_project_root: Path | None = None) -> config.Paths:
            return original_paths(project_root=tmp_path)

        monkeypatch.setattr(config, "Paths", mock_paths)

        # Create pyproject.toml for auto-detection (without pytest config)
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'\n")

        # Create system config path directory
        system_config_dir = tmp_path / "system_config_dir"
        system_config_dir.mkdir()

        # Mock resolve_check_cmd to return a known value
        monkeypatch.setattr(
            cli_module,
            "resolve_check_cmd",
            lambda **_kwargs: "pytest",
        )

        # Mock validate_check_cmd_or_exit to skip validation
        monkeypatch.setattr(cli_module, "validate_check_cmd_or_exit", lambda _cmd: None)

        # Mock PiRunner.run to capture the settings.check_cmd value
        captured_check_cmd = []

        def capturing_run(self: object) -> int:
            settings = getattr(self, "settings", None)
            if settings is not None:
                captured_check_cmd.append(settings.check_cmd)
            return 0

        monkeypatch.setattr(runner.AgyRunner, "run", capturing_run)

        cli_runner = CliRunner()
        result = cli_runner.invoke(main, catch_exceptions=False)

        assert result.exit_code == 0
        # Should have called resolve_check_cmd and set check_cmd
        assert captured_check_cmd == ["pytest"]

    def test_value_error_handling(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test CLI handles ValueError with appropriate error message (lines 107-108)."""
        original_paths = config.Paths

        def mock_paths(_project_root: Path | None = None) -> config.Paths:
            return original_paths(project_root=tmp_path)

        monkeypatch.setattr(config, "Paths", mock_paths)

        # Monkeypatch _run_main to raise ValueError
        error_msg = "Invalid configuration"

        def failing_run(_options: object) -> int:
            raise ValueError(error_msg)

        monkeypatch.setattr(cli_module, "_run_main", failing_run)

        cli_runner = CliRunner()
        result = cli_runner.invoke(main, catch_exceptions=False)

        # Should exit with code 1 and show error message
        assert result.exit_code == 1
        assert "Error: Invalid configuration" in result.output
