"""Antigravity (agy) model policy, CodexBar quota ranking, schemas, and preflight checks.

Combines Containment Loop's model-chain, quota-cooldown, daily-cap, and
version-change preflight patterns with reset-window-aware model ordering via
``codexbar usage --provider antigravity --json --no-color``.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import logging
    from collections.abc import Iterator, Sequence

_SUBPROCESS_TIMEOUT_S = 15.0
_CODEXBAR_TIMEOUT_S = 5.0
_SMOKE_TIMEOUT_S = 45
_FIVE_HOUR_WINDOW_MINUTES = 300
_COOLDOWN_STEPS_S: tuple[int, ...] = (300, 900, 3600)
_MAX_PROVIDER_RESET_CAP_S = 5 * 3600
_NO_WINDOW_SORT_KEY = float("inf")

_WINDOW_3P_5H = "antigravity-quota-summary-3p-5h"
_WINDOW_3P_WEEKLY = "antigravity-quota-summary-3p-weekly"
_WINDOW_GEMINI_5H = "antigravity-quota-summary-gemini-5h"
_WINDOW_GEMINI_WEEKLY = "antigravity-quota-summary-gemini-weekly"
_WINDOW_ARGON_WEEKLY = "antigravity-quota-summary-gemini-argon-wtu-weekly"


class AgentRole(StrEnum):
    """Logical roles for ``agy`` invocations in ``fix-die-repeat``."""

    FIX = "fix"
    REVIEW = "review"
    INTROSPECT = "introspect"
    IMPROVE_PROMPTS = "improve_prompts"


DEFAULT_ROLE_CHAINS: dict[AgentRole, tuple[str, ...]] = {
    AgentRole.FIX: (
        "claude-sonnet-5-5-high",
        "gemini-4-argon-high",
        "gemini-3.8-flash-high",
    ),
    AgentRole.REVIEW: (
        "claude-opus-5-5-high",
        "gemini-4-argon-high",
        "claude-sonnet-5-5-high",
    ),
    AgentRole.INTROSPECT: (
        "gemini-3.8-flash-high",
        "claude-sonnet-5-5-medium",
    ),
    AgentRole.IMPROVE_PROMPTS: (
        "claude-sonnet-5-5-high",
        "gemini-4-argon-medium",
    ),
}

QUOTA_PATTERNS = re.compile(
    r"(?i)\b(?:resource_exhausted|quota|rate[_ -]?limit(?:ed)?|too\s+many\s+requests|"
    r"http\s*429|status\s*429|capacity|overloaded)\b"
)

REVIEW_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "issues_markdown", "summary"],
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["NO_ISSUES", "ISSUES"],
        },
        "issues_markdown": {
            "type": "string",
        },
        "summary": {
            "type": "string",
        },
    },
}

INTROSPECT_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["date", "project", "pr_number", "pr_url", "status", "threads"],
    "properties": {
        "date": {"type": "string"},
        "project": {"type": "string"},
        "pr_number": {"type": "integer"},
        "pr_url": {"type": "string"},
        "status": {
            "type": "string",
            "enum": ["pending", "reviewed"],
        },
        "threads": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "id",
                    "title",
                    "category",
                    "outcome",
                    "summary",
                    "relevance",
                    "lang_check_gap",
                ],
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "category": {"type": "string"},
                    "outcome": {
                        "type": "string",
                        "enum": ["fixed", "wont-fix", "not-attempted"],
                    },
                    "summary": {"type": "string"},
                    "reason": {"type": "string"},
                    "relevance": {"type": "string"},
                    "lang_check_gap": {"type": "string"},
                },
            },
        },
    },
}

FIX_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["outcome", "summary", "files_modified", "resolved_thread_ids"],
    "properties": {
        "outcome": {
            "type": "string",
            "enum": ["fixed", "blocked"],
        },
        "summary": {
            "type": "string",
        },
        "files_modified": {
            "type": "array",
            "items": {"type": "string"},
        },
        "resolved_thread_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
}

IMPROVE_PROMPTS_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["reviewed_thread_ids", "templates_updated", "summary"],
    "properties": {
        "reviewed_thread_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
        "templates_updated": {
            "type": "array",
            "items": {"type": "string"},
        },
        "summary": {
            "type": "string",
        },
    },
}

SMOKE_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status"],
    "properties": {
        "status": {
            "type": "string",
            "enum": ["ok"],
        },
    },
}

ROLE_SCHEMAS: dict[AgentRole, dict[str, object]] = {
    AgentRole.FIX: FIX_SCHEMA,
    AgentRole.REVIEW: REVIEW_SCHEMA,
    AgentRole.INTROSPECT: INTROSPECT_SCHEMA,
    AgentRole.IMPROVE_PROMPTS: IMPROVE_PROMPTS_SCHEMA,
}


def _validate_type(payload: object, expected_type: str) -> str | None:
    if expected_type == "object" and not isinstance(payload, dict):
        return f"expected object, got {type(payload).__name__}"
    if expected_type == "string" and not isinstance(payload, str):
        return f"expected string, got {type(payload).__name__}"
    if expected_type == "integer" and (not isinstance(payload, int) or isinstance(payload, bool)):
        return f"expected integer, got {type(payload).__name__}"
    if expected_type == "array" and not isinstance(payload, list):
        return f"expected array, got {type(payload).__name__}"
    return None


def _validate_dict_properties(
    payload: dict[str, object],
    schema: dict[str, object],
) -> str | None:
    required = schema.get("required")
    if isinstance(required, list):
        for req_key in required:
            if req_key not in payload:
                return f"missing required property '{req_key}'"
    properties = schema.get("properties")
    prop_dict = properties if isinstance(properties, dict) else {}
    if schema.get("additionalProperties") is False:
        for key in payload:
            if key not in prop_dict:
                return f"unexpected property '{key}'"
    for key, prop_schema in prop_dict.items():
        if key in payload and isinstance(prop_schema, dict):
            prop_err = validate_structured_payload(payload[key], prop_schema)
            if prop_err:
                return f"property '{key}': {prop_err}"
    return None


def _validate_list_items(
    payload: list[object],
    schema: dict[str, object],
) -> str | None:
    item_schema = schema.get("items")
    if isinstance(item_schema, dict):
        for i, item in enumerate(payload):
            item_err = validate_structured_payload(item, item_schema)
            if item_err:
                return f"index [{i}]: {item_err}"
    return None


def validate_structured_payload(payload: object, schema: dict[str, object]) -> str | None:
    """Validate ``payload`` against a role JSON schema dictionary, returning error or None."""
    expected_type = schema.get("type")
    if isinstance(expected_type, str):
        type_err = _validate_type(payload, expected_type)
        if type_err:
            return type_err

    enum_vals = schema.get("enum")
    if isinstance(enum_vals, list) and payload not in enum_vals:
        return f"value {payload!r} not in enum {enum_vals}"

    if isinstance(payload, dict):
        return _validate_dict_properties(payload, schema)

    if isinstance(payload, list):
        return _validate_list_items(payload, schema)

    return None


class AgyPolicyError(RuntimeError):
    """Raised when ``agy`` preflight, model validation, or quota caps fail."""


@dataclass(frozen=True)
class QuotaWindow:
    """Single quota rate window reported by ``codexbar``."""

    window_id: str
    title: str
    used_percent: float
    window_minutes: int
    resets_at_iso: str
    resets_at_epoch: float


@dataclass(frozen=True)
class QuotaSnapshot:
    """Parsed Antigravity quota snapshot from ``codexbar``."""

    windows: dict[str, QuotaWindow]
    account_email: str | None = None
    login_method: str | None = None


@dataclass(frozen=True)
class RankedModelSelection:
    """Result of ranking a role's model chain against cooldowns and quota windows."""

    eligible_models: tuple[str, ...]
    skipped_reasons: dict[str, str] = field(default_factory=dict)
    earliest_reset_epoch: float | None = None


