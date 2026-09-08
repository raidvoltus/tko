"""LIVE order execution with intent, normalization, reconciliation. No dry-run."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from tko.core.config import Settings
from tko.core.types import OrderResult, OrderType, Side
from tko.exchange.tokocrypto import TokocryptoClient, TokocryptoError
from tko.execution.errors import ErrorCategory, is_ambiguous
from tko.execution.intent import IntentStore, OrderIntent, OrderIntentStatus
from tko.risk.engine import RiskDecision

logger = logging.getLogger(__name__)


@dataclass
class PositionState:
    symbol: str
    base: str
    quote: str
    amount: float
    entry_price: float
    opened_at: float = field(default_factory=time.time)


class ExecutionEngine:
    def __init__(self, client: TokocryptoClient, settings: Settings, state_dir: Path) -> None:
        self.client = client
        self.s = settings
        self.positions: dict[str, PositionState] = {}
        self.intents = IntentStore(state_dir / "order_intents.json")

    def reconcile_pending(self) -> None:
        for intent in list(self.intents.unresolved_unknown()):
            self._reconcile(intent)

    def buy(self, symbol: str, base: str, quote: str, decision: RiskDecision, last_price: float) -> OrderResult | None:
        if not decision.approved or decision.size_quote <= 0:
            return None
        if self.client.circuit_open:
            logger.error("BUY blocked: circuit breaker open")
            return None
        if self.intents.has_blocking_intent(symbol, "buy"):
            logger.warning("event=order_retry_blocked symbol=%s side=buy reason=active_intent", symbol)
            return None
        ok, reason = self.client.validate_symbol_ready(symbol)
        if not ok:
            logger.error("event=order_validation_failed symbol=%s reason=%s", symbol, reason)
            return None
        constraints = self.client.get_constraints(symbol)
        if constraints is None:
            logger.error("event=order_validation_failed symbol=%s reason=no_constraints", symbol)
            return None
        quote_amt = Decimal(str(decision.size_quote))
        ok_n, n_reason = constraints.validate_notional(quote_amt)
        if not ok_n:
            logger.error("event=order_validation_failed symbol=%s reason=%s", symbol, n_reason)
            return None
        intent = self.intents.create(
            symbol=symbol, side="buy", quote_amount=float(quote_amt),
            last_price=last_price, reason=decision.reason, strategy="btc",
        )
        est_base = float(quote_amt / Decimal(str(last_price))) if last_price > 0 else 0.0
        intent.status = OrderIntentStatus.NORMALIZED
        intent.normalized_base = est_base
        self.intents.update(intent)
        logger.info("event=order_normalized client_order_id=%s side=buy quote_amount=%s", intent.client_order_id, quote_amt)
        return self._submit(intent, side=Side.BUY, base_amount=0.0, quote_amount=float(quote_amt), base=base, quote=quote)

    def sell(self, symbol: str, decision: RiskDecision, last_price: float, *, base: str | None = None, quote: str | None = None) -> OrderResult | None:
        if not decision.approved or decision.size_base <= 0:
            return None
        if self.client.circuit_open:
            logger.error("SELL blocked: circuit breaker open")
            return None
        if self.intents.has_blocking_intent(symbol, "sell"):
            logger.warning("event=order_retry_blocked symbol=%s side=sell reason=active_intent", symbol)
            return None
        ok, reason = self.client.validate_symbol_ready(symbol)
        if not ok:
            logger.error("event=order_validation_failed symbol=%s reason=%s", symbol, reason)
            return None
        constraints = self.client.get_constraints(symbol)
        if constraints is None:
            logger.error("event=order_validation_failed symbol=%s reason=no_constraints", symbol)
            return None
        raw_qty = Decimal(str(decision.size_base)) * Decimal("0.999")
        norm = constraints.normalize_quantity(raw_qty, market_order=True)
        ok_q, q_reason = constraints.validate_quantity(norm, market_order=True)
        if not ok_q:
            logger.error("event=order_validation_failed symbol=%s reason=%s", symbol, q_reason)
            return None
        est_notional = norm * Decimal(str(last_price))
        ok_n, n_reason = constraints.validate_notional(est_notional)
        if not ok_n:
            logger.error("event=order_validation_failed symbol=%s reason=%s", symbol, n_reason)
            return None
        intent = self.intents.create(
            symbol=symbol, side="sell", base_amount=float(raw_qty),
            last_price=last_price, reason=decision.reason, strategy="btc",
        )
        intent.normalized_base = float(norm)
        intent.status = OrderIntentStatus.NORMALIZED
        self.intents.update(intent)
        logger.info("event=order_normalized client_order_id=%s side=sell qty=%s", intent.client_order_id, norm)
        return self._submit(intent, side=Side.SELL, base_amount=float(norm), quote_amount=None, base=base or "", quote=quote or "")

    def _submit(self, intent: OrderIntent, *, side: Side, base_amount: float, quote_amount: float | None, base: str, quote: str) -> OrderResult | None:
        intent.status = OrderIntentStatus.SUBMITTING
        intent.attempts += 1
        self.intents.update(intent)
        logger.info("event=order_submitting client_order_id=%s symbol=%s side=%s attempt=%d mode=LIVE", intent.client_order_id, intent.symbol, side.value, intent.attempts)
        try:
            result = self.client.create_order(
                symbol=intent.symbol, side=side, amount=base_amount,
                order_type=OrderType.MARKET, client_order_id=intent.client_order_id,
                quote_amount=quote_amount if side == Side.BUY else None,
            )
        except TokocryptoError as exc:
            intent.error_category = exc.category.value
            intent.error_message = str(exc)[:300]
            if exc.ambiguous or is_ambiguous(exc.category):
                intent.status = OrderIntentStatus.UNKNOWN
                self.intents.update(intent)
                logger.error("event=order_submission_unknown client_order_id=%s reason=%s", intent.client_order_id, exc.category.value)
                self._reconcile(intent)
                if intent.status == OrderIntentStatus.CONFIRMED:
                    return self._result_from_intent(intent, side)
                return None
            intent.status = OrderIntentStatus.REJECTED
            self.intents.update(intent)
            return None
        except Exception as exc:
            intent.status = OrderIntentStatus.UNKNOWN
            intent.error_category = ErrorCategory.UNKNOWN_ERROR.value
            intent.error_message = str(exc)[:300]
            self.intents.update(intent)
            self._reconcile(intent)
            if intent.status == OrderIntentStatus.CONFIRMED:
                return self._result_from_intent(intent, side)
            return None
        intent.status = OrderIntentStatus.CONFIRMED
        intent.exchange_order_id = result.id
        intent.filled = result.filled
        intent.average = result.average
        self.intents.update(intent)
        logger.info("event=order_submission_success client_order_id=%s exchange_order_id=%s", intent.client_order_id, result.id)
        if side == Side.BUY:
            filled = result.filled or intent.normalized_base or 0.0
            avg = result.average or intent.last_price or 0.0
            self.positions[intent.symbol] = PositionState(symbol=intent.symbol, base=base, quote=quote, amount=filled, entry_price=avg)
        else:
            self.positions.pop(intent.symbol, None)
        return result

    def _reconcile(self, intent: OrderIntent) -> None:
        intent.status = OrderIntentStatus.RECONCILIATION
        self.intents.update(intent)
        logger.info("event=order_reconciliation_started client_order_id=%s", intent.client_order_id)
        found = self.client.find_order_by_client_id(intent.symbol, intent.client_order_id)
        if found:
            intent.status = OrderIntentStatus.CONFIRMED
            intent.exchange_order_id = str(found.get("id") or "")
            intent.filled = float(found.get("filled") or 0)
            avg = found.get("average") or found.get("price")
            intent.average = float(avg) if avg is not None else None
            self.intents.update(intent)
            logger.info("event=order_reconciled client_order_id=%s exchange_order_id=%s final_status=CONFIRMED", intent.client_order_id, intent.exchange_order_id)
            return
        intent.status = OrderIntentStatus.RETRY_ELIGIBLE
        self.intents.update(intent)
        logger.info("event=order_reconciliation_not_found client_order_id=%s final_status=RETRY_ELIGIBLE", intent.client_order_id)

    def _result_from_intent(self, intent: OrderIntent, side: Side) -> OrderResult:
        return OrderResult(
            id=intent.exchange_order_id or "", symbol=intent.symbol, side=side,
            type=OrderType.MARKET, amount=intent.normalized_base or intent.base_amount or 0.0,
            price=intent.average, status="closed", filled=intent.filled or 0.0,
            remaining=0.0, average=intent.average, client_order_id=intent.client_order_id,
        )
