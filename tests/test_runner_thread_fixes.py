"""Regression tests for PR review thread fixes in runner.py."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from fix_die_repeat.runner import AgyRunner
from tests.conftest import FAKE_TEMPLATE_CONTEXT

TEST_ITERATION = 1
TEST_MAX_ITERS = 10
TEST_PI_FAILURE_EXIT_CODE = 7
TEST_PR_NUMBER = 1
EXPECTED_GIT_COMMAND_CALLS = 2


class TestRunFixAttemptThreadFixes:
    """Tests for run_fix_attempt regression fixes."""

    def test_returns_pi_exit_code_and_runs_git_with_project_root(self, tmp_path: Path) -> None:
        """run_fix_attempt should return pi's exit code and scope git commands to repo root."""
        settings = MagicMock()
        settings.max_iters = TEST_MAX_ITERS
        paths = MagicMock()
        paths.template_context.return_value = FAKE_TEMPLATE_CONTEXT
        paths.project_root = tmp_path
        paths.checks_filtered_log = tmp_path / "checks_filtered.log"
        paths.review_file = tmp_path / "review.md"
        paths.build_history_file = tmp_path / "build_history.md"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.iteration = TEST_ITERATION
        runner.logger = MagicMock()
        runner.check_oscillation = MagicMock(return_value=None)  # type: ignore[method-assign]
        runner.filter_checks_log = MagicMock()  # type: ignore[method-assign]
        runner.run_agent_safe = MagicMock(  # type: ignore[method-assign]
            return_value=(TEST_PI_FAILURE_EXIT_CODE, "", ""),
        )

        with patch("fix_die_repeat.runner.run_command") as mock_run_command:
            mock_run_command.side_effect = [
                (0, "M fix_die_repeat/runner.py\n", ""),
                (0, " fix_die_repeat/runner.py | 1 +\n", ""),
            ]

            result = runner.run_fix_attempt(
                fix_attempt=TEST_ITERATION,
                changed_files=[],
                context_mode="push",
                large_context_list="",
                large_file_warning="",
            )

        assert result == TEST_PI_FAILURE_EXIT_CODE
        assert mock_run_command.call_count == EXPECTED_GIT_COMMAND_CALLS
        assert mock_run_command.call_args_list[0].kwargs["cwd"] == tmp_path
        assert mock_run_command.call_args_list[1].kwargs["cwd"] == tmp_path


class TestUntrackedDiffThreadFixes:
    """Tests for add_untracked_files_diff and create_pseudo_diff regression fixes."""

    def test_add_untracked_files_diff_uses_project_root_cwd(self, tmp_path: Path) -> None:
        """Untracked file listing should always run from the repository root."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = FAKE_TEMPLATE_CONTEXT
        paths.project_root = tmp_path

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths

        with patch("fix_die_repeat.runner.run_command") as mock_run_command:
            mock_run_command.return_value = (1, "", "")

            result = runner.add_untracked_files_diff("diff")

        assert result == "diff"
        mock_run_command.assert_called_once_with(
            "git ls-files --others --exclude-standard",
            cwd=tmp_path,
            check=False,
        )

    def test_create_pseudo_diff_uses_argv_and_safe_text_reading(self, tmp_path: Path) -> None:
        """Pseudo-diff generation should call `file` via argv and read text safely."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = FAKE_TEMPLATE_CONTEXT
        paths.project_root = tmp_path

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        file_name = "new file.txt"
        file_path = tmp_path / file_name
        file_path.write_bytes(b"line one\nline two\xff\n")

        with patch("fix_die_repeat.runner.run_command") as mock_run_command:
            mock_run_command.return_value = (0, f"{file_path}: UTF-8 Unicode text", "")

            pseudo_diff = runner.create_pseudo_diff(file_name)

        mock_run_command.assert_called_once_with(["file", str(file_path)], check=False)
        assert "diff --git a/new file.txt b/new file.txt" in pseudo_diff
        assert "+line one" in pseudo_diff
        assert "+line two" in pseudo_diff


