"""Tests for config module."""

import os
import shutil
from pathlib import Path

import pytest

from fix_die_repeat.config import (
    CliOptions,
    Paths,
    Settings,
    get_introspection_archive_file_path,
    get_introspection_file_path,
    get_settings,
    get_user_templates_dir,
)
from fix_die_repeat.utils import run_command

# Constants for default settings values
DEFAULT_MAX_ITERS = 10
DEFAULT_MAX_PR_THREADS = 5
TEST_MAX_ITERS = 5
TEST_MAX_PR_THREADS = 10


def _git_executable() -> str:
    """Return the absolute path to the git executable, or skip if unavailable."""
    git_path = shutil.which("git")
    if git_path is None:
        pytest.skip("git executable is required for this test")
    assert git_path is not None  # pytest.skip() raises, so this is never None
    return git_path


def _run_git_command(project_dir: Path, *args: str) -> None:
    """Run a git command and assert success."""
    git_executable = _git_executable()
    returncode, _stdout, stderr = run_command([git_executable, *args], cwd=project_dir, check=False)
    assert returncode == 0, stderr


def _init_git_repo(project_dir: Path, remote: str | None = None) -> None:
    """Initialize a git repository for path-discovery tests."""
    _run_git_command(project_dir, "init")
    _run_git_command(project_dir, "config", "user.email", "test@example.com")
    _run_git_command(project_dir, "config", "user.name", "Test User")
    if remote is not None:
        _run_git_command(project_dir, "remote", "add", "origin", remote)


class TestSettings:
    """Tests for Settings class."""

    def test_default_settings(self) -> None:
        """Test default settings values."""
        settings = Settings()
        assert settings.check_cmd is None
        assert settings.max_iters == DEFAULT_MAX_ITERS
        assert settings.model is None
        assert not settings.preflight
        assert settings.max_pr_threads == DEFAULT_MAX_PR_THREADS
        assert not settings.archive_artifacts
        assert settings.compact_artifacts
        assert not settings.pr_review
        assert not settings.debug
        assert settings.ntfy_enabled
        assert settings.ntfy_url == "http://localhost:2586"

    def test_settings_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test settings from environment variables."""
        monkeypatch.setenv("FDR_CHECK_CMD", "make test")
        monkeypatch.setenv("FDR_MAX_ITERS", "5")
        monkeypatch.setenv("FDR_MODEL", "anthropic/claude-sonnet-4-5")

        settings = Settings()
        assert settings.check_cmd == "make test"
        assert settings.max_iters == TEST_MAX_ITERS
        assert settings.model == "anthropic/claude-sonnet-4-5"

    def test_languages_default_none(self) -> None:
        """Test that languages defaults to None."""
        settings = Settings()
        assert settings.languages is None

    def test_languages_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that languages can be set via environment variable."""
        monkeypatch.setenv("FDR_LANGUAGES", "python,rust,elixir")

        settings = Settings()
        assert settings.languages == "python,rust,elixir"

    def test_pr_review_introspect_default(self) -> None:
        """Test that pr_review_introspect defaults to False."""
        settings = Settings()
        assert not settings.pr_review_introspect

    def test_pr_review_introspect_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that pr_review_introspect can be set via environment variable."""
        monkeypatch.setenv("FDR_PR_REVIEW_INTROSPECT", "1")
        settings = Settings()
        assert settings.pr_review_introspect

    def test_pr_review_introspect_implies_pr_review(self) -> None:
        """Test that pr_review_introspect=True also sets pr_review=True."""
        options = CliOptions(pr_review_introspect=True)
        settings = get_settings(options)

        assert settings.pr_review_introspect
        assert settings.pr_review, "pr_review should be True when pr_review_introspect is True"

    def test_pr_review_flag_standalone(self) -> None:
        """Test that --pr-review flag can be used without --pr-review-introspect."""
        options = CliOptions(pr_review=True, pr_review_introspect=False)
        settings = get_settings(options)

        assert settings.pr_review
        assert not settings.pr_review_introspect

    def test_improve_prompts_default(self) -> None:
        """improve_prompts defaults to False."""
        settings = Settings()
        assert not settings.improve_prompts

    def test_improve_prompts_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """improve_prompts can be set via FDR_IMPROVE_PROMPTS env var."""
        monkeypatch.setenv("FDR_IMPROVE_PROMPTS", "1")
        settings = Settings()
        assert settings.improve_prompts

    def test_improve_prompts_from_cli(self) -> None:
        """improve_prompts can be set via CliOptions."""
        options = CliOptions(improve_prompts=True)
        settings = get_settings(options)
        assert settings.improve_prompts

    def test_invalid_max_iters(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that invalid max_iters raises ValueError."""
        monkeypatch.setenv("FDR_MAX_ITERS", "0")

        settings = Settings()
        with pytest.raises(ValueError, match="must be a positive integer"):
            settings.validate_max_iters()

    def test_get_settings_with_cli_overrides(self) -> None:
        """Test get_settings with CLI parameter overrides."""
        options = CliOptions(
            check_cmd="pytest",
            max_iters=5,
            model="test-model",
            archive_artifacts=True,
            no_compact=True,
            pr_review=True,
            debug=True,
        )
        settings = get_settings(options)

        assert settings.check_cmd == "pytest"
        assert settings.max_iters == TEST_MAX_ITERS
        assert settings.model == "test-model"
        assert settings.archive_artifacts
        assert not settings.compact_artifacts
        assert settings.pr_review
        assert settings.debug

    def test_get_settings_with_preflight(self) -> None:
        """Test get_settings with preflight parameter."""
        options = CliOptions(preflight=True)
        settings = get_settings(options)

        assert settings.preflight is True

    def test_get_settings_with_max_pr_threads(self) -> None:
        """Test get_settings with max_pr_threads parameter (line 190)."""
        options = CliOptions(max_pr_threads=10)
        settings = get_settings(options)

        assert settings.max_pr_threads == TEST_MAX_PR_THREADS


