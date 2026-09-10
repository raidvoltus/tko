"""Append-only audit log with simple hash chaining (stdlib only)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tko.core.redact import redact_mapping, redact_text

logger = logging.getLogger(__name__)

if os.name == "nt":
    import msvcrt

    def _lock_file(fh: Any) -> None:
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
        except OSError:
            pass

    def _unlock_file(fh: Any) -> None:
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:
    import fcntl

    def _lock_file(fh: Any) -> None:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        except OSError:
            pass

    def _unlock_file(fh: Any) -> None:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass


class AuditLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._prev_hash = self._read_last_hash()

    def _read_last_hash(self) -> str:
        if not self.path.exists():
            return "0" * 64
        try:
            last = ""
            with self.path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        last = line.strip()
            if not last:
                return "0" * 64
            row = json.loads(last)
            return str(row.get("hash") or "0" * 64)
        except Exception:
            return "0" * 64

    def record(
        self,
        event: str,
        *,
        symbol: str = "",
        side: str = "",
        quantity: float | None = None,
        price: float | None = None,
        reason: str = "",
        client_order_id: str = "",
        exchange_order_id: str = "",
        extra: dict[str, Any] | None = None,
    ) -> str:
        ts = datetime.now(timezone.utc).isoformat()
        body = {
            "ts": ts,
            "event": event,
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "price": price,
            "reason": redact_text(reason),
            "client_order_id": client_order_id,
            "exchange_order_id": exchange_order_id,
            "extra": redact_mapping(extra or {}),
            "prev_hash": self._prev_hash,
        }
        payload = json.dumps(body, sort_keys=True, ensure_ascii=False, default=str)
        digest = hashlib.sha256((self._prev_hash + payload).encode("utf-8")).hexdigest()
        body["hash"] = digest
        line = json.dumps(body, ensure_ascii=False, default=str) + "\n"
        with self._lock:
            try:
                with self.path.open("a+", encoding="utf-8") as fh:
                    _lock_file(fh)
                    try:
                        fh.write(line)
                        fh.flush()
                        try:
                            os.fsync(fh.fileno())
                        except OSError:
                            pass
                    finally:
                        _unlock_file(fh)
                self._prev_hash = digest
            except OSError as exc:
                logger.error("audit write failed: %s", exc)
        return digest
