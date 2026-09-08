"""Heartbeat file + stale detection watchdog. Watchdog NEVER submits orders (INV-28)."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class Heartbeat:
    """Process liveness + readiness snapshot. Fresh beat ≠ trading authorized (INV-27)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def beat(
        self,
        status: str = "OK",
        *,
        lifecycle: str | None = None,
        trading_authorized: bool | None = None,
        last_error: str = "",
        extra: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "ts": time.time(),
            "status": status,
            "lifecycle": lifecycle or status,
            "trading_authorized": bool(trading_authorized)
            if trading_authorized is not None
            else (status in ("OK", "READY")),
            "last_error": (last_error or "")[:500],
            "process_alive": True,
        }
        if extra:
            for k, v in extra.items():
                if k.lower() in ("api_key", "api_secret", "secret", "token", "password"):
                    continue
                payload[k] = v
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def read(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return None
            return data
        except Exception as exc:
            logger.warning("heartbeat read failed: %s", exc)
            return None

    def is_trading_authorized(self) -> bool:
        data = self.read()
        if not data:
            return False
        return bool(data.get("trading_authorized")) and str(
            data.get("lifecycle", "")
        ).upper() == "READY"


class Watchdog:
    """Stale heartbeat detector. Does NOT trade (INV-28)."""

    def __init__(self, heartbeat_path: Path, *, stale_after_sec: float = 120.0) -> None:
        self.heartbeat = Heartbeat(heartbeat_path)
        self.stale_after_sec = max(0.0, float(stale_after_sec))

    def check_once(self) -> bool:
        """Backward-compat: True if heartbeat is fresh (not stale)."""
        return not self.check().get("stale", True)

    def check(self) -> dict[str, Any]:
        data = self.heartbeat.read()
        if data is None:
            return {
                "ok": False,
                "reason": "missing_or_malformed_heartbeat",
                "stale": True,
                "trading_authorized": False,
            }
        ts = data.get("ts")
        try:
            age = time.time() - float(ts)
        except (TypeError, ValueError):
            return {
                "ok": False,
                "reason": "malformed_heartbeat_ts",
                "stale": True,
                "trading_authorized": False,
            }
        stale = age > self.stale_after_sec
        lifecycle = str(data.get("lifecycle") or data.get("status") or "")
        trading = (
            bool(data.get("trading_authorized"))
            and lifecycle.upper() == "READY"
            and not stale
        )
        return {
            "ok": not stale,
            "reason": "stale" if stale else "ok",
            "stale": stale,
            "age_sec": age,
            "lifecycle": lifecycle,
            "trading_authorized": trading,
            "process_alive": bool(data.get("process_alive", True)),
        }

    def run_forever(self, poll_sec: float = 5.0) -> None:
        while True:
            result = self.check()
            if result["stale"]:
                logger.warning("event=watchdog_stale detail=%s", result)
            else:
                logger.debug("event=watchdog_ok detail=%s", result)
            time.sleep(poll_sec)
