"""Tests for AgyClient stream-json parsing, stall detection, and git invariant verification."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from fix_die_repeat.agy_client import (
    AgyClient,
    AgyTurnRequest,
    GitSnapshot,
    TokenUsage,
    capture_git_snapshot,
    verify_fix_git_state,
    verify_readonly_git_state,
)
from fix_die_repeat.agy_policy import FIX_SCHEMA, REVIEW_SCHEMA, AgentRole

if TYPE_CHECKING:
    from pathlib import Path

EXPECTED_INPUT_TOKENS = 120
EXPECTED_OUTPUT_TOKENS = 45


def _git_bin() -> str:
    """Return path to git executable or skip test."""
    git_path = shutil.which("git")
    if git_path is None:
        pytest.skip("git executable required")
    assert git_path is not None
    return git_path


def _init_repo(repo_dir: Path) -> None:
    """Initialize a temporary git repository with one commit."""
    git = _git_bin()
    subprocess.run([git, "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(
        [git, "config", "user.email", "test@example.com"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [git, "config", "user.name", "Test User"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [git, "config", "commit.gpgsign", "false"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )
    tracked = repo_dir / "app.py"
    tracked.write_text("x = 1\n")
    subprocess.run([git, "add", "app.py"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(
        [git, "commit", "--no-gpg-sign", "-m", "initial"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )


class TestGitInvariants:
    """Tests for capture_git_snapshot, verify_readonly_git_state, and verify_fix_git_state."""

    def test_verify_readonly_git_state_detects_dirty_worktree(self, tmp_path: Path) -> None:
        """verify_readonly_git_state fails when a file is modified during a read-only turn."""
        _init_repo(tmp_path)
        logger = logging.getLogger("test-git-invariants")
        before = capture_git_snapshot(tmp_path)

        assert verify_readonly_git_state(tmp_path, before, logger) is True

        (tmp_path / "app.py").write_text("x = 2\n")
        assert verify_readonly_git_state(tmp_path, before, logger) is False

    def test_verify_fix_git_state_allows_edits_and_rejects_agents_mutation(
        self,
        tmp_path: Path,
    ) -> None:
        """verify_fix_git_state allows working-tree edits and reverts .agents/ edits."""
        git = _git_bin()
        _init_repo(tmp_path)
        agents_dir = tmp_path / ".agents"
        agents_dir.mkdir()
        rule_file = agents_dir / "AGENTS.md"
        rule_file.write_text("# Original\n")
        subprocess.run([git, "add", ".agents"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(
            [git, "commit", "--no-gpg-sign", "-m", "add agents"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )

        logger = logging.getLogger("test-git-invariants")
        before = capture_git_snapshot(tmp_path)

        # Unstaged edit to tracked code is allowed in fix role
        (tmp_path / "app.py").write_text("x = 2\n")
        assert verify_fix_git_state(tmp_path, before, logger) is True

        # Modifying .agents/ violates the fix invariant and is reverted
        rule_file.write_text("# Tampered\n")
        assert verify_fix_git_state(tmp_path, before, logger) is False
        assert rule_file.read_text() == "# Original\n"

    def test_verify_fix_git_state_soft_resets_head_change(self, tmp_path: Path) -> None:
        """verify_fix_git_state soft-resets when a fix turn creates a commit."""
        logger = logging.getLogger("test-git-invariants")
        before = GitSnapshot(is_git_repo=True, head_sha="aaaa1111", status_porcelain="")
        after = GitSnapshot(is_git_repo=True, head_sha="bbbb2222", status_porcelain="")
        with (
            patch("fix_die_repeat.agy_client.capture_git_snapshot", return_value=after),
            patch("fix_die_repeat.agy_client.run_command", return_value=(0, "", "")) as mock_cmd,
        ):
            assert verify_fix_git_state(tmp_path, before, logger) is True
            assert any("reset" in str(call) for call in mock_cmd.call_args_list)

    def test_verify_readonly_git_state_preserves_preexisting_untracked_files(
        self,
        tmp_path: Path,
    ) -> None:
        """verify_readonly_git_state keeps pre-existing untracked and deletes new."""
        _init_repo(tmp_path)
        logger = logging.getLogger("test-git-invariants")
        baseline_file = tmp_path / "baseline_untracked.txt"
        baseline_file.write_text("keep me\n")
        before = capture_git_snapshot(tmp_path)
        assert "baseline_untracked.txt" in before.untracked_files

        # Simulate agent turn creating new untracked file and mutating tracked file
        (tmp_path / "app.py").write_text("mutated\n")
        new_file = tmp_path / "new_rogue.txt"
        new_file.write_text("delete me\n")

        assert verify_readonly_git_state(tmp_path, before, logger) is False
        assert baseline_file.exists()
        assert baseline_file.read_text() == "keep me\n"
        assert not new_file.exists()

    def test_verify_fix_git_state_restores_staged_index_without_commit(
        self,
        tmp_path: Path,
    ) -> None:
        """verify_fix_git_state restores pre-turn staged index when turn stages files."""
        logger = logging.getLogger("test-git-invariants")
        before = GitSnapshot(
            is_git_repo=True,
            head_sha="aaaa1111",
            staged_tree_sha="tree_before",
            status_porcelain="",
        )
        after = GitSnapshot(
            is_git_repo=True,
            head_sha="aaaa1111",
            staged_tree_sha="tree_after_staged",
            status_porcelain="",
        )
        with (
            patch("fix_die_repeat.agy_client.capture_git_snapshot", return_value=after),
            patch("fix_die_repeat.agy_client.run_command", return_value=(0, "", "")) as mock_cmd,
        ):
            assert verify_fix_git_state(tmp_path, before, logger) is True
            assert any(
                "read-tree" in str(call) and "tree_before" in str(call)
                for call in mock_cmd.call_args_list
            )

    def test_verify_fix_git_state_preserves_baseline_agents_untracked_files(
        self,
        tmp_path: Path,
    ) -> None:
        """verify_fix_git_state removes only newly introduced untracked files in .agents/."""
        _init_repo(tmp_path)
        agents_dir = tmp_path / ".agents"
        agents_dir.mkdir()
        baseline_agent_file = agents_dir / "user_notes.txt"
        baseline_agent_file.write_text("user notes\n")

        logger = logging.getLogger("test-git-invariants")
        before = capture_git_snapshot(tmp_path)

        # Agent introduces a new rogue file into .agents/
        rogue_agent_file = agents_dir / "rogue.md"
        rogue_agent_file.write_text("rogue rule\n")

        assert verify_fix_git_state(tmp_path, before, logger) is False
        assert baseline_agent_file.exists()
        assert baseline_agent_file.read_text() == "user notes\n"
        assert not rogue_agent_file.exists()

    def test_verify_fix_git_state_reverts_committed_agents_mutation(
        self,
        tmp_path: Path,
    ) -> None:
        """verify_fix_git_state resets commit and restores .agents/ if agent committed changes."""
        git = _git_bin()
        _init_repo(tmp_path)
        agents_dir = tmp_path / ".agents"
        agents_dir.mkdir()
        rule_file = agents_dir / "AGENTS.md"
        rule_file.write_text("# Baseline\n")
        subprocess.run([git, "add", ".agents"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(
            [git, "commit", "--no-gpg-sign", "-m", "add baseline agents"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )

        logger = logging.getLogger("test-git-invariants")
        before = capture_git_snapshot(tmp_path)

        # Agent commits a change modifying .agents/
        rule_file.write_text("# Malicious change\n")
        subprocess.run([git, "add", ".agents"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(
            [git, "commit", "--no-gpg-sign", "-m", "agent commit on agents"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )

        assert verify_fix_git_state(tmp_path, before, logger) is False
        assert rule_file.read_text() == "# Baseline\n"

    def test_verify_fix_git_state_detects_and_reverts_untracked_agents_modification(
        self,
        tmp_path: Path,
    ) -> None:
        """verify_fix_git_state detects content hash changes to untracked .agents/ files."""
        _init_repo(tmp_path)
        agents_dir = tmp_path / ".agents"
        agents_dir.mkdir()
        untracked_note = agents_dir / "draft_skill.md"
        untracked_note.write_text("original draft\n")

        logger = logging.getLogger("test-git-invariants")
        before = capture_git_snapshot(tmp_path)
        assert ".agents/draft_skill.md" in before.agents_untracked_backups

        # Agent mutates content without changing untracked porcelain status
        untracked_note.write_text("tampered draft\n")

        assert verify_fix_git_state(tmp_path, before, logger) is False
        assert untracked_note.read_text() == "original draft\n"


class TestAgyClientBuildArgvAndRunTurn:
    """Tests for AgyClient.build_argv and stream-json NDJSON supervision."""

    def test_build_argv_places_prompt_flag_at_end(self, tmp_path: Path) -> None:
        """-p <prompt> must be placed at the end of argv so -p consumes the prompt."""
        client = AgyClient(executable="agy", logger=logging.getLogger("test-agy-client"))
        request = AgyTurnRequest(
            role=AgentRole.REVIEW,
            prompt="Review the diff",
            model="claude-opus-4-6-thinking",
            schema=REVIEW_SCHEMA,
            settings={"permissions": {}},
            working_dir=tmp_path,
            add_dirs=(tmp_path / "fdr",),
        )
        argv = client.build_argv(request)
        assert argv[-2:] == ["-p", "Review the diff"]
        assert "--output-format" in argv
        assert "stream-json" in argv
        assert "--json-schema" in argv
        assert "--dangerously-skip-permissions" not in argv

    def test_run_turn_parses_stream_json_and_writes_transcript(self, tmp_path: Path) -> None:
        """AgyClient.run_turn parses NDJSON events, extracts structured_output, and logs tools."""
        fake_agy = tmp_path / "fake_agy.py"
        events = [
            {
                "event": "step_update",
                "step_update": {
                    "step_index": 1,
                    "step_type": "tool",
                    "state": "ACTIVE",
                    "tool_name": "read_file",
                    "tool_info": {"parameters": {"Path": "app.py"}},
                },
            },
            {
                "event": "step_update",
                "step_update": {
                    "step_index": 1,
                    "step_type": "tool",
                    "state": "DONE",
                    "tool_name": "read_file",
                },
            },
            {
                "event": "result",
                "result": {
                    "status": "SUCCESS",
                    "response": "NO_ISSUES",
                    "structured_output": {
                        "verdict": "NO_ISSUES",
                        "issues_markdown": "NO_ISSUES",
                        "summary": "Clean",
                    },
                    "usage": {
                        "input_tokens": EXPECTED_INPUT_TOKENS,
                        "output_tokens": EXPECTED_OUTPUT_TOKENS,
                    },
                },
            },
        ]
        script_lines = [
            "#!/usr/bin/env python3",
            "import json",
            f"events = {json.dumps(events)!r}",
            "for ev in json.loads(events):",
            "    print(json.dumps(ev), flush=True)",
        ]
        fake_agy.write_text("\n".join(script_lines) + "\n")
        fake_agy.chmod(0o755)

        transcript_path = tmp_path / "agy" / "turn.ndjson"
        client = AgyClient(
            executable=str(fake_agy),
            logger=logging.getLogger("test-agy-client"),
        )
        request = AgyTurnRequest(
            role=AgentRole.REVIEW,
            prompt="Review changes",
            model="gemini-3-flash",
            schema=REVIEW_SCHEMA,
            settings={"permissions": {}},
            working_dir=tmp_path,
            transcript_path=transcript_path,
        )
        result = client.run_turn(request)

        assert result.returncode == 0
        assert result.structured_output == {
            "verdict": "NO_ISSUES",
            "issues_markdown": "NO_ISSUES",
            "summary": "Clean",
        }
        assert result.usage == TokenUsage(
            input_tokens=EXPECTED_INPUT_TOKENS,
            output_tokens=EXPECTED_OUTPUT_TOKENS,
        )
        assert transcript_path.exists()

    def test_run_turn_handles_failed_status_result(self, tmp_path: Path) -> None:
        """AgyClient.run_turn returns returncode=1 when result frame status is not SUCCESS."""
        fake_agy = tmp_path / "fake_agy_bad.py"
        events = [
            {
                "event": "result",
                "result": {
                    "status": "ERROR",
                    "error": "RESOURCE_EXHAUSTED: 429 rate limit",
                },
            },
        ]
        script_lines = [
            "#!/usr/bin/env python3",
            "import json",
            f"events = {json.dumps(events)!r}",
            "for ev in json.loads(events):",
            "    print(json.dumps(ev), flush=True)",
        ]
        fake_agy.write_text("\n".join(script_lines) + "\n")
        fake_agy.chmod(0o755)

        client = AgyClient(
            executable=str(fake_agy),
            logger=logging.getLogger("test-agy-client"),
        )
        request = AgyTurnRequest(
            role=AgentRole.FIX,
            prompt="Fix issue",
            model="gemini-3-flash",
            schema=FIX_SCHEMA,
            settings={"permissions": {}},
            working_dir=tmp_path,
        )
        result = client.run_turn(request)

        assert result.returncode == 1
        assert result.quota_error is True
        assert "RESOURCE_EXHAUSTED" in result.stderr
