"""Tests for the post-run summary emitted by ``--improve-prompts``.

Kept in a sibling file of ``test_runner_improve_prompts.py`` purely to keep
the summary-specific cases grouped; pytest collects both files the same way.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fix_die_repeat.config import (
    Settings,
    get_introspection_file_path,
    get_user_templates_dir,
)
from fix_die_repeat.runner_improve_prompts import ImprovePromptsManager

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


class _PiSpy:
    """Captures agent invocations so tests can assert on arguments."""

    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.calls: list[tuple[object, ...]] = []

    def __call__(self, *args: object, **_kwargs: object) -> tuple[int, str, str]:
        self.calls.append(args)
        return (self.returncode, "", "")


def _manager() -> ImprovePromptsManager:
    """Build a manager with a stub logger."""
    return ImprovePromptsManager(
        settings=Settings(),
        logger=logging.getLogger("test-improve-prompts-summary"),
    )


def _write_introspection(content: str) -> Path:
    """Seed <FDR_HOME>/introspection.yaml with the given raw content."""
    path = get_introspection_file_path()
    path.write_text(content)
    return path


_PENDING_ENTRY = (
    "date: '2026-04-01'\nstatus: pending\npr_number: 1\n"
    "pr_url: https://example.com/pr/1\nproject: demo\nthreads: []\n"
)


class TestSummary:
    """A successful run should print a summary of what pi changed."""

    def test_summary_lists_each_modified_template(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """When pi edits templates, the summary names every changed file."""
        _write_introspection(_PENDING_ENTRY)
        templates_dir = get_user_templates_dir()

        def editing_pi(*_args: object, **_kwargs: object) -> tuple[int, str, str]:
            (templates_dir / "local_review.j2").write_text("edited by pi\n")
            (templates_dir / "partials/_critical_checklist.j2").write_text("edited partial\n")
            return (0, "", "")

        manager = _manager()
        with caplog.at_level(logging.INFO, logger="test-improve-prompts-summary"):
            rc = manager.run_improve_prompts(editing_pi)

        assert rc == 0
        summary_index = next(
            (i for i, r in enumerate(caplog.records) if "Summary" in r.message),
            None,
        )
        assert summary_index is not None, "expected a [ImprovePrompts] Summary log record"
        post_summary = " ".join(r.message for r in caplog.records[summary_index:])
        assert "local_review.j2" in post_summary
        assert "_critical_checklist.j2" in post_summary

    def test_summary_reports_noop_explicitly(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A run where pi edits zero templates must say so, not go silent."""
        _write_introspection(_PENDING_ENTRY)
        spy = _PiSpy()
        manager = _manager()

        with caplog.at_level(logging.INFO, logger="test-improve-prompts-summary"):
            rc = manager.run_improve_prompts(spy)

        assert rc == 0
        summary_messages = [r.message for r in caplog.records if "Summary" in r.message]
        assert summary_messages, "expected at least one [ImprovePrompts] Summary log record"
        joined = " ".join(summary_messages).lower()
        assert any(
            phrase in joined
            for phrase in (
                "no template edits",
                "no edits made",
                "no changes made",
                "no templates modified",
            )
        )

    def test_summary_reports_introspection_entries_consumed(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The summary reports how many pending entries transitioned away."""
        path = _write_introspection(
            "date: '2026-04-01'\nstatus: pending\npr_number: 1\n"
            "pr_url: https://example.com/pr/1\nproject: demo\nthreads: []\n"
            "---\n"
            "date: '2026-04-02'\nstatus: pending\npr_number: 2\n"
            "pr_url: https://example.com/pr/2\nproject: demo\nthreads: []\n",
        )

        def consuming_pi(*_args: object, **_kwargs: object) -> tuple[int, str, str]:
            path.write_text(
                "date: '2026-04-01'\nstatus: reviewed\npr_number: 1\n"
                "pr_url: https://example.com/pr/1\nproject: demo\nthreads: []\n"
                "---\n"
                "date: '2026-04-02'\nstatus: reviewed\npr_number: 2\n"
                "pr_url: https://example.com/pr/2\nproject: demo\nthreads: []\n",
            )
            return (0, "", "")

        manager = _manager()
        with caplog.at_level(logging.INFO, logger="test-improve-prompts-summary"):
            rc = manager.run_improve_prompts(consuming_pi)

        assert rc == 0
        summary_messages = [r.message for r in caplog.records if "Summary" in r.message]
        assert summary_messages, "expected at least one [ImprovePrompts] Summary log record"
        joined = " ".join(summary_messages).lower()
        # Count "2" must appear with "entries" context, not just anywhere in dates/paths.
        assert "2 introspection" in joined or "2 entries" in joined or "2 pending" in joined

    def test_summary_shows_line_delta_per_template(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The per-template line should include a diff-derived hint (line delta)."""
        _write_introspection(_PENDING_ENTRY)
        templates_dir = get_user_templates_dir()

        def extending_pi(*_args: object, **_kwargs: object) -> tuple[int, str, str]:
            target = templates_dir / "fix_checks.j2"
            base = target.read_text()
            if not base.endswith("\n"):
                base += "\n"
            target.write_text(base + "line a\nline b\nline c\n")
            return (0, "", "")

        manager = _manager()
        with caplog.at_level(logging.INFO, logger="test-improve-prompts-summary"):
            rc = manager.run_improve_prompts(extending_pi)

        assert rc == 0
        summary_index = next(
            (i for i, r in enumerate(caplog.records) if "Summary" in r.message),
            None,
        )
        assert summary_index is not None, "expected a [ImprovePrompts] Summary log record"
        post_summary = " ".join(r.message for r in caplog.records[summary_index:])
        assert "fix_checks.j2" in post_summary
        assert "+3" in post_summary or "3 line" in post_summary

    def test_summary_reports_inline_rewrite_with_same_line_count(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """An in-place rewrite preserving line count must report non-zero +/- counts.

        Regression: the old net-delta metric reported ``(+0 lines)`` when pi
        reworded every line but kept the line count stable — indistinguishable
        from "no edit" in the log output.
        """
        _write_introspection(_PENDING_ENTRY)
        templates_dir = get_user_templates_dir()

        def rewriting_pi(*_args: object, **_kwargs: object) -> tuple[int, str, str]:
            target = templates_dir / "partials/_critical_checklist.j2"
            original = target.read_text().splitlines()
            rewritten = [f"ZZZ rewritten line {i}" for i in range(len(original))]
            target.write_text("\n".join(rewritten) + "\n")
            return (0, "", "")

        manager = _manager()
        with caplog.at_level(logging.INFO, logger="test-improve-prompts-summary"):
            rc = manager.run_improve_prompts(rewriting_pi)

        assert rc == 0
        summary_index = next(
            (i for i, r in enumerate(caplog.records) if "Summary" in r.message),
            None,
        )
        assert summary_index is not None, "expected a [ImprovePrompts] Summary log record"
        post_summary = " ".join(r.message for r in caplog.records[summary_index:])
        assert "_critical_checklist.j2" in post_summary
        assert "(+0 lines)" not in post_summary, (
            "a rewrite touching every line must not report the misleading old '+0 lines' format"
        )
        # The new format encodes both sides explicitly, e.g. "(+7 -7 lines)".
        # Require that the counts surfaced are non-zero on at least one side.
        assert "+0 -0" not in post_summary
        assert any(f"+{n}" in post_summary for n in range(1, 200))
        assert any(f"-{n}" in post_summary for n in range(1, 200))

    def test_summary_reports_trailing_newline_edit_with_non_zero_counts(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        r"""A trailing-newline-only edit must surface as non-zero +/- counts.

        Regression: ``splitlines()`` without ``keepends=True`` drops trailing
        terminators, so if pi only strips or adds a final ``\n`` the diff loop
        sees identical line lists and reports ``+0 -0`` — even though the
        ``pre != post`` guard already fired. The summary then contradicts
        itself: "this file changed ... (+0 -0 lines)".
        """
        _write_introspection(_PENDING_ENTRY)
        templates_dir = get_user_templates_dir()

        def strip_trailing_newline_pi(*_args: object, **_kwargs: object) -> tuple[int, str, str]:
            target = templates_dir / "fix_checks.j2"
            base = target.read_text()
            if base.endswith("\n"):
                target.write_text(base.rstrip("\n"))
            else:
                target.write_text(base + "\n")
            return (0, "", "")

        manager = _manager()
        with caplog.at_level(logging.INFO, logger="test-improve-prompts-summary"):
            rc = manager.run_improve_prompts(strip_trailing_newline_pi)

        assert rc == 0
        summary_index = next(
            (i for i, r in enumerate(caplog.records) if "Summary" in r.message),
            None,
        )
        assert summary_index is not None, "expected a [ImprovePrompts] Summary log record"
        post_summary = " ".join(r.message for r in caplog.records[summary_index:])
        assert "fix_checks.j2" in post_summary
        assert "+0 -0" not in post_summary, (
            "a trailing-newline edit must not report '+0 -0' once pre != post has already fired"
        )

    def test_summary_includes_pi_rationale(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Pi's marked summary block must be extracted and echoed in the log."""
        _write_introspection(_PENDING_ENTRY)
        templates_dir = get_user_templates_dir()

        def rationale_pi(*_args: object, **_kwargs: object) -> tuple[int, str, str]:
            (templates_dir / "fix_checks.j2").write_text("edited by pi\n")
            stdout = (
                "preamble noise that must not be echoed\n"
                "some tool-use chatter\n"
                "<IMPROVE_PROMPTS_SUMMARY>\n"
                "- fix_checks.j2 - added retry guidance\n"
                "- Entries reviewed: 1\n"
                "- Compaction: skipped\n"
                "</IMPROVE_PROMPTS_SUMMARY>\n"
                "trailing noise that must not be echoed\n"
            )
            return (0, stdout, "")

        manager = _manager()
        with caplog.at_level(logging.INFO, logger="test-improve-prompts-summary"):
            rc = manager.run_improve_prompts(rationale_pi)

        assert rc == 0
        pi_prefixed = [r.message for r in caplog.records if "agy:" in r.message]
        joined = "\n".join(pi_prefixed)
        assert "added retry guidance" in joined
        assert "Entries reviewed: 1" in joined
        assert "Compaction: skipped" in joined
        all_messages = " ".join(r.message for r in caplog.records)
        assert "preamble noise" not in all_messages
        assert "trailing noise" not in all_messages
        assert "tool-use chatter" not in all_messages

    def test_summary_tolerates_missing_markers(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """If pi ignores the marker contract, the runner warns once and moves on."""
        _write_introspection(_PENDING_ENTRY)
        templates_dir = get_user_templates_dir()

        def noncompliant_pi(*_args: object, **_kwargs: object) -> tuple[int, str, str]:
            (templates_dir / "fix_checks.j2").write_text("edited by pi\n")
            return (0, "pi produced output but forgot the markers entirely", "")

        manager = _manager()
        with caplog.at_level(logging.INFO, logger="test-improve-prompts-summary"):
            rc = manager.run_improve_prompts(noncompliant_pi)

        assert rc == 0
        warnings = [
            r.message
            for r in caplog.records
            if r.levelno >= logging.WARNING and "summary" in r.message.lower()
        ]
        assert len(warnings) == 1, "expected exactly one warning about the missing summary markers"
        pi_prefixed = [r.message for r in caplog.records if "agy:" in r.message]
        assert not pi_prefixed, "no rationale should be echoed when markers are missing"

    def test_summary_counts_added_lines_whose_content_starts_with_plus_plus_plus(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Added lines whose raw content begins with ``+++`` must be counted.

        Regression: the old header-skip check ``startswith(("+++", "---"))``
        also matched real added lines whose content starts with ``+++`` (since
        the diff line becomes ``++++...``), causing an undercount and a
        misleading ``+0 -0`` when every changed line happened to share that
        prefix.
        """
        _write_introspection(_PENDING_ENTRY)
        templates_dir = get_user_templates_dir()

        def plus_prefix_pi(*_args: object, **_kwargs: object) -> tuple[int, str, str]:
            target = templates_dir / "fix_checks.j2"
            target.write_text("+++marker line one\n+++marker line two\n")
            return (0, "", "")

        manager = _manager()
        with caplog.at_level(logging.INFO, logger="test-improve-prompts-summary"):
            rc = manager.run_improve_prompts(plus_prefix_pi)

        assert rc == 0
        summary_index = next(
            (i for i, r in enumerate(caplog.records) if "Summary" in r.message),
            None,
        )
        assert summary_index is not None, "expected a [ImprovePrompts] Summary log record"
        post_summary = " ".join(r.message for r in caplog.records[summary_index:])
        assert "fix_checks.j2" in post_summary
        assert "+2" in post_summary, (
            "two added lines starting with '+++' must both be counted as additions, "
            "not mistaken for diff headers"
        )

    def test_summary_silent_when_pi_stdout_is_empty(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Empty/whitespace-only pi stdout is benign and must not trigger a marker warning.

        Regression: previously ``_emit_pi_rationale`` logged a WARNING pointing
        users to ``pi.log`` whenever stdout was empty, conflating "no rationale
        produced" with "marker contract violated."
        """
        _write_introspection(_PENDING_ENTRY)
        templates_dir = get_user_templates_dir()

        def silent_pi(*_args: object, **_kwargs: object) -> tuple[int, str, str]:
            (templates_dir / "fix_checks.j2").write_text("edited by pi\n")
            return (0, "   \n\t\n", "")

        manager = _manager()
        with caplog.at_level(logging.INFO, logger="test-improve-prompts-summary"):
            rc = manager.run_improve_prompts(silent_pi)

        assert rc == 0
        marker_warnings = [
            r.message
            for r in caplog.records
            if r.levelno >= logging.WARNING and "markers" in r.message.lower()
        ]
        assert not marker_warnings, (
            "empty/whitespace-only pi stdout should not emit a marker-contract warning"
        )
        pi_prefixed = [r.message for r in caplog.records if "agy:" in r.message]
        assert not pi_prefixed, "no rationale lines should be echoed when stdout is empty"
