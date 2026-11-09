"""Tests for runner module."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fix_die_repeat import runner_introspection as runner_introspection_module
from fix_die_repeat.runner import AgyRunner
from fix_die_repeat.runner_introspection import (  # Testing private class is intentional
    _FileLock,
)
from tests.conftest import FAKE_TEMPLATE_CONTEXT

# Constants for runner test values
TEST_AGENT_DELAY_SECONDS = 2
EXPECTED_AGENT_INVOCATION_COUNT = 2
EMERGENCY_COMPACT_LINES = 100
REGULAR_COMPACT_LINES = 50
FILTERED_CHECKS_LOG_SMALL_LINES = 100
FILTERED_CHECKS_LOG_MAX_LINES = 300
TEST_PR_NUMBER = 123
MIN_SEEK_CALLS = 2

_FAKE_TEMPLATE_CONTEXT = FAKE_TEMPLATE_CONTEXT


class TestBeforeAgentCall:
    """Tests for before_agent_call method."""

    def test_first_call_no_delay(self, tmp_path: Path) -> None:
        """Test that first call doesn't add delay."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.fdr_dir = tmp_path
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.agent_invocation_count = 0

        with patch("fix_die_repeat.runner.time.sleep") as mock_sleep:
            runner.before_agent_call()
            assert not mock_sleep.called
            assert runner.agent_invocation_count == 1

    def test_subsequent_call_adds_delay(self, tmp_path: Path) -> None:
        """Test that subsequent calls add delay."""
        settings = MagicMock()
        settings.agent_sequential_delay_seconds = TEST_AGENT_DELAY_SECONDS
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.fdr_dir = tmp_path
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.agent_invocation_count = 1

        with patch("fix_die_repeat.runner.time.sleep") as mock_sleep:
            runner.before_agent_call()
            mock_sleep.assert_called_once_with(TEST_AGENT_DELAY_SECONDS)
            assert runner.agent_invocation_count == EXPECTED_AGENT_INVOCATION_COUNT


class TestGenerateDiff:
    """Tests for generate_diff method."""

    def test_generate_diff_with_start_sha(self, tmp_path: Path) -> None:
        """Test generating diff with start SHA."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.project_root = tmp_path
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.start_sha = "abc123"

        with patch("fix_die_repeat.runner.run_command") as mock_run:
            mock_run.return_value = (0, "diff content", "")

            result = runner.generate_diff()

            assert result == "diff content"
            mock_run.assert_called_once()

    def test_generate_diff_without_start_sha(self, tmp_path: Path) -> None:
        """Test generating diff without start SHA."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.project_root = tmp_path
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.start_sha = ""

        with patch("fix_die_repeat.runner.run_command") as mock_run:
            mock_run.return_value = (0, "diff content", "")

            result = runner.generate_diff()

            assert result == "diff content"
            mock_run.assert_called_once()


class TestCreatePseudoDiff:
    """Tests for create_pseudo_diff method."""

    def test_create_pseudo_diff_text_file(self, tmp_path: Path) -> None:
        """Test creating pseudo-diff for text file."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.project_root = tmp_path
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths

        # Create a text file
        test_file = tmp_path / "new_file.txt"
        test_file.write_text("line1\nline2\nline3")

        result = runner.create_pseudo_diff("new_file.txt")

        assert "diff --git a/new_file.txt" in result
        assert "new file mode 100644" in result
        assert "+line1" in result
        assert "+line2" in result
        assert "+line3" in result

    def test_create_pseudo_diff_binary_file(self, tmp_path: Path) -> None:
        """Test creating pseudo-diff for binary file."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.project_root = tmp_path
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        # Create a file that will be detected as binary by `file` command
        test_file = tmp_path / "binary.dat"
        test_file.write_bytes(b"\x00\x01\x02\x03\x04")

        with patch("fix_die_repeat.runner.run_command") as mock_run:
            mock_run.return_value = (0, "binary.dat: data", "")

            result = runner.create_pseudo_diff("binary.dat")

            assert "Binary file" in result or "binary.dat" in result


class TestAppendReviewEntry:
    """Tests for append_review_entry method."""

    def test_append_review_entry_with_content(self, tmp_path: Path) -> None:
        """Test appending review entry with content."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.fdr_dir = tmp_path
        paths.review_file = tmp_path / "review.md"
        paths.review_current_file = tmp_path / "review_current.md"
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.iteration = 1

        # Create review current with content
        paths.review_current_file.write_text("# Issues\n\n[CRITICAL] Bug found")

        runner.append_review_entry(runner.iteration)

        # Check that content was appended
        assert paths.review_file.exists()
        content = paths.review_file.read_text()
        assert "Iteration 1" in content
        assert "[CRITICAL] Bug found" in content

    def test_append_review_entry_no_content(self, tmp_path: Path) -> None:
        """Test appending review entry when no content."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.fdr_dir = tmp_path
        paths.review_file = tmp_path / "review.md"
        paths.review_current_file = tmp_path / "review_current.md"
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.iteration = 2

        # Create review current as empty
        paths.review_current_file.write_text("")

        runner.append_review_entry(runner.iteration)

        # Check that "No issues found" was written
        assert paths.review_file.exists()
        content = paths.review_file.read_text()
        assert "Iteration 2" in content
        assert "No issues found" in content


