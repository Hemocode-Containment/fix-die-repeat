"""Tests for the --improve-prompts mode manager."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from fix_die_repeat.agy_policy import AgentRole
from fix_die_repeat.config import (
    Settings,
    get_introspection_archive_file_path,
    get_introspection_file_path,
    get_user_templates_dir,
)
from fix_die_repeat.runner_improve_prompts import (
    EDITABLE_TEMPLATES,
    ImprovePromptsManager,
)
from fix_die_repeat.runner_introspection import _FileLock  # Testing private class is intentional

if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest

# Non-zero pi exit code used to verify failure propagation.
PI_FAILURE_EXIT_CODE = 3


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
        logger=logging.getLogger("test-improve-prompts"),
    )


def _write_introspection(content: str) -> Path:
    """Seed <FDR_HOME>/introspection.yaml with the given raw content."""
    path = get_introspection_file_path()
    path.write_text(content)
    return path


class TestPendingDetection:
    """run_improve_prompts should short-circuit unless there's pending work."""

    def test_missing_file_exits_zero_without_calling_pi(self) -> None:
        """No introspection file means nothing to do — and no dotfolder is created."""
        fdr_home = Path(os.environ["FDR_HOME"])
        assert not fdr_home.exists(), "precondition: FDR_HOME starts clean"
        spy = _PiSpy()
        manager = _manager()

        rc = manager.run_improve_prompts(spy)

        assert rc == 0
        assert spy.calls == []
        # And no user templates should be seeded yet
        assert not get_user_templates_dir().exists()
        # No-op runs must not materialize <FDR_HOME>/ as a side effect.
        assert not fdr_home.exists()

    def test_empty_file_exits_zero_without_calling_pi(self) -> None:
        """An empty introspection file is treated the same as missing."""
        _write_introspection("")
        spy = _PiSpy()
        manager = _manager()

        rc = manager.run_improve_prompts(spy)

        assert rc == 0
        assert spy.calls == []

    def test_only_reviewed_entries_skip(self) -> None:
        """If every entry is already reviewed, skip invoking pi."""
        _write_introspection(
            "date: '2025-01-01'\nstatus: reviewed\nthreads: []\n"
            "---\n"
            "date: '2025-02-01'\nstatus: reviewed\nthreads: []\n",
        )
        spy = _PiSpy()
        manager = _manager()

        rc = manager.run_improve_prompts(spy)

        assert rc == 0
        assert spy.calls == []

    def test_malformed_yaml_skips_gracefully(self) -> None:
        """A malformed YAML file should not crash the mode."""
        _write_introspection(":\n  : bad-indent: :\n  -")
        spy = _PiSpy()
        manager = _manager()

        rc = manager.run_improve_prompts(spy)

        assert rc == 0
        assert spy.calls == []


