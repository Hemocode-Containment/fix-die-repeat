"""Tests for process health, process-group reaping, and RunnerLock."""

from __future__ import annotations

import json
import os
import signal
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from fix_die_repeat.health import (
    ProcessIdentity,
    RunnerLock,
    RunnerLockError,
    _process_group_exists,
    process_identity,
    reap_process_group,
    same_process,
)

if TYPE_CHECKING:
    from pathlib import Path

SAMPLE_PID = 12345
OTHER_PID = 54321
DEAD_PID = 999999


class TestProcessIdentity:
    """Tests for process_identity and same_process."""

    def test_current_process_identity(self) -> None:
        """process_identity returns a valid identity for the current process."""
        ident = process_identity(os.getpid())
        assert ident is not None
        assert ident.pid == os.getpid()
        assert ident.start

    def test_invalid_pid_returns_none(self) -> None:
        """Non-positive PID returns None without shelling out."""
        assert process_identity(0) is None
        assert process_identity(-1) is None

    def test_dead_pid_returns_none(self) -> None:
        """Non-existent PID returns None when ps returns empty output."""
        with patch("fix_die_repeat.health.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            assert process_identity(DEAD_PID) is None

    def test_oserror_returns_none(self) -> None:
        """OSError from subprocess.run returns None."""
        with patch(
            "fix_die_repeat.health.subprocess.run",
            side_effect=OSError("ps unavailable"),
        ):
            assert process_identity(SAMPLE_PID) is None

    def test_same_process_matches_live_identity(self) -> None:
        """same_process returns True only when pid, start, and boot match and not zombie."""
        expected = ProcessIdentity(
            pid=SAMPLE_PID,
            ppid=1,
            start="Mon Nov  8 10:00:00 2026",
            boot="boot-1",
        )
        assert same_process(expected, expected) is True

        different_start = ProcessIdentity(
            pid=SAMPLE_PID,
            ppid=1,
            start="Mon Nov  8 11:00:00 2026",
            boot="boot-1",
        )
        assert same_process(expected, different_start) is False

        zombie_ident = ProcessIdentity(
            pid=SAMPLE_PID,
            ppid=1,
            start="Mon Nov  8 10:00:00 2026",
            boot="boot-1",
            zombie=True,
        )
        assert same_process(expected, zombie_ident) is False
        assert same_process(expected, None) is False


class TestReapProcessGroup:
    """Tests for reap_process_group."""

    def test_invalid_or_self_pgid_is_noop(self) -> None:
        """reap_process_group ignores non-positive PGIDs and current process PID."""
        with patch("fix_die_repeat.health.os.killpg") as mock_killpg:
            reap_process_group(0)
            reap_process_group(os.getpid())
            mock_killpg.assert_not_called()

    def test_process_group_exists_handles_errors(self) -> None:
        """_process_group_exists returns False on ProcessLookupError and True on PermissionError."""
        with patch("fix_die_repeat.health.os.killpg", side_effect=ProcessLookupError):
            assert _process_group_exists(SAMPLE_PID) is False
        with patch("fix_die_repeat.health.os.killpg", side_effect=PermissionError):
            assert _process_group_exists(SAMPLE_PID) is True
        with patch("fix_die_repeat.health.os.killpg", return_value=None):
            assert _process_group_exists(SAMPLE_PID) is True

    def test_reaps_process_group_with_sigterm(self) -> None:
        """reap_process_group sends SIGTERM and returns when group exits within grace period."""
        with (
            patch("fix_die_repeat.health.os.killpg") as mock_killpg,
            patch("fix_die_repeat.health._process_group_exists", return_value=False),
        ):
            reap_process_group(SAMPLE_PID, grace_seconds=0.5)
            mock_killpg.assert_called_once_with(SAMPLE_PID, signal.SIGTERM)

    def test_escalates_to_sigkill_on_timeout(self) -> None:
        """reap_process_group sends SIGKILL if process group remains alive past deadline."""
        with (
            patch("fix_die_repeat.health.os.killpg") as mock_killpg,
            patch("fix_die_repeat.health._process_group_exists", return_value=True),
        ):
            reap_process_group(SAMPLE_PID, grace_seconds=0.0)
            assert mock_killpg.call_args_list == [
                ((SAMPLE_PID, signal.SIGTERM),),
                ((SAMPLE_PID, signal.SIGKILL),),
            ]


class TestRunnerLock:
    """Tests for single-instance RunnerLock."""

    def test_acquires_and_releases_lock(self, tmp_path: Path) -> None:
        """RunnerLock creates lock file on enter and removes it on exit."""
        lock_file = tmp_path / "runner.lock"
        lock = RunnerLock(lock_file)

        with lock:
            assert lock_file.exists()
            payload = json.loads(lock_file.read_text())
            assert payload["runner"]["pid"] == os.getpid()

        assert not lock_file.exists()

    def test_blocks_concurrent_acquisition_via_flock(self, tmp_path: Path) -> None:
        """RunnerLock raises RunnerLockError when another instance holds the flock."""
        lock_file = tmp_path / "runner.lock"
        lock1 = RunnerLock(lock_file)
        lock2 = RunnerLock(lock_file)

        with lock1:
            with pytest.raises(RunnerLockError, match="already running"):
                lock2.acquire()

    def test_blocks_when_held_by_live_process(self, tmp_path: Path) -> None:
        """RunnerLock raises RunnerLockError when another live process holds the lock."""
        lock_file = tmp_path / "runner.lock"
        live_ident = ProcessIdentity(
            pid=OTHER_PID,
            ppid=1,
            start="Mon Nov  8 10:00:00 2026",
            boot="boot-1",
        )
        lock_file.write_text(
            json.dumps({"runner": live_ident.to_dict(), "worker": None}),
        )

        lock = RunnerLock(lock_file)
        with (
            patch("fix_die_repeat.health.process_identity", return_value=live_ident),
            pytest.raises(RunnerLockError, match="already running"),
        ):
            lock.acquire()

    def test_reclaims_stale_lock_from_dead_process(self, tmp_path: Path) -> None:
        """RunnerLock reclaims lock file when recorded PID is no longer alive."""
        lock_file = tmp_path / "runner.lock"
        stale_ident = ProcessIdentity(
            pid=DEAD_PID,
            ppid=1,
            start="Mon Nov  8 09:00:00 2026",
            boot="boot-1",
        )
        lock_file.write_text(json.dumps({"runner": stale_ident.to_dict(), "worker": None}))

        lock = RunnerLock(lock_file)
        with lock:
            payload = json.loads(lock_file.read_text())
            assert payload["runner"]["pid"] == os.getpid()

        assert not lock_file.exists()

    def test_reclaims_corrupt_lock_file(self, tmp_path: Path) -> None:
        """RunnerLock reclaims a malformed lock file."""
        lock_file = tmp_path / "runner.lock"
        lock_file.write_text("{not valid json")

        lock = RunnerLock(lock_file)
        with lock:
            payload = json.loads(lock_file.read_text())
            assert payload["runner"]["pid"] == os.getpid()