class TestRunFixAttempt:
    """Tests for run_fix_attempt method."""

    def test_run_fix_attempt_oscillation_warning(self, tmp_path: Path) -> None:
        """Test fix attempt with oscillation warning."""
        settings = MagicMock()
        settings.max_iters = 10
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.fdr_dir = tmp_path
        paths.project_root = tmp_path
        paths.checks_filtered_log = tmp_path / "checks_filtered.log"
        paths.checks_log = tmp_path / "checks.log"
        paths.review_file = tmp_path / "review.md"
        paths.build_history_file = tmp_path / "build_history.md"
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.iteration = 1
        runner.logger = MagicMock()
        runner.before_agent_call = MagicMock()  # type: ignore[method-assign]
        runner.run_agent_safe = MagicMock(return_value=(0, "", ""))  # type: ignore[method-assign]
        runner.check_oscillation = MagicMock(return_value="Oscillation detected!")  # type: ignore[method-assign]
        runner.filter_checks_log = MagicMock()  # type: ignore[method-assign]

        # Create checks log
        paths.checks_log.write_text("error output")
        paths.checks_filtered_log.write_text("filtered output")

        _ = runner.run_fix_attempt(
            1,
            [],
            "push",
            "",
            "",
        )

        # Should have called run_agent_safe
        assert runner.run_agent_safe.called

    def test_run_fix_attempt_pi_failure(self, tmp_path: Path) -> None:
        """Test fix attempt when pi fails."""
        settings = MagicMock()
        settings.max_iters = 10
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.fdr_dir = tmp_path
        paths.project_root = tmp_path
        paths.checks_filtered_log = tmp_path / "checks_filtered.log"
        paths.checks_log = tmp_path / "checks.log"
        paths.review_file = tmp_path / "review.md"
        paths.build_history_file = tmp_path / "build_history.md"
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.iteration = 1
        runner.logger = MagicMock()
        runner.before_agent_call = MagicMock()  # type: ignore[method-assign]
        runner.run_agent_safe = MagicMock(return_value=(1, "", "error"))  # type: ignore[method-assign]
        runner.check_oscillation = MagicMock(return_value=None)  # type: ignore[method-assign]
        runner.filter_checks_log = MagicMock()  # type: ignore[method-assign]

        # Create checks log
        paths.checks_log.write_text("error output")
        paths.checks_filtered_log.write_text("filtered output")

        with patch("fix_die_repeat.runner.run_command") as mock_git:
            mock_git.return_value = (0, "", "")

            _ = runner.run_fix_attempt(
                1,
                [],
                "push",
                "",
                "",
            )

            # Should have logged about pi failure
            assert runner.logger.info.called

    def test_run_fix_attempt_with_review_history(self, tmp_path: Path) -> None:
        """Test fix attempt with review history."""
        settings = MagicMock()
        settings.max_iters = 10
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.fdr_dir = tmp_path
        paths.project_root = tmp_path
        paths.checks_filtered_log = tmp_path / "checks_filtered.log"
        paths.checks_log = tmp_path / "checks.log"
        paths.review_file = tmp_path / "review.md"
        paths.build_history_file = tmp_path / "build_history.md"
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.iteration = 1
        runner.logger = MagicMock()
        runner.before_agent_call = MagicMock()  # type: ignore[method-assign]
        runner.run_agent_safe = MagicMock(return_value=(0, "", ""))  # type: ignore[method-assign]
        runner.check_oscillation = MagicMock(return_value=None)  # type: ignore[method-assign]
        runner.filter_checks_log = MagicMock()  # type: ignore[method-assign]

        # Create files
        paths.checks_log.write_text("error output")
        paths.checks_filtered_log.write_text("filtered output")
        paths.review_file.write_text("# Previous review")

        _ = runner.run_fix_attempt(
            1,
            [],
            "push",
            "",
            "",
        )

        # Should have called run_agent_safe
        assert runner.run_agent_safe.called

    def test_run_fix_attempt_with_build_history(self, tmp_path: Path) -> None:
        """Test fix attempt with build history."""
        settings = MagicMock()
        settings.max_iters = 10
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.fdr_dir = tmp_path
        paths.project_root = tmp_path
        paths.checks_filtered_log = tmp_path / "checks_filtered.log"
        paths.checks_log = tmp_path / "checks.log"
        paths.review_file = tmp_path / "review.md"
        paths.build_history_file = tmp_path / "build_history.md"
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.iteration = 1
        runner.logger = MagicMock()
        runner.before_agent_call = MagicMock()  # type: ignore[method-assign]
        runner.run_agent_safe = MagicMock(return_value=(0, "", ""))  # type: ignore[method-assign]
        runner.check_oscillation = MagicMock(return_value=None)  # type: ignore[method-assign]
        runner.filter_checks_log = MagicMock()  # type: ignore[method-assign]

        # Create files
        paths.checks_log.write_text("error output")
        paths.checks_filtered_log.write_text("filtered output")
        paths.build_history_file.write_text("# Build history")

        _ = runner.run_fix_attempt(
            1,
            [],
            "push",
            "",
            "",
        )

        # Should have called run_agent_safe
        assert runner.run_agent_safe.called

    def test_run_fix_attempt_push_mode(self, tmp_path: Path) -> None:
        """Test fix attempt in push mode."""
        settings = MagicMock()
        settings.max_iters = 10
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.fdr_dir = tmp_path
        paths.project_root = tmp_path
        paths.checks_filtered_log = tmp_path / "checks_filtered.log"
        paths.checks_log = tmp_path / "checks.log"
        paths.review_file = tmp_path / "review.md"
        paths.build_history_file = tmp_path / "build_history.md"
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.iteration = 1
        runner.logger = MagicMock()
        runner.before_agent_call = MagicMock()  # type: ignore[method-assign]
        runner.run_agent_safe = MagicMock(return_value=(0, "", ""))  # type: ignore[method-assign]
        runner.check_oscillation = MagicMock(return_value=None)  # type: ignore[method-assign]
        runner.filter_checks_log = MagicMock()  # type: ignore[method-assign]

        # Create files
        paths.checks_log.write_text("error output")
        paths.checks_filtered_log.write_text("filtered output")

        # Create changed files
        file1 = tmp_path / "file1.py"
        file1.write_text("content")
        file2 = tmp_path / "file2.py"
        file2.write_text("content")

        _ = runner.run_fix_attempt(
            1,
            ["file1.py", "file2.py"],
            "push",
            "",
            "",
        )

        # Should have called run_agent_safe with file attachments
        assert runner.run_agent_safe.called

    def test_run_fix_attempt_pull_mode(self, tmp_path: Path) -> None:
        """Test fix attempt in pull mode."""
        settings = MagicMock()
        settings.max_iters = 10
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.fdr_dir = tmp_path
        paths.project_root = tmp_path
        paths.checks_filtered_log = tmp_path / "checks_filtered.log"
        paths.checks_log = tmp_path / "checks.log"
        paths.review_file = tmp_path / "review.md"
        paths.build_history_file = tmp_path / "build_history.md"
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.iteration = 1
        runner.logger = MagicMock()
        runner.before_agent_call = MagicMock()  # type: ignore[method-assign]
        runner.run_agent_safe = MagicMock(return_value=(0, "", ""))  # type: ignore[method-assign]
        runner.check_oscillation = MagicMock(return_value=None)  # type: ignore[method-assign]
        runner.filter_checks_log = MagicMock()  # type: ignore[method-assign]

        # Create files
        paths.checks_log.write_text("error output")
        paths.checks_filtered_log.write_text("filtered output")

        large_context_list = "The following files have changed:\n- file1.py\n- file2.py"

        _ = runner.run_fix_attempt(
            1,
            [],
            "pull",
            large_context_list,
            "",
        )

        # Should have called run_agent_safe
        assert runner.run_agent_safe.called


