"""Order execution manager — Risk admit → normalize → LIVE RestClient only.

Runtime mode: LIVE only. No PAPER/SHADOW/simulator execution path.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from src.core.order_state import Order, OrderState
from src.execution.filters import SymbolFilters, normalize_order
from src.execution.production_policy import is_production_trading_mode
from src.risk.engine import RiskEngine, RiskStatus
from src.tokocrypto.rest import OrderResultStatus, RestClient

logger = logging.getLogger(__name__)


class ExecutionManager:
    def __init__(
        self,
        rest: RestClient,
        risk: RiskEngine,
        mode: str = "LIVE",
        symbol_filters: Optional[Dict[str, SymbolFilters]] = None,
    ):
        self.rest = rest
        self.risk = risk
        self.mode = mode.upper()
        self.filters = symbol_filters or {}
        self.orders: Dict[str, Order] = {}
        self._paper_id_seq = 1000000

    def set_mode(self, mode: str) -> None:
        from src.execution.production_policy import require_live
        m = require_live(mode)
        if self.risk.kill_switch:
            raise RuntimeError("Cannot enable LIVE while kill switch is active")
        self.mode = m
        logger.info("Execution mode set to LIVE (production=True)")

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

        # 1) Normalize against exchange filters BEFORE risk (authoritative notional)
        order.transition(OrderState.RISK_CHECK)
        filt = self.filters.get(symbol)
        if filt is not None:
            try:
                from decimal import Decimal
                ref = Decimal(str(reference_price)) if reference_price is not None else None
                ok, params, err = normalize_order(
                    filt,
                    side=side,
                    order_type=order_type,
                    quantity=quantity,
                    price=price,
                    quote_order_qty=quote_order_qty,
                    reference_price=ref,
                )
                if not ok:
                    order.transition(OrderState.REJECTED, f"normalize:{err}")
                    return order
                if "quantity" in params:
                    order.quantity = params["quantity"]
                    quantity = order.quantity
                if "price" in params:
                    order.price = params["price"]
                    price = order.price
                if "quote_order_qty" in params:
                    order.quote_order_qty = params["quote_order_qty"]
                    quote_order_qty = order.quote_order_qty
            except Exception as e:
                order.transition(OrderState.REJECTED, f"normalize:{e}")
                return order

        notional = self._notional(quantity, price, quote_order_qty, reference_price)

        # 2) Atomic risk admission (authoritative reduce-only inside engine)
        risk_status: RiskStatus = self.risk.admit(
            symbol=symbol,
            side=side,
            notional=notional,
            available_balance=available_balance,
            client_id=cid,
        )
        if not risk_status.allowed:
            order.transition(OrderState.REJECTED, f"RISK:{risk_status.reason}")
            if risk_status.reservation_id:
                self.risk.release_reservation(risk_status.reservation_id, safe=True)
            return order

        reservation_id = risk_status.reservation_id

        # 3) LIVE only — single RestClient path
        if not is_production_trading_mode(self.mode):
            self.risk.release_reservation(reservation_id, safe=True)
            order.transition(OrderState.REJECTED, "NOT_LIVE")
            return order

        order.transition(OrderState.SUBMIT)
        try:
            status, body = self.rest.new_order(
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=price,
                quote_order_qty=quote_order_qty,
                client_id=cid,
            )
        except Exception as e:
            # UNKNOWN: may have been sent
            self.risk.mark_unknown_order(cid)
            self.risk.release_reservation(reservation_id, safe=False)
            order.transition(OrderState.UNKNOWN, f"submit_exc:{e}")
            return order

        order.raw_last = body if isinstance(body, dict) else {"raw": body}

        if status == OrderResultStatus.ACK:
            self.risk.commit_reservation(reservation_id)
            data = body.get("data") or body if isinstance(body, dict) else {}
            oid = data.get("orderId") or data.get("order_id")
            if oid is not None:
                try:
                    order.exchange_order_id = int(oid)
                except (TypeError, ValueError):
                    pass
            order.transition(OrderState.ACK)
            order.transition(OrderState.TRACK)
        elif status == OrderResultStatus.REJECTED:
            self.risk.release_reservation(reservation_id, safe=True)
            order.transition(OrderState.REJECTED, str(body)[:200])
        elif status == OrderResultStatus.UNKNOWN:
            self.risk.mark_unknown_order(cid)
            self.risk.release_reservation(reservation_id, safe=False)
            order.transition(OrderState.UNKNOWN, "UNKNOWN - needs reconciliation")
        else:
            self.risk.mark_unknown_order(cid)
            self.risk.release_reservation(reservation_id, safe=False)
            order.transition(OrderState.UNKNOWN, str(status))

        return order

    @staticmethod
    def _notional(
        quantity: Optional[str],
        price: Optional[str],
        quote_order_qty: Optional[str],
        reference_price: Optional[float],
    ) -> float:
        try:
            if quantity and price:
                return float(quantity) * float(price)
            if quote_order_qty:
                return float(quote_order_qty)
            if quantity and reference_price:
                return float(quantity) * float(reference_price)
        except (TypeError, ValueError):
            return float("nan")
        return 0.0

    def reconcile(self, order: Order) -> Order:
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
            order.transition(OrderState.REJECTED, "query returned reject")
        else:
            order.transition(OrderState.UNKNOWN, "reconcile still unknown")
        return order

    def _paper_fill(self, order: Order) -> Order:
        """Non-production simulated fill for unit tests only."""
        order.exchange_order_id = self._next_paper_id()
        order.transition(OrderState.ACK, "PAPER_TEST_HARNESS")
        order.filled_qty = order.quantity
        order.avg_price = order.price or "0"
        order.transition(OrderState.FILLED, "PAPER_TEST_HARNESS")
        return order

    def _next_paper_id(self) -> int:
        self._paper_id_seq += 1
        return self._paper_id_seq

    def get_active(self) -> List[Order]:
        return [o for o in self.orders.values() if not o.is_terminal()]
