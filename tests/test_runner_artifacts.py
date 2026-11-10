"""Tests for runner artifact management methods."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from fix_die_repeat import runner as runner_module
from fix_die_repeat.messages import oscillation_warning
from fix_die_repeat.runner import AgyRunner

# Constants for runner test values
TEST_PI_DELAY_SECONDS = 2
EXPECTED_PI_INVOCATION_COUNT = 2
EMERGENCY_COMPACT_LINES = 100
REGULAR_COMPACT_LINES = 50
FILTERED_CHECKS_LOG_SMALL_LINES = 100
FILTERED_CHECKS_LOG_MAX_LINES = 300


def get_file_line_count(path: Path) -> int:
    """Count file lines."""
    try:
        with path.open(encoding="utf-8", errors="ignore") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


class TestEmergencyCompaction:
    """Tests for emergency_compact method."""

    def test_emergency_compact_truncates_files(self, tmp_path: Path) -> None:
        """Test emergency compaction truncates files to 100 lines."""
        settings = MagicMock()
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.review_file = tmp_path / "review.md"
        paths.build_history_file = tmp_path / "build_history.md"
        paths.pi_log = tmp_path / "pi.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths

        # Create large files
        paths.review_file.write_text("\n".join(["line"] * 200))
        paths.build_history_file.write_text("\n".join(["line"] * 150))

        runner.emergency_compact()

        # Check they're truncated to 100 lines
        assert get_file_line_count(paths.review_file) == EMERGENCY_COMPACT_LINES
        assert get_file_line_count(paths.build_history_file) == EMERGENCY_COMPACT_LINES

    def test_emergency_compact_handles_nonexistent_files(self, tmp_path: Path) -> None:
        """Test emergency compaction handles missing files gracefully."""
        settings = MagicMock()
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.review_file = tmp_path / "review.md"
        paths.build_history_file = tmp_path / "build_history.md"
        paths.pi_log = tmp_path / "pi.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths

        # Don't create files
        runner.emergency_compact()

        # Should not raise exception


class TestCheckCompactionNeeded:
    """Tests for check_compaction_needed method."""

    def test_no_compaction_needed(self, tmp_path: Path) -> None:
        """Test when files are below thresholds."""
        settings = MagicMock()
        settings.compact_threshold_lines = 150
        settings.emergency_threshold_lines = 200
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.review_file = tmp_path / "review.md"
        paths.build_history_file = tmp_path / "build_history.md"
        paths.pi_log = tmp_path / "pi.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths

        # Create small files
        paths.review_file.write_text("\n".join(["line"] * 100))
        paths.build_history_file.write_text("\n".join(["line"] * 120))

        needs_emergency, needs_compact = runner.check_compaction_needed()

        assert not needs_emergency
        assert not needs_compact

    def test_compaction_needed(self, tmp_path: Path) -> None:
        """Test when files exceed regular threshold."""
        settings = MagicMock()
        settings.compact_threshold_lines = 150
        settings.emergency_threshold_lines = 200
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.review_file = tmp_path / "review.md"
        paths.build_history_file = tmp_path / "build_history.md"
        paths.pi_log = tmp_path / "pi.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths

        # Create files over regular threshold
        paths.review_file.write_text("\n".join(["line"] * 160))
        paths.build_history_file.write_text("\n".join(["line"] * 120))

        needs_emergency, needs_compact = runner.check_compaction_needed()

        assert not needs_emergency
        assert needs_compact

    def test_emergency_compaction_needed(self, tmp_path: Path) -> None:
        """Test when files exceed emergency threshold."""
        settings = MagicMock()
        settings.compact_threshold_lines = 150
        settings.emergency_threshold_lines = 200
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.review_file = tmp_path / "review.md"
        paths.build_history_file = tmp_path / "build_history.md"
        paths.pi_log = tmp_path / "pi.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths

        # Create files over emergency threshold
        paths.review_file.write_text("\n".join(["line"] * 250))
        paths.build_history_file.write_text("\n".join(["line"] * 120))

        needs_emergency, _needs_compact = runner.check_compaction_needed()

        assert needs_emergency

    def test_missing_files_no_compaction(self, tmp_path: Path) -> None:
        """Test that missing files don't trigger compaction."""
        settings = MagicMock()
        settings.compact_threshold_lines = 150
        settings.emergency_threshold_lines = 200
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.review_file = tmp_path / "review.md"
        paths.build_history_file = tmp_path / "build_history.md"
        paths.pi_log = tmp_path / "pi.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths

        # Don't create files
        needs_emergency, needs_compact = runner.check_compaction_needed()

        assert not needs_emergency
        assert not needs_compact


