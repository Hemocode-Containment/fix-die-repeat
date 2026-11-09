"""Tests for prompt template rendering."""

from pathlib import Path

import pytest
from jinja2 import UndefinedError

from fix_die_repeat.config import get_user_templates_dir
from fix_die_repeat.prompts import clear_prompt_cache, render_prompt
from tests.conftest import FAKE_TEMPLATE_CONTEXT

# Reuse the shared constant so template-context keys stay aligned across tests.
FAKE_PATHS = FAKE_TEMPLATE_CONTEXT


class TestRenderPrompt:
    """Tests for render_prompt."""

    def test_fix_checks_template_with_optional_sections(self) -> None:
        """Render fix_checks prompt with optional context included."""
        prompt = render_prompt(
            "fix_checks.j2",
            check_cmd="pytest",
            oscillation_warning="WARNING: oscillating",
            include_review_history=True,
            include_build_history=True,
            context_mode="pull",
            large_context_list="- app.py\n- tests/test_app.py",
            large_file_warning="CRITICAL WARNING: large file",
            languages=["python", "javascript"],
            **FAKE_PATHS,
        )

        assert "`pytest`" in prompt
        assert "WARNING: oscillating" in prompt
        assert FAKE_PATHS["review_history_path"] in prompt
        assert FAKE_PATHS["build_history_path"] in prompt
        assert "- app.py" in prompt
        assert "CRITICAL WARNING: large file" in prompt
        assert "ANALYZE the log" in prompt
        assert "APPLY the fix" in prompt
        assert "python, javascript" in prompt
        assert "Use idiomatic patterns" in prompt

    def test_fix_checks_template_without_optional_sections(self) -> None:
        """Render fix_checks prompt without optional sections."""
        prompt = render_prompt(
            "fix_checks.j2",
            check_cmd="./scripts/ci.sh",
            oscillation_warning="",
            include_review_history=False,
            include_build_history=False,
            context_mode="push",
            large_context_list="",
            large_file_warning="",
            languages=[],
            **FAKE_PATHS,
        )

        assert "`./scripts/ci.sh`" in prompt
        assert FAKE_PATHS["review_history_path"] not in prompt
        assert FAKE_PATHS["build_history_path"] not in prompt
        assert "I have also attached the currently changed files for context." in prompt
        assert "Use idiomatic patterns" not in prompt

    def test_missing_template_context_raises(self) -> None:
        """Raise when required template variables are missing."""
        with pytest.raises(UndefinedError):
            render_prompt("local_review.j2")

    def test_local_review_with_languages(self) -> None:
        """Render local_review prompt with language-specific checks."""
        prompt = render_prompt(
            "local_review.j2",
            review_prompt_prefix="",
            languages=["python", "rust"],
            **FAKE_PATHS,
        )

        assert "LANGUAGE-SPECIFIC CHECKS:" in prompt
        assert "Python:" in prompt
        assert "Rust:" in prompt

    def test_local_review_without_languages(self) -> None:
        """Render local_review prompt with no language-specific checks."""
        prompt = render_prompt(
            "local_review.j2",
            review_prompt_prefix="",
            languages=[],
            **FAKE_PATHS,
        )

        assert "No test configuration changes without explicit approval" in prompt
        assert "CRITICAL REVIEW CHECKLIST (LANGUAGE-AGNOSTIC):" in prompt
        assert (
            "Data serialization: structured outputs use safe serializers "
            "and are validated against expected schema" in prompt
        )
        assert "library/orchestration code returns status instead of terminating" in prompt
        assert "CLI layers propagate failure codes" in prompt
        assert "tests don't leak global state" in prompt
        assert "LANGUAGE-SPECIFIC CHECKS:" not in prompt

    def test_introspect_pr_review_template(self, tmp_path: Path) -> None:
        """Render introspect_pr_review template with all required variables."""
        output_path = tmp_path / "result.yaml"
        prompt = render_prompt(
            "introspect_pr_review.j2",
            run_date="2026-02-26",
            project_name="test-project",
            pr_number=123,
            pr_url="https://github.com/owner/repo/pull/123",
            output_path=str(output_path),
        )

        assert "2026-02-26" in prompt
        assert "test-project" in prompt
        assert "123" in prompt
        assert "https://github.com/owner/repo/pull/123" in prompt
        assert str(output_path) in prompt
        assert "YAML document" in prompt
        assert "GraphQL thread ID" in prompt
        assert "security, error-handling, performance" in prompt
        assert "JSON schema" in prompt


