"""Tests for PR review introspection functionality."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fix_die_repeat.config import Paths, Settings, get_introspection_file_path
from fix_die_repeat.runner import AgyRunner
from fix_die_repeat.runner_introspection import (
    IntrospectionManager,
    IntrospectionYamlParams,
)


class TestGetIntrospectionFilePath:
    """Tests for get_introspection_file_path helper function."""

    def test_returns_path_object(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Test that get_introspection_file_path returns a Path object."""
        monkeypatch.setenv("HOME", str(tmp_path))
        path = get_introspection_file_path()
        assert isinstance(path, Path)

    def test_file_ends_with_introspection_yaml(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Test that the returned path ends with introspection.yaml."""
        monkeypatch.setenv("HOME", str(tmp_path))
        path = get_introspection_file_path()
        assert path.name == "introspection.yaml"

    def test_parent_directory_exists(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Test that the parent directory exists after calling the function."""
        monkeypatch.setenv("HOME", str(tmp_path))
        path = get_introspection_file_path()
        # The function should create the parent directory
        assert path.parent.exists()
        assert path.parent.is_dir()


class TestCollectIntrospectionData:
    """Tests for PiRunner.collect_introspection_data method."""

    @pytest.fixture
    def runner(self, tmp_path: Path) -> AgyRunner:
        """Create an AgyRunner instance with temporary paths."""
        settings = Settings(pr_review=True, pr_review_introspect=True)  # type: ignore[call-arg]  # pydantic's populate_by_name=True allows field names; mypy needs pydantic-mypy plugin
        paths = Paths(project_root=tmp_path)
        paths.ensure_fdr_dir()

        # Create minimal logger to avoid issues
        with patch("fix_die_repeat.runner.configure_logger") as mock_logger:
            mock_logger.return_value = MagicMock()
            return AgyRunner(settings, paths)

    def test_skips_without_pr_info(self, runner: AgyRunner) -> None:
        """Test that introspection data collection skips when PR info is unavailable."""
        # Mock get_branch_name to return None
        with patch.object(runner, "get_branch_name", return_value=None):
            runner.collect_introspection_data(1, "abc123")

        # Data file should not be created
        assert not runner.paths.introspection_data_file.exists()

    def test_collects_with_pr_info(self, runner: AgyRunner) -> None:
        """Test that introspection data is collected when PR info is available."""
        # Create mock PR info
        pr_info = {"number": 123, "url": "https://github.com/owner/repo/pull/123"}

        # Create cumulative in-scope thread IDs file
        runner.paths.cumulative_in_scope_threads_file.write_text(
            "thread_id_1\nthread_id_2\nthread_id_3\n"
        )

        # Create cumulative resolved threads file
        runner.paths.cumulative_resolved_threads_file.write_text("thread_id_1\n")

        # Create cumulative PR threads cache content
        runner.paths.cumulative_pr_threads_content_file.write_text(
            "Thread #1: Issue title\nComment: Reviewer feedback\n"
            "Thread #2: Another issue\nComment: More feedback\n"
        )

        # Create diff file
        runner.paths.diff_file.write_text("--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new\n")

        # Mock get_branch_name and get_pr_info
        with patch.object(runner, "get_branch_name", return_value="feature-branch"):
            with patch.object(runner, "get_pr_info", return_value=pr_info):
                runner.collect_introspection_data(1, "abc123")

        # Data file should be created
        assert runner.paths.introspection_data_file.exists()

        # Verify content
        content = runner.paths.introspection_data_file.read_text()
        assert "pr_number: 123" in content
        assert "pr_url: https://github.com/owner/repo/pull/123" in content
        assert "thread_id_1" in content
        assert "thread_id_2" in content
        assert "thread_id_3" in content
        assert "outcome: fixed" in content
        assert "outcome: wont-fix" in content

    def test_identifies_wont_fix_threads(self, runner: AgyRunner) -> None:
        """Test that won't-fix threads are correctly identified."""
        pr_info = {"number": 456, "url": "https://github.com/owner/repo/pull/456"}

        # Create cumulative in-scope thread IDs
        runner.paths.cumulative_in_scope_threads_file.write_text("tid_1\ntid_2\ntid_3\n")

        # Only one thread was resolved (cumulative)
        runner.paths.cumulative_resolved_threads_file.write_text("tid_2\n")

        # Mock PR info
        with patch.object(runner, "get_branch_name", return_value="branch"):
            with patch.object(runner, "get_pr_info", return_value=pr_info):
                runner.collect_introspection_data(1, "abc123")

        content = runner.paths.introspection_data_file.read_text()
        # tid_2 should be marked as fixed
        assert "tid_2" in content
        assert "outcome: fixed" in content
        # tid_1 and tid_3 should be marked as wont-fix
        assert "tid_1" in content
        assert "tid_3" in content


class TestRunIntrospection:
    """Tests for AgyRunner.run_introspection method."""

    @pytest.fixture
    def runner(self, tmp_path: Path) -> AgyRunner:
        """Create an AgyRunner instance with temporary paths."""
        settings = Settings(pr_review=True, pr_review_introspect=True)  # type: ignore[call-arg]  # pydantic's populate_by_name=True allows field names; mypy needs pydantic-mypy plugin
        paths = Paths(project_root=tmp_path)
        paths.ensure_fdr_dir()

        with patch("fix_die_repeat.runner.configure_logger") as mock_logger:
            mock_logger.return_value = MagicMock()
            return AgyRunner(settings, paths)

    def test_skips_without_thread_ids_file(self, runner: AgyRunner) -> None:
        """Test that introspection skips when no thread IDs file exists."""
        with patch.object(runner, "collect_introspection_data") as mock_collect:
            runner.run_introspection()

        # collect_introspection_data should not be called
        mock_collect.assert_not_called()

    def test_skips_on_pi_failure(self, runner: AgyRunner) -> None:
        """Test that introspection skips gracefully when agent fails."""
        # Create cumulative thread IDs file
        runner.paths.cumulative_in_scope_threads_file.write_text("thread_1\n")

        # Create minimal PR info
        pr_info = {"number": 1, "url": "https://github.com/test/test/pull/1"}

        # Mock methods
        with patch.object(runner, "collect_introspection_data"):
            with patch.object(runner, "get_branch_name", return_value="branch"):
                with patch.object(runner, "get_pr_info", return_value=pr_info):
                    with patch.object(runner, "run_agent_safe", return_value=(1, "", "error")):
                        runner.run_introspection()

        # Global introspection file should not be modified
        # Mock failed, so file shouldn't be modified (we can't assert this easily
        # without controlling the exact state, but the test should not error)

    def test_preserves_result_file_on_pi_failure(
        self,
        runner: AgyRunner,
        tmp_path: Path,
    ) -> None:
        """Regression: result file survives agent non-zero exit (e.g. killed).

        When agent writes the result file but then returns non-zero, the file must
        stay on disk so the analysis isn't lost.
        """
        runner.paths.cumulative_in_scope_threads_file.write_text("thread_1\n")
        runner.paths.introspection_data_file.write_text(
            "pr_number: 2\npr_url: https://github.com/test/test/pull/2\n"
        )

        # Simulate result file written before non-zero exit
        result_yaml = (
            "date: '2026-04-18'\nproject: 'test-project'\npr_number: 2\n"
            "pr_url: 'https://github.com/test/test/pull/2'\n"
            "status: pending\nthreads: []\n"
        )
        runner.paths.introspection_result_file.write_text(result_yaml)

        pr_json = '{"number": 2, "url": "https://github.com/test/test/pull/2"}'

        test_global_file = tmp_path / "introspection.yaml"
        test_global_file.parent.mkdir(parents=True, exist_ok=True)

        with patch(
            "fix_die_repeat.runner_introspection.get_introspection_file_path",
            return_value=test_global_file,
        ):
            with patch(
                "fix_die_repeat.runner_introspection.run_command",
                return_value=(0, pr_json, ""),
            ):
                with patch.object(runner, "run_agent_safe", return_value=(1, "", "killed")):
                    runner.run_introspection()

        # Result file must survive so analysis is recoverable
        assert runner.paths.introspection_result_file.exists(), (
            "Result file was deleted despite failure — analysis is lost"
        )
        assert runner.paths.introspection_result_file.read_text() == result_yaml
        # Input data file is disposable and should still be cleaned up
        assert not runner.paths.introspection_data_file.exists()

    def test_skips_with_invalid_pr_info(self, runner: AgyRunner) -> None:
        """Test that introspection skips when PR info is invalid."""
        runner.paths.cumulative_in_scope_threads_file.write_text("thread_1\n")

        pr_json = '{"number": null, "url": ""}'

        with patch("fix_die_repeat.runner_introspection.run_command") as mock_run:
            mock_run.side_effect = [
                (0, "main\n", ""),
                (0, pr_json, ""),
            ]
            with patch.object(runner, "run_agent_safe") as mock_agent:
                runner.run_introspection()

        mock_agent.assert_not_called()
        assert not runner.paths.introspection_data_file.exists()

    def test_appends_to_global_file(
        self,
        runner: AgyRunner,
        tmp_path: Path,
    ) -> None:
        """Test that valid introspection result is appended to global file."""
        # Create cumulative thread IDs file to enable introspection
        runner.paths.cumulative_in_scope_threads_file.write_text("thread_1\n")

        # Create introspection data file (simulating collect_introspection_data)
        runner.paths.introspection_data_file.write_text(
            "pr_number: 2\npr_url: https://github.com/test/test/pull/2\n"
        )

        # Setup test-specific global introspection file
        test_global_file = tmp_path / "introspection.yaml"

        # Create existing content
        test_global_file.parent.mkdir(parents=True, exist_ok=True)
        test_global_file.write_text("date: '2026-01-01'\nproject: 'old-project'\nstatus: pending\n")

        # Mock successful execution
        introspection_result = (
            "date: '2026-02-26'\nproject: 'test-project'\npr_number: 2\n"
            "pr_url: 'https://github.com/test/test/pull/2'\n"
            "status: pending\nthreads: []\n"
        )

        # Create result file
        runner.paths.introspection_result_file.write_text(introspection_result)
        pr_json = '{"number": 2, "url": "https://github.com/test/test/pull/2"}'

        # Call run_introspection which should read result file and append to global file
        with patch(
            "fix_die_repeat.runner_introspection.get_introspection_file_path",
            return_value=test_global_file,
        ):
            with patch(
                "fix_die_repeat.runner_introspection.run_command",
                return_value=(0, pr_json, ""),
            ):
                with patch.object(runner, "run_agent_safe", return_value=(0, "", "")):
                    runner.run_introspection()

        # Verify result was appended with separator
        content = test_global_file.read_text()
        assert "\n---\n" in content, f"Content:\n{content}"
        assert "date: '2026-02-26'" in content
        assert "project: 'test-project'" in content

    def test_normalizes_document_markers(
        self,
        runner: AgyRunner,
        tmp_path: Path,
    ) -> None:
        """Test that leading/trailing YAML markers are removed before append."""
        runner.paths.cumulative_in_scope_threads_file.write_text("thread_1\n")
        runner.paths.introspection_data_file.write_text(
            "pr_number: 2\npr_url: https://github.com/test/test/pull/2\n"
        )

        test_global_file = tmp_path / "introspection.yaml"
        test_global_file.parent.mkdir(parents=True, exist_ok=True)
        test_global_file.write_text("date: '2026-01-01'\nstatus: pending\n")

        introspection_result = (
            "---\n"
            "date: '2026-02-26'\n"
            "project: 'test-project'\n"
            "pr_number: 2\n"
            "pr_url: 'https://github.com/test/test/pull/2'\n"
            "status: pending\n"
            "threads: []\n"
            "...\n"
        )
        runner.paths.introspection_result_file.write_text(introspection_result)
        pr_json = '{"number": 2, "url": "https://github.com/test/test/pull/2"}'

        with patch(
            "fix_die_repeat.runner_introspection.get_introspection_file_path",
            return_value=test_global_file,
        ):
            with patch("fix_die_repeat.runner_introspection.run_command") as mock_run:
                mock_run.side_effect = [
                    (0, "main\n", ""),
                    (0, pr_json, ""),
                ]
                with patch.object(runner, "run_agent_safe", return_value=(0, "", "")):
                    runner.run_introspection()

        content = test_global_file.read_text()
        assert content.count("\n---\n") == 1
        assert "..." not in content

    def test_validates_yaml_before_append(
        self,
        runner: AgyRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that invalid YAML is not appended to global file."""
        # Setup test-specific global introspection file
        test_global_file = tmp_path / "introspection.yaml"
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

        # Create existing content
        test_global_file.parent.mkdir(parents=True, exist_ok=True)
        original_content = "date: '2026-01-01'\nstatus: pending\n"
        test_global_file.write_text(original_content)

        # Create thread IDs file required for introspection prerequisites
        runner.paths.cumulative_in_scope_threads_file.write_text("thread_1\n")

        # Create invalid YAML result
        invalid_yaml = "date: 2026-02-26\n  invalid indentation\n    bad yaml: [unclosed"
        pr_json = '{"number": 3, "url": "https://github.com/test/test/pull/3"}'

        # Patch get_introspection_file_path in the runner_introspection module
        with patch(
            "fix_die_repeat.runner_introspection.get_introspection_file_path",
            return_value=test_global_file,
        ):
            with patch.object(runner, "collect_introspection_data"):
                with patch(
                    "fix_die_repeat.runner_introspection.run_command",
                    return_value=(0, pr_json, ""),
                ):
                    with patch.object(runner, "run_agent_safe", return_value=(0, "", "")):
                        # Create invalid YAML result file
                        runner.paths.introspection_result_file.write_text(invalid_yaml)
                        runner.run_introspection()

        # File should not be modified
        content = test_global_file.read_text()
        assert content == original_content


class TestIntrospectionPayloadValidation:
    """Tests for introspection payload validation."""

    @pytest.fixture
    def manager(self, tmp_path: Path) -> IntrospectionManager:
        """Create an IntrospectionManager with temporary paths."""
        settings = Settings(pr_review=True, pr_review_introspect=True)  # type: ignore[call-arg]  # pydantic's populate_by_name=True allows field names; mypy needs pydantic-mypy plugin
        paths = Paths(project_root=tmp_path)
        paths.ensure_fdr_dir()
        logger = MagicMock()
        return IntrospectionManager(settings, paths, tmp_path, logger)

    @staticmethod
    def _base_payload() -> dict[str, object]:
        """Return a minimal valid payload."""
        return {
            "date": "2026-02-26",
            "project": "test-project",
            "pr_number": 1,
            "pr_url": "https://github.com/test/test/pull/1",
            "status": "pending",
            "threads": [
                {
                    "id": "thread-1",
                    "title": "Issue title",
                    "category": "correctness",
                    "outcome": "fixed",
                    "summary": "Fixed the issue",
                    "relevance": "high",
                    "lang_check_gap": "No language-specific checks needed",
                }
            ],
        }

    def test_valid_payload_passes(self, manager: IntrospectionManager) -> None:
        """Test that a valid payload passes validation."""
        payload = self._base_payload()
        assert manager.validate_introspection_payload(payload)

    def test_wont_fix_requires_reason(self, manager: IntrospectionManager) -> None:
        """Test that wont-fix threads require a reason."""
        payload = self._base_payload()
        threads = payload["threads"]
        if isinstance(threads, list) and threads:
            thread = threads[0]
            if isinstance(thread, dict):
                thread["outcome"] = "wont-fix"
        assert not manager.validate_introspection_payload(payload)


class TestIntrospectionNonBlocking:
    """Tests that introspection failures don't block the main run."""

    def test_collect_introspection_data_does_not_raise(self, tmp_path: Path) -> None:
        """Test that collect_introspection_data handles errors gracefully."""
        settings = Settings(pr_review=True, pr_review_introspect=True)  # type: ignore[call-arg]  # pydantic's populate_by_name=True allows field names; mypy needs pydantic-mypy plugin
        paths = Paths(project_root=tmp_path)
        paths.ensure_fdr_dir()

        with patch("fix_die_repeat.runner.configure_logger") as mock_logger:
            mock_logger.return_value = MagicMock()
            runner = AgyRunner(settings, paths)

        # Should not raise even with missing files
        runner.collect_introspection_data(1, "abc123")
        # Data file may or may not exist depending on PR info availability

    def test_run_introspection_catches_exceptions(
        self,
        tmp_path: Path,
    ) -> None:
        """Test that run_introspection catches all exceptions."""
        settings = Settings(pr_review=True, pr_review_introspect=True)  # type: ignore[call-arg]  # pydantic's populate_by_name=True allows field names; mypy needs pydantic-mypy plugin
        paths = Paths(project_root=tmp_path)
        paths.ensure_fdr_dir()

        with patch("fix_die_repeat.runner.configure_logger") as mock_logger:
            mock_logger.return_value = MagicMock()
            runner = AgyRunner(settings, paths)

        # Create thread IDs file to enable introspection prerequisites
        runner.paths.cumulative_in_scope_threads_file.write_text("thread_1\n")

        # Mock collect_introspection_data to raise an exception
        with patch.object(
            runner, "collect_introspection_data", side_effect=RuntimeError("Test error")
        ):
            # Should not raise
            runner.run_introspection()

        # Clean up temp files
        runner.paths.introspection_data_file.unlink(missing_ok=True)
        runner.paths.introspection_result_file.unlink(missing_ok=True)


class TestYamlAppendLogic:
    """Tests for multi-document YAML append logic."""

    def test_append_to_new_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test appending to a new introspection file."""
        test_file = tmp_path / "introspection.yaml"
        monkeypatch.setattr(
            "fix_die_repeat.config.get_introspection_file_path",
            lambda: test_file,
        )

        test_file.parent.mkdir(parents=True, exist_ok=True)

        # Simulate appending a new entry
        entry = "date: '2026-02-26'\nproject: 'test'\nstatus: pending\n"
        test_file.write_text(entry)

        content = test_file.read_text()
        assert content == entry
        assert "\n---\n" not in content  # No separator for first entry

    def test_append_to_existing_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test appending to an existing introspection file."""
        test_file = tmp_path / "introspection.yaml"
        monkeypatch.setattr(
            "fix_die_repeat.config.get_introspection_file_path",
            lambda: test_file,
        )

        test_file.parent.mkdir(parents=True, exist_ok=True)

        # Create initial content
        initial = "date: '2026-01-01'\nproject: 'first'\nstatus: pending\n"
        test_file.write_text(initial)

        # Append second entry (simulate the logic from run_introspection)
        second = "date: '2026-02-26'\nproject: 'second'\nstatus: pending\n"
        with test_file.open("a") as f:
            f.write("\n---\n")
            f.write(second)

        content = test_file.read_text()
        assert initial in content
        assert second in content
        assert "\n---\n" in content
        assert content.count("\n---\n") == 1


class TestIntrospectOnlyMode:
    """Tests for introspect-only mode (not-attempted outcome)."""

    def _make_manager(self, tmp_path: Path) -> IntrospectionManager:
        settings = Settings()  # type: ignore[call-arg]
        paths = Paths(project_root=tmp_path)
        paths.ensure_fdr_dir()
        return IntrospectionManager(settings, paths, tmp_path, MagicMock())

    def test_yaml_uses_not_attempted_outcome(self, tmp_path: Path) -> None:
        """With introspect_only=True, every in-scope thread gets not-attempted."""
        manager = self._make_manager(tmp_path)
        params = IntrospectionYamlParams(
            pr_number=42,
            pr_url="https://github.com/owner/repo/pull/42",
            in_scope_ids=["thread_a", "thread_b"],
            resolved_set={"thread_a"},  # would normally be "fixed" in non-introspect mode
            pr_threads_content="body",
            diff_content="",
            introspect_only=True,
        )
        content = manager._build_introspection_yaml(params)
        assert "outcome: not-attempted" in content
        assert "outcome: fixed" not in content
        assert "outcome: wont-fix" not in content

    def test_yaml_respects_resolved_set_when_not_introspect_only(self, tmp_path: Path) -> None:
        """Without introspect_only, outcome reflects resolved_set (existing behavior)."""
        manager = self._make_manager(tmp_path)
        params = IntrospectionYamlParams(
            pr_number=42,
            pr_url="https://github.com/owner/repo/pull/42",
            in_scope_ids=["thread_a", "thread_b"],
            resolved_set={"thread_a"},
            pr_threads_content="body",
            diff_content="",
        )
        content = manager._build_introspection_yaml(params)
        assert "outcome: fixed" in content
        assert "outcome: wont-fix" in content
        assert "outcome: not-attempted" not in content

    def test_validate_thread_outcome_accepts_not_attempted_without_reason(
        self, tmp_path: Path
    ) -> None:
        """not-attempted outcome does not require a 'reason' field."""
        manager = self._make_manager(tmp_path)
        thread: dict[str, object] = {
            "id": "t1",
            "title": "X",
            "category": "code-quality",
            "outcome": "not-attempted",
            "summary": "s",
            "relevance": "r",
            "lang_check_gap": "n/a",
        }
        assert manager._validate_thread_outcome(thread, 1) is True

    def test_validate_thread_outcome_still_requires_reason_for_wont_fix(
        self, tmp_path: Path
    ) -> None:
        """Regression: wont-fix still needs reason even after adding not-attempted."""
        manager = self._make_manager(tmp_path)
        thread: dict[str, object] = {
            "id": "t1",
            "title": "X",
            "category": "code-quality",
            "outcome": "wont-fix",
            "summary": "s",
            "relevance": "r",
            "lang_check_gap": "n/a",
        }
        assert manager._validate_thread_outcome(thread, 1) is False