class TestSeedingAndPiInvocation:
    """When there's pending work, seed templates and call pi."""

    def test_seeds_missing_user_templates(self) -> None:
        """All four editable templates materialize in the user dir on first run."""
        _write_introspection(
            "date: '2026-04-01'\nstatus: pending\npr_number: 1\npr_url: https://example.com/pr/1\n"
            "project: demo\nthreads: []\n",
        )
        spy = _PiSpy()
        manager = _manager()

        rc = manager.run_improve_prompts(spy)

        assert rc == 0
        templates_dir = get_user_templates_dir()
        for name in EDITABLE_TEMPLATES:
            target = templates_dir / name
            assert target.exists(), f"{name} should be seeded"
            assert target.read_text(), f"{name} seeded file is empty"

    def test_does_not_overwrite_existing_user_templates(self) -> None:
        """If the user already customized a template, seeding must not clobber it."""
        templates_dir = get_user_templates_dir()
        templates_dir.mkdir(parents=True, exist_ok=True)
        customized = templates_dir / "local_review.j2"
        customized.write_text("USER CUSTOMIZED {{ review_history_path }}")

        _write_introspection(
            "date: '2026-04-01'\nstatus: pending\npr_number: 1\npr_url: https://example.com/pr/1\n"
            "project: demo\nthreads: []\n",
        )
        spy = _PiSpy()
        manager = _manager()

        rc = manager.run_improve_prompts(spy)

        assert rc == 0
        # User version preserved
        assert customized.read_text() == "USER CUSTOMIZED {{ review_history_path }}"
        # Others still seeded
        for name in EDITABLE_TEMPLATES:
            if name == "local_review.j2":
                continue
            assert (templates_dir / name).exists()

    def test_invokes_pi_with_read_write_edit_tools(self) -> None:
        """The agent call uses AgentRole.IMPROVE_PROMPTS and carries the rendered prompt."""
        _write_introspection(
            "date: '2026-04-01'\nstatus: pending\npr_number: 1\npr_url: https://example.com/pr/1\n"
            "project: demo\nthreads: []\n",
        )
        spy = _PiSpy()
        manager = _manager()

        manager.run_improve_prompts(spy)

        assert len(spy.calls) == 1
        args = spy.calls[0]
        assert args[0] == AgentRole.IMPROVE_PROMPTS
        prompt = args[1]
        assert isinstance(prompt, str)
        # Rendered prompt should reference the introspection file and every editable template
        assert str(get_introspection_file_path()) in prompt
        for name in EDITABLE_TEMPLATES:
            assert name in prompt

    def test_propagates_pi_nonzero_exit(self) -> None:
        """A non-zero pi exit is surfaced as the mode's exit code."""
        _write_introspection(
            "date: '2026-04-01'\nstatus: pending\npr_number: 1\npr_url: https://example.com/pr/1\n"
            "project: demo\nthreads: []\n",
        )
        spy = _PiSpy(returncode=PI_FAILURE_EXIT_CODE)
        manager = _manager()

        rc = manager.run_improve_prompts(spy)

        assert rc == PI_FAILURE_EXIT_CODE


class TestEnsureUserTemplatesName:
    """The seeding helper should be named for its ensure-or-noop semantics."""

    def test_method_named_ensure_user_templates(self) -> None:
        """ImprovePromptsManager exposes _ensure_user_templates (the rename from _seed_...)."""
        assert hasattr(ImprovePromptsManager, "_ensure_user_templates"), (
            "_seed_user_templates was renamed to _ensure_user_templates because it "
            "returns all paths regardless of whether they were freshly seeded"
        )
        assert not hasattr(ImprovePromptsManager, "_seed_user_templates"), (
            "old name should be gone — rename, don't alias"
        )


