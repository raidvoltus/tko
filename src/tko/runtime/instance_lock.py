"""Single-instance lock via PID file in state directory."""

from __future__ import annotations

import atexit
import logging
import os
import sys
import time
from pathlib import Path
from typing import Self

logger = logging.getLogger(__name__)


class InstanceLockError(RuntimeError):
    pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            err = kernel32.GetLastError()
            return err == 5
        except Exception:  # noqa: BLE001
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class InstanceLock:
    """Acquire exclusive bot lock; release on exit."""

    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        self._held = False

    def acquire(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        if self.lock_path.exists():
            try:
                raw = self.lock_path.read_text(encoding="utf-8").strip()
                old_pid = int(raw.split()[0])
            except (OSError, ValueError, IndexError):
                old_pid = -1
            if old_pid > 0 and _pid_alive(old_pid):
                raise InstanceLockError(
                    f"Bot already running (pid={old_pid}). "
                    f"Stop the other process or remove stale lock: {self.lock_path}"
                )
            try:
                self.lock_path.unlink(missing_ok=True)
            except OSError as exc:
                raise InstanceLockError(f"Cannot remove stale lock {self.lock_path}: {exc}") from exc

        payload = f"{os.getpid()} {time.time():.3f}\n"
        try:
            fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            try:
                os.write(fd, payload.encode("utf-8"))
            finally:
                os.close(fd)
        except FileExistsError:
            raise InstanceLockError(f"Bot already running (lock busy): {self.lock_path}") from None
        except OSError as exc:
            raise InstanceLockError(f"Cannot create lock {self.lock_path}: {exc}") from exc

        self._held = True
        atexit.register(self.release)
        logger.info("event=instance_lock_acquired path=%s pid=%s", self.lock_path, os.getpid())

    def release(self) -> None:
        if not self._held:
            return
        self._held = False
        try:
            if self.lock_path.exists():
                raw = self.lock_path.read_text(encoding="utf-8").strip()
                if raw.startswith(str(os.getpid())):
                    self.lock_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Failed to release lock %s: %s", self.lock_path, exc)
        logger.info("event=instance_lock_released path=%s", self.lock_path)

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(self, *args: object) -> None:
        self.release()
