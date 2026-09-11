"""Persistent open-position store with balance reconciliation."""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PositionDiscrepancy:
    symbol: str
    base: str
    store_amount: float
    exchange_free: float
    delta: float  # store - exchange_free (>0 means store overstates)
    note: str


@dataclass
class StoredPosition:
    symbol: str
    base: str
    quote: str
    amount: float
    entry_price: float
    opened_at: float
    order_id: str = ""
    client_order_id: str = ""
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StoredPosition":
        return cls(
            symbol=str(data["symbol"]),
            base=str(data.get("base") or ""),
            quote=str(data.get("quote") or ""),
            amount=float(data.get("amount") or 0.0),
            entry_price=float(data.get("entry_price") or 0.0),
            opened_at=float(data.get("opened_at") or time.time()),
            order_id=str(data.get("order_id") or ""),
            client_order_id=str(data.get("client_order_id") or ""),
            updated_at=float(data.get("updated_at") or time.time()),
        )


class PositionStore:
    """JSON file of open positions keyed by symbol."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._positions: dict[str, StoredPosition] = {}
        self._applied_fill_ids: set[str] = set()
        self.corrupted: bool = False
        self.corruption_reason: str = ""
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw_text = self.path.read_text(encoding="utf-8")
            if not raw_text.strip():
                self.corrupted = True
                self.corruption_reason = "position store empty"
                self._positions = {}
                self._applied_fill_ids = set()
                logger.critical("event=position_store_corrupted reason=%s", self.corruption_reason)
                return
            raw = json.loads(raw_text)
            items = raw.get("positions") if isinstance(raw, dict) else raw
            if not isinstance(items, list):
                self.corrupted = True
                self.corruption_reason = "positions not a list"
                self._positions = {}
                self._applied_fill_ids = set()
                logger.critical("event=position_store_corrupted reason=%s", self.corruption_reason)
                return
            loaded = {}
            for item in items:
                if not isinstance(item, dict):
                    self.corrupted = True
                    self.corruption_reason = "position non-dict"
                    self._positions = {}
                    self._applied_fill_ids = set()
                    logger.critical("event=position_store_corrupted reason=%s", self.corruption_reason)
                    return
                pos = StoredPosition.from_dict(item)
                if not pos.symbol:
                    self.corrupted = True
                    self.corruption_reason = "missing symbol"
                    self._positions = {}
                    self._applied_fill_ids = set()
                    logger.critical("event=position_store_corrupted reason=%s", self.corruption_reason)
                    return
                if pos.amount > 0:
                    loaded[pos.symbol] = pos
            applied = raw.get("applied_fill_ids") if isinstance(raw, dict) else []
            if applied is None:
                applied = []
            if not isinstance(applied, list):
                self.corrupted = True
                self.corruption_reason = "applied_fill_ids not list"
                self._positions = {}
                self._applied_fill_ids = set()
                logger.critical("event=position_store_corrupted reason=%s", self.corruption_reason)
                return
            self._positions = loaded
            self._applied_fill_ids = {str(x) for x in applied}
            self.corrupted = False
            self.corruption_reason = ""
        except Exception as exp:
            self.corrupted = True
            self.corruption_reason = f"position load failed: {type(exp).__name__}: {exp}"
            self._positions = {}
            self._applied_fill_ids = set()
            logger.critical("event=position_store_corrupted reason=%s", self.corruption_reason)

    def _save(self) -> None:
        payload = {
            "updated_at": time.time(),
            "positions": [p.to_dict() for p in self._positions.values()],
            "applied_fill_ids": sorted(self._applied_fill_ids),
        }
        tmp = self.path.with_suffix(".tmp")
        data = json.dumps(payload, indent=2)
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            try:
                import os
                os.fsync(fh.fileno())
            except OSError:
                pass
        tmp.replace(self.path)

    def all(self) -> list[StoredPosition]:
        with self._lock:
            return list(self._positions.values())

    def get(self, symbol: str) -> StoredPosition | None:
        with self._lock:
            return self._positions.get(symbol)

    def upsert(
        self,
        *,
        symbol: str,
        base: str,
        quote: str,
        amount: float,
        entry_price: float,
        order_id: str = "",
        client_order_id: str = "",
        opened_at: float | None = None,
        fill_event_id: str = "",
    ) -> StoredPosition:
        with self._lock:
            fid = (fill_event_id or "").strip()
            if fid and fid in self._applied_fill_ids:
                existing = self._positions.get(symbol)
                if existing:
                    return existing
                return StoredPosition(
                    symbol=symbol, base=base, quote=quote, amount=0.0,
                    entry_price=entry_price, opened_at=time.time(),
                )
            existing = self._positions.get(symbol)
            if existing and existing.amount > 0 and amount > 0:
                total_base = existing.amount + amount
                if total_base > 0:
                    entry = (
                        (existing.entry_price * existing.amount) + (entry_price * amount)
                    ) / total_base
                else:
                    entry = entry_price
                pos = StoredPosition(
                    symbol=symbol,
                    base=base or existing.base,
                    quote=quote or existing.quote,
                    amount=total_base,
                    entry_price=entry,
                    opened_at=existing.opened_at,
                    order_id=order_id or existing.order_id,
                    client_order_id=client_order_id or existing.client_order_id,
                    updated_at=time.time(),
                )
            else:
                pos = StoredPosition(
                    symbol=symbol,
                    base=base,
                    quote=quote,
                    amount=amount,
                    entry_price=entry_price,
                    opened_at=opened_at if opened_at is not None else time.time(),
                    order_id=order_id,
                    client_order_id=client_order_id,
                    updated_at=time.time(),
                )
            if pos.amount <= 0:
                self._positions.pop(symbol, None)
            else:
                self._positions[symbol] = pos
            if fid:
                self._applied_fill_ids.add(fid)
            self._save()
            return pos

    def reduce_or_close(
        self,
        symbol: str,
        amount_sold: float,
        *,
        fill_event_id: str = "",
    ) -> StoredPosition | None:
        with self._lock:
            fid = (fill_event_id or "").strip()
            if fid and fid in self._applied_fill_ids:
                return self._positions.get(symbol)
            existing = self._positions.get(symbol)
            if not existing:
                return None
            remaining = existing.amount - amount_sold
            if remaining <= 1e-12:
                self._positions.pop(symbol, None)
                if fid:
                    self._applied_fill_ids.add(fid)
                self._save()
                return None
            existing.amount = remaining
            existing.updated_at = time.time()
            if fid:
                self._applied_fill_ids.add(fid)
            self._save()
            return existing

    def remove(self, symbol: str) -> None:
        with self._lock:
            if symbol in self._positions:
                del self._positions[symbol]
                self._save()

    def detect_discrepancies(
        self,
        free_map: dict[str, float],
        *,
        min_dust: float = 1e-8,
        stable_like: frozenset[str] | None = None,
    ) -> list[PositionDiscrepancy]:
        """Compare PositionStore vs exchange free balances. No mutation, no trades (S6)."""
        _stables = stable_like or frozenset({"IDR", "USDT", "USDC", "BUSD", "USD"})
        out: list[PositionDiscrepancy] = []
        with self._lock:
            for symbol, pos in list(self._positions.items()):
                base = (pos.base or (symbol.split("/")[0] if "/" in symbol else symbol)).upper()
                if base in _stables:
                    continue
                free = float(free_map.get(base, 0.0))
                if free + min_dust < pos.amount:
                    delta = float(pos.amount) - free
                    out.append(
                        PositionDiscrepancy(
                            symbol=symbol,
                            base=base,
                            store_amount=float(pos.amount),
                            exchange_free=free,
                            delta=delta,
                            note=(
                                f"store_overstates {symbol}: store={pos.amount:.8f} "
                                f"exchange_free={free:.8f}"
                            ),
                        )
                    )
        return out

    def reconcile_with_balances(
        self,
        free_map: dict[str, float],
        *,
        min_dust: float = 1e-8,
        stable_like: frozenset[str] | None = None,
        apply_align: bool = True,
    ) -> list[str]:
        """Align internal store DOWN to exchange free when overstated.

        S6 rule: never place corrective BUY/SELL. Only adjust internal accounting
        to the exchange SSOT for free base, and report notes for audit.
        """
        _stables = stable_like or frozenset({"IDR", "USDT", "USDC", "BUSD", "USD"})
        notes: list[str] = []
        with self._lock:
            to_delete: list[str] = []
            for symbol, pos in list(self._positions.items()):
                base = (pos.base or symbol.split("/")[0]).upper()
                free = float(free_map.get(base, 0.0))
                if free <= min_dust:
                    to_delete.append(symbol)
                    notes.append(f"removed {symbol}: no free {base} on exchange")
                    continue
                if free + 1e-12 < pos.amount:
                    notes.append(
                        f"shrunk {symbol}: store={pos.amount:.8f} exchange_free={free:.8f}"
                    )
                    if apply_align:
                        pos.amount = free
                        pos.updated_at = time.time()
            if apply_align:
                for symbol in to_delete:
                    self._positions.pop(symbol, None)
                self._save()
        for n in notes:
            logger.info("event=position_reconcile %s", n)
        return notes