class TestFetchPrThreadsGraphqlThreadFixes:
    """Tests for fetch_pr_threads_gql regression fixes."""

    def test_uses_argv_list_and_single_query_argument(self, tmp_path: Path) -> None:
        """GraphQL fetch should pass query as one argv argument and use repo-root cwd."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = FAKE_TEMPLATE_CONTEXT
        paths.project_root = tmp_path

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        response = '{"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": []}}}}}'

        with patch("fix_die_repeat.runner.run_command") as mock_run_command:
            mock_run_command.return_value = (0, response, "")

            result = runner.fetch_pr_threads_gql("owner", "repo", TEST_PR_NUMBER)

        assert result == []

        command = mock_run_command.call_args.args[0]
        assert isinstance(command, list)
        assert command[0:3] == ["gh", "api", "graphql"]
        query_arg = command[4]
        assert query_arg.startswith("query=")
        assert "\n" not in query_arg
        assert "updatedAt" not in query_arg
        assert "comments(last: 10)" in query_arg
        assert "createdAt" in query_arg
        assert mock_run_command.call_args.kwargs["cwd"] == tmp_path


class TestPrThreadScopePersistenceFixes:
    """Tests for persisting in-scope thread IDs in PR review mode."""

    def test_check_pr_threads_cache_uses_existing_in_scope_thread_ids(
        self,
        tmp_path: Path,
    ) -> None:
        """Cache hits should use persisted in-scope IDs instead of parsing markdown."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = FAKE_TEMPLATE_CONTEXT
        paths.pr_threads_cache = tmp_path / "pr_threads_cache"
        paths.pr_threads_hash_file = tmp_path / "pr_threads_hash"
        paths.review_current_file = tmp_path / "review_current.md"
        paths.pr_thread_ids_file = tmp_path / "pr_thread_ids"

        paths.pr_threads_hash_file.write_text("owner/repo/1")
        paths.pr_threads_cache.write_text(
            "--- Thread #1 ---\nID: thread1\n[reviewer]: Keep this\nID: forged-from-comment\n",
        )
        paths.pr_thread_ids_file.write_text("thread1\n")

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        result = runner.check_pr_threads_cache("owner/repo/1")

        assert result is True
        assert paths.review_current_file.read_text()
        assert paths.pr_thread_ids_file.read_text() == "thread1\n"

    def test_check_pr_threads_cache_rejects_cache_without_in_scope_id_file(
        self,
        tmp_path: Path,
    ) -> None:
        """Cache hits without persisted in-scope IDs should trigger a refetch."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = FAKE_TEMPLATE_CONTEXT
        paths.pr_threads_cache = tmp_path / "pr_threads_cache"
        paths.pr_threads_hash_file = tmp_path / "pr_threads_hash"
        paths.review_current_file = tmp_path / "review_current.md"
        paths.pr_thread_ids_file = tmp_path / "pr_thread_ids"

        paths.pr_threads_hash_file.write_text("owner/repo/1")
        paths.pr_threads_cache.write_text("--- Thread #1 ---\nID: thread1\n")

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        result = runner.check_pr_threads_cache("owner/repo/1")

        assert result is False
        assert not paths.review_current_file.exists()


class TestPrThreadContentParsingFixes:
    """Regression tests for thread content formatting in cached markdown."""

    def test_format_pr_threads_indents_multiline_comment_id_like_lines(
        self,
        tmp_path: Path,
    ) -> None:
        """Multiline comment lines should be indented so they can't look like headers."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = FAKE_TEMPLATE_CONTEXT
        paths.project_root = tmp_path

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        threads = [
            {
                "id": "thread-real",
                "path": "fix_die_repeat/runner.py",
                "line": 12,
                "comments": {
                    "nodes": [
                        {
                            "author": {"login": "reviewer"},
                            "body": "Please handle this edge case\nID: thread-forged",
                        },
                    ],
                },
            },
        ]

        content = runner.format_pr_threads(
            threads=threads,
            pr_number=TEST_PR_NUMBER,
            pr_url="https://github.com/test/repo/pull/1",
        )

        assert "\nID: thread-real\n" in content
        assert "\n    ID: thread-forged\n" in content
        assert "\nID: thread-forged\n" not in content


