"""Order execution manager - Risk → Filters → Submit → Track → Reconcile."""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from src.core.order_state import Order, OrderState
from src.execution.filters import SymbolFilters, normalize_order
from src.risk.engine import RiskEngine, RiskStatus
from src.tokocrypto.rest import OrderResultStatus, RestClient

logger = logging.getLogger(__name__)


class ExecutionManager:
    def __init__(
        self,
        rest: RestClient,
        risk: RiskEngine,
        mode: str = "PAPER",
        symbol_filters: Optional[Dict[str, SymbolFilters]] = None,
    ):
        self.rest = rest
        self.risk = risk
        self.mode = mode.upper()  # PAPER | SHADOW | LIVE
        self.filters = symbol_filters or {}
        self.orders: Dict[str, Order] = {}  # client_id -> Order
        self._paper_id_seq = 1000000

    def set_mode(self, mode: str) -> None:
        m = mode.upper()
        if m not in ("PAPER", "SHADOW", "LIVE"):
            raise ValueError("mode must be PAPER/SHADOW/LIVE")
        if m == "LIVE" and self.risk.kill_switch:
            raise RuntimeError("Cannot enable LIVE while kill switch is active")
        self.mode = m
        logger.info("Execution mode set to %s", self.mode)

    def submit(
        self,
        symbol: str,
        side: int,
        order_type: int,
        quantity: Optional[str] = None,
        price: Optional[str] = None,
        quote_order_qty: Optional[str] = None,
        available_balance: float = 0.0,
        reference_price: Optional[float] = None,
        client_id: Optional[str] = None,
    ) -> Order:
        cid = client_id or Order.make_client_id()
        order = Order(
            client_id=cid,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity or "0",
            price=price,
            quote_order_qty=quote_order_qty,
            mode=self.mode,
        )
        self.orders[cid] = order

        # 1. RISK_CHECK
        order.transition(OrderState.RISK_CHECK)
        notional = 0.0
        try:
            if quantity and price:
                notional = float(quantity) * float(price)
            elif quote_order_qty:
                notional = float(quote_order_qty)
            elif quantity and reference_price:
                notional = float(quantity) * reference_price
        except Exception:
            notional = 0.0

        risk_status: RiskStatus = self.risk.check(
            symbol=symbol,
            side=side,
            notional=notional,
            available_balance=available_balance,
        )
        if not risk_status.allowed:
            order.transition(OrderState.REJECTED, risk_status.reason)
            logger.warning("Order %s rejected by Risk: %s", cid, risk_status.reason)
            return order

        # 2. EXCHANGE_RULE_CHECK + NORMALIZE
        order.transition(OrderState.EXCHANGE_RULE_CHECK)
        sf = self.filters.get(symbol)
        if sf is None:
            order.transition(OrderState.REJECTED, "no symbol filters")
            return order

        from decimal import Decimal
        ref = Decimal(str(reference_price)) if reference_price else None
        ok, norm, err = normalize_order(
            sf, side, order_type, quantity, price, quote_order_qty, ref
        )
        if not ok:
            order.transition(OrderState.REJECTED, err)
            return order
        order.transition(OrderState.NORMALIZE, str(norm))
        if "quantity" in norm:
            order.quantity = norm["quantity"]
        if "price" in norm:
            order.price = norm["price"]
        if "quoteOrderQty" in norm:
            order.quote_order_qty = norm["quoteOrderQty"]

        # 3. SUBMIT
        order.transition(OrderState.SUBMIT)
        if self.mode == "PAPER":
            return self._paper_fill(order)
        if self.mode == "SHADOW":
            # simulate submit but do not send real order
            order.transition(OrderState.ACK, "SHADOW simulated")
            order.exchange_order_id = self._next_paper_id()
            return order

        # LIVE
        status, body = self.rest.new_order(
            symbol=order.symbol,
            side=order.side,
            order_type=order.order_type,
            quantity=order.quantity,
            price=order.price,
            quote_order_qty=order.quote_order_qty,
            client_id=order.client_id,
        )
        order.raw_ack = body

        if status == OrderResultStatus.ACK:
            order.transition(OrderState.ACK)
            # extract orderId if present
            data = body.get("data") or body
            oid = data.get("orderId") or data.get("order_id")
            if oid is not None:
                order.exchange_order_id = int(oid)
            order.transition(OrderState.TRACK)
        elif status == OrderResultStatus.REJECTED:
            order.transition(OrderState.REJECTED, str(body)[:200])
        elif status == OrderResultStatus.RATE_LIMITED:
            order.transition(OrderState.REJECTED, "RATE_LIMITED")
            self.risk.record_api_error()
        elif status == OrderResultStatus.IP_BANNED:
            order.transition(OrderState.REJECTED, "IP_BANNED")
            self.risk.activate_kill_switch("IP_BANNED 418")
        elif status == OrderResultStatus.UNKNOWN:
            order.transition(OrderState.UNKNOWN, "UNKNOWN - needs reconciliation")
            # DO NOT treat as failed; schedule reconcile
        else:
            order.transition(OrderState.UNKNOWN, str(status))

        return order

    def reconcile(self, order: Order) -> Order:
        """Query exchange for true status of UNKNOWN orders."""
        if order.state != OrderState.UNKNOWN and order.state != OrderState.TRACK:
            return order
        if not order.exchange_order_id and not order.client_id:
            return order
        status, body = self.rest.query_order(
            order_id=order.exchange_order_id,
            client_id=order.client_id if not order.exchange_order_id else None,
        )
        order.raw_last = body
        if status == OrderResultStatus.ACK:
            data = body.get("data") or body
            st = str(data.get("status", "")).upper()
            filled = data.get("executedQty") or data.get("filledQty") or "0"
            order.filled_qty = str(filled)
            if st in ("FILLED", "2"):
                order.transition(OrderState.FILLED)
            elif st in ("PARTIALLY_FILLED", "1"):
                order.transition(OrderState.PARTIAL)
            elif st in ("CANCELED", "CANCELLED", "4"):
                order.transition(OrderState.CANCELED)
            elif st in ("EXPIRED", "5"):
                order.transition(OrderState.EXPIRED)
            elif st in ("NEW", "0"):
                order.transition(OrderState.TRACK)
            else:
                order.transition(OrderState.TRACK, f"status={st}")
        elif status == OrderResultStatus.REJECTED:
            # order may not exist
            order.transition(OrderState.REJECTED, "query returned reject")
        else:
            # still unknown
            order.transition(OrderState.UNKNOWN, "reconcile still unknown")
        return order

    def _paper_fill(self, order: Order) -> Order:
        """Immediate fill simulation for PAPER mode."""
        order.exchange_order_id = self._next_paper_id()
        order.transition(OrderState.ACK, "PAPER")
        order.filled_qty = order.quantity
        order.avg_price = order.price or "0"
        order.transition(OrderState.FILLED, "PAPER simulated fill")
        return order

    def _next_paper_id(self) -> int:
        self._paper_id_seq += 1
        return self._paper_id_seq

    def get_active(self) -> List[Order]:
        return [o for o in self.orders.values() if not o.is_terminal()]
