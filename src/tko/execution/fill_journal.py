"""Append-only durable fill event journal — exactly-once barrier (S5-B5).

Invariant
---------
Side effects (position, PnL, reservation) MUST only run after the corresponding
fill event is durable in this journal. On restart, unapplied events are replayed
idempotently so a crash between any durable writes cannot double-count.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FillEvent:
    event_id: str
    client_order_id: str
    symbol: str
    side: str
    delta: float
    cumulative: float
    average: float
    notional: float
    order_id: str = ""
    partial: bool = False
    remaining: float = 0.0
    quote_amount: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FillEvent":
        return cls(
            event_id=str(data["event_id"]),
            client_order_id=str(data.get("client_order_id") or ""),
            symbol=str(data.get("symbol") or ""),
            side=str(data.get("side") or ""),
            delta=float(data.get("delta") or 0.0),
            cumulative=float(data.get("cumulative") or 0.0),
            average=float(data.get("average") or 0.0),
            notional=float(data.get("notional") or 0.0),
            order_id=str(data.get("order_id") or ""),
            partial=bool(data.get("partial") or False),
            remaining=float(data.get("remaining") or 0.0),
            quote_amount=float(data.get("quote_amount") or 0.0),
        )


def make_event_id(client_order_id: str, cumulative: float) -> str:
    """Stable id for a cumulative fill watermark on a client order."""
    return f"{client_order_id}:{cumulative:.10f}"


class FillJournal:
    """Durable append-only journal with applied-set for idempotent replay."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._applied_path = path.with_suffix(".applied.json")
        self._lock = threading.RLock()
        self._events: dict[str, FillEvent] = {}
        self._applied: set[str] = set()
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                with self.path.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        data = json.loads(line)
                        if isinstance(data, dict) and data.get("event_id"):
                            ev = FillEvent.from_dict(data)
                            self._events[ev.event_id] = ev
            except Exception as exc:
                logger.warning("fill journal load failed: %s", exc)
        if self._applied_path.exists():
            try:
                raw = json.loads(self._applied_path.read_text(encoding="utf-8"))
                ids = raw.get("applied") if isinstance(raw, dict) else raw
                if isinstance(ids, list):
                    self._applied = {str(x) for x in ids}
            except Exception as exc:
                logger.warning("fill applied-set load failed: %s", exc)

    def _append_line(self, event: FillEvent) -> None:
        """Append one JSONL line with flush + fsync (crash barrier)."""
        line = json.dumps(event.to_dict(), separators=(",", ":")) + "\n"
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        try:
            dir_fd = os.open(str(self.path.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass

    def _save_applied(self) -> None:
        payload = {"applied": sorted(self._applied)}
        tmp = self._applied_path.with_suffix(".tmp")
        data = json.dumps(payload, indent=2)
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        tmp.replace(self._applied_path)

    def try_record(self, event: FillEvent) -> bool:
        """Durably record *event* if new. Returns True if this is the first time.

        This is the exactly-once barrier: callers MUST NOT apply side effects
        unless this returns True (or during replay of unapplied events).
        """
        with self._lock:
            if event.event_id in self._events:
                return False
            self._append_line(event)
            self._events[event.event_id] = event
            logger.info(
                "event=fill_journal_recorded id=%s delta=%.8f cum=%.8f",
                event.event_id, event.delta, event.cumulative,
            )
            return True

    def mark_applied(self, event_id: str) -> None:
        with self._lock:
            if event_id in self._applied:
                return
            self._applied.add(event_id)
            self._save_applied()

    def is_applied(self, event_id: str) -> bool:
        with self._lock:
            return event_id in self._applied

    def unapplied_events(self) -> list[FillEvent]:
        with self._lock:
            return [e for eid, e in self._events.items() if eid not in self._applied]

    def has_event(self, event_id: str) -> bool:
        with self._lock:
            return event_id in self._events

    def events_for_cid(self, client_order_id: str) -> list[FillEvent]:
        with self._lock:
            return [e for e in self._events.values() if e.client_order_id == client_order_id]