def is_quota_error(text_or_error: object) -> bool:
    """Return ``True`` if ``text_or_error`` matches known quota/capacity error patterns."""
    return bool(QUOTA_PATTERNS.search(str(text_or_error or "")))


def parse_iso_timestamp(iso_str: str) -> float | None:
    """Parse an ISO-8601 timestamp string into a UTC epoch float."""
    if not iso_str or not isinstance(iso_str, str):
        return None
    normalized = iso_str.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


def model_window_ids(model_id: str) -> tuple[str, tuple[str, ...]]:
    """Map an ``agy`` model ID to ``(five_hour_window_id, all_window_ids)``."""
    lower = model_id.strip().lower()
    if lower.startswith(("claude-", "gpt-")):
        return (_WINDOW_3P_5H, (_WINDOW_3P_5H, _WINDOW_3P_WEEKLY))
    if lower.startswith("gemini-4-argon"):
        return (
            _WINDOW_GEMINI_5H,
            (_WINDOW_GEMINI_5H, _WINDOW_ARGON_WEEKLY, _WINDOW_GEMINI_WEEKLY),
        )
    return (_WINDOW_GEMINI_5H, (_WINDOW_GEMINI_5H, _WINDOW_GEMINI_WEEKLY))


def parse_codexbar_payload(raw_json: str) -> QuotaSnapshot | None:
    """Parse JSON stdout from ``codexbar usage --provider antigravity --json``."""
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list) or not parsed:
        return None
    first = parsed[0]
    if not isinstance(first, dict):
        return None
    usage = first.get("usage")
    if not isinstance(usage, dict):
        return None
    extra = usage.get("extraRateWindows")
    windows: dict[str, QuotaWindow] = {}
    if isinstance(extra, list):
        for entry in extra:
            win = _parse_rate_window_entry(entry)
            if win is not None:
                windows[win.window_id] = win
    account_email = usage.get("accountEmail")
    login_method = usage.get("loginMethod")
    return QuotaSnapshot(
        windows=windows,
        account_email=str(account_email) if isinstance(account_email, str) else None,
        login_method=str(login_method) if isinstance(login_method, str) else None,
    )