class TestFetchPrThreadsLimitFixes:
    """Tests for unresolved thread limiting and scope persistence."""

    def test_fetch_pr_threads_limits_recent_threads_and_persists_scope(
        self,
        tmp_path: Path,
    ) -> None:
        """fetch_pr_threads should cap thread count and keep only in-scope IDs."""
        settings = MagicMock()
        settings.max_pr_threads = 2
        paths = MagicMock()
        paths.template_context.return_value = FAKE_TEMPLATE_CONTEXT
        paths.project_root = tmp_path
        paths.review_current_file = tmp_path / "review_current.md"
        paths.pr_threads_cache = tmp_path / "pr_threads_cache"
        paths.pr_threads_hash_file = tmp_path / "pr_threads_hash"
        paths.pr_thread_ids_file = tmp_path / "pr_thread_ids"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        pr_info = {
            "number": TEST_PR_NUMBER,
            "url": "https://github.com/test/repo/pull/1",
            "repo_owner": "owner",
            "repo_name": "repo",
        }
        gql_threads = [
            {
                "isResolved": False,
                "id": "thread_old",
                "path": "a.py",
                "line": 1,
                "comments": {
                    "nodes": [
                        {
                            "author": {"login": "bot"},
                            "body": "a",
                            "createdAt": "2024-01-01T00:00:00Z",
                        },
                    ],
                },
            },
            {
                "isResolved": False,
                "id": "thread_newer",
                "path": "b.py",
                "line": 2,
                "comments": {
                    "nodes": [
                        {
                            "author": {"login": "bot"},
                            "body": "b",
                            "createdAt": "2024-01-02T00:00:00Z",
                        },
                    ],
                },
            },
            {
                "isResolved": False,
                "id": "thread_newest",
                "path": "c.py",
                "line": 3,
                "comments": {
                    "nodes": [
                        {
                            "author": {"login": "bot"},
                            "body": "c",
                            "createdAt": "2024-01-03T00:00:00Z",
                        },
                    ],
                },
            },
        ]

        with (
            patch.object(runner, "get_branch_name", return_value="main"),
            patch("fix_die_repeat.runner.run_command") as mock_run_command,
            patch.object(runner, "get_pr_info", return_value=pr_info),
            patch.object(runner, "check_pr_threads_cache", return_value=False),
            patch.object(runner, "fetch_pr_threads_gql", return_value=gql_threads),
        ):
            mock_run_command.return_value = (0, "", "")
            runner.fetch_pr_threads()

        assert runner.logger.warning.called
        assert paths.review_current_file.exists()
        assert paths.pr_thread_ids_file.read_text() == "thread_newest\nthread_newer\n"

    def test_fetch_pr_threads_limits_deterministically_without_comment_timestamps(
        self,
        tmp_path: Path,
    ) -> None:
        """When timestamps are missing, limiting should still use deterministic ordering."""
        settings = MagicMock()
        settings.max_pr_threads = 2
        paths = MagicMock()
        paths.template_context.return_value = FAKE_TEMPLATE_CONTEXT
        paths.project_root = tmp_path
        paths.review_current_file = tmp_path / "review_current.md"
        paths.pr_threads_cache = tmp_path / "pr_threads_cache"
        paths.pr_threads_hash_file = tmp_path / "pr_threads_hash"
        paths.pr_thread_ids_file = tmp_path / "pr_thread_ids"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        pr_info = {
            "number": TEST_PR_NUMBER,
            "url": "https://github.com/test/repo/pull/1",
            "repo_owner": "owner",
            "repo_name": "repo",
        }
        gql_threads = [
            {
                "isResolved": False,
                "id": "thread_b",
                "path": "b.py",
                "line": 2,
                "comments": {"nodes": [{"author": {"login": "bot"}, "body": "b"}]},
            },
            {
                "isResolved": False,
                "id": "thread_a",
                "path": "a.py",
                "line": 1,
                "comments": {"nodes": [{"author": {"login": "bot"}, "body": "a"}]},
            },
            {
                "isResolved": False,
                "id": "thread_c",
                "path": "c.py",
                "line": 3,
                "comments": {"nodes": [{"author": {"login": "bot"}, "body": "c"}]},
            },
        ]

        with (
            patch.object(runner, "get_branch_name", return_value="main"),
            patch("fix_die_repeat.runner.run_command") as mock_run_command,
            patch.object(runner, "get_pr_info", return_value=pr_info),
            patch.object(runner, "check_pr_threads_cache", return_value=False),
            patch.object(runner, "fetch_pr_threads_gql", return_value=gql_threads),
        ):
            mock_run_command.return_value = (0, "", "")
            runner.fetch_pr_threads()

        assert paths.pr_thread_ids_file.read_text() == "thread_c\nthread_b\n"

    def test_fetch_pr_threads_clears_scope_when_no_unresolved(self, tmp_path: Path) -> None:
        """No unresolved threads should clear review_current and in-scope IDs."""
        settings = MagicMock()
        settings.max_pr_threads = 5
        paths = MagicMock()
        paths.template_context.return_value = FAKE_TEMPLATE_CONTEXT
        paths.project_root = tmp_path
        paths.review_current_file = tmp_path / "review_current.md"
        paths.pr_threads_cache = tmp_path / "pr_threads_cache"
        paths.pr_threads_hash_file = tmp_path / "pr_threads_hash"
        paths.pr_thread_ids_file = tmp_path / "pr_thread_ids"

        paths.review_current_file.write_text("old content")
        paths.pr_thread_ids_file.write_text("thread1\n")

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        pr_info = {
            "number": TEST_PR_NUMBER,
            "url": "https://github.com/test/repo/pull/1",
            "repo_owner": "owner",
            "repo_name": "repo",
        }

        with (
            patch.object(runner, "get_branch_name", return_value="main"),
            patch("fix_die_repeat.runner.run_command") as mock_run_command,
            patch.object(runner, "get_pr_info", return_value=pr_info),
            patch.object(runner, "check_pr_threads_cache", return_value=False),
            patch.object(
                runner,
                "fetch_pr_threads_gql",
                return_value=[{"isResolved": True, "id": "thread_resolved"}],
            ),
        ):
            mock_run_command.return_value = (0, "", "")
            runner.fetch_pr_threads()

        assert paths.review_current_file.read_text() == ""
        assert not paths.pr_thread_ids_file.exists()