class TestPrepareFixContext:
    """Tests for prepare_fix_context method."""

    def test_prepare_fix_context_no_files(self, tmp_path: Path) -> None:
        """Test preparing fix context with no changed files."""
        settings = MagicMock()
        settings.auto_attach_threshold = 1000000
        settings.large_file_lines = 2000
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.project_root = tmp_path
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        with (
            patch("fix_die_repeat.runner.get_changed_files") as mock_get,
            patch("fix_die_repeat.runner.detect_large_files") as mock_detect,
        ):
            mock_get.return_value = []
            mock_detect.return_value = ""

            changed_files, context_mode, large_context_list, large_file_warning = (
                runner.prepare_fix_context()
            )

            assert changed_files == []
            assert context_mode == "push"
            assert large_context_list == ""
            assert large_file_warning == ""

    def test_prepare_fix_context_push_mode(self, tmp_path: Path) -> None:
        """Test preparing fix context in push mode."""
        settings = MagicMock()
        settings.auto_attach_threshold = 1000000
        settings.large_file_lines = 2000
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.project_root = tmp_path
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        with (
            patch("fix_die_repeat.runner.get_changed_files") as mock_get,
            patch("fix_die_repeat.runner.detect_large_files") as mock_detect,
            patch("fix_die_repeat.runner.get_file_size") as mock_size,
        ):
            mock_get.return_value = ["file1.py", "file2.py"]
            mock_detect.return_value = ""
            mock_size.return_value = 50000  # 50KB each

            changed_files, context_mode, large_context_list, large_file_warning = (
                runner.prepare_fix_context()
            )

            assert changed_files == ["file1.py", "file2.py"]
            assert context_mode == "push"
            assert large_context_list == ""
            assert large_file_warning == ""

    def test_prepare_fix_context_pull_mode(self, tmp_path: Path) -> None:
        """Test preparing fix context in pull mode."""
        settings = MagicMock()
        settings.auto_attach_threshold = 50000  # 50KB threshold
        settings.large_file_lines = 2000
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.project_root = tmp_path
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        with (
            patch("fix_die_repeat.runner.get_changed_files") as mock_get,
            patch("fix_die_repeat.runner.detect_large_files") as mock_detect,
            patch("fix_die_repeat.runner.get_file_size") as mock_size,
        ):
            mock_get.return_value = ["file1.py", "file2.py"]
            mock_detect.return_value = ""
            mock_size.return_value = 40000  # 40KB each, 80KB total

            changed_files, context_mode, large_context_list, _large_file_warning = (
                runner.prepare_fix_context()
            )

            assert changed_files == ["file1.py", "file2.py"]
            assert context_mode == "pull"
            assert "too large to pre-load" in large_context_list
            assert "file1.py" in large_context_list
            assert "file2.py" in large_context_list


class TestFormatPrThreads:
    """Tests for format_pr_threads method."""

    def test_format_single_thread(self, tmp_path: Path) -> None:
        """Test formatting a single PR thread."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.fdr_dir = tmp_path
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths

        threads = [
            {
                "id": "thread1",
                "path": "file.py",
                "line": 42,
                "comments": {
                    "nodes": [
                        {"author": {"login": "user1"}, "body": "Fix this bug"},
                    ],
                },
            },
        ]

        result = runner.format_pr_threads(threads, 123, "https://github.com/test/repo/pull/123")

        assert "Thread #1" in result
        assert "thread1" in result
        assert "file.py" in result
        assert "42" in result
        assert "[user1]:" in result
        assert "Fix this bug" in result
        assert "123" in result

    def test_format_thread_without_line(self, tmp_path: Path) -> None:
        """Test formatting a thread without line number."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.fdr_dir = tmp_path
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths

        threads = [
            {
                "id": "thread1",
                "path": "file.py",
                "comments": {
                    "nodes": [
                        {"author": {"login": "user1"}, "body": "Comment"},
                    ],
                },
            },
        ]

        result = runner.format_pr_threads(threads, 123, "https://github.com/test/repo/pull/123")

        assert "Thread #1" in result
        assert "Line:" not in result

    def test_format_thread_without_author(self, tmp_path: Path) -> None:
        """Test formatting a thread without author."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.fdr_dir = tmp_path
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths

        threads = [
            {
                "id": "thread1",
                "path": "file.py",
                "line": 42,
                "comments": {
                    "nodes": [
                        {"body": "Anonymous comment"},
                    ],
                },
            },
        ]

        result = runner.format_pr_threads(threads, 123, "https://github.com/test/repo/pull/123")

        assert "Thread #1" in result
        assert "[unknown]:" in result

    def test_format_thread_without_path(self, tmp_path: Path) -> None:
        """Test formatting a thread without file path."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.fdr_dir = tmp_path
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths

        threads = [
            {
                "id": "thread1",
                "line": 42,
                "comments": {
                    "nodes": [
                        {"author": {"login": "user1"}, "body": "Comment"},
                    ],
                },
            },
        ]

        result = runner.format_pr_threads(threads, 123, "https://github.com/test/repo/pull/123")

        assert "Thread #1" in result
        assert "N/A" in result

    def test_format_multiple_threads(self, tmp_path: Path) -> None:
        """Test formatting multiple threads."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.fdr_dir = tmp_path
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths

        threads = [
            {
                "id": "thread1",
                "path": "file1.py",
                "line": 42,
                "comments": {"nodes": [{"author": {"login": "user1"}, "body": "Comment 1"}]},
            },
            {
                "id": "thread2",
                "path": "file2.py",
                "line": 99,
                "comments": {"nodes": [{"author": {"login": "user2"}, "body": "Comment 2"}]},
            },
        ]

        result = runner.format_pr_threads(threads, 123, "https://github.com/test/repo/pull/123")

        assert "Thread #1" in result
        assert "Thread #2" in result
        assert "thread1" in result
        assert "thread2" in result


class TestBuildReviewPrompt:
    """Tests for build_review_prompt method."""

    def test_build_review_prompt_pull_mode(self, tmp_path: Path) -> None:
        """Test building review prompt in pull mode."""
        settings = MagicMock()
        settings.auto_attach_threshold = 100000
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.fdr_dir = tmp_path
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        attachments: list[Path] = []
        result = runner.build_review_prompt(200000, attachments)

        assert "too large to attach" in result
        assert "MUST use the 'read_file' tool" in result
        assert len(attachments) == 0  # Should not append diff file

    def test_build_review_prompt_push_mode(self, tmp_path: Path) -> None:
        """Test building review prompt in push mode."""
        settings = MagicMock()
        settings.auto_attach_threshold = 200000
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.fdr_dir = tmp_path
        paths.agent_log = tmp_path / "agy.log"

        # Create actual diff file
        diff_file = tmp_path / "changes.diff"
        paths.diff_file = diff_file

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        attachments: list[Path] = []
        result = runner.build_review_prompt(100000, attachments)

        assert "changes.diff" in result
        assert len(attachments) == 1
        assert attachments[0] == diff_file


class TestRunAgentReview:
    """Tests for run_agent_review method."""

    def test_run_agent_review_push_mode(self, tmp_path: Path) -> None:
        """Test running agent review in push mode."""
        settings = MagicMock()
        settings.auto_attach_threshold = 200000
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.fdr_dir = tmp_path
        paths.agent_log = tmp_path / "agy.log"
        paths.review_file = tmp_path / "review.md"
        paths.diff_file = tmp_path / "changes.diff"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()
        runner.before_agent_call = MagicMock()  # type: ignore[method-assign]
        runner.run_agent_safe = MagicMock(return_value=(0, "", ""))  # type: ignore[method-assign]

        # Create diff file
        paths.diff_file.write_text("diff content")

        runner.run_agent_review(100000, runner.run_agent_safe)  # type: ignore[arg-type]

        # Check that run_agent_safe was called
        assert runner.run_agent_safe.called

    def test_run_agent_review_pull_mode(self, tmp_path: Path) -> None:
        """Test running agent review in pull mode."""
        settings = MagicMock()
        settings.auto_attach_threshold = 100000
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.fdr_dir = tmp_path
        paths.agent_log = tmp_path / "agy.log"
        paths.review_file = tmp_path / "review.md"
        paths.diff_file = tmp_path / "changes.diff"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()
        runner.before_agent_call = MagicMock()  # type: ignore[method-assign]
        runner.run_agent_safe = MagicMock(return_value=(0, "", ""))  # type: ignore[method-assign]

        runner.run_agent_review(200000, runner.run_agent_safe)  # type: ignore[arg-type]

        # Check that run_agent_safe was called
        assert runner.run_agent_safe.called

    def test_run_agent_review_with_history(self, tmp_path: Path) -> None:
        """Test running agent review with existing history."""
        settings = MagicMock()
        settings.auto_attach_threshold = 200000
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.fdr_dir = tmp_path
        paths.agent_log = tmp_path / "agy.log"
        paths.review_file = tmp_path / "review.md"
        paths.diff_file = tmp_path / "changes.diff"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()
        runner.before_agent_call = MagicMock()  # type: ignore[method-assign]
        runner.run_agent_safe = MagicMock(return_value=(0, "", ""))  # type: ignore[method-assign]

        # Create review history
        paths.review_file.write_text("# Previous review\n\nIssues found.")

        runner.run_agent_review(100000, runner.run_agent_safe)  # type: ignore[arg-type]

        # Check that run_agent_safe was called
        assert runner.run_agent_safe.called


class TestGetBranchName:
    """Tests for get_branch_name method."""

    def test_get_branch_name_success(self, tmp_path: Path) -> None:
        """Test getting branch name successfully."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.project_root = tmp_path
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths

        with patch("fix_die_repeat.runner.run_command") as mock_run:
            mock_run.return_value = (0, "main\n", "")

            result = runner.get_branch_name()

            assert result == "main"

    def test_get_branch_name_failure(self, tmp_path: Path) -> None:
        """Test getting branch name on failure."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.project_root = tmp_path
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths

        with patch("fix_die_repeat.runner.run_command") as mock_run:
            mock_run.return_value = (1, "", "error")

            result = runner.get_branch_name()

            assert result is None

    def test_get_branch_name_empty_response(self, tmp_path: Path) -> None:
        """Test getting branch name with empty response."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.project_root = tmp_path
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths

        with patch("fix_die_repeat.runner.run_command") as mock_run:
            mock_run.return_value = (0, "\n", "")

            result = runner.get_branch_name()

            assert result is None