def _parse_rate_window_entry(entry: object) -> QuotaWindow | None:
    """Parse a single entry from ``usage.extraRateWindows``."""
    if not isinstance(entry, dict):
        return None
    window_id = entry.get("id")
    title = entry.get("title", "")
    win_obj = entry.get("window")
    if not isinstance(window_id, str) or not isinstance(win_obj, dict):
        return None
    used_raw = win_obj.get("usedPercent", 0.0)
    minutes_raw = win_obj.get("windowMinutes", 0)
    resets_iso = win_obj.get("resetsAt", "")
    if not isinstance(used_raw, (int, float)) or not isinstance(minutes_raw, int):
        return None
    resets_epoch = parse_iso_timestamp(str(resets_iso))
    if resets_epoch is None:
        return None
    return QuotaWindow(
        window_id=window_id,
        title=str(title),
        used_percent=float(used_raw),
        window_minutes=minutes_raw,
        resets_at_iso=str(resets_iso),
        resets_at_epoch=resets_epoch,
    )


def fetch_codexbar_snapshot(
    *,
    quota_source: str = "auto",
    codexbar_bin: str = "codexbar",
    logger: logging.Logger | None = None,
) -> QuotaSnapshot | None:
    """Run ``codexbar`` and return a parsed :class:`QuotaSnapshot`, failing open on error."""
    source = quota_source.strip().lower()
    if source == "none":
        return None
    resolved = shutil.which(codexbar_bin)
    if not resolved:
        if source == "codexbar" and logger:
            logger.warning("FDR_QUOTA_SOURCE=codexbar set, but '%s' is not on PATH", codexbar_bin)
        return None
    try:
        result = subprocess.run(  # noqa: S603
            [resolved, "usage", "--provider", "antigravity", "--json", "--no-color"],
            capture_output=True,
            text=True,
            timeout=_CODEXBAR_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as err:
        if logger:
            logger.debug("codexbar invocation failed (falling back to static chain): %s", err)
        return None
    if result.returncode != 0 or not result.stdout.strip():
        if logger:
            logger.debug("codexbar exited with code %s", result.returncode)
        return None
    return parse_codexbar_payload(result.stdout)


class QuotaStateStore:
    """Persistent store for per-model quota cooldowns and UTC daily call counts."""

    def __init__(self, state_path: Path, *, default_cooldown_s: float | None = None) -> None:
        """Initialize the quota state store backed by ``state_path``."""
        self._path = state_path
        self._default_cooldown_s = default_cooldown_s

    @property
    def path(self) -> Path:
        """Path to the underlying JSON state file."""
        return self._path

    @contextlib.contextmanager
    def _lock(self) -> Iterator[None]:
        lock_file = self._path.with_suffix(".lock")
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        with lock_file.open("a+") as fd:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)

    @staticmethod
    def _utc_day(now: float | None = None) -> str:
        ts = datetime.now(UTC) if now is None else datetime.fromtimestamp(now, tz=UTC)
        return ts.strftime("%Y-%m-%d")

    def _load(self) -> dict[str, object]:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict[str, object]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(f"{self._path.suffix}.tmp.{os.getpid()}")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self._path)

    def daily_calls_used(self, now: float | None = None) -> int:
        """Return the number of ``agy`` invocations recorded for the current UTC day."""
        with self._lock():
            data = self._load()
            daily = data.get("daily_runs")
            if not isinstance(daily, dict) or daily.get("date") != self._utc_day(now):
                return 0
            count = daily.get("count", 0)
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                return 0
            return count

    def increment_daily_calls(self, model: str, now: float | None = None) -> int:
        """Increment and return today's ``agy`` invocation count."""
        with self._lock():
            data = self._load()
            today = self._utc_day(now)
            daily = data.get("daily_runs")
            current = 0
            if isinstance(daily, dict) and daily.get("date") == today:
                raw_count = daily.get("count", 0)
                if (
                    isinstance(raw_count, int)
                    and not isinstance(raw_count, bool)
                    and raw_count >= 0
                ):
                    current = raw_count
            updated_count = current + 1
            data["daily_runs"] = {
                "date": today,
                "count": updated_count,
                "last_model": model,
            }
            self._save(data)
            return updated_count

    def reserve_daily_call(
        self,
        model: str,
        max_calls: int,
        now: float | None = None,
    ) -> int:
        """Atomically verify daily cap and reserve an invocation slot under lock."""
        with self._lock():
            data = self._load()
            today = self._utc_day(now)
            daily = data.get("daily_runs")
            current = 0
            if isinstance(daily, dict) and daily.get("date") == today:
                raw_count = daily.get("count", 0)
                if (
                    isinstance(raw_count, int)
                    and not isinstance(raw_count, bool)
                    and raw_count >= 0
                ):
                    current = raw_count

            if max_calls > 0 and current >= max_calls:
                msg = (
                    f"Daily Antigravity call limit reached ({current}/{max_calls} calls today). "
                    "Increase FDR_DAILY_MAX_CALLS (or set to 0 for unlimited) to continue."
                )
                raise AgyPolicyError(msg)

            updated_count = current + 1
            data["daily_runs"] = {
                "date": today,
                "count": updated_count,
                "last_model": model,
            }
            self._save(data)
            return updated_count

    def check_daily_cap(self, max_calls: int, now: float | None = None) -> None:
        """Raise :class:`AgyPolicyError` if ``max_calls > 0`` and today's cap is reached."""
        if max_calls <= 0:
            return
        used = self.daily_calls_used(now)
        if used >= max_calls:
            msg = (
                f"Daily Antigravity call limit reached ({used}/{max_calls} calls today). "
                "Increase FDR_DAILY_MAX_CALLS (or set to 0 for unlimited) to continue."
            )
            raise AgyPolicyError(msg)

    def cooldown_until(self, model: str, now: float | None = None) -> float:
        """Return the epoch timestamp until which ``model`` is cooling down, or ``0.0``."""
        now_val = time.time() if now is None else now
        with self._lock():
            data = self._load()
            cooldowns = data.get("cooldowns")
            if not isinstance(cooldowns, dict):
                return 0.0
            entry = cooldowns.get(model)
            if not isinstance(entry, dict):
                return 0.0
            retry_at = entry.get("retry_at", 0.0)
            if not isinstance(retry_at, (int, float)) or retry_at <= now_val:
                return 0.0
            return float(retry_at)

    def record_quota_cooldown(
        self,
        model: str,
        *,
        reason: str = "quota",
        provider_reset_epoch: float | None = None,
        now: float | None = None,
    ) -> float:
        """Record a quota cooldown for ``model`` and return the ``retry_at`` epoch."""
        now_val = time.time() if now is None else now
        with self._lock():
            data = self._load()
            cooldowns = data.get("cooldowns")
            if not isinstance(cooldowns, dict):
                cooldowns = {}
            prev = cooldowns.get(model)
            attempts = 0
            if isinstance(prev, dict):
                raw_attempts = prev.get("attempts", 0)
                if isinstance(raw_attempts, int) and not isinstance(raw_attempts, bool):
                    attempts = max(0, raw_attempts)
            if self._default_cooldown_s is not None and self._default_cooldown_s > 0:
                delay_s = self._default_cooldown_s
            else:
                step_index = min(attempts, len(_COOLDOWN_STEPS_S) - 1)
                delay_s = float(_COOLDOWN_STEPS_S[step_index])
            retry_at = now_val + delay_s
            if provider_reset_epoch is not None and provider_reset_epoch > now_val:
                retry_at = min(provider_reset_epoch, now_val + _MAX_PROVIDER_RESET_CAP_S)
            cooldowns[model] = {
                "attempts": attempts + 1,
                "retry_at": retry_at,
                "reason": reason[:200],
                "updated_at": now_val,
            }
            data["cooldowns"] = cooldowns
            self._save(data)
            return retry_at

    def clear_cooldown(self, model: str) -> None:
        """Clear any recorded cooldown for ``model`` after a successful turn."""
        with self._lock():
            data = self._load()
            cooldowns = data.get("cooldowns")
            if isinstance(cooldowns, dict) and model in cooldowns:
                del cooldowns[model]
                data["cooldowns"] = cooldowns
                self._save(data)


