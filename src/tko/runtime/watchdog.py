"""Heartbeat file + stale detection watchdog."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


class Heartbeat:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def beat(self, *, status: str = "OK", extra: dict | None = None) -> None:
        payload = {"ts": time.time(), "status": status, "extra": extra or {}}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(self.path)

    def age_seconds(self) -> float | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return time.time() - float(data.get("ts") or 0)
        except Exception:
            return None


class Watchdog:
    def __init__(
        self,
        heartbeat_path: Path,
        *,
        stale_after_sec: float = 300.0,
        stale_flag_path: Path | None = None,
        on_stale: Callable[[float], None] | None = None,
    ) -> None:
        self.heartbeat = Heartbeat(heartbeat_path)
        self.stale_after_sec = stale_after_sec
        self.stale_flag_path = stale_flag_path or heartbeat_path.parent / "STALE"
        self.on_stale = on_stale

    def check_once(self) -> bool:
        age = self.heartbeat.age_seconds()
        if age is None:
            self.stale_flag_path.write_text("no_heartbeat", encoding="utf-8")
            if self.on_stale:
                self.on_stale(float("inf"))
            return False
        if age > self.stale_after_sec:
            self.stale_flag_path.write_text(f"stale_age={age:.1f}", encoding="utf-8")
            logger.warning("event=watchdog_stale age=%.1f", age)
            if self.on_stale:
                self.on_stale(age)
            return False
        if self.stale_flag_path.exists():
            self.stale_flag_path.unlink(missing_ok=True)
        return True

    def run_loop(self, interval_sec: float = 30.0) -> None:
        logger.info("watchdog started stale_after=%.0fs", self.stale_after_sec)
        while True:
            self.check_once()
            time.sleep(interval_sec)
