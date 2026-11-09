"""Tests for Antigravity policy: model chains, codexbar ranking, permissions, and preflight."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from fix_die_repeat.agy_policy import (
    AgentRole,
    AgyPolicyError,
    PermissionProfileContext,
    QuotaSnapshot,
    QuotaStateStore,
    QuotaWindow,
    RankingContext,
    build_permission_settings,
    ensure_agy_ready,
    fetch_codexbar_snapshot,
    is_quota_error,
    parse_chain_csv,
    parse_codexbar_payload,
    parse_iso_timestamp,
    rank_models_for_role,
    resolve_role_chain,
)

if TYPE_CHECKING:
    from pathlib import Path

WINDOW_5H_MINUTES = 300
WINDOW_WEEKLY_MINUTES = 10080
DEFAULT_MAX_USED_PCT = 95.0
DAILY_CAP_LIMIT = 2


class TestParseModelsAndChains:
    """Tests for model chain parsing and role selection."""

    def test_parse_chain_csv_strips_whitespace(self) -> None:
        """parse_chain_csv strips whitespace and ignores empty segments."""
        models = parse_chain_csv(
            " claude-opus-4-6-thinking , gemini-3-flash , ",
        )
        assert models == ("claude-opus-4-6-thinking", "gemini-3-flash")

    def test_resolve_role_chain_prepends_primary_override(self) -> None:
        """Primary -m / FDR_MODEL override is prepended without duplicates."""
        chain = resolve_role_chain(
            AgentRole.FIX,
            primary_override="gemini-3-flash",
            role_chain_override="claude-opus-4-6-thinking,gemini-3-flash",
        )
        assert chain == ("gemini-3-flash", "claude-opus-4-6-thinking")

    def test_resolve_role_chain_uses_configured_chain(self) -> None:
        """Role chain parses comma-separated models when no primary override is set."""
        chain = resolve_role_chain(
            AgentRole.REVIEW,
            primary_override=None,
            role_chain_override="claude-opus-4-6-thinking,gemini-3.1-pro-high",
        )
        assert chain == ("claude-opus-4-6-thinking", "gemini-3.1-pro-high")


class TestCodexbarSnapshotAndRanking:
    """Tests for codexbar JSON parsing and reset-window-aware model ordering."""

    def test_parse_codexbar_payload_extracts_extra_rate_windows(self) -> None:
        """parse_codexbar_payload extracts extraRateWindows from codexbar list output."""
        raw = json.dumps(
            [
                {
                    "provider": "antigravity",
                    "usage": {
                        "extraRateWindows": [
                            {
                                "id": "antigravity-quota-summary-gemini-5h",
                                "title": "Gemini 5h",
                                "window": {
                                    "windowMinutes": WINDOW_5H_MINUTES,
                                    "usedPercent": 20.0,
                                    "resetsAt": "2026-11-08T15:00:00Z",
                                },
                            },
                            {
                                "id": "antigravity-quota-summary-3p-5h",
                                "title": "Claude/GPT-OSS 5h",
                                "window": {
                                    "windowMinutes": WINDOW_5H_MINUTES,
                                    "usedPercent": 10.0,
                                    "resetsAt": "2026-11-08T13:00:00Z",
                                },
                            },
                        ],
                    },
                },
            ],
        )
        snapshot = parse_codexbar_payload(raw)
        assert snapshot is not None
        assert "antigravity-quota-summary-gemini-5h" in snapshot.windows
        assert "antigravity-quota-summary-3p-5h" in snapshot.windows

    def test_rank_models_orders_by_earliest_5h_reset_and_filters_exhausted(
        self,
        tmp_path: Path,
    ) -> None:
        """Eligible models are sorted by earliest 5h resetsAt, preserving chain order on ties."""
        store = QuotaStateStore(tmp_path / "quota_state.json")
        now_epoch = datetime(2026, 11, 8, 12, 0, tzinfo=UTC).timestamp()
        t_early = datetime(2026, 11, 8, 13, 0, tzinfo=UTC).timestamp()
        t_late = datetime(2026, 11, 8, 16, 0, tzinfo=UTC).timestamp()
        snapshot = QuotaSnapshot(
            windows={
                "antigravity-quota-summary-gemini-5h": QuotaWindow(
                    window_id="antigravity-quota-summary-gemini-5h",
                    title="Gemini 5h",
                    window_minutes=WINDOW_5H_MINUTES,
                    used_percent=25.0,
                    resets_at_iso="2026-11-08T13:00:00Z",
                    resets_at_epoch=t_early,
                ),
                "antigravity-quota-summary-gemini-weekly": QuotaWindow(
                    window_id="antigravity-quota-summary-gemini-weekly",
                    title="Gemini Weekly",
                    window_minutes=WINDOW_WEEKLY_MINUTES,
                    used_percent=40.0,
                    resets_at_iso="2026-11-08T16:00:00Z",
                    resets_at_epoch=t_late,
                ),
                "antigravity-quota-summary-3p-5h": QuotaWindow(
                    window_id="antigravity-quota-summary-3p-5h",
                    title="3P 5h",
                    window_minutes=WINDOW_5H_MINUTES,
                    used_percent=10.0,
                    resets_at_iso="2026-11-08T16:00:00Z",
                    resets_at_epoch=t_late,
                ),
                "antigravity-quota-summary-3p-weekly": QuotaWindow(
                    window_id="antigravity-quota-summary-3p-weekly",
                    title="3P Weekly",
                    window_minutes=WINDOW_WEEKLY_MINUTES,
                    used_percent=30.0,
                    resets_at_iso="2026-11-08T16:00:00Z",
                    resets_at_epoch=t_late,
                ),
            },
        )
        chain = [
            "claude-opus-4-6-thinking",
            "claude-sonnet-4-6-thinking",
            "gemini-3.1-pro-high",
            "gemini-3-flash",
        ]
        # Gemini 5h resets earlier (13:00Z) than 3P 5h (16:00Z), so Gemini models come first
        # while preserving their relative chain order.
        selection = rank_models_for_role(
            chain,
            RankingContext(
                quota_state=store,
                snapshot=snapshot,
                max_used_pct=DEFAULT_MAX_USED_PCT,
                now=now_epoch,
            ),
        )
        assert selection.eligible_models == (
            "gemini-3.1-pro-high",
            "gemini-3-flash",
            "claude-opus-4-6-thinking",
            "claude-sonnet-4-6-thinking",
        )

    def test_rank_models_skips_exhausted_window_and_cooldown(self, tmp_path: Path) -> None:
        """Exhausted windows and cooldowns are excluded when alternatives exist."""
        store = QuotaStateStore(tmp_path / "quota_state.json")
        now_epoch = datetime(2026, 11, 8, 12, 0, tzinfo=UTC).timestamp()
        store.record_quota_cooldown(
            "gemini-3.1-pro-high",
            reason="503 overloaded",
            now=now_epoch,
        )
        snapshot = QuotaSnapshot(
            windows={
                "antigravity-quota-summary-3p-5h": QuotaWindow(
                    window_id="antigravity-quota-summary-3p-5h",
                    title="3P 5h",
                    window_minutes=WINDOW_5H_MINUTES,
                    used_percent=98.0,
                    resets_at_iso="2026-11-08T13:00:00Z",
                    resets_at_epoch=now_epoch + 3600,
                ),
                "antigravity-quota-summary-gemini-5h": QuotaWindow(
                    window_id="antigravity-quota-summary-gemini-5h",
                    title="Gemini 5h",
                    window_minutes=WINDOW_5H_MINUTES,
                    used_percent=20.0,
                    resets_at_iso="2026-11-08T14:00:00Z",
                    resets_at_epoch=now_epoch + 7200,
                ),
            },
        )
        chain = ["claude-opus-4-6-thinking", "gemini-3.1-pro-high", "gemini-3-flash"]
        selection = rank_models_for_role(
            chain,
            RankingContext(
                quota_state=store,
                snapshot=snapshot,
                max_used_pct=DEFAULT_MAX_USED_PCT,
                now=now_epoch,
            ),
        )
        assert selection.eligible_models == ("gemini-3-flash",)

    def test_fetch_codexbar_snapshot_returns_none_when_disabled(self) -> None:
        """fetch_codexbar_snapshot returns None immediately when quota_source='none'."""
        assert fetch_codexbar_snapshot(quota_source="none") is None


class TestQuotaStateStore:
    """Tests for persistent cooldowns and daily call caps."""

    def test_cooldown_persistence_and_expiry(self, tmp_path: Path) -> None:
        """Cooldowns persist to disk and expire after retry_at."""
        state_file = tmp_path / "quota_state.json"
        store = QuotaStateStore(state_file)
        now_epoch = datetime(2026, 11, 8, 12, 0, tzinfo=UTC).timestamp()

        retry_at = store.record_quota_cooldown(
            "claude-opus-4-6-thinking",
            reason="429 rate limit",
            now=now_epoch,
        )
        assert store.cooldown_until("claude-opus-4-6-thinking", now=now_epoch + 60) == retry_at
        assert store.cooldown_until("claude-opus-4-6-thinking", now=retry_at + 1) == 0.0

    def test_daily_call_cap_enforcement(self, tmp_path: Path) -> None:
        """check_daily_cap raises AgyPolicyError once daily cap is reached."""
        store = QuotaStateStore(tmp_path / "quota_state.json")
        now_epoch = datetime(2026, 11, 8, 12, 0, tzinfo=UTC).timestamp()

        store.check_daily_cap(DAILY_CAP_LIMIT, now=now_epoch)
        assert store.increment_daily_calls("model-a", now=now_epoch) == 1
        store.check_daily_cap(DAILY_CAP_LIMIT, now=now_epoch)
        assert store.increment_daily_calls("model-a", now=now_epoch) == DAILY_CAP_LIMIT

        with pytest.raises(AgyPolicyError, match="Daily Antigravity call limit reached"):
            store.check_daily_cap(DAILY_CAP_LIMIT, now=now_epoch)

    def test_quota_error_and_iso_timestamp_parsing(self) -> None:
        """is_quota_error and parse_iso_timestamp detect rate-limit messages and timestamps."""
        assert is_quota_error("Error 429: RESOURCE_EXHAUSTED until 2026-11-08T15:00:00Z")
        assert not is_quota_error("SyntaxError: invalid syntax")

        expected_epoch = datetime(2026, 11, 8, 15, 0, tzinfo=UTC).timestamp()
        assert parse_iso_timestamp("2026-11-08T15:00:00Z") == expected_epoch


class TestPermissionSettingsAndPreflight:
    """Tests for per-role --settings permission profiles and preflight checks."""

    def test_build_permission_settings_review_denies_writes_and_commands(
        self,
        tmp_path: Path,
    ) -> None:
        """Review role denies write_file, command, and mcp."""
        ctx = PermissionProfileContext(
            project_root=tmp_path / "repo",
            fdr_dir=tmp_path / "fdr" / "repos" / "slug",
            fdr_home=tmp_path / "fdr",
        )
        parsed = build_permission_settings(AgentRole.REVIEW, ctx)
        permissions = parsed["permissions"]
        assert isinstance(permissions, dict)
        deny = permissions["deny"]
        assert isinstance(deny, list)
        assert "write_file(/)" in deny
        assert "command(*)" in deny
        assert "mcp(*)" in deny

    def test_build_permission_settings_fix_denies_fdr_dir_writes_and_git_commits(
        self,
        tmp_path: Path,
    ) -> None:
        """Fix role denies writing into fdr_dir and running git commit/push."""
        fdr_home = tmp_path / "fdr"
        fdr_dir = fdr_home / "repos" / "slug"
        ctx = PermissionProfileContext(
            project_root=tmp_path / "repo",
            fdr_dir=fdr_dir,
            fdr_home=fdr_home,
        )
        parsed = build_permission_settings(AgentRole.FIX, ctx)
        permissions = parsed["permissions"]
        assert isinstance(permissions, dict)
        deny = permissions["deny"]
        assert isinstance(deny, list)
        assert f"write_file({fdr_dir.resolve()}/)" in deny
        assert "command(git commit)" in deny
        assert "command(git push)" in deny

    def test_ensure_agy_ready_uses_cache_when_version_matches(self, tmp_path: Path) -> None:
        """ensure_agy_ready skips smoke test when cached version matches."""
        fake_agy = tmp_path / "bin" / "agy"
        fake_agy.parent.mkdir(parents=True, exist_ok=True)
        fake_agy.write_text("#!/bin/sh\n")
        fake_agy.chmod(0o755)

        state_file = tmp_path / "agy_state.json"
        state_file.write_text(
            json.dumps(
                {
                    "version": "Antigravity 2.1.29",
                    "models": ["claude-opus-4-6-thinking", "gemini-3-flash"],
                },
            ),
        )
        with (
            patch("fix_die_repeat.agy_policy.subprocess.run") as mock_run,
            patch("fix_die_repeat.agy_policy.fetch_codexbar_snapshot", return_value=None),
        ):
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="Antigravity 2.1.29\n",
                stderr="",
            )
            result = ensure_agy_ready(
                agy_bin=str(fake_agy),
                state_file=state_file,
                required_models=["claude-opus-4-6-thinking"],
                quota_source="none",
            )

        assert result.version == "Antigravity 2.1.29"
        assert result.smoke_tested is False
        assert mock_run.call_count == 1
