"""Pure-Python client for invoking and supervising ``agy -p`` stream-json turns.

Spawns each ``agy`` invocation in its own POSIX session, tracks NDJSON
``init`` / ``step_update`` / ``result`` frames with tool-aware stall detection,
logs live tool activity, aggregates token usage, and verifies post-turn git
invariants.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from fix_die_repeat.agy_policy import AgentRole, is_quota_error
from fix_die_repeat.health import RunnerLock, reap_process_group
from fix_die_repeat.utils import run_command

if TYPE_CHECKING:
    import logging
    from pathlib import Path

_DEFAULT_IDLE_TIMEOUT_S = 120.0
_DEFAULT_TOOL_TIMEOUT_S = 1200.0
_DEFAULT_HARD_TIMEOUT_S = 3600.0
_POLL_INTERVAL_S = 0.5
_HARD_TIMEOUT_BUFFER_S = 15.0
_PROC_WAIT_TIMEOUT_S = 5.0
_READER_JOIN_TIMEOUT_S = 2.0
_TOOL_ARG_SUMMARY_MAX = 80


@dataclass(frozen=True)
class TokenUsage:
    """Token consumption reported by ``agy`` in ``step_update`` and ``result`` frames."""

    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    cache_read_tokens: int = 0
    total_tokens: int = 0

    @classmethod
    def from_dict(cls, data: object) -> TokenUsage:
        """Parse a ``usage`` dictionary from an ``agy`` frame."""
        if not isinstance(data, dict):
            return cls()

        def _int_field(key: str) -> int:
            val = data.get(key, 0)
            if isinstance(val, int) and not isinstance(val, bool):
                return max(0, val)
            return 0

        return cls(
            input_tokens=_int_field("input_tokens"),
            output_tokens=_int_field("output_tokens"),
            thinking_tokens=_int_field("thinking_tokens"),
            cache_read_tokens=_int_field("cache_read_tokens"),
            total_tokens=_int_field("total_tokens"),
        )

    def add(self, other: TokenUsage) -> TokenUsage:
        """Return the sum of two :class:`TokenUsage` records."""
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            thinking_tokens=self.thinking_tokens + other.thinking_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


@dataclass(frozen=True)
class AgyTurnRequest:
    """Parameters for a single ``agy -p`` invocation."""

    role: AgentRole
    prompt: str
    model: str
    schema: dict[str, object]
    settings: dict[str, object]
    working_dir: Path
    add_dirs: tuple[Path, ...] = ()
    transcript_path: Path | None = None
    idle_timeout_s: float = _DEFAULT_IDLE_TIMEOUT_S
    tool_timeout_s: float = _DEFAULT_TOOL_TIMEOUT_S
    hard_timeout_s: float = _DEFAULT_HARD_TIMEOUT_S


@dataclass(frozen=True)
class AgyTurnResult:
    """Outcome of a single ``agy -p`` invocation."""

    returncode: int
    response_text: str
    stderr: str
    structured_output: dict[str, object] | None = None
    usage: TokenUsage = field(default_factory=TokenUsage)
    model: str = ""
    quota_error: bool = False


@dataclass(frozen=True)
class GitSnapshot:
    """Snapshot of git working-tree, index, and ``HEAD`` state before an agent turn."""

    is_git_repo: bool
    head_sha: str = ""
    status_porcelain: str = ""
    diff_sha256: str = ""
    staged_tree_sha: str = ""
    stash_sha: str = ""
    untracked_files: frozenset[str] = frozenset()
    agents_status: str = ""
    agents_file_hashes: dict[str, str] = field(default_factory=dict)
    agents_untracked_backups: dict[str, bytes] = field(default_factory=dict)


def _snapshot_agents_state(
    project_root: Path,
    untracked_files: frozenset[str],
) -> tuple[dict[str, str], dict[str, bytes]]:
    """Capture content hashes and untracked file contents for everything under .agents/."""
    agents_dir = project_root / ".agents"
    if not agents_dir.is_dir():
        return {}, {}
    hashes: dict[str, str] = {}
    backups: dict[str, bytes] = {}
    for path in sorted(agents_dir.rglob("*")):
        if path.is_file() and not path.is_symlink():
            rel = str(path.relative_to(project_root))
            try:
                content = path.read_bytes()
            except OSError:
                continue
            hashes[rel] = hashlib.sha256(content).hexdigest()
            if rel in untracked_files:
                backups[rel] = content
    return hashes, backups


def capture_git_snapshot(project_root: Path) -> GitSnapshot:
    """Capture ``HEAD`` SHA, ``git status --porcelain``, and working-tree diff digest."""
    git_bin = shutil.which("git")
    if not git_bin:
        return GitSnapshot(is_git_repo=False)

    rc_inside, inside_out, _ = run_command(
        [git_bin, "rev-parse", "--is-inside-work-tree"],
        cwd=project_root,
        check=False,
    )
    if rc_inside != 0 or inside_out.strip() != "true":
        return GitSnapshot(is_git_repo=False)

    rc_head, head_out, _ = run_command(
        [git_bin, "rev-parse", "HEAD"],
        cwd=project_root,
        check=False,
    )
    head_sha = head_out.strip() if rc_head == 0 else ""

    rc_status, status_out, _ = run_command(
        [git_bin, "status", "--porcelain", "-u"],
        cwd=project_root,
        check=False,
    )
    status_porcelain = status_out if rc_status == 0 else ""

    diff_target = [git_bin, "diff", "HEAD"] if head_sha else [git_bin, "diff"]
    rc_diff, diff_out, _ = run_command(diff_target, cwd=project_root, check=False)
    diff_text = diff_out if rc_diff == 0 else ""
    diff_sha256 = hashlib.sha256(diff_text.encode("utf-8", errors="replace")).hexdigest()

    rc_tree, tree_out, _ = run_command([git_bin, "write-tree"], cwd=project_root, check=False)
    staged_tree_sha = tree_out.strip() if rc_tree == 0 else ""

    rc_stash, stash_out, _ = run_command(
        [git_bin, "stash", "create"],
        cwd=project_root,
        check=False,
    )
    stash_sha = stash_out.strip() if rc_stash == 0 else ""

    rc_untracked, untracked_out, _ = run_command(
        [git_bin, "ls-files", "--others", "--exclude-standard"],
        cwd=project_root,
        check=False,
    )
    untracked_files = frozenset(untracked_out.splitlines()) if rc_untracked == 0 else frozenset()

    rc_agents, agents_out, _ = run_command(
        [git_bin, "status", "--porcelain", "-u", "--", ".agents"],
        cwd=project_root,
        check=False,
    )
    agents_status = agents_out if rc_agents == 0 else ""

    agents_file_hashes, agents_untracked_backups = _snapshot_agents_state(
        project_root, untracked_files
    )

    return GitSnapshot(
        is_git_repo=True,
        head_sha=head_sha,
        status_porcelain=status_porcelain,
        diff_sha256=diff_sha256,
        staged_tree_sha=staged_tree_sha,
        stash_sha=stash_sha,
        untracked_files=untracked_files,
        agents_status=agents_status,
        agents_file_hashes=agents_file_hashes,
        agents_untracked_backups=agents_untracked_backups,
    )


def _revert_readonly_mutation(
    project_root: Path,
    before: GitSnapshot,
    after: GitSnapshot,
    git_bin: str,
) -> None:
    if before.head_sha:
        run_command(
            [git_bin, "reset", "--hard", before.head_sha],
            cwd=project_root,
            check=False,
        )
    for new_untracked in after.untracked_files - before.untracked_files:
        target = project_root / new_untracked
        if target.is_file() or target.is_symlink():
            target.unlink(missing_ok=True)
        elif target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
    for rel, content in before.agents_untracked_backups.items():
        target = project_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    if before.stash_sha:
        run_command(
            [git_bin, "stash", "apply", "--index", before.stash_sha],
            cwd=project_root,
            check=False,
        )


def verify_readonly_git_state(
    project_root: Path,
    before: GitSnapshot,
    logger: logging.Logger,
) -> bool:
    """Verify that a read-only turn did not mutate ``HEAD``, index, or working tree."""
    if not before.is_git_repo:
        return True
    after = capture_git_snapshot(project_root)
    git_bin = shutil.which("git") or "git"

    mutated = (
        bool(before.head_sha and after.head_sha and after.head_sha != before.head_sha)
        or after.status_porcelain != before.status_porcelain
        or after.diff_sha256 != before.diff_sha256
        or after.agents_file_hashes != before.agents_file_hashes
    )

    if mutated:
        logger.error("Read-only agy turn mutated HEAD, working tree, or index; restoring snapshot.")
        _revert_readonly_mutation(project_root, before, after, git_bin)
        return False

    return True


def _revert_agents_mutation(
    project_root: Path,
    before: GitSnapshot,
    after: GitSnapshot,
    git_bin: str,
) -> None:
    for new_untracked in after.untracked_files - before.untracked_files:
        if new_untracked.startswith(".agents/") or new_untracked == ".agents":
            target = project_root / new_untracked
            if target.is_file() or target.is_symlink():
                target.unlink(missing_ok=True)
            elif target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
    new_hashed = set(after.agents_file_hashes.keys()) - set(before.agents_file_hashes.keys())
    for item in new_hashed:
        target = project_root / item
        if target.is_file() or target.is_symlink():
            target.unlink(missing_ok=True)
    for rel, content in before.agents_untracked_backups.items():
        target = project_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    if before.stash_sha:
        run_command(
            [git_bin, "checkout", before.stash_sha, "--", ".agents"],
            cwd=project_root,
            check=False,
        )
    elif before.head_sha:
        run_command(
            [git_bin, "checkout", before.head_sha, "--", ".agents"],
            cwd=project_root,
            check=False,
        )


def verify_fix_git_state(
    project_root: Path,
    before: GitSnapshot,
    logger: logging.Logger,
) -> bool:
    """Verify that a ``fix`` turn did not advance ``HEAD`` or modify ``.agents/``."""
    if not before.is_git_repo:
        return True
    after = capture_git_snapshot(project_root)
    git_bin = shutil.which("git") or "git"

    if before.head_sha and after.head_sha and after.head_sha != before.head_sha:
        logger.warning(
            "Fix turn created a commit (%s -> %s); mixed-resetting to %s to restore index",
            before.head_sha[:8],
            after.head_sha[:8],
            before.head_sha[:8],
        )
        run_command([git_bin, "reset", "--mixed", before.head_sha], cwd=project_root, check=False)
        if before.staged_tree_sha:
            run_command(
                [git_bin, "read-tree", before.staged_tree_sha],
                cwd=project_root,
                check=False,
            )
        after = capture_git_snapshot(project_root)
    elif before.staged_tree_sha and after.staged_tree_sha != before.staged_tree_sha:
        logger.warning(
            "Fix turn staged changes without committing; restoring pre-turn staged index",
        )
        run_command(
            [git_bin, "read-tree", before.staged_tree_sha],
            cwd=project_root,
            check=False,
        )
        after = capture_git_snapshot(project_root)

    agents_mutated = (
        after.agents_status != before.agents_status
        or after.agents_file_hashes != before.agents_file_hashes
    )

    if agents_mutated:
        logger.error("Fix turn modified protected .agents/ customizations; reverting .agents/.")
        _revert_agents_mutation(project_root, before, after, git_bin)
        return False

    return True


def summarize_tool_args(args: object) -> str:
    """Render a compact single-line summary of an ``agy`` tool's parameters."""
    if not isinstance(args, dict):
        return ""
    for preferred_key in ("CommandLine", "Path", "SearchPath", "Query", "Pattern", "query"):
        val = args.get(preferred_key)
        if isinstance(val, str) and val.strip():
            return _truncate_summary(val)
    for value in args.values():
        if isinstance(value, str) and value.strip():
            return _truncate_summary(value)
    return ""