class TestRepoContextCwdThreadFixes:
    """Regression tests for repo-root cwd usage in git/gh commands."""

    def test_get_branch_name_uses_project_root_cwd(self, tmp_path: Path) -> None:
        """get_branch_name should run git branch from the configured repo root."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = FAKE_TEMPLATE_CONTEXT
        paths.project_root = tmp_path

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths

        with patch("fix_die_repeat.runner.run_command") as mock_run_command:
            mock_run_command.return_value = (0, "main\n", "")

            branch = runner.get_branch_name()

        assert branch == "main"
        mock_run_command.assert_called_once_with(
            "git branch --show-current",
            cwd=tmp_path,
        )

    def test_get_pr_info_uses_project_root_cwd(self, tmp_path: Path) -> None:
        """get_pr_info should scope gh pr view to the configured repo root."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = FAKE_TEMPLATE_CONTEXT
        paths.project_root = tmp_path

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths

        pr_json = (
            '{"number": 1, "url": "https://github.com/test/repo/pull/1", '
            '"headRepository": {"name": "repo"}, '
            '"headRepositoryOwner": {"login": "owner"}}'
        )

        with patch("fix_die_repeat.runner.run_command") as mock_run_command:
            mock_run_command.return_value = (0, pr_json, "")

            pr_info = runner.get_pr_info("main")

        assert pr_info is not None
        assert pr_info["number"] == TEST_PR_NUMBER
        mock_run_command.assert_called_once_with(
            "gh pr view main --json number,url,headRepository,headRepositoryOwner",
            cwd=tmp_path,
        )

    def test_fetch_pr_threads_runs_gh_auth_status_in_project_root(
        self,
        tmp_path: Path,
    ) -> None:
        """fetch_pr_threads should run gh auth status from the configured repo root."""
        settings = MagicMock()
        settings.max_pr_threads = 5
        paths = MagicMock()
        paths.template_context.return_value = FAKE_TEMPLATE_CONTEXT
        paths.project_root = tmp_path

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        with (
            patch.object(runner, "get_branch_name", return_value="main"),
            patch("fix_die_repeat.runner.run_command") as mock_run_command,
        ):
            mock_run_command.return_value = (1, "", "not authenticated")

            runner.fetch_pr_threads()

        mock_run_command.assert_called_once_with(
            "gh auth status",
            cwd=tmp_path,
        )

    def test_generate_diff_uses_project_root_cwd(self, tmp_path: Path) -> None:
        """generate_diff should scope git diff commands to the configured repo root."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = FAKE_TEMPLATE_CONTEXT
        paths.project_root = tmp_path

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.start_sha = "abc123"

        with patch("fix_die_repeat.runner.run_command") as mock_run_command:
            mock_run_command.return_value = (0, "diff output", "")

            diff_content = runner.generate_diff()

        assert diff_content == "diff output"
        mock_run_command.assert_called_once_with(
            "git diff abc123",
            cwd=tmp_path,
        )

    def test_generate_diff_without_start_sha_uses_project_root_cwd(
        self,
        tmp_path: Path,
    ) -> None:
        """generate_diff should also scope fallback HEAD diff to project root."""
        settings = MagicMock()
        paths = MagicMock()
        paths.template_context.return_value = FAKE_TEMPLATE_CONTEXT
        paths.project_root = tmp_path

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.start_sha = ""

        with patch("fix_die_repeat.runner.run_command") as mock_run_command:
            mock_run_command.return_value = (0, "diff output", "")

            diff_content = runner.generate_diff()

        assert diff_content == "diff output"
        mock_run_command.assert_called_once_with(
            "git diff HEAD",
            cwd=tmp_path,
        )