@dataclass(frozen=True)
class RankingContext:
    """Parameters controlling model-chain eligibility and ordering."""

    quota_state: QuotaStateStore
    snapshot: QuotaSnapshot | None = None
    available_models: frozenset[str] = frozenset()
    max_used_pct: float = 95.0
    now: float | None = None


def rank_models_for_role(
    chain: Sequence[str],
    context: RankingContext,
) -> RankedModelSelection:
    """Filter and sort ``chain`` by soonest 5-hour quota window reset."""
    now_val = time.time() if context.now is None else context.now
    skipped: dict[str, str] = {}
    reset_candidates: list[float] = []
    scored: list[tuple[float, int, str]] = []

    for idx, raw_model in enumerate(chain):
        model = raw_model.strip()
        if not model:
            continue
        if context.available_models and model not in context.available_models:
            skipped[model] = "not in agy models inventory"
            continue

        cooldown_epoch = context.quota_state.cooldown_until(model, now=now_val)
        if cooldown_epoch > now_val:
            remaining = int(cooldown_epoch - now_val)
            skipped[model] = f"in cooldown for {remaining}s"
            reset_candidates.append(cooldown_epoch)
            continue

        exhausted_reason, window_reset = _check_snapshot_windows(
            model,
            context.snapshot,
            context.max_used_pct,
            now_val,
        )
        if exhausted_reason is not None:
            skipped[model] = exhausted_reason
            if window_reset is not None:
                reset_candidates.append(window_reset)
            continue

        five_hour_sort = _five_hour_reset_sort_key(model, context.snapshot, now_val)
        scored.append((five_hour_sort, idx, model))

    scored.sort(key=lambda item: (item[0], item[1]))
    eligible = tuple(item[2] for item in scored)
    earliest_reset = min(reset_candidates) if reset_candidates else None
    return RankedModelSelection(
        eligible_models=eligible,
        skipped_reasons=skipped,
        earliest_reset_epoch=earliest_reset,
    )


