"""IPC token lifecycle — CSPRNG, atomic create, fail-closed.

Canonical Windows path: C:\\ProgramData\\TKO\\ipc.token
Never log the token value. Never bundle into EXE. Never hardcode.
"""
from __future__ import annotations

import logging
import os
import secrets
import tempfile
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
TOKEN_BYTES = 32  # 256-bit
TOKEN_FILENAME = "ipc.token"
DIR_NAME = "TKO"


class TokenError(Exception):
    """Fail-closed token errors."""


def _program_data_root() -> Path:
    """Windows: %ProgramData%\\TKO  |  else: XDG or /tmp/TKO for tests."""
    if os.name == "nt":
        base = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
        return Path(base) / DIR_NAME
    # Non-Windows (dev/CI): deterministic but not world-writable preferred
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / DIR_NAME
    # Allow override for tests
    override = os.environ.get("TKO_IPC_DIR")
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / DIR_NAME


def token_path() -> Path:
    return _program_data_root() / TOKEN_FILENAME


def _secure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            os.chmod(path, 0o700)
        except OSError as e:
            logger.warning("Could not chmod IPC dir: %s", e)
    # Windows ACL hardening requires win32security — best-effort without Everyone Full Control


def _is_valid_token_file(path: Path) -> bool:
    if not path.exists():
        return False
    if path.is_dir():
        return False
    try:
        data = path.read_bytes()
    except OSError:
        return False
    if not data or len(data) < 16:
        return False
    # hex form preferred
    text = data.strip()
    try:
        if all(chr(b) in "0123456789abcdefABCDEF" for b in text):
            return len(text) >= 32
    except Exception:
        pass
    return len(data) >= 16


def ensure_ipc_token() -> Path:
    """
    Create token if missing. Do NOT regenerate if valid token exists.
    Atomic create under lock to avoid concurrent Core/GUI race.
    Returns path to token file.
    """
    with _LOCK:
        root = _program_data_root()
        _secure_dir(root)
        path = root / TOKEN_FILENAME

        if path.exists() and path.is_dir():
            raise TokenError(f"IPC token path is a directory: {path}")

        if _is_valid_token_file(path):
            logger.info("IPC token present at %s", path)
            return path

        # Generate CSPRNG token
        raw = secrets.token_hex(TOKEN_BYTES)
        # Atomic write
        fd, tmp = tempfile.mkstemp(dir=str(root), prefix=".ipc_")
        try:
            os.write(fd, raw.encode("ascii"))
            os.close(fd)
            fd = -1
            if os.name != "nt":
                os.chmod(tmp, 0o600)
            os.replace(tmp, str(path))
        finally:
            if fd >= 0:
                try:
                    os.close(fd)
                except Exception:
                    pass
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except Exception:
                    pass

        if not _is_valid_token_file(path):
            raise TokenError("Failed to create valid IPC token")

        logger.info("IPC token created at %s", path)
        return path


def load_ipc_token() -> str:
    """Load token or raise TokenError (fail-closed). Never logs token value."""
    path = token_path()
    if not path.exists():
        raise TokenError(f"Ipc token missing: {path}")
    if path.is_dir():
        raise TokenError(f"Ipc token path is a directory: {path}")
    try:
        data = path.read_bytes().strip()
    except PermissionError as e:
        raise TokenError(f"Ipc token permission denied: {path}") from e
    except OSError as e:
        raise TokenError(f"Ipc token unreadable: {path}: {e}") from e
    if not data:
        raise TokenError("Ipc token empty")
    text = data.decode("ascii", errors="strict")
    if len(text) < 32:
        raise TokenError("Ipc token corrupt/too short")
    return text


def rotate_ipc_token() -> Path:
    """Explicit rotation only — not called on normal startup."""
    path = token_path()
    if path.exists() and path.is_file():
        path.unlink()
    return ensure_ipc_token()
