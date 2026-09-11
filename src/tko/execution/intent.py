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
    ORDER_INTENT_CREATED = "ORDER_INTENT_CREATED"
    CLIENT_ID_ASSIGNED = "CLIENT_ID_ASSIGNED"
    PERSISTED = "PERSISTED"
    PRE_TRADE_VALIDATION = "PRE_TRADE_VALIDATION"
    NORMALIZED = "NORMALIZED"
    SUBMITTING = "SUBMITTING"
    CONFIRMED = "CONFIRMED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"
    RECONCILIATION = "RECONCILIATION"
    RETRY_ELIGIBLE = "RETRY_ELIGIBLE"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    GOVERNOR_AUTONOMOUS = "GOVERNOR_AUTONOMOUS"
    FAILED = "FAILED"


BLOCKS_DUPLICATE = frozenset(
    {
        OrderIntentStatus.SUBMITTING,
        OrderIntentStatus.PARTIALLY_FILLED,
        OrderIntentStatus.UNKNOWN,
        OrderIntentStatus.RECONCILIATION,
        OrderIntentStatus.GOVERNOR_AUTONOMOUS,
        OrderIntentStatus.MANUAL_REVIEW,
        OrderIntentStatus.RETRY_ELIGIBLE,
        OrderIntentStatus.NORMALIZED,
        OrderIntentStatus.PRE_TRADE_VALIDATION,
        OrderIntentStatus.PERSISTED,
        OrderIntentStatus.CLIENT_ID_ASSIGNED,
    }
)


def generate_client_order_id(side: str, symbol: str, strategy: str = "btc") -> str:
    sym = symbol.replace("/", "").replace("_", "")[:10]
    ts = int(time.time() * 1000) % 10_000_000_000
    uniq = uuid.uuid4().hex[:8]
    return f"TKO-{strategy[:6]}-{sym}-{side[:1].upper()}-{ts}-{uniq}"[:48]


@dataclass
class OrderIntent:
    intent_id: str
    client_order_id: str
    symbol: str
    side: str
    status: OrderIntentStatus
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    base_amount: float = 0.0
    quote_amount: float = 0.0
    normalized_base: float = 0.0
    last_price: float = 0.0
    reason: str = ""
    strategy: str = "btc"
    attempts: int = 0
    exchange_order_id: str = ""
    filled: float = 0.0
    accounted_filled: float = 0.0
    average: float | None = None
    error_category: str = ""
    error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OrderIntent":
        st = data.get("status", "ORDER_INTENT_CREATED")
        try:
            status = OrderIntentStatus(st)
        except ValueError:
            status = OrderIntentStatus.FAILED
        if status == OrderIntentStatus.MANUAL_REVIEW:
            status = OrderIntentStatus.GOVERNOR_AUTONOMOUS
        return cls(
            intent_id=str(data.get("intent_id") or uuid.uuid4().hex),
            client_order_id=str(data.get("client_order_id") or ""),
            symbol=str(data.get("symbol") or ""),
            side=str(data.get("side") or ""),
            status=status,
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
            base_amount=float(data.get("base_amount") or 0.0),
            quote_amount=float(data.get("quote_amount") or 0.0),
            normalized_base=float(data.get("normalized_base") or 0.0),
            last_price=float(data.get("last_price") or 0.0),
            reason=str(data.get("reason") or ""),
            strategy=str(data.get("strategy") or "btc"),
            attempts=int(data.get("attempts") or 0),
            exchange_order_id=str(data.get("exchange_order_id") or ""),
            filled=float(data.get("filled") or 0.0),
            accounted_filled=float(data.get("accounted_filled") or 0.0),
            average=(float(data["average"]) if data.get("average") is not None else None),
            error_category=str(data.get("error_category") or ""),
            error_message=str(data.get("error_message") or ""),
        )