def _check_snapshot_windows(
    model: str,
    snapshot: QuotaSnapshot | None,
    max_used_pct: float,
    now_val: float,
) -> tuple[str | None, float | None]:
    """Return ``(exhausted_reason, reset_epoch)`` if any mapped window exceeds threshold."""
    if snapshot is None or not snapshot.windows:
        return (None, None)
    _, all_win_ids = model_window_ids(model)
    for win_id in all_win_ids:
        win = snapshot.windows.get(win_id)
        if win is None:
            continue
        if win.used_percent >= max_used_pct and win.resets_at_epoch > now_val:
            reason = f"{win.title} at {win.used_percent:.1f}% (resets {win.resets_at_iso})"
            return (reason, win.resets_at_epoch)
    return (None, None)


def _five_hour_reset_sort_key(
    model: str,
    snapshot: QuotaSnapshot | None,
    now_val: float,
) -> float:
    """Return the 5-hour window ``resets_at_epoch`` for ``model``, or ``inf`` if unknown."""
    if snapshot is None or not snapshot.windows:
        return _NO_WINDOW_SORT_KEY
    five_hour_id, _ = model_window_ids(model)
    win = snapshot.windows.get(five_hour_id)
    if win is None or win.window_minutes != _FIVE_HOUR_WINDOW_MINUTES:
        return _NO_WINDOW_SORT_KEY
    if win.resets_at_epoch <= now_val:
        return _NO_WINDOW_SORT_KEY
    return win.resets_at_epoch