class TestFileLockHeldDuringPi:
    """run_improve_prompts must serialize with other FDR processes via _FileLock."""

    def test_holds_file_lock_around_pi_call(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The pi callback must run inside a _FileLock context on the introspection file."""
        _write_introspection(
            "date: '2026-04-01'\nstatus: pending\npr_number: 1\npr_url: https://example.com/pr/1\n"
            "project: demo\nthreads: []\n",
        )

        events: list[str] = []

        original_enter = _FileLock.__enter__
        original_exit = _FileLock.__exit__

        def spy_enter(self: _FileLock) -> object:
            events.append("lock-enter")
            return original_enter(self)

        def spy_exit(
            self: _FileLock,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: object,
        ) -> None:
            events.append("lock-exit")
            return original_exit(self, exc_type, exc_val, exc_tb)  # type: ignore[arg-type]

        monkeypatch.setattr(_FileLock, "__enter__", spy_enter)
        monkeypatch.setattr(_FileLock, "__exit__", spy_exit)

        def pi_spy(*_args: object, **_kwargs: object) -> tuple[int, str, str]:
            events.append("pi")
            return (0, "", "")

        manager = _manager()
        rc = manager.run_improve_prompts(pi_spy)

        assert rc == 0
        assert events == ["lock-enter", "pi", "lock-exit"], (
            f"pi must run between lock acquire and release; got {events!r}"
        )


class TestValidationAndRollback:
    """The introspection file must survive a misbehaving pi."""

    _PENDING_ENTRY = (
        "date: '2026-04-01'\nstatus: pending\npr_number: 1\n"
        "pr_url: https://example.com/pr/1\nproject: demo\nthreads: []\n"
    )

    def test_rolls_back_when_pi_corrupts_introspection_file(self) -> None:
        """If pi leaves malformed YAML, restore pre-pi content and return non-zero."""
        path = _write_introspection(self._PENDING_ENTRY)
        original = path.read_text()

        def corrupting_pi(*_args: object, **_kwargs: object) -> tuple[int, str, str]:
            path.write_text(": : : bad yaml : : :")
            return (0, "", "")

        manager = _manager()
        rc = manager.run_improve_prompts(corrupting_pi)

        assert path.read_text() == original, "pre-pi content must be restored"
        assert rc != 0, "rollback must surface a non-zero exit code"

    def test_accepts_valid_pi_edits(self) -> None:
        """Valid YAML after pi must be kept as-is with a zero exit."""
        path = _write_introspection(self._PENDING_ENTRY)
        new_content = (
            "date: '2026-04-01'\nstatus: reviewed\npr_number: 1\n"
            "pr_url: https://example.com/pr/1\nproject: demo\nthreads: []\n"
        )

        def well_behaved_pi(*_args: object, **_kwargs: object) -> tuple[int, str, str]:
            path.write_text(new_content)
            return (0, "", "")

        manager = _manager()
        rc = manager.run_improve_prompts(well_behaved_pi)

        assert rc == 0
        assert path.read_text() == new_content

    def test_rolls_back_when_pi_corrupts_archive(self) -> None:
        """If pi creates/rewrites a malformed archive, rollback both files."""
        path = _write_introspection(self._PENDING_ENTRY)
        archive = get_introspection_archive_file_path()
        # Pre-existing valid archive that pi will clobber.
        archive_original = (
            "date: '2025-01-01'\nstatus: reviewed\npr_number: 99\n"
            "pr_url: https://example.com/pr/99\nproject: demo\nthreads: []\n"
        )
        archive.write_text(archive_original)
        main_original = path.read_text()

        def archive_corrupting_pi(*_args: object, **_kwargs: object) -> tuple[int, str, str]:
            # pi leaves main file valid but archive malformed.
            path.write_text(self._PENDING_ENTRY.replace("pending", "reviewed"))
            archive.write_text(": : : bad yaml : : :")
            return (0, "", "")

        manager = _manager()
        rc = manager.run_improve_prompts(archive_corrupting_pi)

        assert rc != 0
        assert path.read_text() == main_original, "main file must roll back too"
        assert archive.read_text() == archive_original, "archive must roll back"

    def test_rolls_back_when_pi_deletes_introspection_file(self) -> None:
        """If pi unlinks the introspection file, restore it and return non-zero."""
        path = _write_introspection(self._PENDING_ENTRY)
        original = path.read_text()

        def deleting_pi(*_args: object, **_kwargs: object) -> tuple[int, str, str]:
            path.unlink()
            return (0, "", "")

        manager = _manager()
        rc = manager.run_improve_prompts(deleting_pi)

        assert path.exists(), "missing introspection file must be restored"
        assert path.read_text() == original, "pre-pi content must be restored"
        assert rc != 0, "rollback must surface a non-zero exit code"

    def test_rolls_back_when_pi_deletes_pre_existing_archive(self) -> None:
        """If pi unlinks an archive that existed before, restore it and return non-zero."""
        path = _write_introspection(self._PENDING_ENTRY)
        archive = get_introspection_archive_file_path()
        archive_original = (
            "date: '2025-01-01'\nstatus: reviewed\npr_number: 99\n"
            "pr_url: https://example.com/pr/99\nproject: demo\nthreads: []\n"
        )
        archive.write_text(archive_original)
        main_original = path.read_text()

        def archive_deleting_pi(*_args: object, **_kwargs: object) -> tuple[int, str, str]:
            path.write_text(self._PENDING_ENTRY.replace("pending", "reviewed"))
            archive.unlink()
            return (0, "", "")

        manager = _manager()
        rc = manager.run_improve_prompts(archive_deleting_pi)

        assert rc != 0
        assert path.read_text() == main_original, "main file must roll back too"
        assert archive.exists(), "pre-existing archive must be restored"
        assert archive.read_text() == archive_original, "archive must roll back"

    def test_no_backup_files_left_on_success(self) -> None:
        """Happy path must not leave .bak files under FDR_HOME."""
        _write_introspection(self._PENDING_ENTRY)

        manager = _manager()
        manager.run_improve_prompts(lambda *_a, **_k: (0, "", ""))

    def test_rolls_back_when_template_has_invalid_jinja_syntax(self) -> None:
        """If agent writes invalid Jinja2 syntax to a user template, roll back."""
        _write_introspection(self._PENDING_ENTRY)
        templates_dir = get_user_templates_dir()

        def bad_jinja_agent(*_args: object, **_kwargs: object) -> tuple[int, str, str]:
            (templates_dir / "fix_checks.j2").write_text("{% if unclosed %}")
            return (0, "", "")

        manager = _manager()
        rc = manager.run_improve_prompts(bad_jinja_agent)

        assert rc != 0
        assert "{% if unclosed %}" not in (templates_dir / "fix_checks.j2").read_text()

        fdr_home = Path(os.environ["FDR_HOME"])
        assert not list(fdr_home.rglob("*.bak"))

    def test_no_backup_files_left_on_rollback(self) -> None:
        """Rollback path must also clean up .bak files."""
        path = _write_introspection(self._PENDING_ENTRY)

        def corrupting_pi(*_args: object, **_kwargs: object) -> tuple[int, str, str]:
            path.write_text(": : : bad yaml : : :")
            return (0, "", "")

        manager = _manager()
        manager.run_improve_prompts(corrupting_pi)

        fdr_home = Path(os.environ["FDR_HOME"])
        assert not list(fdr_home.rglob("*.bak"))

    def test_readonly_introspection_file_returns_nonzero_without_crashing(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A non-writable introspection file must log and exit cleanly, not crash."""
        path = _write_introspection(self._PENDING_ENTRY)
        original_mode = path.stat().st_mode
        path.chmod(0o400)  # read-only
        try:
            spy = _PiSpy()
            manager = _manager()

            with caplog.at_level(logging.ERROR, logger="test-improve-prompts"):
                rc = manager.run_improve_prompts(spy)

            assert rc != 0, "read-only file must surface a non-zero exit code"
            assert spy.calls == [], "pi must not be invoked if we can't lock the file"
            assert any(str(path) in record.message for record in caplog.records), (
                "error log must name the unwritable path"
            )
        finally:
            path.chmod(original_mode)


class TestCacheClear:
    """After pi finishes, the Jinja cache should be cleared so edits are visible."""

    def test_clears_prompt_cache_after_run(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The manager calls clear_prompt_cache after invoking pi."""
        cleared: list[bool] = []

        def _spy_clear() -> None:
            cleared.append(True)

        monkeypatch.setattr(
            "fix_die_repeat.runner_improve_prompts.clear_prompt_cache",
            _spy_clear,
        )

        _write_introspection(
            "date: '2026-04-01'\nstatus: pending\npr_number: 1\npr_url: https://example.com/pr/1\n"
            "project: demo\nthreads: []\n",
        )
        spy: Callable[..., tuple[int, str, str]] = _PiSpy()
        manager = _manager()

        manager.run_improve_prompts(spy)

        assert cleared == [True]
