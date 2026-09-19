"""Order lifecycle state machine."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class OrderState(str, Enum):
    INTENT = "INTENT"
    RISK_CHECK = "RISK_CHECK"
    EXCHANGE_RULE_CHECK = "EXCHANGE_RULE_CHECK"
    NORMALIZE = "NORMALIZE"
    SUBMIT = "SUBMIT"
    ACK = "ACK"
    TRACK = "TRACK"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


TERMINAL_STATES = {
    OrderState.FILLED,
    OrderState.CANCELED,
    OrderState.EXPIRED,
    OrderState.REJECTED,
}


@dataclass
class Order:
    client_id: str
    symbol: str
    side: int          # 0 BUY, 1 SELL
    order_type: int    # 1 LIMIT, 2 MARKET
    quantity: str
    price: Optional[str] = None
    quote_order_qty: Optional[str] = None
    time_in_force: Optional[int] = None
    state: OrderState = OrderState.INTENT
    exchange_order_id: Optional[int] = None
    filled_qty: str = "0"
    avg_price: Optional[str] = None
    status_text: str = ""
    created_ts: float = field(default_factory=time.time)
    updated_ts: float = field(default_factory=time.time)
    history: List[Dict[str, Any]] = field(default_factory=list)
    mode: str = "LIVE"  # PAPER / SHADOW / LIVE
    raw_ack: Optional[Dict] = None
    raw_last: Optional[Dict] = None

    def transition(self, new_state: OrderState, detail: str = "") -> None:
        old = self.state
        self.state = new_state
        self.updated_ts = time.time()
        self.history.append({
            "from": old.value,
            "to": new_state.value,
            "ts": self.updated_ts,
            "detail": detail,
        })

    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @staticmethod
    def make_client_id(prefix: str = "bot") -> str:
        return f"{prefix}-{uuid.uuid4().hex[:16]}"