def parse_chain_csv(csv_value: str | None) -> tuple[str, ...]:
    """Split a comma-separated model chain string into a tuple of non-empty IDs."""
    if not csv_value:
        return ()
    return tuple(part.strip() for part in csv_value.split(",") if part.strip())


def resolve_role_chain(
    role: AgentRole | str,
    *,
    primary_override: str | None = None,
    role_chain_override: str | None = None,
) -> tuple[str, ...]:
    """Build the ordered model chain for ``role``, prepending ``primary_override`` if set."""
    role_enum = AgentRole(role)
    base_chain = parse_chain_csv(role_chain_override) or DEFAULT_ROLE_CHAINS[role_enum]
    ordered: list[str] = []
    if primary_override and primary_override.strip():
        ordered.append(primary_override.strip())
    for item in base_chain:
        if item not in ordered:
            ordered.append(item)
    return tuple(ordered)


@dataclass(frozen=True)
class PermissionProfileContext:
    """Filesystem paths needed to construct a per-role ``--settings`` permission profile."""

    project_root: Path
    fdr_dir: Path
    fdr_home: Path


def build_permission_settings(
    role: AgentRole | str,
    paths: PermissionProfileContext,
) -> dict[str, object]:
    """Build the per-call ``--settings`` JSON object enforcing role tool boundaries."""
    role_enum = AgentRole(role)
    proj = str(paths.project_root.resolve())
    fdr_dir = str(paths.fdr_dir.resolve())
    fdr_home = str(paths.fdr_home.resolve())

    if role_enum in (AgentRole.REVIEW, AgentRole.INTROSPECT):
        return {
            "permissions": {
                "deny": [
                    "write_file(/)",
                    "edit_file(/)",
                    "command(*)",
                    "mcp(*)",
                ],
                "allow": [
                    f"read_file({proj})",
                    f"read_file({fdr_dir})",
                ],
            },
        }

    if role_enum == AgentRole.IMPROVE_PROMPTS:
        templates_dir = str((paths.fdr_home / "templates").resolve())
        introspection_file = str((paths.fdr_home / "introspection.yaml").resolve())
        return {
            "permissions": {
                "deny": [
                    "command(*)",
                    "mcp(*)",
                    f"write_file({proj}/)",
                    f"edit_file({proj}/)",
                    f"write_file({introspection_file})",
                    f"edit_file({introspection_file})",
                ],
                "allow": [
                    f"read_file({fdr_home})",
                    f"write_file({templates_dir}/)",
                    f"edit_file({templates_dir}/)",
                ],
            },
        }

    # AgentRole.FIX
    return {
        "permissions": {
            "deny": [
                f"write_file({proj}/.git/)",
                f"edit_file({proj}/.git/)",
                f"write_file({proj}/.agents/)",
                f"edit_file({proj}/.agents/)",
                f"write_file({fdr_dir}/)",
                f"edit_file({fdr_dir}/)",
                "command(git commit*)",
                "command(git push*)",
                "command(git reset*)",
                "command(git checkout*)",
                "command(git switch*)",
                "command(git rebase*)",
                "command(gh pr merge*)",
                "command(fix-die-repeat*)",
            ],
            "allow": [
                f"read_file({proj})",
                f"write_file({proj})",
                f"edit_file({proj})",
                f"read_file({fdr_dir})",
                "command(*)",
            ],
        },
    }