class TestGetPrInfo:
    """Tests for get_pr_info method."""

    def test_get_pr_info_success(self, tmp_path: Path) -> None:
        """Test getting PR info successfully."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.project_root = tmp_path
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths

        pr_json = """{
            "number": 123,
            "url": "https://github.com/test/repo/pull/123",
            "headRepository": {"name": "repo"},
            "headRepositoryOwner": {"login": "owner"}
        }"""

        with patch("fix_die_repeat.runner.run_command") as mock_run:
            mock_run.return_value = (0, pr_json, "")

            result = runner.get_pr_info("main")

            assert result is not None
            assert result["number"] == TEST_PR_NUMBER
            assert result["url"] == "https://github.com/test/repo/pull/123"
            assert result["repo_owner"] == "owner"
            assert result["repo_name"] == "repo"

    def test_get_pr_info_failure(self, tmp_path: Path) -> None:
        """Test getting PR info on failure."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.project_root = tmp_path
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths

        with patch("fix_die_repeat.runner.run_command") as mock_run:
            mock_run.return_value = (1, "", "error")

            result = runner.get_pr_info("main")

            assert result is None


class TestCheckPrThreadsCache:
    """Tests for check_pr_threads_cache method."""

    def test_cache_hit(self, tmp_path: Path) -> None:
        """Test cache hit scenario."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.fdr_dir = tmp_path
        paths.pr_threads_cache = tmp_path / "pr_threads_cache"
        paths.pr_threads_hash_file = tmp_path / "pr_threads_hash"
        paths.review_current_file = tmp_path / "review_current.md"
        paths.pr_thread_ids_file = tmp_path / "pr_thread_ids"
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        # Setup cache
        paths.pr_threads_hash_file.write_text("owner/repo/123")
        paths.pr_threads_cache.write_text("--- Thread #1 ---\nID: thread1\n")
        paths.pr_thread_ids_file.write_text("thread1\n")
        paths.review_current_file.write_text("")

        result = runner.check_pr_threads_cache("owner/repo/123")

        assert result is True
        assert runner.logger.info.called

    def test_cache_miss_hash_mismatch(self, tmp_path: Path) -> None:
        """Test cache miss due to hash mismatch."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.fdr_dir = tmp_path
        paths.pr_threads_cache = tmp_path / "pr_threads_cache"
        paths.pr_threads_hash_file = tmp_path / "pr_threads_hash"
        paths.review_current_file = tmp_path / "review_current.md"
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        # Setup cache with different key
        paths.pr_threads_hash_file.write_text("owner/repo/456")
        paths.pr_threads_cache.write_text("--- Thread #1 ---\nID: thread1\n")

        result = runner.check_pr_threads_cache("owner/repo/123")

        assert result is False

    def test_cache_miss_files_missing(self, tmp_path: Path) -> None:
        """Test cache miss when cache files don't exist."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.fdr_dir = tmp_path
        paths.pr_threads_cache = tmp_path / "pr_threads_cache"
        paths.pr_threads_hash_file = tmp_path / "pr_threads_hash"
        paths.review_current_file = tmp_path / "review_current.md"
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths

        result = runner.check_pr_threads_cache("owner/repo/123")

        assert result is False


class TestFetchPrThreadsGql:
    """Tests for fetch_pr_threads_gql method."""

    def test_fetch_pr_threads_success(self, tmp_path: Path) -> None:
        """Test successful PR thread fetch."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.fdr_dir = tmp_path
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        response = """{
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [
                                {
                                    "isResolved": false,
                                    "id": "thread1",
                                    "path": "file.py",
                                    "line": 42,
                                    "comments": {
                                        "nodes": [
                                            {"author": {"login": "user1"}, "body": "Comment"}
                                        ]
                                    }
                                }
                            ]
                        }
                    }
                }
            }
        }"""

        with patch("fix_die_repeat.runner.run_command") as mock_run:
            mock_run.return_value = (0, response, "")

            result = runner.fetch_pr_threads_gql("owner", "repo", 123)

            assert result is not None
            assert isinstance(result, list)
            assert len(result) == 1
            assert result[0]["id"] == "thread1"

    def test_fetch_pr_threads_command_failure(self, tmp_path: Path) -> None:
        """Test PR thread fetch on command failure."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.fdr_dir = tmp_path
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths

        with patch("fix_die_repeat.runner.run_command") as mock_run:
            mock_run.return_value = (1, "", "error")

            result = runner.fetch_pr_threads_gql("owner", "repo", 123)

            assert result is None

    def test_fetch_pr_threads_json_decode_error(self, tmp_path: Path) -> None:
        """Test PR thread fetch with invalid JSON."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.fdr_dir = tmp_path
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        with patch("fix_die_repeat.runner.run_command") as mock_run:
            mock_run.return_value = (0, "invalid json", "")

            result = runner.fetch_pr_threads_gql("owner", "repo", 123)

            assert result is None
            assert runner.logger.exception.called


