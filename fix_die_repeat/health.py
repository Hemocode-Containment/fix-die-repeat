"""Process-identity locking and orphan worker process-group recovery.

Adapted from Containment Loop's supervisor health module to prevent concurrent
``fix-die-repeat`` runs on the same repository and to reap orphaned ``agy``
process groups left behind if a previous run was killed abruptly.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import signal
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    import logging
    from types import TracebackType

_PS_MIN_PARTS = 4
_STAT_PPID_INDEX = 1
_STAT_START_INDEX = 19
_SUBPROCESS_TIMEOUT_S = 5.0
_DEFAULT_REAP_GRACE_S = 2.0
_REAP_POLL_INTERVAL_S = 0.05
_LOCK_FILE_MODE = 0o600

_BOOT_ID_CACHE: list[str] = []


class RunnerLockError(RuntimeError):
    """Raised when another ``fix-die-repeat`` instance holds the repository lock."""


@dataclass(frozen=True)
class ProcessIdentity:
    """Operating-system process identity bound to PID, start time, and boot ID."""

    pid: int
    ppid: int
    start: str
    boot: str
    zombie: bool = False

    def to_dict(self) -> dict[str, object]:
        """Serialize identity to a JSON-compatible dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: object) -> Self | None:
        """Deserialize a dictionary into a :class:`ProcessIdentity`, or ``None`` if invalid."""
        if not isinstance(data, dict):
            return None
        pid = data.get("pid")
        ppid = data.get("ppid")
        start = data.get("start")
        boot = data.get("boot")
        zombie = data.get("zombie", False)
        if (
            not isinstance(pid, int)
            or isinstance(pid, bool)
            or not isinstance(ppid, int)
            or isinstance(ppid, bool)
            or not isinstance(start, str)
            or not isinstance(boot, str)
            or not isinstance(zombie, bool)
        ):
            return None
        return cls(pid=pid, ppid=ppid, start=start, boot=boot, zombie=zombie)