class TestPerformEmergencyCompaction:
    """Tests for perform_emergency_compaction method."""

    def test_emergency_compaction_logs_and_truncates(self, tmp_path: Path) -> None:
        """Test emergency compaction logs and truncates files."""
        settings = MagicMock()
        settings.emergency_threshold_lines = 200
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.review_file = tmp_path / "review.md"
        paths.build_history_file = tmp_path / "build_history.md"
        paths.pi_log = tmp_path / "pi.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        # Create large files
        paths.review_file.write_text("\n".join(["line"] * 300))
        paths.build_history_file.write_text("\n".join(["line"] * 250))

        runner.perform_emergency_compaction()

        # Check log was called
        assert runner.logger.info.called

        # Check truncation
        assert get_file_line_count(paths.review_file) == EMERGENCY_COMPACT_LINES
        assert get_file_line_count(paths.build_history_file) == EMERGENCY_COMPACT_LINES


class TestPerformRegularCompaction:
    """Tests for perform_regular_compaction method."""

    def test_regular_compaction_logs_and_truncates(self, tmp_path: Path) -> None:
        """Test regular compaction logs and truncates files to 50 lines."""
        settings = MagicMock()
        settings.compact_threshold_lines = 150
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.review_file = tmp_path / "review.md"
        paths.build_history_file = tmp_path / "build_history.md"
        paths.pi_log = tmp_path / "pi.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        # Create large files
        paths.review_file.write_text("\n".join(["line"] * 160))
        paths.build_history_file.write_text("\n".join(["line"] * 155))

        runner.perform_regular_compaction()

        # Check log was called
        assert runner.logger.info.called

        # Check truncation to 50 lines
        assert get_file_line_count(paths.review_file) == REGULAR_COMPACT_LINES
        assert get_file_line_count(paths.build_history_file) == REGULAR_COMPACT_LINES


