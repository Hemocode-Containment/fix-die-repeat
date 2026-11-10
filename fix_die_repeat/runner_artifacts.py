"""Artifact management for fix-die-repeat runner.

This module handles filtering, compacting, and managing persistent artifacts
such as review files, build history, and check logs.
"""

import logging
import re

from fix_die_repeat.config import Paths, Settings
from fix_die_repeat.messages import oscillation_warning
from fix_die_repeat.utils import (
    get_file_line_count,
    get_git_revision_hash,
)


class ArtifactManager:
    """Manages persistent artifacts for the fix-die-repeat runner.

    Handles:
    - Filtering check logs to extract relevant error information
    - Compacting large artifacts to stay within size limits
    - Detecting oscillation by tracking check output hashes
    """

    def __init__(self, settings: Settings, paths: Paths, logger: logging.Logger) -> None:
        """Initialize the artifact manager.

        Args:
            settings: Configuration settings
            paths: Path management
            logger: Logger instance for output

        """
        self.settings = settings
        self.paths = paths
        self.logger = logger

    def filter_checks_log(self) -> None:
        """Filter checks.log to extract the most useful failure information."""
        max_lines = 300
        context_lines = 3
        tail_lines = 80

        if not self.paths.checks_log.exists():
            return

        total_lines = get_file_line_count(self.paths.checks_log)

        if total_lines <= max_lines:
            # Log is small enough, just copy it
            self.paths.checks_filtered_log.write_text(self.paths.checks_log.read_text())
            return

        self.logger.info(
            "Filtering checks.log (%s lines -> ~%s target)...",
            total_lines,
            max_lines,
        )

        # Extract error lines with context
        error_pattern = re.compile(
            r"(error[:\[ ]|ERROR[:\[ ]|fatal|FATAL|FAILED|panic|exception|undefined reference|"
            r"cannot find|no such file|not found|segfault|abort|compilation failed|build failed|"
            r"assert)",
            re.IGNORECASE,
        )

        lines = self.paths.checks_log.read_text().splitlines()
        filtered_lines = [
            f"=== FILTERED CHECK OUTPUT (full log: {self.paths.checks_log}, "
            f"{total_lines} lines) ===",
            "",
            "--- Error/failure lines with context ---",
        ]

        seen_indices = set()
        for i, line in enumerate(lines):
            if error_pattern.search(line):
                start = max(0, i - context_lines)
                end = min(len(lines), i + context_lines + 1)
                for j in range(start, end):
                    if j not in seen_indices:
                        filtered_lines.append(lines[j])
                        seen_indices.add(j)

        # Add tail
        filtered_lines.extend(["", f"--- Last {tail_lines} lines ---"])
        filtered_lines.extend(lines[-tail_lines:])

        # Limit to 200 error lines + tail
        if len(filtered_lines) > max_lines:
            filtered_lines = filtered_lines[: max_lines - tail_lines] + lines[-tail_lines:]

        self.paths.checks_filtered_log.write_text("\n".join(filtered_lines))

        filtered_count = get_file_line_count(self.paths.checks_filtered_log)
        self.logger.info("Filtered checks.log: %s -> %s lines", total_lines, filtered_count)

    def check_oscillation(self, iteration: int) -> str | None:
        """Check for oscillation by tracking check output hashes.

        Args:
            iteration: Current iteration number

        Returns:
            Warning message if oscillation detected, None otherwise

        """
        current_hash = get_git_revision_hash(self.paths.checks_log)

        if self.paths.checks_hash_file.exists():
            hashes = self.paths.checks_hash_file.read_text().strip().split("\n")
            for entry in reversed(hashes):
                if entry.startswith(f"{current_hash}:"):
                    prev_iter = int(entry.split(":")[-1])
                    self.logger.info(
                        "Detected oscillation: iteration %s matches iteration %s",
                        iteration,
                        prev_iter,
                    )
                    warning = oscillation_warning(prev_iter)
                    # Record this hash
                    with self.paths.checks_hash_file.open("a") as f:
                        f.write(f"{current_hash}:{iteration}\n")
                    return warning

        # Record this hash
        with self.paths.checks_hash_file.open("a") as f:
            f.write(f"{current_hash}:{iteration}\n")

        return None

    def check_compaction_needed(self) -> tuple[bool, bool]:
        """Check if artifacts need compaction.

        Returns:
            Tuple of (needs_emergency, needs_compact)

        """
        needs_compact = False
        needs_emergency = False

        for f in [self.paths.review_file, self.paths.build_history_file]:
            if f.exists():
                line_count = get_file_line_count(f)
                if line_count > self.settings.emergency_threshold_lines:
                    needs_emergency = True
                    break
                if line_count > self.settings.compact_threshold_lines:
                    needs_compact = True

        return needs_emergency, needs_compact

    def perform_emergency_compaction(self) -> None:
        """Perform emergency compaction (truncate to 100 lines)."""
        self.logger.info(
            "Emergency: artifacts exceed %s lines. Truncating to last 100 lines...",
            self.settings.emergency_threshold_lines,
        )
        for f in [self.paths.review_file, self.paths.build_history_file]:
            if f.exists():
                lines = f.read_text().splitlines()[-100:]
                f.write_text("\n".join(lines))

    def perform_regular_compaction(self) -> None:
        """Perform regular compaction (truncate to 50 lines)."""
        self.logger.info(
            "Artifacts exceed %s lines. Compacting artifacts...",
            self.settings.compact_threshold_lines,
        )

        for f in [self.paths.review_file, self.paths.build_history_file]:
            if f.exists():
                before = get_file_line_count(f)
                lines = f.read_text().splitlines()[-50:]
                f.write_text("\n".join(lines))
                after = get_file_line_count(f)
                self.logger.info("Compacted %s from %s to %s lines", f.name, before, after)

    def check_and_compact_artifacts(self) -> bool:
        """Check and compact large persistent artifacts.

        Returns:
            True if compaction was performed, False otherwise

        """
        if not self.settings.compact_artifacts:
            return False

        needs_emergency, needs_compact = self.check_compaction_needed()

        if needs_emergency:
            self.perform_emergency_compaction()
            return True

        if needs_compact:
            self.perform_regular_compaction()
            return True

        return False

    def emergency_compact(self) -> None:
        """Force emergency truncation of artifacts."""
        for f in [self.paths.review_file, self.paths.build_history_file]:
            if f.exists():
                lines = f.read_text().splitlines()[-100:]
                f.write_text("\n".join(lines))