@dataclass(frozen=True)
class PreflightResult:
    """Verified ``agy`` CLI version, available models, and quota snapshot."""

    executable: str
    version: str
    available_models: frozenset[str]
    smoke_tested: bool
    quota_snapshot: QuotaSnapshot | None = None


def parse_agy_models_output(stdout: str) -> frozenset[str]:
    """Parse model IDs from ``agy models`` tabular output."""
    models: set[str] = set()
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped or stripped.lower().startswith("fetching"):
            continue
        first_col = stripped.split()[0]
        if re.fullmatch(r"[a-z0-9][a-z0-9._-]*", first_col):
            models.add(first_col)
    return frozenset(models)


def ensure_agy_ready(
    *,
    agy_bin: str,
    state_file: Path,
    required_models: Sequence[str] = (),
    force_smoke: bool = False,
    quota_source: str = "auto",
) -> PreflightResult:
    """Verify ``agy --version``, ``agy models``, and version-change smoke check."""
    resolved = shutil.which(agy_bin) if not Path(agy_bin).is_absolute() else agy_bin
    if not resolved or (Path(resolved).is_absolute() and not Path(resolved).is_file()):
        msg = (
            f"Antigravity CLI ('{agy_bin}') not found on PATH. "
            "Install Antigravity CLI and authenticate with `agy` before running fix-die-repeat."
        )
        raise AgyPolicyError(msg)

    version = _run_agy_version(resolved)
    cached = _read_cached_agy_state(state_file)
    cached_version = cached.get("version") if isinstance(cached, dict) else None
    cached_models_raw = cached.get("models") if isinstance(cached, dict) else None

    if (
        not force_smoke
        and cached_version == version
        and isinstance(cached_models_raw, list)
        and cached_models_raw
    ):
        available_models = frozenset(str(m) for m in cached_models_raw if isinstance(m, str))
        smoke_tested = False
    else:
        available_models = _run_agy_models(resolved)
        _run_agy_smoke_test(resolved)
        smoke_tested = True
        _write_cached_agy_state(state_file, version, sorted(available_models))

    _validate_requested_models(required_models, available_models)
    snapshot = fetch_codexbar_snapshot(quota_source=quota_source)
    return PreflightResult(
        executable=resolved,
        version=version,
        available_models=available_models,
        smoke_tested=smoke_tested,
        quota_snapshot=snapshot,
    )


