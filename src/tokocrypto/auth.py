"""HMAC SHA256 authentication for Tokocrypto SIGNED endpoints."""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Dict, Optional, Any
from urllib.parse import urlencode


def generate_signature(secret: str, total_params: str) -> str:
    """HMAC-SHA256 signature. Case-insensitive per docs."""
    key = secret.encode("utf-8")
    msg = total_params.encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def prepare_signed_params(
    params: Dict[str, Any],
    secret: str,
    recv_window: int = 5000,
    timestamp_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Add timestamp + recvWindow and compute signature.
    totalParams = query string (or body) concatenated in the order the
    parameters will be sent. We sort keys for determinism then rebuild.
    """
    p = dict(params)
    if timestamp_ms is None:
        timestamp_ms = int(time.time() * 1000)
    p["timestamp"] = timestamp_ms
    p["recvWindow"] = recv_window

    # Build totalParams string exactly as server expects (query order).
    # Tokocrypto docs: totalParams = query string concatenated with request body.
    # For form body we use the same key=value&... style.
    items = []
    for k in sorted(p.keys()):
        v = p[k]
        if v is None:
            continue
        items.append(f"{k}={v}")
    total_params = "&".join(items)
    signature = generate_signature(secret, total_params)
    p["signature"] = signature
    return p


def headers(api_key: str) -> Dict[str, str]:
    return {
        "X-MBX-APIKEY": api_key,
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "TokocryptoBot/1.0 (Win7-compatible)",
    }
