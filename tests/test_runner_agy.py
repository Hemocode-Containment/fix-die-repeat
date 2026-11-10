"""Tests for AgyRunner agent interactions, structured output materialization, and setup."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
import yaml

from fix_die_repeat.agy_client import AgyTurnResult
from fix_die_repeat.agy_policy import (
    AgentRole,
    AgyPolicyError,
    PreflightResult,
    QuotaStateStore,
)
from fix_die_repeat.config import Paths, Settings
from fix_die_repeat.runner import AgyRunner
from fix_die_repeat.utils import get_git_revision_hash

if TYPE_CHECKING:
    from pathlib import Path


def _build_runner(tmp_path: Path) -> tuple[AgyRunner, MagicMock]:
    """Construct an AgyRunner with isolated paths and a mock client for unit testing."""
    settings = Settings()
    paths = Paths(project_root=tmp_path)
    paths.ensure_fdr_dir()

    runner = AgyRunner.__new__(AgyRunner)
    runner.settings = settings
    runner.paths = paths
    runner.logger = MagicMock()
    runner.iteration = 1
    runner.agent_invocation_count = 0
    runner.consecutive_toolless_attempts = 0
    runner._quota_store = QuotaStateStore(paths.quota_state_file)
    mock_client = MagicMock()
    runner._client = mock_client
    runner.before_agent_call = MagicMock()  # type: ignore[method-assign]
    return runner, mock_client


class TestRunAgentAndMaterializeOutput:
    """Tests for run_agent, run_agent_safe, and _materialize_role_output."""

    def test_run_agent_materializes_review_output(self, tmp_path: Path) -> None:
        """run_agent writes REVIEW_SCHEMA findings to review_current.md."""
        runner, mock_client = _build_runner(tmp_path)
        turn = AgyTurnResult(
            returncode=0,
            response_text="[CRITICAL] app.py:10 - Null deref",
            stderr="",
            structured_output={
                "verdict": "ISSUES",
                "issues_markdown": "[CRITICAL] app.py:10 - Null deref",
                "summary": "Null deref found",
            },
            model="claude-opus-4-6-thinking",
        )
        mock_client.run_turn.return_value = turn
        runner._verify_turn_git_state = MagicMock(return_value=True)  # type: ignore[method-assign]

        with patch("fix_die_repeat.runner.fetch_codexbar_snapshot", return_value=None):
            rc, _stdout, _stderr = runner.run_agent(AgentRole.REVIEW, "Review diff")

        assert rc == 0
        assert runner.paths.review_current_file.read_text() == "[CRITICAL] app.py:10 - Null deref\n"
        assert runner.paths.agy_log.exists()

    def test_run_agent_materializes_fix_resolved_threads(self, tmp_path: Path) -> None:
        """run_agent writes FIX_SCHEMA resolved_thread_ids to .resolved_threads."""
        runner, mock_client = _build_runner(tmp_path)
        turn = AgyTurnResult(
            returncode=0,
            response_text="Fixed 2 threads",
            stderr="",
            structured_output={
                "outcome": "fixed",
                "summary": "Fixed 2 threads",
                "files_modified": ["app.py"],
                "resolved_thread_ids": ["PRRT_1", "PRRT_2"],
            },
            model="claude-opus-4-6-thinking",
        )
        mock_client.run_turn.return_value = turn
        runner._verify_turn_git_state = MagicMock(return_value=True)  # type: ignore[method-assign]

        with patch("fix_die_repeat.runner.fetch_codexbar_snapshot", return_value=None):
            rc, _stdout, _stderr = runner.run_agent(AgentRole.FIX, "Fix threads")

        assert rc == 0
        assert runner.paths.pr_resolved_threads_file.read_text() == "PRRT_1\nPRRT_2\n"

    def test_run_agent_materializes_fix_unlinks_resolved_threads_when_empty(
        self,
        tmp_path: Path,
    ) -> None:
        """run_agent unlinks .resolved_threads when FIX returns empty resolved_thread_ids."""
        runner, mock_client = _build_runner(tmp_path)
        runner.paths.pr_resolved_threads_file.write_text("PRRT_OLD\n")
        turn = AgyTurnResult(
            returncode=0,
            response_text="No threads resolved",
            stderr="",
            structured_output={
                "outcome": "blocked",
                "summary": "No threads needed fix",
                "files_modified": [],
                "resolved_thread_ids": [],
            },
            model="claude-opus-4-6-thinking",
        )
        mock_client.run_turn.return_value = turn
        runner._verify_turn_git_state = MagicMock(return_value=True)  # type: ignore[method-assign]

        with patch("fix_die_repeat.runner.fetch_codexbar_snapshot", return_value=None):
            rc, _stdout, _stderr = runner.run_agent(AgentRole.FIX, "Fix threads")

        assert rc == 0
        assert not runner.paths.pr_resolved_threads_file.exists()

    def test_run_agent_improves_prompts_marks_only_matching_entries(
        self,
        tmp_path: Path,
    ) -> None:
        """IMPROVE_PROMPTS marks only introspection entries matching reviewed_thread_ids."""
        runner, mock_client = _build_runner(tmp_path)
        intro_file = runner.paths.fdr_home / "introspection.yaml"
        intro_doc1 = {
            "date": "2026-11-08",
            "project": "proj",
            "pr_number": 1,
            "pr_url": "url1",
            "status": "pending",
            "threads": [{"id": "T1", "title": "t1"}],
        }
        intro_doc2 = {
            "date": "2026-11-08",
            "project": "proj",
            "pr_number": 2,
            "pr_url": "url2",
            "status": "pending",
            "threads": [{"id": "T2", "title": "t2"}],
        }
        intro_file.write_text(
            yaml.safe_dump(intro_doc1) + "\n---\n" + yaml.safe_dump(intro_doc2),
        )

        turn = AgyTurnResult(
            returncode=0,
            response_text="Updated templates",
            stderr="",
            structured_output={
                "reviewed_thread_ids": ["T1"],
                "templates_updated": ["fix_checks.j2"],
                "summary": "Added T1 guidance",
            },
            model="claude-opus-4-6-thinking",
        )
        mock_client.run_turn.return_value = turn
        runner._verify_turn_git_state = MagicMock(return_value=True)  # type: ignore[method-assign]

        with (
            patch("fix_die_repeat.runner._get_introspection_file_path", return_value=intro_file),
            patch("fix_die_repeat.runner.fetch_codexbar_snapshot", return_value=None),
        ):
            rc, _stdout, _stderr = runner.run_agent(AgentRole.IMPROVE_PROMPTS, "Improve templates")

        assert rc == 0
        docs = list(yaml.safe_load_all(intro_file.read_text()))
        assert docs[0]["status"] == "reviewed"
        assert docs[1]["status"] == "pending"

    def test_run_agent_safe_fails_when_all_models_cooling_down(self, tmp_path: Path) -> None:
        """run_agent_safe returns error without fallback when all models are cooling down."""
        runner, _mock_client = _build_runner(tmp_path)
        runner.settings.models_fix = "model-a,model-b"
        runner.settings.quota_max_wait_s = 0
        store = runner._get_quota_store()
        store.record_quota_cooldown("model-a", reason="rate limit")
        store.record_quota_cooldown("model-b", reason="quota exceeded")

        with patch("fix_die_repeat.runner.fetch_codexbar_snapshot", return_value=None):
            rc, _stdout, stderr = runner.run_agent_safe(AgentRole.FIX, "Fix")

        assert rc == 1
        assert "cooling down or exhausted" in stderr

    def test_run_agent_materializes_introspect_yaml(self, tmp_path: Path) -> None:
        """run_agent serializes INTROSPECT_SCHEMA output to .introspection_result.yaml."""
        runner, mock_client = _build_runner(tmp_path)
        turn = AgyTurnResult(
            returncode=0,
            response_text="Introspected",
            stderr="",
            structured_output={
                "date": "2026-11-08",
                "project": "demo",
                "pr_number": 7,
                "pr_url": "https://github.com/owner/repo/pull/7",
                "status": "pending",
                "threads": [],
            },
            model="gemini-3.1-pro-low",
        )
        mock_client.run_turn.return_value = turn
        runner._verify_turn_git_state = MagicMock(return_value=True)  # type: ignore[method-assign]

        with patch("fix_die_repeat.runner.fetch_codexbar_snapshot", return_value=None):
            rc, _stdout, _stderr = runner.run_agent(AgentRole.INTROSPECT, "Introspect PR")

        assert rc == 0
        assert runner.paths.introspection_result_file.exists()
        assert "pr_number: 7" in runner.paths.introspection_result_file.read_text()

    def test_run_agent_advances_chain_on_capacity_error(self, tmp_path: Path) -> None:
        """run_agent marks a model in cooldown on capacity error and retries next model in chain."""
        runner, mock_client = _build_runner(tmp_path)
        runner.settings.models_review = "claude-opus-4-6-thinking,gemini-3-flash"
        fail_turn = AgyTurnResult(
            returncode=1,
            response_text="",
            stderr="429 RESOURCE_EXHAUSTED",
            model="claude-opus-4-6-thinking",
            quota_error=True,
        )
        ok_turn = AgyTurnResult(
            returncode=0,
            response_text="NO_ISSUES",
            stderr="",
            structured_output={
                "verdict": "NO_ISSUES",
                "issues_markdown": "NO_ISSUES",
                "summary": "Clean",
            },
            model="gemini-3-flash",
        )
        mock_client.run_turn.side_effect = [fail_turn, ok_turn]
        runner._verify_turn_git_state = MagicMock(return_value=True)  # type: ignore[method-assign]

        with patch("fix_die_repeat.runner.fetch_codexbar_snapshot", return_value=None):
            rc, _stdout, _stderr = runner.run_agent_safe(AgentRole.REVIEW, "Review diff")

        assert rc == 0
        assert runner._get_quota_store().cooldown_until("claude-opus-4-6-thinking") > 0
        assert runner.paths.review_current_file.read_text() == "NO_ISSUES"

    def test_run_agent_enforces_daily_call_cap(self, tmp_path: Path) -> None:
        """run_agent aborts when FDR_DAILY_MAX_CALLS is exceeded."""
        runner, _mock_client = _build_runner(tmp_path)
        runner.settings.daily_max_calls = 1
        runner._get_quota_store().increment_daily_calls("model-x")

        with patch("fix_die_repeat.runner.fetch_codexbar_snapshot", return_value=None):
            rc, _stdout, stderr = runner.run_agent(AgentRole.FIX, "Fix check")

        assert rc == 1
        assert "Daily Antigravity call limit reached" in stderr

    def test_run_agent_safe_triggers_emergency_compact_on_long_context(
        self,
        tmp_path: Path,
    ) -> None:
        """run_agent_safe calls emergency_compact when agy reports context length exceeded."""
        runner, _mock_client = _build_runner(tmp_path)
        runner.emergency_compact = MagicMock()  # type: ignore[method-assign]
        runner.run_agent = MagicMock(  # type: ignore[method-assign]
            side_effect=[
                (1, "", "Error: 429 long context exceeded"),
                (0, "fixed", ""),
            ],
        )

        rc, stdout, _stderr = runner.run_agent_safe(AgentRole.FIX, "Fix")

        assert rc == 0
        assert stdout == "fixed"
        runner.emergency_compact.assert_called_once()

    def test_run_agent_materialize_fails_on_schema_violation(self, tmp_path: Path) -> None:
        """run_agent fails closed when structured_output violates role schema."""
        runner, mock_client = _build_runner(tmp_path)
        turn = AgyTurnResult(
            returncode=0,
            response_text="Bad review output",
            stderr="",
            structured_output={
                "verdict": "INVALID_VERDICT",
                "issues_markdown": "none",
                "summary": "invalid",
            },
            model="gemini-3-flash",
        )
        mock_client.run_turn.return_value = turn
        runner._verify_turn_git_state = MagicMock(return_value=True)  # type: ignore[method-assign]

        with patch("fix_die_repeat.runner.fetch_codexbar_snapshot", return_value=None):
            rc, _stdout, stderr = runner.run_agent(AgentRole.REVIEW, "Review diff")

        assert rc == 1
        assert "schema validation failed" in stderr
        assert not runner.paths.review_current_file.exists()

    def test_run_agent_materialize_fails_on_issues_verdict_with_empty_or_no_issues_markdown(
        self,
        tmp_path: Path,
    ) -> None:
        """run_agent fails closed when verdict ISSUES has empty or NO_ISSUES markdown."""
        runner, mock_client = _build_runner(tmp_path)
        turn = AgyTurnResult(
            returncode=0,
            response_text="Contradictory output",
            stderr="",
            structured_output={
                "verdict": "ISSUES",
                "issues_markdown": "NO_ISSUES",
                "summary": "Clean",
            },
            model="gemini-3-flash",
        )
        mock_client.run_turn.return_value = turn
        runner._verify_turn_git_state = MagicMock(return_value=True)  # type: ignore[method-assign]

        with patch("fix_die_repeat.runner.fetch_codexbar_snapshot", return_value=None):
            rc, _stdout, stderr = runner.run_agent(AgentRole.REVIEW, "Review diff")

        assert rc == 1
        assert "Review verdict 'ISSUES' requires non-empty issues_markdown" in stderr
        assert not runner.paths.review_current_file.exists()


class TestPreflightAndSetup:
    """Tests for run_preflight and setup_run."""

    def test_run_preflight_exits_zero_when_flag_set(self, tmp_path: Path) -> None:
        """run_preflight exits 0 after verification when --preflight is active."""
        runner, _mock_client = _build_runner(tmp_path)
        runner.settings.preflight = True

        fake_res = PreflightResult(
            executable="/bin/agy",
            version="2.0.0",
            available_models=frozenset(["gemini-3.8-flash-high"]),
            smoke_tested=True,
        )

        with (
            patch("fix_die_repeat.runner.ensure_agy_ready", return_value=fake_res),
            pytest.raises(SystemExit) as excinfo,
        ):
            runner.run_preflight()

        assert excinfo.value.code == 0

    def test_run_preflight_exits_nonzero_on_failure(self, tmp_path: Path) -> None:
        """run_preflight exits 1 when ensure_agy_ready fails."""
        runner, _mock_client = _build_runner(tmp_path)

        with (
            patch(
                "fix_die_repeat.runner.ensure_agy_ready",
                side_effect=AgyPolicyError("missing agy"),
            ),
            pytest.raises(SystemExit) as excinfo,
        ):
            runner.run_preflight()

        assert excinfo.value.code == 1

    def test_setup_run_archives_artifacts(self, tmp_path: Path) -> None:
        """setup_run archives existing artifacts, runs preflight, and initializes agy.log."""
        runner, _mock_client = _build_runner(tmp_path)
        runner.settings.archive_artifacts = True
        runner.settings.preflight = False
        runner.settings.compact_artifacts = True
        runner.session_log = runner.paths.fdr_dir / "session.log"
        runner.run_preflight = MagicMock()  # type: ignore[method-assign]
        runner.check_and_compact_artifacts = MagicMock()  # type: ignore[method-assign]

        existing_file = runner.paths.fdr_dir / "old.log"
        existing_file.write_text("data")

        with patch("fix_die_repeat.runner.run_command", return_value=(0, "abc123\n", "")):
            runner.setup_run()

        archive_dirs = list((runner.paths.fdr_dir / "archive").glob("*"))
        assert archive_dirs
        assert runner.paths.agy_log.exists()
        assert runner.paths.checks_hash_file.exists()
        assert runner.paths.start_sha_file.read_text() == "abc123"
        runner.run_preflight.assert_called_once()
        runner.check_and_compact_artifacts.assert_called_once()

    def test_setup_run_preserves_runner_lock_sidecar(self, tmp_path: Path) -> None:
        """setup_run preserves both runner.lock and runner.lock.lck during archive."""
        runner, _mock_client = _build_runner(tmp_path)
        runner.settings.archive_artifacts = True
        runner.settings.preflight = False
        runner.settings.compact_artifacts = False
        runner.session_log = runner.paths.fdr_dir / "session.log"
        runner.run_preflight = MagicMock()  # type: ignore[method-assign]
        runner.check_and_compact_artifacts = MagicMock()  # type: ignore[method-assign]

        lock_file = runner.paths.fdr_dir / "runner.lock"
        lock_file.write_text("12345:inst")
        sidecar_file = runner.paths.fdr_dir / "runner.lock.lck"
        sidecar_file.write_text("lock-sidecar")
        artifact_file = runner.paths.fdr_dir / "checks.log"
        artifact_file.write_text("old checks")

        with patch("fix_die_repeat.runner.run_command", return_value=(0, "abc123\n", "")):
            runner.setup_run()

        assert lock_file.exists()
        assert lock_file.read_text() == "12345:inst"
        assert sidecar_file.exists()
        assert sidecar_file.read_text() == "lock-sidecar"

        archive_dirs = list((runner.paths.fdr_dir / "archive").glob("*"))
        assert archive_dirs
        archived_files = [p.name for p in archive_dirs[0].iterdir()]
        assert "checks.log" in archived_files
        assert "runner.lock" not in archived_files
        assert "runner.lock.lck" not in archived_files

    def test_check_oscillation_detects_repeat(self, tmp_path: Path) -> None:
        """check_oscillation returns warning for repeated hash."""
        runner, _mock_client = _build_runner(tmp_path)
        runner.iteration = 2
        runner.paths.checks_log.write_text("same output")
        current_hash = get_git_revision_hash(runner.paths.checks_log)
        runner.paths.checks_hash_file.write_text(f"{current_hash}:1\n")

        warning = runner.check_oscillation()

        assert warning is not None
        assert "iteration 1" in warning