class TestCheckOscillation:
    """Tests for check_oscillation method."""

    def test_no_oscillation_first_iteration(self, tmp_path: Path) -> None:
        """Test first iteration doesn't detect oscillation."""
        settings = MagicMock()
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.checks_log = tmp_path / "checks.log"
        paths.checks_hash_file = tmp_path / "checks_hashes"
        paths.pi_log = tmp_path / "pi.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.iteration = 1

        paths.checks_log.write_text("output 1")

        result = runner.check_oscillation()

        assert result is None

    def test_no_oscillation_different_hashes(self, tmp_path: Path) -> None:
        """Test different hashes don't trigger oscillation."""
        settings = MagicMock()
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.checks_log = tmp_path / "checks.log"
        paths.checks_hash_file = tmp_path / "checks_hashes"
        paths.pi_log = tmp_path / "pi.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.iteration = 2

        # Create hash file with different hash
        paths.checks_hash_file.write_text("abc123:1\n")

        paths.checks_log.write_text("output 2")

        result = runner.check_oscillation()

        assert result is None

    def test_oscillation_detected_same_hash(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test same hash triggers oscillation warning."""
        settings = MagicMock()
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.checks_log = tmp_path / "checks.log"
        paths.checks_hash_file = tmp_path / "checks_hashes"
        paths.pi_log = tmp_path / "pi.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.iteration = 3
        runner.logger = MagicMock()

        # Create hash file with a hash we'll match
        paths.checks_hash_file.write_text("abc123:1\ndef456:2\n")
        paths.checks_log.write_text("output 2")

        monkeypatch.setattr(runner_module, "get_git_revision_hash", lambda _path: "abc123")

        warning = runner.check_oscillation()

        assert warning == oscillation_warning(1)
        assert paths.checks_hash_file.read_text().splitlines()[-1] == "abc123:3"


class TestCheckAndCompactArtifacts:
    """Tests for check_and_compact_artifacts method."""

    def test_compaction_disabled(self, tmp_path: Path) -> None:
        """Test that compaction is skipped when disabled."""
        settings = MagicMock()
        settings.compact_artifacts = False
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.review_file = tmp_path / "review.md"
        paths.build_history_file = tmp_path / "build_history.md"
        paths.pi_log = tmp_path / "pi.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths

        result = runner.check_and_compact_artifacts()

        assert result is False

    def test_emergency_compaction_performed(self, tmp_path: Path) -> None:
        """Test emergency compaction is performed when needed."""
        settings = MagicMock()
        settings.compact_artifacts = True
        settings.emergency_threshold_lines = 200
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.review_file = tmp_path / "review.md"
        paths.build_history_file = tmp_path / "build_history.md"
        paths.pi_log = tmp_path / "pi.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        # Create large file
        paths.review_file.write_text("\n".join(["line"] * 300))

        result = runner.check_and_compact_artifacts()

        assert result is True
        assert get_file_line_count(paths.review_file) == EMERGENCY_COMPACT_LINES

    def test_regular_compaction_performed(self, tmp_path: Path) -> None:
        """Test regular compaction is performed when needed."""
        settings = MagicMock()
        settings.compact_artifacts = True
        settings.compact_threshold_lines = 150
        settings.emergency_threshold_lines = 200
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.review_file = tmp_path / "review.md"
        paths.build_history_file = tmp_path / "build_history.md"
        paths.pi_log = tmp_path / "pi.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        # Create file over regular threshold
        paths.review_file.write_text("\n".join(["line"] * 160))

        result = runner.check_and_compact_artifacts()

        assert result is True
        assert get_file_line_count(paths.review_file) == REGULAR_COMPACT_LINES

    def test_no_compaction_performed(self, tmp_path: Path) -> None:
        """Test no compaction when files are small."""
        settings = MagicMock()
        settings.compact_artifacts = True
        settings.compact_threshold_lines = 150
        settings.emergency_threshold_lines = 200
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.review_file = tmp_path / "review.md"
        paths.build_history_file = tmp_path / "build_history.md"
        paths.pi_log = tmp_path / "pi.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        # Create small files
        paths.review_file.write_text("\n".join(["line"] * EMERGENCY_COMPACT_LINES))

        result = runner.check_and_compact_artifacts()

        assert result is False
        assert get_file_line_count(paths.review_file) == EMERGENCY_COMPACT_LINES


class TestFilterChecksLog:
    """Tests for filter_checks_log method."""

    def test_filter_checks_log_small_file(self, tmp_path: Path) -> None:
        """Test filtering when log is small enough."""
        settings = MagicMock()
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.checks_log = tmp_path / "checks.log"
        paths.checks_filtered_log = tmp_path / "checks_filtered.log"
        paths.pi_log = tmp_path / "pi.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        # Create small log (under 300 lines)
        paths.checks_log.write_text("\n".join(["line"] * FILTERED_CHECKS_LOG_SMALL_LINES))

        runner.filter_checks_log()

        # Should just copy it
        assert paths.checks_filtered_log.exists()
        content = paths.checks_filtered_log.read_text()
        assert len(content.splitlines()) == FILTERED_CHECKS_LOG_SMALL_LINES

    def test_filter_checks_log_large_file(self, tmp_path: Path) -> None:
        """Test filtering when log exceeds threshold."""
        settings = MagicMock()
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.checks_log = tmp_path / "checks.log"
        paths.checks_filtered_log = tmp_path / "checks_filtered.log"
        paths.pi_log = tmp_path / "pi.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        # Create large log with error lines
        lines = []
        for i in range(400):
            if i % 50 == 0:
                lines.append(f"ERROR: error at line {i}")
            else:
                lines.append(f"line {i}")

        paths.checks_log.write_text("\n".join(lines))

        runner.filter_checks_log()

        # Should be filtered to ~300 lines max
        filtered_lines = paths.checks_filtered_log.read_text().splitlines()
        assert len(filtered_lines) <= FILTERED_CHECKS_LOG_MAX_LINES
        # Should contain error lines
        assert any("ERROR" in line for line in filtered_lines)

    def test_filter_checks_log_no_log_file(self, tmp_path: Path) -> None:
        """Test filtering when checks.log doesn't exist."""
        settings = MagicMock()
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.checks_log = tmp_path / "checks.log"
        paths.checks_filtered_log = tmp_path / "checks_filtered.log"
        paths.pi_log = tmp_path / "pi.log"

        runner = AgyRunner.__new__(AgyRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()

        # Don't create checks.log
        runner.filter_checks_log()

        # Should not create filtered log
        assert not paths.checks_filtered_log.exists()