def _darwin_boot_id() -> str:
    """Return the macOS kernel boot time string, cached per process."""
    if _BOOT_ID_CACHE:
        return _BOOT_ID_CACHE[0]
    boot_id = "darwin"
    sysctl_bin = Path("/usr/sbin/sysctl")
    if sysctl_bin.exists():
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            result = subprocess.run(  # noqa: S603
                [str(sysctl_bin), "-n", "kern.boottime"],
                capture_output=True,
                text=True,
                timeout=_SUBPROCESS_TIMEOUT_S,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                boot_id = result.stdout.strip()
    _BOOT_ID_CACHE.append(boot_id)
    return boot_id


def _linux_proc_identity(pid: int, proc: Path) -> ProcessIdentity | None:
    """Read process identity from Linux ``/proc/<pid>/stat``."""
    stat_file = proc / str(pid) / "stat"
    boot_file = proc / "sys/kernel/random/boot_id"
    try:
        text = stat_file.read_text(encoding="utf-8")
        rparen = text.rfind(")")
        if rparen < 0:
            return None
        fields = text[rparen + 2 :].split()
        if len(fields) <= _STAT_START_INDEX:
            return None
        boot = boot_file.read_text(encoding="utf-8").strip()
        return ProcessIdentity(
            pid=pid,
            ppid=int(fields[_STAT_PPID_INDEX]),
            start=fields[_STAT_START_INDEX],
            boot=boot,
            zombie=fields[0] == "Z",
        )
    except (OSError, ValueError, IndexError):
        return None


def _ps_identity(pid: int) -> ProcessIdentity | None:
    """Read process identity via ``/bin/ps`` on macOS / POSIX systems."""
    ps_bin = Path("/bin/ps")
    if not ps_bin.exists():
        return None
    try:
        result = subprocess.run(  # noqa: S603
            [str(ps_bin), "-o", "pid=,ppid=,state=,lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    line = result.stdout.strip()
    if result.returncode != 0 or not line:
        return None
    parts = line.split(None, _PS_MIN_PARTS - 1)
    if len(parts) < _PS_MIN_PARTS:
        return None
    try:
        parsed_pid = int(parts[0])
        parsed_ppid = int(parts[1])
    except ValueError:
        return None
    state = parts[2]
    start = parts[3].strip()
    return ProcessIdentity(
        pid=parsed_pid,
        ppid=parsed_ppid,
        start=start,
        boot=_darwin_boot_id(),
        zombie=state.startswith("Z"),
    )


def process_identity(pid: int, proc: Path = Path("/proc")) -> ProcessIdentity | None:
    """Return the OS :class:`ProcessIdentity` for ``pid``, or ``None`` if not running."""
    if pid <= 0:
        return None
    if (proc / "self").exists() or (proc / str(pid) / "stat").exists():
        return _linux_proc_identity(pid, proc)
    if proc != Path("/proc"):
        return None
    return _ps_identity(pid)


def same_process(first: ProcessIdentity | None, second: ProcessIdentity | None) -> bool:
    """Return ``True`` when both identities refer to the same live OS process."""
    if first is None or second is None:
        return False
    if first.zombie or second.zombie:
        return False
    return first.pid == second.pid and first.start == second.start and first.boot == second.boot


def reap_process_group(
    pgid: int,
    *,
    grace_seconds: float = _DEFAULT_REAP_GRACE_S,
    logger: logging.Logger | None = None,
) -> None:
    """Terminate and reap a process group (``SIGTERM`` followed by ``SIGKILL``)."""
    if pgid <= 0 or pgid == os.getpid():
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        if logger:
            logger.warning("Permission denied sending SIGTERM to process group %s", pgid)
        return

    deadline = time.monotonic() + max(0.0, grace_seconds)
    while time.monotonic() < deadline:
        current = process_identity(pgid)
        if current is None or current.zombie:
            return
        time.sleep(_REAP_POLL_INTERVAL_S)

    with contextlib.suppress(ProcessLookupError, PermissionError):
        if logger:
            logger.warning("Process group %s did not exit after SIGTERM; sending SIGKILL", pgid)
        os.killpg(pgid, signal.SIGKILL)


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    """Atomically write ``payload`` as JSON to ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp.{os.getpid()}")
    tmp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


class RunnerLock:
    """Per-repository lock that prevents concurrent runs and reaps orphaned workers."""

    def __init__(self, lock_path: Path, logger: logging.Logger | None = None) -> None:
        """Initialize the lock manager for ``lock_path``."""
        self._lock_path = lock_path
        self._flock_path = lock_path.with_name(f"{lock_path.name}.lck")
        self._logger = logger
        self._runner_identity: ProcessIdentity | None = None
        self._worker_identity: ProcessIdentity | None = None
        self._lock_fd: int | None = None
        self._acquired = False

    def __enter__(self) -> Self:
        """Acquire the repository lock, reaping any stale orphaned worker first."""
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Release the repository lock and terminate any active worker."""
        self.release()

    def acquire(self) -> None:
        """Acquire the lock or raise :class:`RunnerLockError` if held by a live process."""
        self._flock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._flock_path, os.O_CREAT | os.O_RDWR, _LOCK_FILE_MODE)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            os.close(fd)
            existing = self._read_existing_lock()
            saved_runner = ProcessIdentity.from_dict(existing.get("runner")) if existing else None
            pid_str = f"PID {saved_runner.pid}" if saved_runner else "another process"
            msg = (
                f"Another fix-die-repeat process ({pid_str}) is already "
                f"running for this repository (lock: {self._lock_path})."
            )
            raise RunnerLockError(msg) from exc

        self._lock_fd = fd
        try:
            existing = self._read_existing_lock()
            if existing is not None:
                self._handle_existing_lock(existing)
            current = process_identity(os.getpid())
            if current is None:
                current = ProcessIdentity(
                    pid=os.getpid(),
                    ppid=os.getppid(),
                    start=str(int(time.time())),
                    boot=_darwin_boot_id(),
                )
            self._runner_identity = current
            self._worker_identity = None
            self._write_lock()
            self._acquired = True
        except Exception:
            if self._lock_fd is not None:
                with contextlib.suppress(OSError):
                    fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                    os.close(self._lock_fd)
                self._lock_fd = None
            raise

    def release(self) -> None:
        """Reap any in-flight worker and remove the lock file if owned by this process."""
        if not self._acquired:
            return
        try:
            if self._worker_identity is not None:
                live_worker = process_identity(self._worker_identity.pid)
                if same_process(live_worker, self._worker_identity):
                    reap_process_group(self._worker_identity.pid, logger=self._logger)
                self._worker_identity = None
            with contextlib.suppress(OSError):
                existing = self._read_existing_lock()
                if existing is None or self._is_owned_by_self(existing):
                    self._lock_path.unlink(missing_ok=True)
        finally:
            if self._lock_fd is not None:
                with contextlib.suppress(OSError):
                    fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                    os.close(self._lock_fd)
                self._lock_fd = None
            self._acquired = False

    def set_active_worker(self, pid: int) -> ProcessIdentity | None:
        """Record an active ``agy`` child process in the lock file."""
        ident = process_identity(pid)
        if ident is None:
            ident = ProcessIdentity(
                pid=pid,
                ppid=os.getpid(),
                start=str(int(time.time())),
                boot=_darwin_boot_id(),
            )
        self._worker_identity = ident
        if self._acquired:
            self._write_lock()
        return ident

    def clear_active_worker(self) -> None:
        """Clear the active ``agy`` worker entry in the lock file."""
        self._worker_identity = None
        if self._acquired:
            self._write_lock()

    def _read_existing_lock(self) -> dict[str, object] | None:
        """Read and parse the lock file, returning ``None`` if absent or malformed."""
        if not self._lock_path.exists():
            return None
        try:
            raw = json.loads(self._lock_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return raw if isinstance(raw, dict) else None

    def _is_owned_by_self(self, lock_data: dict[str, object]) -> bool:
        """Return ``True`` if ``lock_data`` belongs to this runner instance."""
        saved_runner = ProcessIdentity.from_dict(lock_data.get("runner"))
        return saved_runner is not None and saved_runner.pid == os.getpid()

    def _handle_existing_lock(self, lock_data: dict[str, object]) -> None:
        """Verify whether an existing lock is active or stale, reaping orphaned workers."""
        saved_runner = ProcessIdentity.from_dict(lock_data.get("runner"))
        if saved_runner is not None and saved_runner.pid != os.getpid():
            live_runner = process_identity(saved_runner.pid)
            if same_process(live_runner, saved_runner):
                msg = (
                    f"Another fix-die-repeat process (PID {saved_runner.pid}) is already "
                    f"running for this repository (lock: {self._lock_path})."
                )
                raise RunnerLockError(msg)

        saved_worker = ProcessIdentity.from_dict(lock_data.get("worker"))
        if saved_worker is not None:
            live_worker = process_identity(saved_worker.pid)
            if same_process(live_worker, saved_worker):
                if self._logger:
                    self._logger.warning(
                        "Reaping orphaned agy worker process group (PGID %s) from previous run",
                        saved_worker.pid,
                    )
                reap_process_group(saved_worker.pid, logger=self._logger)

    def _write_lock(self) -> None:
        """Write current runner and worker identities to the lock file."""
        payload: dict[str, object] = {
            "runner": self._runner_identity.to_dict() if self._runner_identity else None,
            "worker": self._worker_identity.to_dict() if self._worker_identity else None,
            "updated_at": time.time(),
        }
        _atomic_write_json(self._lock_path, payload)