class TestLanguageSpecificRendering:
    """Tests for language-conditional template rendering."""

    def test_local_review_with_single_language(self) -> None:
        """Review prompt with one language includes only that partial."""
        prompt = render_prompt(
            "local_review.j2",
            review_prompt_prefix="",
            languages=["python"],
            **FAKE_PATHS,
        )

        assert "LANGUAGE-SPECIFIC CHECKS:" in prompt
        assert "Python:" in prompt
        assert "Rust:" not in prompt
        assert "JavaScript/TypeScript:" not in prompt
        # Check Python-specific content
        assert "No `eval()`/`exec()`/`compile()` with untrusted input" in prompt
        assert "No bare `except:`" in prompt
        assert "No mutable default arguments" in prompt

    def test_local_review_with_multiple_languages(self) -> None:
        """Review prompt with multiple languages includes all partials."""
        prompt = render_prompt(
            "local_review.j2",
            review_prompt_prefix="",
            languages=["python", "rust", "elixir"],
            **FAKE_PATHS,
        )

        assert "LANGUAGE-SPECIFIC CHECKS:" in prompt
        assert "Python:" in prompt
        assert "Rust:" in prompt
        assert "Elixir/Phoenix:" in prompt
        # Check Python content
        assert "No `eval()`/`exec()`/`compile()`" in prompt
        # Check Rust content
        assert "`unsafe` blocks are justified" in prompt
        # Check Elixir content
        assert "No `Code.eval_string()`" in prompt

    def test_local_review_with_empty_languages(self) -> None:
        """Review prompt with empty languages list has no language section."""
        prompt = render_prompt(
            "local_review.j2",
            review_prompt_prefix="",
            languages=[],
            **FAKE_PATHS,
        )

        assert "LANGUAGE-SPECIFIC CHECKS:" not in prompt
        assert "Python:" not in prompt
        assert "Rust:" not in prompt

    def test_fix_checks_with_single_language(self) -> None:
        """Fix prompt includes language hint for single language."""
        prompt = render_prompt(
            "fix_checks.j2",
            check_cmd="pytest",
            oscillation_warning="",
            include_review_history=False,
            include_build_history=False,
            context_mode="push",
            large_context_list="",
            large_file_warning="",
            languages=["rust"],
            **FAKE_PATHS,
        )

        assert "Use idiomatic patterns and best practices for rust" in prompt

    def test_fix_checks_with_multiple_languages(self) -> None:
        """Fix prompt includes language hint for multiple languages."""
        prompt = render_prompt(
            "fix_checks.j2",
            check_cmd="pytest",
            oscillation_warning="",
            include_review_history=False,
            include_build_history=False,
            context_mode="push",
            large_context_list="",
            large_file_warning="",
            languages=["python", "javascript"],
            **FAKE_PATHS,
        )

        assert "python, javascript" in prompt
        assert "Use idiomatic patterns and best practices for python/javascript" in prompt

    def test_fix_checks_without_languages(self) -> None:
        """Fix prompt without languages has no language hint."""
        prompt = render_prompt(
            "fix_checks.j2",
            check_cmd="pytest",
            oscillation_warning="",
            include_review_history=False,
            include_build_history=False,
            context_mode="push",
            large_context_list="",
            large_file_warning="",
            languages=[],
            **FAKE_PATHS,
        )

        assert "idiomatic patterns" not in prompt

    @pytest.mark.parametrize(
        ("language", "expected_content"),
        [
            ("python", ["eval()", "exec()", "compile()", "bare `except:`", "mutable default"]),
            ("rust", ["unsafe", ".unwrap()", ".expect()", "Mutex guards", ".await"]),
            ("javascript", ["eval()", "innerHTML", "__proto__", "Strict equality", "`any` type"]),
            ("elixir", ["Code.eval_string()", "Ecto", "parameterized", "changesets", "Logger"]),
            ("csharp", ["`using` statements", ".Result", ".Wait()", "BinaryFormatter", "catch"]),
        ],
    )
    def test_each_language_partial_renders(
        self,
        language: str,
        expected_content: list[str],
    ) -> None:
        """Each lang_checks/*.j2 partial renders without error and contains expected content."""
        prompt = render_prompt(
            "local_review.j2",
            review_prompt_prefix="",
            languages=[language],
            **FAKE_PATHS,
        )

        for content in expected_content:
            assert content in prompt, f"Expected '{content}' in {language} partial"


