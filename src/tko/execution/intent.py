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
    CLIENT_ID_ASSIGNED = "CLIENT_ID_ASSIGNED"
    PRE_TRADE_VALIDATION = "PRE_TRADE_VALIDATION"
    PERSISTED = "PERSISTED"
    NORMALIZED = "NORMALIZED"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    RECONCILIATION = "RECONCILIATION"
    GOVERNOR_AUTONOMOUS = "GOVERNOR_AUTONOMOUS"


BLOCKS_DUPLICATE = frozenset(
    {
        OrderIntentStatus.PENDING,
        OrderIntentStatus.CLIENT_ID_ASSIGNED,
        OrderIntentStatus.PRE_TRADE_VALIDATION,
        OrderIntentStatus.PERSISTED,
        OrderIntentStatus.NORMALIZED,
        OrderIntentStatus.SUBMITTING,
        OrderIntentStatus.SUBMITTED,
        OrderIntentStatus.PARTIALLY_FILLED,
        OrderIntentStatus.UNKNOWN,
        OrderIntentStatus.RECONCILIATION,
        OrderIntentStatus.GOVERNOR_AUTONOMOUS,
        OrderIntentStatus.CONFIRMED,
    }
)


def generate_client_order_id(side: str, symbol: str, strategy: str = "btc") -> str:
    sym = (symbol or "").replace("/", "").replace("-", "")[:12]
    return f"tko-{side[:1]}-{sym}-{strategy}-{uuid.uuid4().hex[:12]}"


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
    strategy: str = "btc"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value if isinstance(self.status, OrderIntentStatus) else str(self.status)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OrderIntent":
        status_raw = data.get("status", "PENDING")
        try:
            status = OrderIntentStatus(status_raw)
        except ValueError:
            status = OrderIntentStatus.UNKNOWN
        return cls(
            client_order_id=str(data.get("client_order_id") or ""),
            symbol=str(data.get("symbol") or ""),
            side=str(data.get("side") or ""),
            order_type=str(data.get("order_type") or ""),
            quote_amount=float(data.get("quote_amount") or 0),
            base_amount=float(data.get("base_amount") or 0),
            price=(float(data["price"]) if data.get("price") is not None else None),
            status=status,
            exchange_order_id=str(data.get("exchange_order_id") or ""),
            filled=float(data.get("filled") or 0),
            average=(float(data["average"]) if data.get("average") is not None else None),
            remaining=(float(data["remaining"]) if data.get("remaining") is not None else None),
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
            notes=str(data.get("notes") or ""),
            last_error=str(data.get("last_error") or ""),
            strategy=str(data.get("strategy") or "btc"),
        )