SLUG_HASH_LEN = 8

EXPECTED_TEMPLATE_CONTEXT_KEYS = {
    "fdr_dir_path",
    "review_history_path",
    "review_current_path",
    "build_history_path",
    "checks_log_path",
    "checks_filtered_log_path",
    "diff_file_path",
    "resolved_threads_path",
    "config_file_path",
}


class TestPaths:
    """Tests for Paths class."""

    def test_fdr_dir_under_fdr_home(self, tmp_path: Path) -> None:
        """Paths.fdr_dir lives under FDR_HOME/repos/<slug>, not inside the repo."""
        project_dir = tmp_path / "myproj"
        project_dir.mkdir()
        _init_git_repo(project_dir)

        paths = Paths(project_root=project_dir)
        fdr_home = Path(os.environ["FDR_HOME"])

        assert paths.fdr_dir.parent == fdr_home / "repos"
        assert paths.fdr_dir.name.startswith("myproj-")
        # 8-char hash suffix
        suffix = paths.fdr_dir.name[len("myproj-") :]
        assert len(suffix) == SLUG_HASH_LEN
        # Nothing written inside the repo
        assert not (project_dir / ".fix-die-repeat").exists()

    def test_project_root_from_git(self, tmp_path: Path) -> None:
        """project_root is discovered from git toplevel when unspecified."""
        project_dir = tmp_path / "git_project"
        project_dir.mkdir()
        _init_git_repo(project_dir)

        original_cwd = Path.cwd()
        try:
            os.chdir(project_dir)
            paths = Paths()
            assert paths.project_root == project_dir
        finally:
            os.chdir(original_cwd)

    def test_project_root_without_git(self, tmp_path: Path) -> None:
        """project_root falls back to cwd when not in a git repo."""
        no_git_dir = tmp_path / "no_git_project"
        no_git_dir.mkdir()

        original_cwd = Path.cwd()
        try:
            os.chdir(no_git_dir)
            paths = Paths()
            assert paths.project_root == no_git_dir
        finally:
            os.chdir(original_cwd)

    def test_slug_is_stable_across_calls(self, tmp_path: Path) -> None:
        """Same project_root yields the same slug on repeated construction."""
        project_dir = tmp_path / "stable"
        project_dir.mkdir()
        _init_git_repo(project_dir, remote="https://example.com/a/b.git")

        first = Paths(project_root=project_dir).fdr_dir.name
        second = Paths(project_root=project_dir).fdr_dir.name
        assert first == second

    def test_slug_varies_with_remote(self, tmp_path: Path) -> None:
        """Two repos with identical basenames but different remotes get different slugs."""
        a = tmp_path / "a" / "proj"
        b = tmp_path / "b" / "proj"
        a.mkdir(parents=True)
        b.mkdir(parents=True)
        _init_git_repo(a, remote="https://example.com/one/proj.git")
        _init_git_repo(b, remote="https://example.com/two/proj.git")

        # Basenames match, so any slug difference must come from the remote hash
        slug_a = Paths(project_root=a).fdr_dir.name
        slug_b = Paths(project_root=b).fdr_dir.name
        assert slug_a != slug_b

    def test_slug_matches_same_remote(self, tmp_path: Path) -> None:
        """Two clones of the same origin remote share the same hash suffix."""
        a = tmp_path / "clone_one"
        b = tmp_path / "clone_two"
        a.mkdir()
        b.mkdir()
        remote = "https://example.com/shared/proj.git"
        _init_git_repo(a, remote=remote)
        _init_git_repo(b, remote=remote)

        # Slug has form <basename>-<hash>. Hashes should match even though
        # basenames differ.
        slug_a = Paths(project_root=a).fdr_dir.name
        slug_b = Paths(project_root=b).fdr_dir.name
        assert slug_a.split("-")[-1] == slug_b.split("-")[-1]

    def test_slug_without_remote_falls_back_to_path(self, tmp_path: Path) -> None:
        """With no git remote, the slug still resolves (path-based hash)."""
        project_dir = tmp_path / "no_remote"
        project_dir.mkdir()
        _init_git_repo(project_dir)  # no remote added

        paths = Paths(project_root=project_dir)
        assert paths.fdr_dir.name.startswith("no_remote-")
        suffix = paths.fdr_dir.name[len("no_remote-") :]
        assert len(suffix) == SLUG_HASH_LEN

    def test_ensure_fdr_dir_creates_central_dir(self, tmp_path: Path) -> None:
        """ensure_fdr_dir creates the central state dir."""
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        _init_git_repo(project_dir)

        paths = Paths(project_root=project_dir)
        paths.ensure_fdr_dir()

        assert paths.fdr_dir.exists()
        assert paths.fdr_dir.is_dir()

    def test_ensure_fdr_dir_does_not_touch_repo(self, tmp_path: Path) -> None:
        """ensure_fdr_dir must NOT create or modify .gitignore in the repo."""
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        _init_git_repo(project_dir)

        # Pre-existing gitignore should remain untouched
        gitignore = project_dir / ".gitignore"
        gitignore.write_text("*.pyc\n__pycache__/\n")
        original = gitignore.read_text()

        paths = Paths(project_root=project_dir)
        paths.ensure_fdr_dir()

        assert gitignore.read_text() == original
        assert ".fix-die-repeat" not in gitignore.read_text()
        # And no .fix-die-repeat/ created in the repo
        assert not (project_dir / ".fix-die-repeat").exists()

    def test_ensure_fdr_dir_does_not_create_gitignore(self, tmp_path: Path) -> None:
        """ensure_fdr_dir must not create a .gitignore if one doesn't exist."""
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        _init_git_repo(project_dir)

        gitignore = project_dir / ".gitignore"
        if gitignore.exists():
            gitignore.unlink()

        paths = Paths(project_root=project_dir)
        paths.ensure_fdr_dir()

        assert not gitignore.exists()

    def test_path_properties_all_under_fdr_dir(self, tmp_path: Path) -> None:
        """Every path attribute is rooted at the central fdr_dir."""
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        _init_git_repo(project_dir)

        paths = Paths(project_root=project_dir)

        assert paths.review_file == paths.fdr_dir / "review.md"
        assert paths.review_current_file == paths.fdr_dir / "review_current.md"
        assert paths.review_recent_file == paths.fdr_dir / "review_recent.md"
        assert paths.build_history_file == paths.fdr_dir / "build_history.md"
        assert paths.checks_log == paths.fdr_dir / "checks.log"
        assert paths.checks_filtered_log == paths.fdr_dir / "checks_filtered.log"
        assert paths.checks_hash_file == paths.fdr_dir / ".checks_hashes"
        assert paths.agy_log == paths.fdr_dir / "agy.log"
        assert paths.agy_transcripts_dir == paths.fdr_dir / "agy"
        assert paths.runner_lock_file == paths.fdr_dir / "runner.lock"
        assert paths.fdr_log == paths.fdr_dir / "fdr.log"
        assert paths.pr_threads_cache == paths.fdr_dir / ".pr_threads_cache"
        assert paths.pr_threads_hash_file == paths.fdr_dir / ".pr_threads_hash"
        assert paths.start_sha_file == paths.fdr_dir / ".start_sha"
        assert paths.pr_thread_ids_file == paths.fdr_dir / ".pr_thread_ids_in_scope"
        assert paths.pr_resolved_threads_file == paths.fdr_dir / ".resolved_threads"
        assert paths.diff_file == paths.fdr_dir / "changes.diff"
        assert paths.run_timestamps_file == paths.fdr_dir / "run_timestamps.md"
        assert paths.introspection_data_file == paths.fdr_dir / ".introspection_data.yaml"
        assert paths.introspection_result_file == paths.fdr_dir / ".introspection_result.yaml"

    def test_template_context_keys_are_fixed(self, tmp_path: Path) -> None:
        """Paths.template_context() returns the expected pinned key set."""
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        _init_git_repo(project_dir)

        paths = Paths(project_root=project_dir)
        ctx = paths.template_context()

        assert set(ctx.keys()) == EXPECTED_TEMPLATE_CONTEXT_KEYS
        for key, value in ctx.items():
            assert isinstance(value, str), f"{key} must be a string"
            assert Path(value).is_absolute(), f"{key} must be absolute: {value}"


