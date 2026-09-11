"""Order intent lifecycle, persistence, and idempotency."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class OrderIntentStatus(str, Enum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    RECONCILIATION = "RECONCILIATION"


# Statuses that block a new intent with the same client_order_id (no-repost guard).
BLOCKS_DUPLICATE = frozenset(
    {
        OrderIntentStatus.PENDING,
        OrderIntentStatus.SUBMITTED,
        OrderIntentStatus.PARTIALLY_FILLED,
        OrderIntentStatus.UNKNOWN,
        OrderIntentStatus.RECONCILIATION,
        OrderIntentStatus.CONFIRMED,
    }
)


@dataclass
class OrderIntent:
    client_order_id: str
    symbol: str
    side: str
    order_type: str
    quote_amount: float = 0.0
    base_amount: float = 0.0
    price: float | None = None
    status: OrderIntentStatus = OrderIntentStatus.PENDING
    exchange_order_id: str = ""
    filled: float = 0.0
    average: float | None = None
    remaining: float | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    notes: str = ""
    last_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value if isinstance(self.status, OrderIntentStatus) else str(self.status)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "OrderIntent":
        status_raw = d.get("status", "PENDING")
        try:
            status = OrderIntentStatus(status_raw)
        except ValueError:
            status = OrderIntentStatus.UNKNOWN
        return cls(
            client_order_id=str(d.get("client_order_id") or ""),
            symbol=str(d.get("symbol") or ""),
            side=str(d.get("side") or ""),
            order_type=str(d.get("order_type") or ""),
            quote_amount=float(d.get("quote_amount") or 0),
            base_amount=float(d.get("base_amount") or 0),
            price=(float(d["price"]) if d.get("price") is not None else None),
            status=status,
            exchange_order_id=str(d.get("exchange_order_id") or ""),
            filled=float(d.get("filled") or 0),
            average=(float(d["average"]) if d.get("average") is not None else None),
            remaining=(float(d["remaining"]) if d.get("remaining") is not None else None),
            created_at=float(d.get("created_at") or time.time()),
            updated_at=float(d.get("updated_at") or time.time()),
            notes=str(d.get("notes") or ""),
            last_error=str(d.get("last_error") or ""),
        )


class IntentStore:
    """Durable intent store. Corruption → fail-closed (no create, no silent recovery)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._intents: dict[str, OrderIntent] = {}
        self.corrupted: bool = False
        self.corruption_reason: str = ""
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = self.path.read_text(encoding="utf-8")
            if not raw.strip():
                return
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("intent store root is not object")
            items = data.get("intents") if "intents" in data else data
            if not isinstance(items, dict):
                raise ValueError("intents is not object")
            loaded: dict[str, OrderIntent] = {}
            for k, v in items.items():
                if not isinstance(v, dict):
                    raise ValueError(f"intent {k!r} is not object")
                intent = OrderIntent.from_dict(v)
                if not intent.client_order_id:
                    intent.client_order_id = str(k)
                loaded[intent.client_order_id] = intent
            self._intents = loaded
        except Exception as exc:
            self.corrupted = True
            self.corruption_reason = f"intent store load failed: {exc}"
            self._intents = {}
            logger.critical("event=intent_store_corrupted reason=%s", self.corruption_reason)

    def _persist(self) -> None:
        if self.corrupted:
            raise RuntimeError(f"intent store corrupted: {self.corruption_reason}")
        payload = {"intents": {k: v.to_dict() for k, v in self._intents.items()}}
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, indent=2))
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        tmp.replace(self.path)

    def get(self, client_order_id: str) -> OrderIntent | None:
        with self._lock:
            return self._intents.get(client_order_id)

    def all(self) -> list[OrderIntent]:
        with self._lock:
            return list(self._intents.values())

    def create_if_absent(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str,
        quote_amount: float = 0.0,
        base_amount: float = 0.0,
        price: float | None = None,
        client_order_id: str | None = None,
    ) -> OrderIntent:
        if self.corrupted:
            raise RuntimeError(f"intent store corrupted: {self.corruption_reason}")
        cid = (client_order_id or "").strip() or f"tko-{uuid.uuid4().hex[:16]}"
        with self._lock:
            existing = self._intents.get(cid)
            if existing is not None:
                if existing.status in BLOCKS_DUPLICATE:
                    raise RuntimeError(
                        f"no-repost: client_order_id={cid} already exists status={existing.status.value}"
                    )
                return existing
            intent = OrderIntent(
                client_order_id=cid,
                symbol=symbol,
                side=side,
                order_type=order_type,
                quote_amount=float(quote_amount or 0),
                base_amount=float(base_amount or 0),
                price=price,
            )
            self._intents[cid] = intent
            self._persist()
            return intent

    def update(self, intent: OrderIntent) -> None:
        if self.corrupted:
            raise RuntimeError(f"intent store corrupted: {self.corruption_reason}")
        intent.updated_at = time.time()
        with self._lock:
            self._intents[intent.client_order_id] = intent
            self._persist()

    def mark(
        self,
        client_order_id: str,
        status: OrderIntentStatus,
        *,
        exchange_order_id: str = "",
        filled: float | None = None,
        average: float | None = None,
        remaining: float | None = None,
        notes: str = "",
        last_error: str = "",
    ) -> OrderIntent | None:
        if self.corrupted:
            raise RuntimeError(f"intent store corrupted: {self.corruption_reason}")
        with self._lock:
            intent = self._intents.get(client_order_id)
            if intent is None:
                return None
            intent.status = status
            if exchange_order_id:
                intent.exchange_order_id = exchange_order_id
            if filled is not None:
                intent.filled = float(filled)
            if average is not None:
                intent.average = float(average)
            if remaining is not None:
                intent.remaining = float(remaining)
            if notes:
                intent.notes = notes
            if last_error:
                intent.last_error = last_error
            intent.updated_at = time.time()
            self._persist()
            return intent

    def open_or_unknown(self) -> list[OrderIntent]:
        with self._lock:
            return [
                i
                for i in self._intents.values()
                if i.status
                in (
                    OrderIntentStatus.PENDING,
                    OrderIntentStatus.SUBMITTED,
                    OrderIntentStatus.PARTIALLY_FILLED,
                    OrderIntentStatus.UNKNOWN,
                    OrderIntentStatus.RECONCILIATION,
                )
            ]
