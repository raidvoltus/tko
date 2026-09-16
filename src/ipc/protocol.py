"""IPC message framing + HMAC authentication. Fail-closed on bad auth."""
from __future__ import annotations

import hashlib
import hmac
import json
import struct
import time
from typing import Any, Dict, Optional, Tuple

MAX_MESSAGE_BYTES = 256 * 1024  # 256 KiB
HEADER = struct.Struct("!I")  # 4-byte length prefix


def sign_payload(token: str, body: bytes, ts: int, nonce: str) -> str:
    msg = f"{ts}:{nonce}:".encode("utf-8") + body
    return hmac.new(token.encode("ascii"), msg, hashlib.sha256).hexdigest()


def verify_payload(token: str, body: bytes, ts: int, nonce: str, signature: str, max_skew_sec: int = 60) -> bool:
    if not signature or not nonce:
        return False
    now = int(time.time())
    if abs(now - int(ts)) > max_skew_sec:
        return False
    expected = sign_payload(token, body, int(ts), nonce)
    return hmac.compare_digest(expected, signature)


def encode_message(token: str, payload: Dict[str, Any]) -> bytes:
    body = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
    if len(body) > MAX_MESSAGE_BYTES:
        raise ValueError("message too large")
    ts = int(time.time())
    nonce = hashlib.sha256(f"{ts}:{id(payload)}:{len(body)}".encode()).hexdigest()[:16]
    sig = sign_payload(token, body, ts, nonce)
    envelope = {
        "ts": ts,
        "nonce": nonce,
        "sig": sig,
        "body": json.loads(body.decode("utf-8")),
    }
    raw = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ValueError("envelope too large")
    return HEADER.pack(len(raw)) + raw


def decode_message(token: str, data: bytes) -> Dict[str, Any]:
    if len(data) < HEADER.size:
        raise ValueError("truncated header")
    (n,) = HEADER.unpack(data[: HEADER.size])
    if n <= 0 or n > MAX_MESSAGE_BYTES:
        raise ValueError("invalid length")
    if len(data) < HEADER.size + n:
        raise ValueError("truncated body")
    raw = data[HEADER.size : HEADER.size + n]
    env = json.loads(raw.decode("utf-8"))
    ts = int(env.get("ts", 0))
    nonce = str(env.get("nonce", ""))
    sig = str(env.get("sig", ""))
    body_obj = env.get("body")
    if not isinstance(body_obj, dict):
        raise ValueError("invalid body")
    body = json.dumps(body_obj, separators=(",", ":"), default=str).encode("utf-8")
    if not verify_payload(token, body, ts, nonce, sig):
        raise PermissionError("IPC authentication failed")
    return body_obj


def try_read_frame(buffer: bytes) -> Tuple[Optional[bytes], bytes]:
    """Extract one length-prefixed frame from buffer; return (frame_or_None, remainder)."""
    if len(buffer) < HEADER.size:
        return None, buffer
    (n,) = HEADER.unpack(buffer[: HEADER.size])
    if n <= 0 or n > MAX_MESSAGE_BYTES:
        raise ValueError("invalid frame length")
    total = HEADER.size + n
    if len(buffer) < total:
        return None, buffer
    return buffer[:total], buffer[total:]
