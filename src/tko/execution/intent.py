"""Order intent lifecycle, persistence, and idempotency."""

from __future__ import annotations

import json
import logging
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
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"
    RECONCILIATION = "RECONCILIATION"
    RETRY_ELIGIBLE = "RETRY_ELIGIBLE"
    FAILED = "FAILED"


BLOCKS_DUPLICATE = frozenset(
    {
        OrderIntentStatus.SUBMITTING,
        OrderIntentStatus.UNKNOWN,
        OrderIntentStatus.RECONCILIATION,
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
    created_at: float
    updated_at: float
    quote_amount: float | None = None
    base_amount: float | None = None
    normalized_base: float | None = None
    last_price: float | None = None
    exchange_order_id: str | None = None
    filled: float | None = None
    average: float | None = None
    error_category: str | None = None
    error_message: str | None = None
    reason: str = ""
    attempts: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OrderIntent":
        st = data.get("status", OrderIntentStatus.ORDER_INTENT_CREATED.value)
        return cls(
            intent_id=str(data["intent_id"]),
            client_order_id=str(data["client_order_id"]),
            symbol=str(data["symbol"]),
            side=str(data["side"]),
            status=OrderIntentStatus(st),
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
            quote_amount=data.get("quote_amount"),
            base_amount=data.get("base_amount"),
            normalized_base=data.get("normalized_base"),
            last_price=data.get("last_price"),
            exchange_order_id=data.get("exchange_order_id"),
            filled=data.get("filled"),
            average=data.get("average"),
            error_category=data.get("error_category"),
            error_message=data.get("error_message"),
            reason=str(data.get("reason") or ""),
            attempts=int(data.get("attempts") or 0),
            extra=dict(data.get("extra") or {}),
        )


class IntentStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._intents: dict[str, OrderIntent] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for item in raw.get("intents", []):
                intent = OrderIntent.from_dict(item)
                self._intents[intent.intent_id] = intent
        except Exception as exc:
            logger.warning("Failed to load intent store: %s", exc)

    def _save(self) -> None:
        payload = {
            "updated_at": time.time(),
            "intents": [i.to_dict() for i in self._intents.values()],
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def create(
        self,
        *,
        symbol: str,
        side: str,
        quote_amount: float | None = None,
        base_amount: float | None = None,
        last_price: float | None = None,
        reason: str = "",
        strategy: str = "btc",
    ) -> OrderIntent:
        with self._lock:
            intent_id = uuid.uuid4().hex
            cid = generate_client_order_id(side, symbol, strategy)
            now = time.time()
            intent = OrderIntent(
                intent_id=intent_id,
                client_order_id=cid,
                symbol=symbol,
                side=side.lower(),
                status=OrderIntentStatus.CLIENT_ID_ASSIGNED,
                created_at=now,
                updated_at=now,
                quote_amount=quote_amount,
                base_amount=base_amount,
                last_price=last_price,
                reason=reason,
            )
            self._intents[intent_id] = intent
            intent.status = OrderIntentStatus.PERSISTED
            intent.updated_at = time.time()
            self._save()
            logger.info(
                "event=order_intent_created client_order_id=%s symbol=%s side=%s mode=LIVE",
                intent.client_order_id, symbol, side,
            )
            return intent

    def update(self, intent: OrderIntent) -> None:
        with self._lock:
            intent.updated_at = time.time()
            self._intents[intent.intent_id] = intent
            self._save()

    def get(self, intent_id: str) -> OrderIntent | None:
        with self._lock:
            return self._intents.get(intent_id)

    def by_client_id(self, client_order_id: str) -> OrderIntent | None:
        with self._lock:
            for i in self._intents.values():
                if i.client_order_id == client_order_id:
                    return i
        return None

    def active_for_symbol(self, symbol: str, side: str | None = None) -> list[OrderIntent]:
        with self._lock:
            out = []
            for i in self._intents.values():
                if i.symbol != symbol:
                    continue
                if side and i.side != side.lower():
                    continue
                if i.status in BLOCKS_DUPLICATE or i.status == OrderIntentStatus.RETRY_ELIGIBLE:
                    out.append(i)
            return out

    def has_blocking_intent(self, symbol: str, side: str | None = None) -> bool:
        for i in self.active_for_symbol(symbol, side):
            if i.status in BLOCKS_DUPLICATE:
                return True
        return False

    def unresolved_unknown(self) -> list[OrderIntent]:
        with self._lock:
            return [
                i for i in self._intents.values()
                if i.status in (OrderIntentStatus.UNKNOWN, OrderIntentStatus.RECONCILIATION)
            ]