class TestNoLegacyPathsInTemplates:
    """Regression: no template should render the literal '.fix-die-repeat' prefix."""

    def test_fix_checks_has_no_legacy_literal(self) -> None:
        """fix_checks.j2 must not render the legacy literal."""
        prompt = render_prompt(
            "fix_checks.j2",
            check_cmd="pytest",
            oscillation_warning="",
            include_review_history=True,
            include_build_history=True,
            context_mode="push",
            large_context_list="",
            large_file_warning="",
            languages=[],
            **FAKE_PATHS,
        )
        assert ".fix-die-repeat" not in prompt

    def test_local_review_has_no_legacy_literal(self) -> None:
        """local_review.j2 must not render the legacy literal."""
        prompt = render_prompt(
            "local_review.j2",
            review_prompt_prefix="",
            languages=[],
            **FAKE_PATHS,
        )
        assert ".fix-die-repeat" not in prompt

    def test_full_codebase_review_has_no_legacy_literal(self) -> None:
        """full_codebase_review.j2 must not render the legacy literal."""
        prompt = render_prompt(
            "full_codebase_review.j2",
            languages=[],
            **FAKE_PATHS,
        )
        assert ".fix-die-repeat" not in prompt

    def test_full_codebase_review_omits_history_reference(self) -> None:
        """full_codebase_review.j2 must not reference prior review.md as history.

        The single-pass mode no longer attaches review.md, so the template must
        not instruct pi to consult it — otherwise pi sees a dangling path.
        """
        prompt = render_prompt(
            "full_codebase_review.j2",
            languages=[],
            **FAKE_PATHS,
        )
        assert FAKE_PATHS["review_history_path"] not in prompt
        assert "historical context" not in prompt

    def test_resolve_review_issues_has_no_legacy_literal(self) -> None:
        """resolve_review_issues.j2 must not render the legacy literal."""
        prompt = render_prompt(
            "resolve_review_issues.j2",
            **FAKE_PATHS,
        )
        assert ".fix-die-repeat" not in prompt

    def test_contextual_review_uncommitted_scope(self) -> None:
        """contextual_review.j2 renders correctly for uncommitted scope."""
        prompt = render_prompt(
            "contextual_review.j2",
            scope="uncommitted",
            file_list=["dirty.py", "also_dirty.py"],
            diff_context="Diff attached.",
            default_branch="",
            languages=["python"],
            **FAKE_PATHS,
        )
        assert "uncommitted" in prompt.lower()
        assert "dirty.py" in prompt
        assert "also_dirty.py" in prompt
        assert ".fix-die-repeat" not in prompt

    def test_contextual_review_branch_scope(self) -> None:
        """contextual_review.j2 renders correctly for branch scope."""
        prompt = render_prompt(
            "contextual_review.j2",
            scope="branch",
            file_list=["feature.py"],
            diff_context="Diff attached.",
            default_branch="main",
            languages=[],
            **FAKE_PATHS,
        )
        assert "branch" in prompt.lower()
        assert "main" in prompt
        assert "feature.py" in prompt

    def test_contextual_review_includes_language_checks(self) -> None:
        """contextual_review.j2 includes language-specific checks."""
        prompt = render_prompt(
            "contextual_review.j2",
            scope="uncommitted",
            file_list=["app.py"],
            diff_context="Diff attached.",
            default_branch="",
            languages=["python"],
            **FAKE_PATHS,
        )
        assert "LANGUAGE-SPECIFIC CHECKS" in prompt

    def test_contextual_review_has_no_legacy_literal(self) -> None:
        """contextual_review.j2 must not render the legacy literal."""
        prompt = render_prompt(
            "contextual_review.j2",
            scope="uncommitted",
            file_list=["f.py"],
            diff_context="",
            default_branch="",
            languages=[],
            **FAKE_PATHS,
        )
        assert ".fix-die-repeat" not in prompt

    def test_pr_threads_header_has_no_legacy_literal(self) -> None:
        """pr_threads_header.j2 must not render the legacy literal."""
        prompt = render_prompt(
            "pr_threads_header.j2",
            unresolved_count=3,
            pr_number=42,
            pr_url="https://example.com/pr/42",
            **FAKE_PATHS,
        )
        assert ".fix-die-repeat" not in prompt