class IntentStore:
    """Durable intent store. Corruption → fail-closed (no create, no silent recovery)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._items: dict[str, OrderIntent] = {}
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
            self._items = loaded
        except Exception as e:
            self.corrupted = True
            self.corruption_reason = f"intent store load failed: {e}"
            self._items = {}
            logger.critical("event=intent_store_corrupted reason=%s", self.corruption_reason)

    def _save(self) -> None:
        if self.corrupted:
            raise RuntimeError(f"intent store corrupted: {self.corruption_reason}")
        payload = {"intents": {k: v.to_dict() for k, v in self._items.items()}}
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, indent=2))
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        tmp.replace(self.path)

    def create(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str,
        quote_amount: float = 0.0,
        base_amount: float = 0.0,
        price: float | None = None,
        client_order_id: str | None = None,
        strategy: str = "btc",
    ) -> OrderIntent:
        if self.corrupted:
            raise RuntimeError(f"intent store corrupted: {self.corruption_reason}")
        return self._create_unlocked(
            symbol=symbol,
            side=side,
            order_type=order_type,
            quote_amount=quote_amount,
            base_amount=base_amount,
            price=price,
            client_order_id=client_order_id,
            strategy=strategy,
        )

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
        strategy: str = "btc",
    ) -> OrderIntent:
        if self.corrupted:
            raise RuntimeError(f"intent store corrupted: {self.corruption_reason}")
        cid = (client_order_id or "").strip()
        if not cid:
            cid = generate_client_order_id(side, symbol, strategy)
        with self._lock:
            existing = self._items.get(cid)
            if existing is not None:
                if existing.status in BLOCKS_DUPLICATE:
                    raise RuntimeError(
                        f"no-repost: client_order_id={cid} already exists status={existing.status.value}"
                    )
                return existing
            return self._create_unlocked(
                symbol=symbol,
                side=side,
                order_type=order_type,
                quote_amount=quote_amount,
                base_amount=base_amount,
                price=price,
                client_order_id=cid,
                strategy=strategy,
            )

    def _create_unlocked(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str,
        quote_amount: float = 0.0,
        base_amount: float = 0.0,
        price: float | None = None,
        client_order_id: str | None = None,
        strategy: str = "btc",
    ) -> OrderIntent:
        cid = (client_order_id or "").strip() or generate_client_order_id(side, symbol, strategy)
        intent = OrderIntent(
            client_order_id=cid,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quote_amount=float(quote_amount or 0),
            base_amount=float(base_amount or 0),
            price=price,
            strategy=strategy,
        )
        self._items[cid] = intent
        self._save()
        return intent

    def update(self, intent: OrderIntent) -> None:
        if self.corrupted:
            raise RuntimeError(f"intent store corrupted: {self.corruption_reason}")
        intent.updated_at = time.time()
        with self._lock:
            self._items[intent.client_order_id] = intent
            self._save()

    def by_client_id(self, client_order_id: str) -> OrderIntent | None:
        with self._lock:
            return self._items.get(client_order_id)

    def has_blocking_intent(self, symbol: str, side: str) -> bool:
        with self._lock:
            for i in self._items.values():
                if i.symbol == symbol and i.side == side and i.status in BLOCKS_DUPLICATE:
                    return True
            return False

    def unresolved_unknown(self) -> list[OrderIntent]:
        with self._lock:
            return [
                i
                for i in self._items.values()
                if i.status in (OrderIntentStatus.UNKNOWN, OrderIntentStatus.RECONCILIATION)
            ]

    def unresolved_for_recovery(self) -> list[OrderIntent]:
        recover = (
            OrderIntentStatus.PENDING,
            OrderIntentStatus.CLIENT_ID_ASSIGNED,
            OrderIntentStatus.PRE_TRADE_VALIDATION,
            OrderIntentStatus.PERSISTED,
            OrderIntentStatus.NORMALIZED,
            OrderIntentStatus.SUBMITTING,
            OrderIntentStatus.SUBMITTED,
            OrderIntentStatus.PARTIALLY_FILLED,
            OrderIntentStatus.UNKNOWN,
            OrderIntentStatus.RECONCILIATION,
            OrderIntentStatus.GOVERNOR_AUTONOMOUS,
        )
        with self._lock:
            return [i for i in self._items.values() if i.status in recover]

    def buy_intents_holding_budget(self) -> list[OrderIntent]:
        holding = (
            OrderIntentStatus.SUBMITTING,
            OrderIntentStatus.NORMALIZED,
            OrderIntentStatus.UNKNOWN,
            OrderIntentStatus.RECONCILIATION,
            OrderIntentStatus.PARTIALLY_FILLED,
            OrderIntentStatus.GOVERNOR_AUTONOMOUS,
            OrderIntentStatus.PRE_TRADE_VALIDATION,
            OrderIntentStatus.PERSISTED,
            OrderIntentStatus.CLIENT_ID_ASSIGNED,
        )
        with self._lock:
            return [
                i
                for i in self._items.values()
                if i.side == "buy" and i.status in holding and float(i.quote_amount or 0) > 0
            ]

    def governor_intents(self) -> list[OrderIntent]:
        with self._lock:
            return [
                i
                for i in self._items.values()
                if i.status == OrderIntentStatus.GOVERNOR_AUTONOMOUS
            ]

    def blocking_intents(self, symbol: str, side: str) -> list[OrderIntent]:
        with self._lock:
            return [
                i
                for i in self._items.values()
                if i.symbol == symbol and i.side == side and i.status in BLOCKS_DUPLICATE
            ]
