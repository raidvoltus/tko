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
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    return datetime.fromtimestamp(ts, tz=tz).strftime("%Y-%m-%d")


@dataclass(frozen=True, slots=True)
class DayStats:
    day: str
    realized_pnl: float
    notional_traded: float
    trade_count: int


class DailyPnLTracker:
    """Append-only JSONL ledger + rolling day aggregates."""

    def __init__(self, path: Path, *, timezone_name: str = DEFAULT_TZ) -> None:
        self.path = path
        self.timezone_name = timezone_name or DEFAULT_TZ
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._realized_by_day: dict[str, float] = {}
        self._notional_by_day: dict[str, float] = {}
        self._count_by_day: dict[str, int] = {}
        self._fill_event_ids: set[str] = set()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    day = str(row.get("day") or "")
                    if not day:
                        continue
                    pnl = float(row.get("pnl") or 0.0)
                    notional = float(row.get("notional") or 0.0)
                    self._realized_by_day[day] = self._realized_by_day.get(day, 0.0) + pnl
                    self._notional_by_day[day] = self._notional_by_day.get(day, 0.0) + abs(notional)
                    self._count_by_day[day] = self._count_by_day.get(day, 0) + 1
                    feid = str(row.get("fill_event_id") or "")
                    if feid:
                        self._fill_event_ids.add(feid)
        except OSError as exc:
            logger.warning("Failed to load PnL ledger %s: %s", self.path, exc)

    def today_key(self, now: float | None = None) -> str:
        return _day_key(now if now is not None else time.time(), self.timezone_name)

    def stats_for_day(self, day: str | None = None) -> DayStats:
        d = day or self.today_key()
        with self._lock:
            return DayStats(
                day=d,
                realized_pnl=float(self._realized_by_day.get(d, 0.0)),
                notional_traded=float(self._notional_by_day.get(d, 0.0)),
                trade_count=int(self._count_by_day.get(d, 0)),
            )

    def today_realized_pnl(self) -> float:
        return self.stats_for_day().realized_pnl

    def today_notional(self) -> float:
        return self.stats_for_day().notional_traded

    def record_trade(
        self,
        *,
        side: str,
        symbol: str,
        notional: float,
        pnl: float = 0.0,
        order_id: str = "",
        client_order_id: str = "",
        ts: float | None = None,
        extra: dict[str, Any] | None = None,
        fill_event_id: str = "",
    ) -> DayStats:
        now = ts if ts is not None else time.time()
        day = _day_key(now, self.timezone_name)
        feid = (fill_event_id or "").strip()
        row: dict[str, Any] = {
            "ts": now,
            "day": day,
            "side": side.lower(),
            "symbol": symbol,
            "pnl": float(pnl),
            "notional": float(abs(notional)),
            "order_id": order_id or "",
            "client_order_id": client_order_id or "",
        }
        if feid:
            row["fill_event_id"] = feid
        if extra:
            row["extra"] = extra
        with self._lock:
            # S5-B5: skip duplicate fill_event_id (crash replay)
            if feid and feid in self._fill_event_ids:
                return self.stats_for_day(day)
            # S5-B5.1: durable append is fail-closed — do not update aggregates
            # or claim success if the ledger write/fsync fails. Callers must not
            # mark_applied until record_trade returns without raising.
            try:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                    fh.flush()
                    try:
                        os.fsync(fh.fileno())
                    except OSError as exc:
                        logger.error("Failed to fsync PnL event: %s", exc)
                        raise
            except OSError as exc:
                logger.error("Failed to append PnL event: %s", exc)
                raise
            self._realized_by_day[day] = self._realized_by_day.get(day, 0.0) + float(pnl)
            self._notional_by_day[day] = self._notional_by_day.get(day, 0.0) + abs(float(notional))
            self._count_by_day[day] = self._count_by_day.get(day, 0) + 1
            if feid:
                self._fill_event_ids.add(feid)
            stats = DayStats(
                day=day,
                realized_pnl=self._realized_by_day[day],
                notional_traded=self._notional_by_day[day],
                trade_count=self._count_by_day[day],
            )
        logger.info(
            "event=pnl_recorded day=%s side=%s symbol=%s pnl=%.8f notional=%.8f day_pnl=%.8f day_notional=%.8f",
            day, side, symbol, pnl, abs(notional), stats.realized_pnl, stats.notional_traded,
        )
        return stats
