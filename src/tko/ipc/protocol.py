"""Framed JSON IPC protocol with HMAC authentication (stdlib only).

Frame layout: [4-byte BE body_len][4-byte BE sig_len][body][sig]

Transport is platform-specific (Named Pipe on Windows, TCP loopback for tests).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import struct
import time
from pathlib import Path
from typing import Any

PIPE_NAME = r"\\.\pipe\tko-core-v1"
PROTO_VERSION = 1
MAX_FRAME = 1 << 20  # 1 MiB


def default_token_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "TKO"
    else:
        base = Path.home() / ".tko"
    return base / "ipc.token"


def default_ipc_port() -> int:
    """TCP fallback port for non-Windows / unit tests (not production Windows path)."""
    return int(os.environ.get("TKO_IPC_PORT", "17891"))


def ensure_token(path: Path | None = None) -> bytes:
    path = Path(path or default_token_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        tok = secrets.token_hex(32).encode("ascii")
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(tok)
        tmp.replace(path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        if os.name == "nt":
            try:
                import subprocess

                subprocess.run(
                    ["icacls", str(path), "/inheritance:r", "/grant", "SYSTEM:F", "/grant", "Administrators:F"],
                    check=False,
                    capture_output=True,
                )
            except OSError:
                pass
    return path.read_bytes().strip()


def load_token(path: Path | None = None) -> bytes:
    path = Path(path or default_token_path())
    if not path.exists():
        raise FileNotFoundError(f"IPC token missing: {path}")
    return path.read_bytes().strip()


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