def _truncate_summary(value: str) -> str:
    collapsed = " ".join(value.split())
    if len(collapsed) > _TOOL_ARG_SUMMARY_MAX:
        return collapsed[: _TOOL_ARG_SUMMARY_MAX - 1] + "…"
    return collapsed


@dataclass
class _TurnStreamState:
    """Mutable state accumulated while consuming ``agy``'s stream-json stdout."""

    idle_timeout_s: float
    tool_timeout_s: float
    hard_timeout_s: float
    idle_deadline: float = field(init=False)
    hard_deadline: float = field(init=False)
    tool_deadline: float = 0.0
    active_tool_steps: set[int] = field(default_factory=set)
    result_frame: dict[str, object] | None = None
    last_usage: TokenUsage = field(default_factory=TokenUsage)
    protocol_error: str | None = None

    def __post_init__(self) -> None:
        now = time.monotonic()
        self.idle_deadline = now + self.idle_timeout_s
        self.hard_deadline = now + self.hard_timeout_s + _HARD_TIMEOUT_BUFFER_S

    def reset_idle(self) -> None:
        """Advance the idle deadline from the current monotonic clock."""
        self.idle_deadline = time.monotonic() + self.idle_timeout_s

    def check_deadlines(self) -> str | None:
        """Return a timeout reason string if either the hard or stall deadline passed."""
        now = time.monotonic()
        if now >= self.hard_deadline:
            return f"agy turn exceeded hard timeout ({self.hard_timeout_s:.1f}s)"
        effective_idle = self.idle_deadline
        if self.active_tool_steps and self.tool_deadline > effective_idle:
            effective_idle = self.tool_deadline
        if now >= effective_idle:
            if self.active_tool_steps:
                return f"agy active tool step exceeded tool timeout ({self.tool_timeout_s:.1f}s)"
            return f"agy turn idle for more than {self.idle_timeout_s:.1f}s"
        return None


