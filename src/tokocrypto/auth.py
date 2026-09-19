"""HMAC SHA256 authentication for Tokocrypto SIGNED endpoints.

totalParams must match the exact query string / body that is sent
(parameter order included). Signature is always the last parameter.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any, Dict, List, Optional, Tuple


def generate_signature(secret: str, total_params: str) -> str:
    key = secret.encode("utf-8")
    msg = total_params.encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def _fmt(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        # avoid scientific notation
        s = format(v, "f")
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return s
    return str(v)


def build_signed_query(
    params: Dict[str, Any],
    secret: str,
    recv_window: int = 5000,
    timestamp_ms: Optional[int] = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    Build query/body string and signed param dict.

    Order (matches Tokocrypto examples closely):
      1) business params in sorted key order (stable)
      2) timestamp
      3) recvWindow
      4) signature (appended after HMAC over 1-3)

    Returns (total_params_with_signature, ordered_dict_for_send).
    """
    if timestamp_ms is None:
        timestamp_ms = int(time.time() * 1000)

    items: List[Tuple[str, str]] = []
    for k in sorted(params.keys()):
        if k in ("signature", "timestamp", "recvWindow"):
            continue
        v = params[k]
        if v is None:
            continue
        items.append((k, _fmt(v)))

    items.append(("timestamp", str(int(timestamp_ms))))
    items.append(("recvWindow", str(int(recv_window))))

    total_params = "&".join(f"{k}={v}" for k, v in items)
    signature = generate_signature(secret, total_params)
    items.append(("signature", signature))

    ordered = {k: v for k, v in items}
    signed_qs = "&".join(f"{k}={v}" for k, v in items)
    return signed_qs, ordered


def prepare_signed_params(
    params: Dict[str, Any],
    secret: str,
    recv_window: int = 5000,
    timestamp_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """Back-compat: return ordered dict including signature."""
    _, ordered = build_signed_query(params, secret, recv_window, timestamp_ms)
    return ordered


def headers(api_key: str) -> Dict[str, str]:
    return {
        "X-MBX-APIKEY": api_key,
        "User-Agent": "TokocryptoBot/1.0",
    }