class TestHasNoReviewIssues:
    """Tests for has_no_review_issues method."""

    def test_no_issues_marker(self) -> None:
        """Test that NO_ISSUES marker returns True."""
        # Create a minimal AgyRunner instance
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths

        # Explicit marker
        assert runner.has_no_review_issues("NO_ISSUES") is True
        assert runner.has_no_review_issues("NO_ISSUES\n") is True
        assert runner.has_no_review_issues("  NO_ISSUES  ") is True

    def test_empty_file_warns(self) -> None:
        """Test that empty file returns True but logs warning."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        # Empty content
        assert runner.has_no_review_issues("") is True
        runner.logger.warning.assert_called_once()
        assert "expected 'NO_ISSUES' marker" in runner.logger.warning.call_args[0][0]

    def test_whitespace_only_warns(self) -> None:
        """Test that whitespace-only content returns True but logs warning."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        # Whitespace only
        assert runner.has_no_review_issues("   \n  \n") is True
        runner.logger.warning.assert_called_once()
        assert "expected 'NO_ISSUES' marker" in runner.logger.warning.call_args[0][0]

    def test_legacy_no_critical_issues(self) -> None:
        """Test legacy 'no critical issues found' text is handled."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        # Legacy format with only that text
        assert runner.has_no_review_issues("No critical issues found.") is True

        # Legacy format with headers only
        assert runner.has_no_review_issues("# Review\nNo critical issues found.") is True

    def test_legacy_with_actual_issues(self) -> None:
        """Test that legacy format with actual issues returns False."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        # Legacy format but has actual content
        content = "No critical issues found.\n\n[CRITICAL] Bug in line 42"
        assert runner.has_no_review_issues(content) is False

    def test_has_issues_returns_false(self) -> None:
        """Test that issues content returns False."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        # Actual issues
        assert runner.has_no_review_issues("[CRITICAL] Bug found") is False
        assert runner.has_no_review_issues("# Issues\n[CRITICAL] Bug\n[NIT] Style") is False
        assert runner.has_no_review_issues("This is a bug report") is False


class TestRunChecks:
    """Tests for run_checks method."""

    def test_run_checks_success(self, tmp_path: Path) -> None:
        """Test running checks successfully."""
        settings = MagicMock()
        settings.check_cmd = "echo 'checks passed'"
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.project_root = tmp_path
        paths.checks_log = tmp_path / "checks.log"
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        returncode, output = runner.run_checks()

        assert returncode == 0
        assert "checks passed" in output
        assert paths.checks_log.exists()

    def test_run_checks_failure(self, tmp_path: Path) -> None:
        """Test running checks that fail."""
        settings = MagicMock()
        settings.check_cmd = f'{sys.executable} -c "import sys; sys.exit(1)"'
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.project_root = tmp_path
        paths.checks_log = tmp_path / "checks.log"
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        returncode, _output = runner.run_checks()

        assert returncode == 1
        assert paths.checks_log.exists()


class TestFetchPrThreads:
    """Tests for fetch_pr_threads method."""

    def test_fetch_pr_threads_no_branch(self, tmp_path: Path) -> None:
        """Test fetching PR threads when not on a branch."""
        settings = MagicMock()
        settings.pr_review = True
        settings.max_pr_threads = 5
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.fdr_dir = tmp_path
        paths.project_root = tmp_path
        paths.review_current_file = tmp_path / "review_current.md"
        paths.pr_threads_cache = tmp_path / "pr_threads_cache"
        paths.pr_threads_hash_file = tmp_path / "pr_threads_hash"
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        with patch.object(runner, "get_branch_name", return_value=None):
            runner.fetch_pr_threads()

            # Should log error about not on a branch
            assert runner.logger.error.called

    def test_fetch_pr_threads_no_gh_auth(self, tmp_path: Path) -> None:
        """Test fetching PR threads when gh not authenticated."""
        settings = MagicMock()
        settings.pr_review = True
        settings.max_pr_threads = 5
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.fdr_dir = tmp_path
        paths.project_root = tmp_path
        paths.review_current_file = tmp_path / "review_current.md"
        paths.pr_threads_cache = tmp_path / "pr_threads_cache"
        paths.pr_threads_hash_file = tmp_path / "pr_threads_hash"
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        with (
            patch.object(runner, "get_branch_name", return_value="main"),
            patch("fix_die_repeat.runner.run_command") as mock_run,
        ):
            # gh auth status fails
            mock_run.return_value = (1, "", "error")

            runner.fetch_pr_threads()

            # Should log error about gh auth
            assert runner.logger.error.called

    def test_fetch_pr_threads_no_pr_found(self, tmp_path: Path) -> None:
        """Test fetching PR threads when no PR is found."""
        settings = MagicMock()
        settings.pr_review = True
        settings.max_pr_threads = 5
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.fdr_dir = tmp_path
        paths.project_root = tmp_path
        paths.review_current_file = tmp_path / "review_current.md"
        paths.pr_threads_cache = tmp_path / "pr_threads_cache"
        paths.pr_threads_hash_file = tmp_path / "pr_threads_hash"
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        with (
            patch.object(runner, "get_branch_name", return_value="main"),
            patch("fix_die_repeat.runner.run_command") as mock_run,
            patch.object(runner, "get_pr_info", return_value=None),
        ):
            # gh auth succeeds
            mock_run.return_value = (0, "", "")

            runner.fetch_pr_threads()

            # Should log info about no PR found
            assert runner.logger.info.called

    def test_fetch_pr_threads_no_unresolved(self, tmp_path: Path) -> None:
        """Test fetching PR threads with no unresolved threads."""
        settings = MagicMock()
        settings.pr_review = True
        settings.max_pr_threads = 5
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.fdr_dir = tmp_path
        paths.project_root = tmp_path
        paths.review_current_file = tmp_path / "review_current.md"
        paths.pr_threads_cache = tmp_path / "pr_threads_cache"
        paths.pr_threads_hash_file = tmp_path / "pr_threads_hash"
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        pr_info = {
            "number": 123,
            "url": "https://github.com/test/repo/pull/123",
            "repo_owner": "owner",
            "repo_name": "repo",
        }

        with (
            patch.object(runner, "get_branch_name", return_value="main"),
            patch("fix_die_repeat.runner.run_command") as mock_run,
            patch.object(runner, "get_pr_info", return_value=pr_info),
            patch.object(runner, "check_pr_threads_cache", return_value=False),
            patch.object(
                runner,
                "fetch_pr_threads_gql",
                return_value=[
                    {"isResolved": True, "id": "thread1"},
                    {"isResolved": True, "id": "thread2"},
                ],
            ),
        ):
            mock_run.return_value = (0, "", "")

            runner.fetch_pr_threads()

            # Should log info about no unresolved threads
            assert runner.logger.info.called

    def test_fetch_pr_threads_with_unresolved(self, tmp_path: Path) -> None:
        """Test fetching PR threads with unresolved threads."""
        settings = MagicMock()
        settings.pr_review = True
        settings.max_pr_threads = 5
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.fdr_dir = tmp_path
        paths.project_root = tmp_path
        paths.review_current_file = tmp_path / "review_current.md"
        paths.pr_threads_cache = tmp_path / "pr_threads_cache"
        paths.pr_threads_hash_file = tmp_path / "pr_threads_hash"
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        pr_info = {
            "number": 123,
            "url": "https://github.com/test/repo/pull/123",
            "repo_owner": "owner",
            "repo_name": "repo",
        }

        with (
            patch.object(runner, "get_branch_name", return_value="main"),
            patch("fix_die_repeat.runner.run_command") as mock_run,
            patch.object(runner, "get_pr_info", return_value=pr_info),
            patch.object(runner, "check_pr_threads_cache", return_value=False),
            patch.object(
                runner,
                "fetch_pr_threads_gql",
                return_value=[
                    {
                        "isResolved": False,
                        "id": "thread1",
                        "path": "file.py",
                        "line": 42,
                        "comments": {
                            "nodes": [
                                {"author": {"login": "user1"}, "body": "Fix this"},
                            ],
                        },
                    },
                ],
            ),
        ):
            mock_run.return_value = (0, "", "")

            runner.fetch_pr_threads()

            # Should have written to review_current_file
            assert paths.review_current_file.exists()
            # Should log about found threads
            assert runner.logger.info.called

    def test_fetch_pr_threads_cache_hit(self, tmp_path: Path) -> None:
        """Test fetching PR threads with cache hit."""
        settings = MagicMock()
        settings.pr_review = True
        settings.max_pr_threads = 5
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.fdr_dir = tmp_path
        paths.project_root = tmp_path
        paths.review_current_file = tmp_path / "review_current.md"
        paths.pr_threads_cache = tmp_path / "pr_threads_cache"
        paths.pr_threads_hash_file = tmp_path / "pr_threads_hash"
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        pr_info = {
            "number": 123,
            "url": "https://github.com/test/repo/pull/123",
            "repo_owner": "owner",
            "repo_name": "repo",
        }

        with (
            patch.object(runner, "get_branch_name", return_value="main"),
            patch("fix_die_repeat.runner.run_command") as mock_run,
            patch.object(runner, "get_pr_info", return_value=pr_info),
            patch.object(runner, "check_pr_threads_cache", return_value=True),
        ):
            mock_run.return_value = (0, "", "")

            runner.fetch_pr_threads()

            # Should log about using cache
            assert runner.logger.info.called


class TestCompleteSuccess:
    """Tests for complete_success method."""

    def test_complete_success(self, tmp_path: Path) -> None:
        """Test completing the run successfully."""
        settings = MagicMock()
        settings.ntfy_enabled = False
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.fdr_dir = tmp_path
        paths.review_file = tmp_path / "review.md"
        paths.review_current_file = tmp_path / "review_current.md"
        paths.start_sha_file = tmp_path / "start_sha"
        paths.project_root = tmp_path
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.iteration = 1
        runner.script_start_time = 0
        runner.session_log = tmp_path / "session.log"
        runner.logger = MagicMock()

        # Create files
        paths.review_current_file.write_text("content")
        paths.start_sha_file.write_text("abc123")

        with (
            patch("fix_die_repeat.runner.play_completion_sound"),
            patch("fix_die_repeat.runner.format_duration") as mock_format,
        ):
            mock_format.return_value = "0s"
            result = runner.complete_success()

            # Check that the method returns 0
            assert result == 0
            # Check that temporary files were cleaned up
            assert not paths.review_current_file.exists()
            assert not paths.start_sha_file.exists()

    def test_complete_success_with_ntfy(self, tmp_path: Path) -> None:
        """Test completing the run with ntfy notification."""
        settings = MagicMock()
        settings.ntfy_enabled = True
        settings.ntfy_url = "http://localhost:2586"
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.fdr_dir = tmp_path
        paths.project_root = tmp_path
        paths.review_file = tmp_path / "review.md"
        paths.review_current_file = tmp_path / "review_current.md"
        paths.start_sha_file = tmp_path / "start_sha"
        paths.agent_log = tmp_path / "agy.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.iteration = 1
        runner.script_start_time = 330  # 5 min 30 sec
        runner.session_log = tmp_path / "session.log"
        runner.logger = MagicMock()

        # Create files
        paths.review_current_file.write_text("content")
        paths.start_sha_file.write_text("abc123")

        with (
            patch("fix_die_repeat.runner.play_completion_sound"),
            patch("fix_die_repeat.runner.send_ntfy_notification") as mock_ntfy,
            patch("fix_die_repeat.runner.format_duration") as mock_format,
        ):
            mock_format.return_value = "5m 30s"
            result = runner.complete_success()

            # Check that the method returns 0
            assert result == 0
            # Check that ntfy notification was sent with correct parameters
            mock_ntfy.assert_called_once_with(
                exit_code=0,
                duration_str="5m 30s",
                repo_name=tmp_path.name,
                ntfy_url="http://localhost:2586",
                logger=runner.logger,
            )


class TestFileLock:
    """Tests for _FileLock context manager."""

    def test_file_lock_acquires_and_releases_on_unix(self, tmp_path: Path) -> None:
        """Test that _FileLock acquires and releases lock on Unix systems."""
        # Create a test file
        test_file = tmp_path / "test_lock.txt"
        test_file.write_text("test content")

        with test_file.open() as f:
            with patch("fix_die_repeat.runner_introspection.fcntl") as mock_fcntl:
                file_lock = _FileLock(f)

                with file_lock:
                    # Verify lock was acquired (flock called with LOCK_EX)
                    mock_fcntl.flock.assert_called_once_with(f.fileno(), mock_fcntl.LOCK_EX)

                # Verify lock was released (flock called with LOCK_UN)
                mock_fcntl.flock.assert_called_with(f.fileno(), mock_fcntl.LOCK_UN)

    def test_file_lock_acquires_and_releases_on_windows(self, tmp_path: Path) -> None:
        """Test that _FileLock acquires and releases lock on Windows systems."""
        # Create a test file
        test_file = tmp_path / "test_lock.txt"
        test_file.write_text("test content")

        # Mock msvcrt module for Windows platform
        mock_msvcrt = MagicMock()
        mock_msvcrt.LK_LOCK = 0
        mock_msvcrt.LK_UNLCK = 1

        # Save original platform value
        original_platform = sys.platform

        try:
            # Set platform to Windows
            sys.platform = "win32"

            # Inject msvcrt into the runner_introspection module namespace
            # (This is intentional: we're simulating Windows behavior on a non-Windows platform)
            runner_introspection_module.msvcrt = mock_msvcrt  # type: ignore[attr-defined]

            with test_file.open() as f:
                # Wrap f.seek so we can assert it is called with position 0 before unlocking
                with patch.object(f, "seek", wraps=f.seek) as mock_seek:
                    file_lock = _FileLock(f)

                    with file_lock:
                        # Verify lock was acquired (locking called with LK_LOCK)
                        mock_msvcrt.locking.assert_called_once_with(
                            f.fileno(), mock_msvcrt.LK_LOCK, 65535
                        )

                    # Verify lock was released (locking called with LK_UNLCK)
                    mock_msvcrt.locking.assert_called_with(f.fileno(), mock_msvcrt.LK_UNLCK, 65535)
                    # Verify that seek was called at least twice (before lock and unlock)
                    assert mock_seek.call_count >= MIN_SEEK_CALLS
                    mock_seek.assert_any_call(0)
        finally:
            # Restore original platform
            sys.platform = original_platform
            # Clean up injected msvcrt
            if hasattr(runner_introspection_module, "msvcrt"):
                delattr(runner_introspection_module, "msvcrt")

    def test_file_lock_returns_self_on_enter(self, tmp_path: Path) -> None:
        """Test that _FileLock __enter__ returns self."""
        test_file = tmp_path / "test_lock.txt"
        test_file.write_text("test content")

        with test_file.open() as f:
            with patch("fix_die_repeat.runner_introspection.fcntl"):
                file_lock = _FileLock(f)

                with file_lock as acquired_lock:
                    # __enter__ should return self
                    assert acquired_lock is file_lock

    def test_file_lock_handles_exception_during_context(self, tmp_path: Path) -> None:
        """Test that _FileLock releases lock even when exception occurs."""
        test_file = tmp_path / "test_lock.txt"
        test_file.write_text("test content")

        with test_file.open() as f:
            with patch("fix_die_repeat.runner_introspection.fcntl") as mock_fcntl:
                file_lock = _FileLock(f)
                test_exception = ValueError("Test exception")

                with pytest.raises(ValueError, match="Test exception"):
                    with file_lock:
                        raise test_exception

                # Verify lock was still released despite exception
                mock_fcntl.flock.assert_called_with(f.fileno(), mock_fcntl.LOCK_UN)


class TestRunFullCodebaseReviewOnce:
    """Tests for the single-pass full-codebase review entrypoint on AgyRunner."""

    def _build_runner(
        self,
        tmp_path: Path,
    ) -> tuple[AgyRunner, MagicMock, MagicMock]:
        settings = MagicMock()
        settings.max_iters = 5
        settings.ntfy_enabled = False
        settings.full_codebase_review = True
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.fdr_dir = tmp_path
        paths.project_root = tmp_path
        paths.review_file = tmp_path / "review.md"
        paths.review_current_file = tmp_path / "review_current.md"
        paths.start_sha_file = tmp_path / ".start_sha"
        paths.agent_log = tmp_path / "agy.log"

        review_manager = MagicMock()
        logger = MagicMock()
        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.iteration = 0
        runner.logger = logger
        runner.script_start_time = 0
        runner.session_log = tmp_path / "session.log"
        runner.artifact_manager = MagicMock()
        runner.review_manager = review_manager
        runner.run_agent_safe = MagicMock(return_value=(0, "", ""))  # type: ignore[method-assign]
        return runner, review_manager, logger

    def test_runs_exactly_one_pass_and_prints_findings(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Single pass runs review manager once, prints findings to stdout, returns success."""
        runner, review_manager, _logger = self._build_runner(tmp_path)

        def fake_review(_iteration: int, _callback: object) -> None:
            runner.paths.review_current_file.write_text(
                "[CRITICAL] boom at file.py:1\n[NIT] style issue\n",
            )
            runner.paths.review_file.write_text(
                "[CRITICAL] boom at file.py:1\n[NIT] style issue\n",
            )

        review_manager.run_full_codebase_review.side_effect = fake_review
        review_manager.has_no_review_issues.return_value = False

        result = runner.run_full_codebase_review_once()

        assert result == 0
        assert review_manager.run_full_codebase_review.call_count == 1
        # Findings should have been printed to stdout (no log timestamp prefix)
        captured = capsys.readouterr()
        assert "===== Full-codebase review findings =====" in captured.out
        assert "[CRITICAL] boom at file.py:1" in captured.out
        assert "[NIT] style issue" in captured.out
        # And must NOT go through the logger (which would prefix timestamps)
        assert "[fdr]" not in captured.out

    def test_no_issues_path_still_returns_success(self, tmp_path: Path) -> None:
        """When pi reports NO_ISSUES, single pass still completes successfully."""
        runner, review_manager, logger = self._build_runner(tmp_path)

        def fake_review(_iteration: int, _callback: object) -> None:
            runner.paths.review_current_file.write_text("NO_ISSUES")
            runner.paths.review_file.write_text("NO_ISSUES\n")

        review_manager.run_full_codebase_review.side_effect = fake_review
        review_manager.has_no_review_issues.return_value = True

        result = runner.run_full_codebase_review_once()

        assert result == 0
        assert review_manager.run_full_codebase_review.call_count == 1
        logged = " ".join(str(call) for call in logger.info.call_args_list)
        assert "No critical issues found" in logged

    def test_no_review_file_created_is_treated_as_no_issues(self, tmp_path: Path) -> None:
        """If pi didn't create review_current, the runner falls back to NO_ISSUES."""
        runner, review_manager, _logger = self._build_runner(tmp_path)

        def fake_review(_iteration: int, _callback: object) -> None:
            # Simulate pi failing to produce review_current.md
            runner.paths.review_current_file.unlink(missing_ok=True)

        review_manager.run_full_codebase_review.side_effect = fake_review
        review_manager.has_no_review_issues.return_value = True

        result = runner.run_full_codebase_review_once()

        assert result == 0
        assert review_manager.run_full_codebase_review.call_count == 1