class AgyClient:
    """Subprocess driver for ``agy -p --output-format stream-json`` invocations."""

    def __init__(
        self,
        *,
        executable: str = "agy",
        logger: logging.Logger,
        runner_lock: RunnerLock | None = None,
    ) -> None:
        """Initialize the client with the ``agy`` executable path and logger."""
        self._executable = executable
        self._logger = logger
        self._runner_lock = runner_lock

    def build_argv(self, request: AgyTurnRequest) -> list[str]:
        """Construct the ``agy`` CLI argument vector with ``-p`` placed last."""
        schema_str = json.dumps(request.schema, separators=(",", ":"))
        settings_str = json.dumps(request.settings, separators=(",", ":"))
        argv = [
            self._executable,
            "--output-format",
            "stream-json",
            "--json-schema",
            schema_str,
            "--settings",
            settings_str,
            "--print-timeout",
            f"{max(1, int(request.hard_timeout_s))}s",
            "--disable-slash-commands",
            "--model",
            request.model,
        ]
        for extra_dir in request.add_dirs:
            argv.extend(["--add-dir", str(extra_dir)])
        argv.extend(["-p", request.prompt])
        return argv

    def run_turn(self, request: AgyTurnRequest) -> AgyTurnResult:
        """Spawn ``agy`` for one turn, stream NDJSON events, and return the result."""
        argv = self.build_argv(request)
        try:
            proc = subprocess.Popen(  # noqa: S603
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                cwd=str(request.working_dir),
                start_new_session=True,
            )
        except OSError as err:
            msg = f"Failed to launch agy at '{self._executable}': {err}"
            return AgyTurnResult(
                returncode=1,
                response_text="",
                stderr=msg,
                model=request.model,
            )

        if self._runner_lock is not None:
            self._runner_lock.set_active_worker(proc.pid)

        events: queue.Queue[tuple[str, object]] = queue.Queue()
        stderr_lines: list[str] = []
        stdout_thread = threading.Thread(
            target=self._stdout_reader,
            args=(proc, events, request.transcript_path),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=self._stderr_reader,
            args=(proc, stderr_lines),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        stream_state = _TurnStreamState(
            idle_timeout_s=request.idle_timeout_s,
            tool_timeout_s=request.tool_timeout_s,
            hard_timeout_s=request.hard_timeout_s,
        )

        try:
            timeout_reason = self._consume_event_stream(proc, events, stream_state)
        finally:
            if proc.poll() is None:
                reap_process_group(proc.pid, logger=self._logger)
            with contextlib.suppress(subprocess.SubprocessError):
                proc.wait(timeout=_PROC_WAIT_TIMEOUT_S)
            stdout_thread.join(timeout=_READER_JOIN_TIMEOUT_S)
            stderr_thread.join(timeout=_READER_JOIN_TIMEOUT_S)
            if self._runner_lock is not None:
                self._runner_lock.clear_active_worker()

        return self._build_turn_result(
            proc_returncode=proc.returncode,
            stream_state=stream_state,
            stderr_lines=stderr_lines,
            timeout_reason=timeout_reason,
            model=request.model,
        )

    def _consume_event_stream(
        self,
        proc: subprocess.Popen[str],
        events: queue.Queue[tuple[str, object]],
        state: _TurnStreamState,
    ) -> str | None:
        """Consume queued stdout frames until EOF, protocol error, or timeout."""
        while True:
            deadline_err = state.check_deadlines()
            if deadline_err is not None:
                return deadline_err
            try:
                kind, payload = events.get(timeout=_POLL_INTERVAL_S)
            except queue.Empty:
                if proc.poll() is not None and events.empty():
                    return None
                continue

            if kind == "eof":
                return None
            if kind == "protocol_error":
                state.protocol_error = str(payload)
                return None
            if kind == "frame" and isinstance(payload, dict):
                state.reset_idle()
                self._handle_frame(payload, state)

    def _handle_frame(self, frame: dict[str, object], state: _TurnStreamState) -> None:
        """Process a single parsed NDJSON frame from ``agy``."""
        event_kind = frame.get("event") or frame.get("type")
        if event_kind == "step_update":
            step = frame.get("step_update")
            if isinstance(step, dict):
                self._handle_step_update(step, state)
        elif event_kind == "result":
            res = frame.get("result")
            if isinstance(res, dict):
                state.result_frame = res
                if "usage" in res:
                    state.last_usage = TokenUsage.from_dict(res.get("usage"))
            else:
                state.protocol_error = "Malformed result payload in stream-json"

    def _handle_step_update(
        self,
        step: dict[str, object],
        state: _TurnStreamState,
    ) -> None:
        """Track active tool executions and log one-line tool progress."""
        step_type = step.get("step_type")
        step_state = step.get("state")
        raw_index = step.get("step_index", 0)
        step_index = raw_index if isinstance(raw_index, int) else 0

        if step_type in ("tool", "run_command"):
            if step_state in ("ACTIVE", "RUNNING"):
                state.active_tool_steps.add(step_index)
                state.tool_deadline = time.monotonic() + state.tool_timeout_s
                tool_name = str(step.get("tool_name") or step_type)
                tool_info = step.get("tool_info")
                params = tool_info.get("parameters") if isinstance(tool_info, dict) else None
                summary = summarize_tool_args(params)
                if summary:
                    self._logger.info("agy: %s %s", tool_name, summary)
                else:
                    self._logger.info("agy: %s", tool_name)
            elif step_state in ("DONE", "ERROR"):
                state.active_tool_steps.discard(step_index)
                if not state.active_tool_steps:
                    state.tool_deadline = 0.0

    def _build_turn_result(
        self,
        *,
        proc_returncode: int | None,
        stream_state: _TurnStreamState,
        stderr_lines: list[str],
        timeout_reason: str | None,
        model: str,
    ) -> AgyTurnResult:
        """Assemble the final :class:`AgyTurnResult` from stream state and exit code."""
        stderr_text = "\n".join(stderr_lines)
        if timeout_reason is not None:
            combined_err = f"{stderr_text}\n{timeout_reason}".strip()
            return AgyTurnResult(
                returncode=1,
                response_text="",
                stderr=combined_err,
                usage=stream_state.last_usage,
                model=model,
                quota_error=is_quota_error(combined_err),
            )

        if stream_state.protocol_error is not None:
            combined_err = f"{stderr_text}\n{stream_state.protocol_error}".strip()
            return AgyTurnResult(
                returncode=1,
                response_text="",
                stderr=combined_err,
                usage=stream_state.last_usage,
                model=model,
                quota_error=is_quota_error(combined_err),
            )

        res = stream_state.result_frame
        if res is None:
            rc = proc_returncode if proc_returncode and proc_returncode != 0 else 1
            msg = stderr_text or f"agy exited (code {proc_returncode}) without a result frame"
            return AgyTurnResult(
                returncode=rc,
                response_text="",
                stderr=msg,
                usage=stream_state.last_usage,
                model=model,
                quota_error=is_quota_error(msg),
            )

        status = res.get("status")
        response_text = str(res.get("response", ""))
        candidate = res.get("structured_output")
        structured = candidate if isinstance(candidate, dict) else None

        if status != "SUCCESS" or (proc_returncode not in (0, None)):
            err_detail = json.dumps(res.get("error") or res)
            combined_err = f"{stderr_text}\n{err_detail}".strip()
            return AgyTurnResult(
                returncode=1,
                response_text=response_text,
                stderr=combined_err,
                structured_output=structured,
                usage=stream_state.last_usage,
                model=model,
                quota_error=is_quota_error(combined_err),
            )

        return AgyTurnResult(
            returncode=0,
            response_text=response_text,
            stderr=stderr_text,
            structured_output=structured,
            usage=stream_state.last_usage,
            model=model,
            quota_error=False,
        )

    @staticmethod
    def _stdout_reader(
        proc: subprocess.Popen[str],
        events: queue.Queue[tuple[str, object]],
        transcript_path: Path | None,
    ) -> None:
        """Read NDJSON frames from ``proc.stdout`` and optionally tee to ``transcript_path``."""
        if proc.stdout is None:
            events.put(("eof", None))
            return
        transcript_handle = None
        if transcript_path is not None:
            with contextlib.suppress(OSError):
                transcript_path.parent.mkdir(parents=True, exist_ok=True)
                transcript_handle = transcript_path.open("w", encoding="utf-8")
        try:
            for line in proc.stdout:
                stripped = line.strip()
                if not stripped:
                    continue
                if transcript_handle is not None:
                    with contextlib.suppress(OSError):
                        transcript_handle.write(stripped + "\n")
                        transcript_handle.flush()
                try:
                    frame = json.loads(stripped)
                except json.JSONDecodeError:
                    events.put(
                        ("protocol_error", f"Malformed JSON on agy stdout: {stripped[:120]}"),
                    )
                    return
                if not isinstance(frame, dict):
                    events.put(("protocol_error", "Non-object JSON frame on agy stdout"))
                    return
                events.put(("frame", frame))
        finally:
            if transcript_handle is not None:
                with contextlib.suppress(OSError):
                    transcript_handle.close()
            events.put(("eof", None))

    def _stderr_reader(
        self,
        proc: subprocess.Popen[str],
        stderr_lines: list[str],
    ) -> None:
        """Read diagnostic lines from ``proc.stderr``."""
        if proc.stderr is None:
            return
        for line in proc.stderr:
            stripped = line.rstrip("\n")
            if not stripped:
                continue
            stderr_lines.append(stripped)
            self._logger.debug("agy stderr: %s", stripped)
