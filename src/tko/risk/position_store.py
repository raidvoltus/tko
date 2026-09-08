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
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            items = raw.get("positions") if isinstance(raw, dict) else raw
            if not isinstance(items, list):
                return
            for item in items:
                if not isinstance(item, dict):
                    continue
                pos = StoredPosition.from_dict(item)
                if pos.amount > 0 and pos.symbol:
                    self._positions[pos.symbol] = pos
        except Exception as exc:
            logger.warning("Failed to load positions from %s: %s", self.path, exc)

    def _save(self) -> None:
        payload = {
            "updated_at": time.time(),
            "positions": [p.to_dict() for p in self._positions.values()],
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
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
    ) -> StoredPosition:
        with self._lock:
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
            self._save()
            return pos

    def reduce_or_close(self, symbol: str, amount_sold: float) -> StoredPosition | None:
        with self._lock:
            existing = self._positions.get(symbol)
            if not existing:
                return None
            remaining = existing.amount - amount_sold
            if remaining <= 1e-12:
                self._positions.pop(symbol, None)
                self._save()
                return None
            existing.amount = remaining
            existing.updated_at = time.time()
            self._save()
            return existing

    def remove(self, symbol: str) -> None:
        with self._lock:
            if symbol in self._positions:
                del self._positions[symbol]
                self._save()

    def reconcile_with_balances(
        self,
        free_map: dict[str, float],
        *,
        min_dust: float = 1e-8,
        stable_like: frozenset[str] | None = None,
    ) -> list[str]:
        stables = stable_like or frozenset({"IDR", "USDT", "USDC", "BUSD", "USD"})
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
                    pos.amount = free
                    pos.updated_at = time.time()
            for symbol in to_delete:
                self._positions.pop(symbol, None)
            self._save()
        for n in notes:
            logger.info("event=position_reconcile %s", n)
        return notes
