"""Durable daily PnL and notional tracking (JSONL, no external deps)."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

DEFAULT_TZ = "Asia/Jakarta"


def _day_key(ts: float, tz_name: str) -> str:
    """Calendar day key in configured TZ; resilient without tzdata package."""
    try:
        tz = ZoneInfo(tz_name)
        return datetime.fromtimestamp(ts, tz=tz).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        try:
            tz = ZoneInfo("UTC")
            return datetime.fromtimestamp(ts, tz=tz).strftime("%Y-%m-%d")
        except Exception:  # noqa: BLE001
            # Frozen Windows without tzdata: use UTC via datetime.timezone
            from datetime import timezone as _tz

            return datetime.fromtimestamp(ts, tz=_tz.utc).strftime("%Y-%m-%d")


@dataclass(frozen=True, slots=True)
class DayStats:
    day: str
    realized_pnl: float
    notional_traded: float
    trade_count: int


class DailyPnLTracker:
    """Append-only JSONL ledger + rolling day aggregates."""

    def __init__(self, path: Path, *, timezone_name: str = DEFAULT_TZ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.timezone_name = timezone_name or DEFAULT_TZ
        self._lock = threading.RLock()
        self._realized: dict[str, float] = {}
        self._notional: dict[str, float] = {}
        self._count: dict[str, int] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                day = str(rec.get("day") or "")
                if not day:
                    continue
                self._realized[day] = self._realized.get(day, 0.0) + float(rec.get("realized_pnl", 0.0) or 0.0)
                self._notional[day] = self._notional.get(day, 0.0) + float(rec.get("notional", 0.0) or 0.0)
                self._count[day] = self._count.get(day, 0) + int(rec.get("trade_count", 1) or 1)
        except Exception as exc:  # noqa: BLE001
            logger.warning("event=pnl_load_failed err=%s", type(exc).__name__)

    def _append(self, rec: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
            try:
                fh.flush()
                os.fsync(fh.fileno())
            except OSError:
                pass

    def record_fill(
        self,
        *,
        realized_pnl: float = 0.0,
        notional: float = 0.0,
        ts: float | None = None,
        meta: dict[str, Any] | None = None,
    ) -> DayStats:
        ts = float(ts if ts is not None else time.time())
        day = _day_key(ts, self.timezone_name)
        with self._lock:
            self._realized[day] = self._realized.get(day, 0.0) + float(realized_pnl)
            self._notional[day] = self._notional.get(day, 0.0) + float(notional)
            self._count[day] = self._count.get(day, 0) + 1
            rec: dict[str, Any] = {
                "ts": ts,
                "day": day,
                "realized_pnl": float(realized_pnl),
                "notional": float(notional),
                "trade_count": 1,
            }
            if meta:
                rec["meta"] = {k: v for k, v in meta.items() if k not in ("api_key", "secret", "token")}
            self._append(rec)
            return self.stats_for_day(day=day, ts=ts)

    def stats_for_day(self, *, day: str | None = None, ts: float | None = None) -> DayStats:
        ts = float(ts if ts is not None else time.time())
        day = day or _day_key(ts, self.timezone_name)
        with self._lock:
            return DayStats(
                day=day,
                realized_pnl=float(self._realized.get(day, 0.0)),
                notional_traded=float(self._notional.get(day, 0.0)),
                trade_count=int(self._count.get(day, 0)),
            )