class TestGetIntrospectionFilePath:
    """Tests for get_introspection_file_path function."""

    def test_returns_path_object(self) -> None:
        """get_introspection_file_path returns a Path object."""
        path = get_introspection_file_path()
        assert isinstance(path, Path)

    def test_lives_under_fdr_home(self) -> None:
        """The introspection file lives at FDR_HOME/introspection.yaml."""
        path = get_introspection_file_path()
        fdr_home = Path(os.environ["FDR_HOME"])
        assert path == fdr_home / "introspection.yaml"

    def test_creates_parent_directories(self) -> None:
        """Parent directory is created if missing."""
        fdr_home = Path(os.environ["FDR_HOME"])
        if fdr_home.exists():
            shutil.rmtree(fdr_home)

        path = get_introspection_file_path()

        assert path.parent.exists()
        assert path.parent.is_dir()


class TestGetIntrospectionArchiveFilePath:
    """Tests for get_introspection_archive_file_path function."""

    def test_lives_under_fdr_home(self) -> None:
        """The archive file path lives at FDR_HOME/introspection-archive.yaml."""
        path = get_introspection_archive_file_path()
        fdr_home = Path(os.environ["FDR_HOME"])
        assert path == fdr_home / "introspection-archive.yaml"

    def test_does_not_create_parent(self) -> None:
        """Unlike the main introspection path, the archive helper does not create FDR_HOME."""
        fdr_home = Path(os.environ["FDR_HOME"])
        if fdr_home.exists():
            shutil.rmtree(fdr_home)

        path = get_introspection_archive_file_path()

        # The archive is only materialized by the compaction step itself,
        # so the helper must not eagerly create the parent directory.
        assert not fdr_home.exists()
        assert path.parent == fdr_home


class TestGetUserTemplatesDir:
    """Tests for get_user_templates_dir function."""

    def test_lives_under_fdr_home(self) -> None:
        """The user templates dir lives at FDR_HOME/templates/."""
        path = get_user_templates_dir()
        fdr_home = Path(os.environ["FDR_HOME"])
        assert path == fdr_home / "templates"

    def test_does_not_create_directory(self) -> None:
        """The helper does not create the directory on access."""
        fdr_home = Path(os.environ["FDR_HOME"])
        if fdr_home.exists():
            shutil.rmtree(fdr_home)

        path = get_user_templates_dir()

        assert not path.exists()