class TestRunContextualReviewOnce:
    """Tests for the single-pass contextual review entrypoint on AgyRunner."""

    def _build_runner(
        self,
        tmp_path: Path,
    ) -> tuple[AgyRunner, MagicMock, MagicMock]:
        settings = MagicMock()
        settings.max_iters = 5
        settings.ntfy_enabled = False
        settings.contextual_review = True
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.fdr_dir = tmp_path
        paths.project_root = tmp_path
        paths.review_file = tmp_path / "review.md"
        paths.review_current_file = tmp_path / "review_current.md"
        paths.start_sha_file = tmp_path / ".start_sha"
        paths.agent_log = tmp_path / "agy.log"

        review_manager = MagicMock()
        logger = MagicMock()
        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.iteration = 0
        runner.logger = logger
        runner.script_start_time = 0
        runner.session_log = tmp_path / "session.log"
        runner.artifact_manager = MagicMock()
        runner.review_manager = review_manager
        runner.run_agent_safe = MagicMock(return_value=(0, "", ""))  # type: ignore[method-assign]
        return runner, review_manager, logger

    def test_runs_exactly_one_pass_and_prints_findings(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Single pass runs review manager once, prints findings to stdout."""
        runner, review_manager, _logger = self._build_runner(tmp_path)

        def fake_review(_iteration: int, _callback: object) -> None:
            runner.paths.review_current_file.write_text(
                "[CRITICAL] boom at file.py:1\n[NIT] style issue\n",
            )
            runner.paths.review_file.write_text(
                "[CRITICAL] boom at file.py:1\n[NIT] style issue\n",
            )

        review_manager.run_contextual_review.side_effect = fake_review
        review_manager.has_no_review_issues.return_value = False

        result = runner.run_contextual_review_once()

        assert result == 0
        assert review_manager.run_contextual_review.call_count == 1
        captured = capsys.readouterr()
        assert "===== Contextual review findings =====" in captured.out
        assert "[CRITICAL] boom at file.py:1" in captured.out
        assert "[NIT] style issue" in captured.out

    def test_no_issues_path_returns_success(self, tmp_path: Path) -> None:
        """When pi reports NO_ISSUES, single pass completes successfully."""
        runner, review_manager, logger = self._build_runner(tmp_path)

        def fake_review(_iteration: int, _callback: object) -> None:
            runner.paths.review_current_file.write_text("NO_ISSUES")
            runner.paths.review_file.write_text("NO_ISSUES\n")

        review_manager.run_contextual_review.side_effect = fake_review
        review_manager.has_no_review_issues.return_value = True

        result = runner.run_contextual_review_once()

        assert result == 0
        assert review_manager.run_contextual_review.call_count == 1
        logged = " ".join(str(call) for call in logger.info.call_args_list)
        assert "No critical issues found" in logged

    def test_dispatch_priority_over_full_codebase(self, tmp_path: Path) -> None:
        """contextual_review dispatch takes priority over full_codebase_review."""
        runner, review_manager, _logger = self._build_runner(tmp_path)
        runner.settings.pr_threads_introspect_only = False
        runner.settings.improve_prompts = False
        runner.settings.full_codebase_review = True

        def fake_review(_iteration: int, _callback: object) -> None:
            runner.paths.review_current_file.write_text("NO_ISSUES")
            runner.paths.review_file.write_text("NO_ISSUES\n")

        review_manager.run_contextual_review.side_effect = fake_review
        review_manager.has_no_review_issues.return_value = True

        with patch.object(runner, "setup_run"):
            result = runner.run()

        assert result == 0
        review_manager.run_contextual_review.assert_called_once()
        review_manager.run_full_codebase_review.assert_not_called()


class TestPrThreadsIntrospectOnly:
    """Tests for the PR-threads introspect-only runner entrypoint."""

    def test_passes_empty_start_sha_to_introspection(self, tmp_path: Path) -> None:
        """introspect-only must pass empty start_sha.

        Prevents _read_diff_content from falling back to `git diff {start_sha}`
        and leaking uncommitted changes into the introspection YAML.
        """
        paths = MagicMock()
        paths.template_context.return_value = _FAKE_TEMPLATE_CONTEXT
        paths.fdr_dir = tmp_path
        paths.project_root = tmp_path
        paths.diff_file = tmp_path / "changes.diff"

        pr_manager = MagicMock()
        introspection_manager = MagicMock()
        logger = MagicMock()

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = MagicMock()
        runner.paths = paths
        runner.iteration = 0
        runner.logger = logger
        runner.start_sha = "abc123"
        runner.run_agent_safe = MagicMock(return_value=(0, "", ""))  # type: ignore[method-assign]

        with (
            patch.object(runner, "_get_pr_manager", return_value=pr_manager),
            patch.object(runner, "_get_introspection_manager", return_value=introspection_manager),
            patch.object(
                runner,
                "_fetch_unresolved_pr_threads",
                return_value=(MagicMock(), [{"id": "t1"}]),
            ),
            patch.object(runner, "_write_introspect_only_inputs"),
        ):
            result = runner.run_pr_threads_introspect_only()

        assert result == 0
        introspection_manager.run_introspection.assert_called_once()
        call_args = introspection_manager.run_introspection.call_args
        # Signature: (iteration, start_sha, run_pi_callback, *, introspect_only=...)
        assert call_args.args[1] == ""
        assert call_args.kwargs.get("introspect_only") is True