def _run_agy_version(executable: str) -> str:
    try:
        result = subprocess.run(  # noqa: S603
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as err:
        msg = f"Failed to execute '{executable} --version': {err}"
        raise AgyPolicyError(msg) from err
    if result.returncode != 0 or not result.stdout.strip():
        stderr = result.stderr.strip()
        msg = f"'{executable} --version' failed (exit {result.returncode}): {stderr}"
        raise AgyPolicyError(msg)
    return result.stdout.strip().splitlines()[0].strip()


def _run_agy_models(executable: str) -> frozenset[str]:
    try:
        result = subprocess.run(  # noqa: S603
            [executable, "models"],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as err:
        msg = f"Failed to execute '{executable} models': {err}"
        raise AgyPolicyError(msg) from err
    if result.returncode != 0:
        stderr = result.stderr.strip()
        msg = (
            f"'{executable} models' failed (exit {result.returncode}): {stderr}. "
            "Run `agy` interactively once to complete authentication."
        )
        raise AgyPolicyError(msg)
    models = parse_agy_models_output(result.stdout)
    if not models:
        msg = f"'{executable} models' returned no recognizable models."
        raise AgyPolicyError(msg)
    return models


def _run_agy_smoke_test(executable: str) -> None:
    schema_str = json.dumps(SMOKE_SCHEMA, separators=(",", ":"))
    settings_str = json.dumps(
        {"permissions": {"deny": ["write_file(/)", "command(*)", "mcp(*)"]}},
        separators=(",", ":"),
    )
    argv = [
        executable,
        "--output-format",
        "stream-json",
        "--json-schema",
        schema_str,
        "--settings",
        settings_str,
        "--print-timeout",
        f"{_SMOKE_TIMEOUT_S}s",
        "--disable-slash-commands",
        "--dangerously-skip-permissions",
        "-p",
        "Return status ok without calling any tools.",
    ]
    try:
        result = subprocess.run(  # noqa: S603
            argv,
            capture_output=True,
            text=True,
            timeout=_SMOKE_TIMEOUT_S + 5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as err:
        msg = f"Antigravity smoke test failed to run: {err}"
        raise AgyPolicyError(msg) from err
    if result.returncode != 0:
        stderr = result.stderr.strip()
        msg = f"Antigravity smoke test failed (exit {result.returncode}): {stderr}"
        raise AgyPolicyError(msg)
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            frame = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(frame, dict) and frame.get("event") == "result":
            res = frame.get("result")
            if isinstance(res, dict) and res.get("status") == "SUCCESS":
                structured = res.get("structured_output")
                if isinstance(structured, dict) and structured.get("status") == "ok":
                    return
    msg = "Antigravity smoke test did not emit a valid structured result frame."
    raise AgyPolicyError(msg)


def _validate_requested_models(
    required_models: Sequence[str],
    available_models: frozenset[str],
) -> None:
    if not available_models:
        return
    unknown = sorted({m for m in required_models if m and m not in available_models})
    if unknown:
        joined = ", ".join(unknown)
        available_str = ", ".join(sorted(available_models))
        msg = (
            f"Unknown Antigravity model ID(s): {joined}. "
            f"Available models from `agy models`: {available_str}"
        )
        raise AgyPolicyError(msg)


def _read_cached_agy_state(state_file: Path) -> dict[str, object] | None:
    if not state_file.exists():
        return None
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write_cached_agy_state(state_file: Path, version: str, models: list[str]) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "version": version,
        "models": models,
        "verified_at": time.time(),
    }
    tmp = state_file.with_suffix(f"{state_file.suffix}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(state_file)