class TestUserTemplateOverride:
    """User-dir templates win over the shipped package copies (ChoiceLoader)."""

    def test_user_template_overrides_package(self) -> None:
        """A file under FDR_HOME/templates/ takes precedence when rendered."""
        user_dir = get_user_templates_dir()
        user_dir.mkdir(parents=True, exist_ok=True)
        override = user_dir / "pr_threads_header.j2"
        override.write_text("USER OVERRIDE {{ unresolved_count }} {{ pr_url }}")
        clear_prompt_cache()

        try:
            prompt = render_prompt(
                "pr_threads_header.j2",
                unresolved_count=7,
                pr_number=1,
                pr_url="https://example.com/pr/1",
                **FAKE_PATHS,
            )
        finally:
            override.unlink()
            clear_prompt_cache()

        assert "USER OVERRIDE 7" in prompt
        assert "https://example.com/pr/1" in prompt

    def test_package_fallback_when_no_user_file(self) -> None:
        """When FDR_HOME/templates/ has no match, the shipped package file is used."""
        user_dir = get_user_templates_dir()
        # The autouse fixture points FDR_HOME at a tmp dir so this is clean.
        assert not (user_dir / "pr_threads_header.j2").exists()
        clear_prompt_cache()

        prompt = render_prompt(
            "pr_threads_header.j2",
            unresolved_count=3,
            pr_number=42,
            pr_url="https://example.com/pr/42",
            **FAKE_PATHS,
        )

        # The shipped template does not contain the user sentinel.
        assert "USER OVERRIDE" not in prompt
        # And renders the PR URL the shipped template asks for.
        assert "https://example.com/pr/42" in prompt


class TestImprovePromptsTemplate:
    """Coverage for the improve_prompts.j2 template."""

    def test_renders_with_expected_context(self, tmp_path: Path) -> None:
        """improve_prompts.j2 renders with all required variables and lists target templates."""
        templates_dir = tmp_path / "templates"
        template_paths = {
            "fix_checks.j2": str(templates_dir / "fix_checks.j2"),
            "local_review.j2": str(templates_dir / "local_review.j2"),
            "resolve_review_issues.j2": str(templates_dir / "resolve_review_issues.j2"),
            "pr_threads_header.j2": str(templates_dir / "pr_threads_header.j2"),
        }

        prompt = render_prompt(
            "improve_prompts.j2",
            introspection_file_path=str(tmp_path / "introspection.yaml"),
            archive_file_path=str(tmp_path / "introspection-archive.yaml"),
            templates_dir=str(templates_dir),
            template_paths=template_paths,
        )

        assert "introspection.yaml" in prompt
        assert "introspection-archive.yaml" in prompt
        for name, path in template_paths.items():
            assert name in prompt
            assert path in prompt
        # Workflow markers
        assert "status: pending" in prompt
        assert "status: reviewed" in prompt
        # Language-agnostic guard rail
        assert "language-agnostic" in prompt


class TestReviewPartials:
    """Each partial renders standalone with the context keys it actually needs."""

    def test_review_readonly_task_renders(self) -> None:
        """_review_readonly_task emits the identify-don't-fix framing."""
        prompt = render_prompt("partials/_review_readonly_task.j2")
        assert "identify and document issues" in prompt
        assert "'edit_file', 'write_file', or 'run_command'" in prompt

    def test_issue_classification_renders(self) -> None:
        """_issue_classification defines CRITICAL vs NIT."""
        prompt = render_prompt("partials/_issue_classification.j2")
        assert "[CRITICAL]" in prompt
        assert "[NIT]" in prompt

    def test_critical_checklist_renders(self) -> None:
        """_critical_checklist renders the language-agnostic checklist."""
        prompt = render_prompt("partials/_critical_checklist.j2")
        assert "CRITICAL REVIEW CHECKLIST (LANGUAGE-AGNOSTIC):" in prompt
        assert "No test configuration changes without explicit approval" in prompt

    def test_language_checks_renders_with_languages(self) -> None:
        """_language_checks emits the lang-specific section when languages is non-empty."""
        prompt = render_prompt("partials/_language_checks.j2", languages=["python"])
        assert "LANGUAGE-SPECIFIC CHECKS:" in prompt
        assert "Python:" in prompt

    def test_language_checks_empty_is_empty(self) -> None:
        """_language_checks skips its section entirely when languages is empty."""
        prompt = render_prompt("partials/_language_checks.j2", languages=[])
        assert "LANGUAGE-SPECIFIC CHECKS" not in prompt

    def test_review_reporting_rules_renders(self) -> None:
        """_review_reporting_rules emits the canonical reporting rules with line numbers."""
        prompt = render_prompt("partials/_review_reporting_rules.j2")
        assert "ONLY report [NIT] issues" in prompt
        assert "Include file paths and line numbers" in prompt

    def test_review_output_contract_renders(self, tmp_path: Path) -> None:
        """_review_output_contract emits the write/NO_ISSUES contract with the given path."""
        review_current = tmp_path / "review_current.md"
        prompt = render_prompt(
            "partials/_review_output_contract.j2",
            review_current_path=str(review_current),
        )
        assert str(review_current) in prompt
        assert "NO_ISSUES" in prompt