class IntentStore:
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
            raw_text = self.path.read_text(encoding="utf-8")
            if not raw_text.strip():
                self.corrupted = True
                self.corruption_reason = "intent store file is empty"
                self._items = {}
                logger.critical("event=intent_store_corrupted reason=%s", self.corruption_reason)
                return
            raw = json.loads(raw_text)
            items = raw.get("intents") if isinstance(raw, dict) else raw
            if not isinstance(items, list):
                self.corrupted = True
                self.corruption_reason = "intent store schema invalid"
                self._items = {}
                logger.critical("event=intent_store_corrupted reason=%s", self.corruption_reason)
                return
            loaded: dict = {}
            for item in items:
                if not isinstance(item, dict):
                    self.corrupted = True
                    self.corruption_reason = "intent non-dict entry"
                    self._items = {}
                    logger.critical("event=intent_store_corrupted reason=%s", self.corruption_reason)
                    return
                intent = OrderIntent.from_dict(item)
                if not intent.client_order_id:
                    self.corrupted = True
                    self.corruption_reason = "missing client_order_id"
                    self._items = {}
                    logger.critical("event=intent_store_corrupted reason=%s", self.corruption_reason)
                    return
                loaded[intent.client_order_id] = intent
            self._items = loaded
            self.corrupted = False
            self.corruption_reason = ""
        except Exception as exc:
            self.corrupted = True
            self.corruption_reason = f"intent load failed: {type(exc).__name__}: {exc}"
            self._items = {}
            logger.critical("event=intent_store_corrupted reason=%s", self.corruption_reason)

    def _save(self) -> None:
        payload = {"intents": [i.to_dict() for i in self._items.values()]}
        tmp = self.path.with_suffix(".tmp")
        data = json.dumps(payload, indent=2)
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        tmp.replace(self.path)
        try:
            dir_fd = os.open(str(self.path.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass

    def create(
        self,
        *,
        symbol: str,
        side: str,
        base_amount: float = 0.0,
        quote_amount: float = 0.0,
        last_price: float = 0.0,
        reason: str = "",
        strategy: str = "btc",
    ) -> OrderIntent:
        with self._lock:
            return self._create_unlocked(
                symbol=symbol, side=side, base_amount=base_amount,
                quote_amount=quote_amount, last_price=last_price,
                reason=reason, strategy=strategy,
            )

    def create_if_absent(
        self,
        *,
        symbol: str,
        side: str,
        base_amount: float = 0.0,
        quote_amount: float = 0.0,
        last_price: float = 0.0,
        reason: str = "",
        strategy: str = "btc",
    ) -> OrderIntent | None:
        with self._lock:
            if self.corrupted:
                logger.critical("event=intent_create_blocked reason=store_corrupted")
                return None
            for intent in self._items.values():
                if (
                    intent.symbol == symbol
                    and intent.side == side
                    and intent.status in BLOCKS_DUPLICATE
                ):
                    return None
            return self._create_unlocked(
                symbol=symbol, side=side, base_amount=base_amount,
                quote_amount=quote_amount, last_price=last_price,
                reason=reason, strategy=strategy,
            )

    def _create_unlocked(
        self,
        *,
        symbol: str,
        side: str,
        base_amount: float = 0.0,
        quote_amount: float = 0.0,
        last_price: float = 0.0,
        reason: str = "",
        strategy: str = "btc",
    ) -> OrderIntent:
        cid = generate_client_order_id(side, symbol, strategy)
        intent = OrderIntent(
            intent_id=uuid.uuid4().hex,
            client_order_id=cid,
            symbol=symbol,
            side=side,
            status=OrderIntentStatus.PERSISTED,
            base_amount=base_amount,
            quote_amount=quote_amount,
            last_price=last_price,
            reason=reason,
            strategy=strategy,
        )
        self._items[cid] = intent
        self._save()
        return intent

    def update(self, intent: OrderIntent) -> None:
        intent.updated_at = time.time()
        with self._lock:
            self._items[intent.client_order_id] = intent
            self._save()

    def by_client_id(self, client_order_id: str) -> OrderIntent | None:
        with self._lock:
            return self._items.get(client_order_id)

    def has_blocking_intent(self, symbol: str, side: str) -> bool:
        with self._lock:
            for intent in self._items.values():
                if (
                    intent.symbol == symbol
                    and intent.side == side
                    and intent.status in BLOCKS_DUPLICATE
                ):
                    return True
        return False

    def unresolved_unknown(self) -> list[OrderIntent]:
        with self._lock:
            return [
                i for i in self._items.values()
                if i.status in (
                    OrderIntentStatus.UNKNOWN,
                    OrderIntentStatus.RECONCILIATION,
                    OrderIntentStatus.PARTIALLY_FILLED,
                )
            ]

    def unresolved_for_recovery(self) -> list[OrderIntent]:
        recover = (
            OrderIntentStatus.SUBMITTING,
            OrderIntentStatus.UNKNOWN,
            OrderIntentStatus.RECONCILIATION,
            OrderIntentStatus.PARTIALLY_FILLED,
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
                i for i in self._items.values()
                if i.side == "buy" and i.status in holding and float(i.quote_amount or 0) > 0
            ]

    def governor_intents(self) -> list[OrderIntent]:
        with self._lock:
            return [
                i for i in self._items.values()
                if i.status == OrderIntentStatus.GOVERNOR_AUTONOMOUS
            ]

    def blocking_intents(self, symbol: str, side: str) -> list[OrderIntent]:
        with self._lock:
            return [
                i for i in self._items.values()
                if i.symbol == symbol and i.side == side and i.status in BLOCKS_DUPLICATE
            ]
