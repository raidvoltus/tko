"""Framed JSON IPC protocol with HMAC authentication (stdlib only).

Frame layout: [4-byte BE body_len][4-byte BE sig_len][body][sig]

Transport is platform-specific (Named Pipe on Windows, TCP loopback for tests).

IPC token lifecycle (production):
  - Canonical path: %PROGRAMDATA%\\TKO\\ipc.token (Windows) or ~/.tko/ipc.token
  - Created on first-run via ensure_token() (CSPRNG, atomic, exclusive)
  - Never bundled into EXE / git / installer / logs
  - Core and GUI share the same path; concurrent first-start is race-safe
  - Corrupt/empty material is fail-closed (no silent rewrite)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import struct
import time
from pathlib import Path
from typing import Any

PIPE_NAME = r"\\.\pipe\tko-core-v1"
PROTO_VERSION = 1
MAX_FRAME = 1 << 20  # 1 MiB

_TOKEN_HEX_LEN = 64
_TOKEN_MIN_LEN = 32

logger = logging.getLogger(__name__)


class IpcTokenError(Exception):
    """IPC authentication material missing or invalid (fail-closed)."""


def default_token_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "TKO"
    else:
        base = Path.home() / ".tko"
    return base / "ipc.token"


def default_ipc_port() -> int:
    """TCP fallback port for non-Windows / unit tests (not production Windows path)."""
    return int(os.environ.get("TKO_IPC_PORT", "17891"))


def _is_valid_token_bytes(raw: bytes) -> bool:
    if not raw or len(raw) < _TOKEN_MIN_LEN:
        return False
    if any(b <= 0x20 or b >= 0x7F for b in raw):
        return False
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        return False
    if len(text) < _TOKEN_MIN_LEN:
        return False
    return all(c in "0123456789abcdefABCDEF" for c in text)


def _read_validate(path: Path) -> bytes:
    """Read and validate token file. Raises IpcTokenError on any defect."""
    if not path.exists():
        raise IpcTokenError(
            f"IPC authentication material is missing or invalid: {path}. "
            "Start Core once to bootstrap, or reinstall."
        )
    if path.is_dir():
        raise IpcTokenError(
            f"IPC authentication material is missing or invalid: {path} is a directory"
        )
    try:
        raw = path.read_bytes().strip()
    except OSError as exc:
        raise IpcTokenError(
            f"IPC authentication material is missing or invalid: cannot read {path}"
        ) from exc
    if not _is_valid_token_bytes(raw):
        raise IpcTokenError(
            f"IPC authentication material is missing or invalid: {path}"
        )
    return raw


def _harden_permissions(path: Path) -> None:
    """Restrictive filesystem permissions. Never logs token contents."""
    try:
        path.chmod(0o600)
    except OSError:
        pass
    if os.name != "nt":
        return
    try:
        import getpass
        import subprocess

        user = (
            os.environ.get("USERNAME")
            or os.environ.get("USER")
            or getpass.getuser()
        )
        cmd = [
            "icacls",
            str(path),
            "/inheritance:r",
            "/grant:r",
            "SYSTEM:F",
            "/grant:r",
            "Administrators:F",
        ]
        if user:
            cmd.extend(["/grant:r", f"{user}:F"])
        result = subprocess.run(cmd, check=False, capture_output=True, text=True)
        if result.returncode != 0:
            logger.warning(
                "event=ipc_token_acl_failed path=%s code=%s",
                path,
                result.returncode,
            )
    except OSError as exc:
        logger.warning("event=ipc_token_acl_error err=%s", type(exc).__name__)


def ensure_token(path: Path | None = None) -> bytes:
    """Idempotent first-run bootstrap: create token if missing, else load existing.

    Atomic exclusive create prevents split-brain when Core and GUI start together.
    Does NOT overwrite an existing corrupt file (fail-closed).
    Never logs the token value.
    """
    path = Path(path or default_token_path())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise IpcTokenError(
            f"IPC authentication material is missing or invalid: "
            f"cannot create directory {path.parent}"
        ) from exc

    if path.exists():
        return _read_validate(path)

    tok = secrets.token_hex(32).encode("ascii")
    created = False
    try:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY  # type: ignore[attr-defined]
        fd = os.open(str(path), flags, 0o600)
        try:
            os.write(fd, tok)
            try:
                os.fsync(fd)
            except OSError:
                pass
            created = True
        finally:
            os.close(fd)
    except FileExistsError:
        return _read_validate_retry(path)
    except OSError as exc:
        if path.exists():
            return _read_validate_retry(path)
        raise IpcTokenError(
            f"IPC authentication material is missing or invalid: cannot create {path}"
        ) from exc

    if created:
        _harden_permissions(path)
        logger.info("event=ipc_token_initialized path=%s", path)

    return _read_validate(path)


def _read_validate_retry(path: Path, attempts: int = 20, delay: float = 0.01) -> bytes:
    """Retry read briefly — writer may still be flushing under contention."""
    last: Exception | None = None
    for _ in range(attempts):
        try:
            return _read_validate(path)
        except IpcTokenError as exc:
            last = exc
            time.sleep(delay)
    assert last is not None
    raise last


def load_token(path: Path | None = None) -> bytes:
    """Load existing token only (no create). Fail-closed on missing/invalid."""
    path = Path(path or default_token_path())
    return _read_validate(path)


def sign(msg_bytes: bytes, token: bytes) -> str:
    return hmac.new(token, msg_bytes, hashlib.sha256).hexdigest()


def pack(msg: dict[str, Any], token: bytes) -> bytes:
    body_obj = {**msg, "v": PROTO_VERSION, "ts": time.time()}
    body = json.dumps(body_obj, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
    if len(body) > MAX_FRAME:
        raise ValueError("frame too large")
    sig = sign(body, token).encode("ascii")
    return struct.pack(">I", len(body)) + struct.pack(">I", len(sig)) + body + sig


def unpack(data: bytes, token: bytes) -> dict[str, Any]:
    if len(data) < 8:
        raise ValueError("short frame")
    blen, slen = struct.unpack(">II", data[:8])
    if blen + slen > MAX_FRAME or 8 + blen + slen > len(data):
        raise ValueError("frame length invalid")
    body = data[8 : 8 + blen]
    sig = data[8 + blen : 8 + blen + slen]
    expected = sign(body, token).encode("ascii")
    if not hmac.compare_digest(sig, expected):
        raise PermissionError("bad HMAC")
    obj = json.loads(body.decode("utf-8"))
    if int(obj.get("v", 0)) != PROTO_VERSION:
        raise ValueError(f"protocol version mismatch: {obj.get('v')}")
    return obj


def split_header(data: bytes) -> tuple[int, int]:
    if len(data) < 8:
        raise ValueError("short header")
    return struct.unpack(">II", data[:8])
