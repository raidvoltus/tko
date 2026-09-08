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
from tko.risk.engine import RiskDecision, RiskEngine
from tko.risk.position_store import PositionStore

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
    def __init__(
        self,
        client: TokocryptoClient,
        settings: Settings,
        state_dir: Path,
        risk: RiskEngine | None = None,
        positions_store: PositionStore | None = None,
        audit: object | None = None,
        metrics: object | None = None,
    ) -> None:
        self.client = client
        self.s = settings
        self.risk = risk
        self.audit = audit
        self.metrics = metrics
        self.positions_store = positions_store or PositionStore(state_dir / "positions.json")
        self.positions: dict[str, PositionState] = {}
        self.intents = IntentStore(state_dir / "order_intents.json")
        self._hydrate_positions_memory()

    def _hydrate_positions_memory(self) -> None:
        for pos in self.positions_store.all():
            self.positions[pos.symbol] = PositionState(
                symbol=pos.symbol, base=pos.base, quote=pos.quote,
                amount=pos.amount, entry_price=pos.entry_price, opened_at=pos.opened_at,
            )

    def load_entry_price(self, symbol: str) -> float | None:
        mem = self.positions.get(symbol)
        if mem and mem.entry_price > 0:
            return mem.entry_price
        stored = self.positions_store.get(symbol)
        if stored and stored.entry_price > 0:
            return stored.entry_price
        return None

    def reconcile_pending(self) -> None:
        for intent in list(self.intents.unresolved_unknown()):
            self._reconcile(intent)

    def _precheck_notional(self, notional: float, *, side: str) -> tuple[bool, str]:
        if notional <= 0:
            return False, "notional is zero"
        if self.s.max_order_notional > 0 and notional > float(self.s.max_order_notional) + 1e-9:
            return False, f"notional {notional:.4f} exceeds max_order_notional {self.s.max_order_notional:.4f}"
        if self.risk is not None and self.s.max_daily_notional > 0:
            used = self.risk.pnl.today_notional()
            if used + notional > float(self.s.max_daily_notional) + 1e-9:
                return False, (
                    f"daily notional {used:.4f}+{notional:.4f} exceeds "
                    f"max_daily_notional {self.s.max_daily_notional:.4f}"
                )
        return True, "ok"

    def buy(self, symbol: str, base: str, quote: str, decision: RiskDecision, last_price: float) -> OrderResult | None:
        if not decision.approved or decision.size_quote <= 0:
            return None
        if self.client.circuit_open:
            logger.error("BUY blocked: circuit breaker open")
            return None
        if self.intents.has_blocking_intent(symbol, "buy"):
            logger.warning("event=order_retry_blocked symbol=%s side=buy reason=active_intent", symbol)
            return None
        ok_n, n_reason = self._precheck_notional(decision.size_quote, side="buy")
        if not ok_n:
            logger.error("event=order_validation_failed symbol=%s reason=%s", symbol, n_reason)
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
        ok_n2, n_reason2 = constraints.validate_notional(quote_amt)
        if not ok_n2:
            logger.error("event=order_validation_failed symbol=%s reason=%s", symbol, n_reason2)
            return None
        intent = self.intents.create(
            symbol=symbol, side="buy", quote_amount=float(quote_amt),
            last_price=last_price, reason=decision.reason, strategy="btc",
        )
        est_base = float(quote_amt / Decimal(str(last_price))) if last_price > 0 else 0.0
        intent.status = OrderIntentStatus.NORMALIZED
        intent.normalized_base = est_base
        self.intents.update(intent)
        if self.audit is not None:
            try:
                self.audit.record(  # type: ignore[attr-defined]
                    "INTENT_CREATED", symbol=symbol, side="buy",
                    client_order_id=intent.client_order_id, reason=decision.reason,
                    quantity=float(quote_amt),
                )
            except Exception:
                pass
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
        est_notional = decision.size_base * last_price
        ok_n, n_reason = self._precheck_notional(est_notional, side="sell")
        if not ok_n and "kill" not in decision.reason.lower():
            logger.error("event=order_validation_failed symbol=%s reason=%s", symbol, n_reason)
            return None
        ok, reason = self.client.validate_symbol_ready(symbol)
        if not ok:
            logger.error("event=order_validation_failed symbol=%s reason=%s", symbol, reason)
            return None
        constraints = self.client.get_constraints(symbol)
        if constraints is None:
            return None
        raw_qty = Decimal(str(decision.size_base)) * Decimal("0.999")
        norm = constraints.normalize_quantity(raw_qty, market_order=True)
        ok_q, q_reason = constraints.validate_quantity(norm, market_order=True)
        if not ok_q:
            return None
        intent = self.intents.create(
            symbol=symbol, side="sell", base_amount=float(raw_qty),
            last_price=last_price, reason=decision.reason, strategy="btc",
        )
        intent.normalized_base = float(norm)
        intent.status = OrderIntentStatus.NORMALIZED
        self.intents.update(intent)
        return self._submit(intent, side=Side.SELL, base_amount=float(norm), quote_amount=None, base=base or "", quote=quote or "")

    def _submit(self, intent: OrderIntent, *, side: Side, base_amount: float, quote_amount: float | None, base: str, quote: str) -> OrderResult | None:
        intent.status = OrderIntentStatus.SUBMITTING
        intent.attempts += 1
        self.intents.update(intent)
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
                self._reconcile(intent)
                if intent.status == OrderIntentStatus.CONFIRMED:
                    return self._result_from_intent(intent, side)
                return None
            intent.status = OrderIntentStatus.REJECTED
            self.intents.update(intent)
            if self.metrics is not None:
                try:
                    self.metrics.record_order(success=False)  # type: ignore[attr-defined]
                except Exception:
                    pass
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
        if self.audit is not None:
            try:
                self.audit.record(  # type: ignore[attr-defined]
                    "ORDER_FILLED", symbol=intent.symbol, side=side.value,
                    client_order_id=intent.client_order_id or "",
                    exchange_order_id=result.id, quantity=result.filled, price=result.average,
                )
            except Exception:
                pass
        if self.metrics is not None:
            try:
                self.metrics.record_order(success=True)  # type: ignore[attr-defined]
            except Exception:
                pass
        self._on_fill_confirmed(intent, side, result, base=base, quote=quote)
        return result

    def _on_fill_confirmed(self, intent: OrderIntent, side: Side, result: OrderResult, *, base: str, quote: str) -> None:
        avg = result.average or intent.last_price or 0.0
        filled = result.filled or intent.normalized_base or intent.base_amount or 0.0
        if side == Side.BUY:
            notional = float(intent.quote_amount or (filled * avg))
            self.positions[intent.symbol] = PositionState(
                symbol=intent.symbol, base=base, quote=quote, amount=filled, entry_price=avg
            )
            self.positions_store.upsert(
                symbol=intent.symbol, base=base, quote=quote, amount=filled, entry_price=avg,
                order_id=result.id, client_order_id=intent.client_order_id or "",
            )
            if self.risk is not None:
                self.risk.record_fill(side="buy", symbol=intent.symbol, notional=notional, pnl=0.0,
                                      order_id=result.id, client_order_id=intent.client_order_id or "")
        else:
            entry = self.load_entry_price(intent.symbol) or avg
            notional = filled * avg
            pnl = (avg - entry) * filled if entry > 0 else 0.0
            self.positions_store.reduce_or_close(intent.symbol, filled)
            if intent.symbol in self.positions:
                left = self.positions[intent.symbol].amount - filled
                if left <= 1e-12:
                    self.positions.pop(intent.symbol, None)
                else:
                    self.positions[intent.symbol].amount = left
            if self.risk is not None:
                self.risk.record_fill(side="sell", symbol=intent.symbol, notional=notional, pnl=pnl,
                                      order_id=result.id, client_order_id=intent.client_order_id or "")

    def _reconcile(self, intent: OrderIntent, *, max_misses: int = 5) -> None:
        """Unified recon policy (Stage 3): stay blocking until FOUND or MANUAL_REVIEW.

        SUBMITTING/UNKNOWN → RECONCILIATION (blocking)
          found → CONFIRMED
          miss  → remain RECONCILIATION; after max_misses → MANUAL_REVIEW (blocking)
        Never transitions to RETRY_ELIGIBLE (no automatic re-POST).
        """
        intent.status = OrderIntentStatus.RECONCILIATION
        intent.attempts = int(intent.attempts or 0) + 1
        self.intents.update(intent)
        found = None
        try:
            found = self.client.find_order_by_client_id(intent.symbol, intent.client_order_id)
        except Exception as exc:
            logger.warning("event=reconcile_query_failed cid=%s err=%s", intent.client_order_id, exc)
        if found:
            oid = str(found.get("id") or "").strip()
            if not oid:
                logger.warning("event=reconcile_found_without_id cid=%s", intent.client_order_id)
            else:
                intent.status = OrderIntentStatus.CONFIRMED
                intent.exchange_order_id = oid
                try:
                    intent.filled = float(found.get("filled") or 0)
                except (TypeError, ValueError):
                    intent.filled = 0.0
                avg = found.get("average") or found.get("price")
                try:
                    intent.average = float(avg) if avg is not None else None
                except (TypeError, ValueError):
                    intent.average = None
                self.intents.update(intent)
                side = Side.BUY if intent.side == "buy" else Side.SELL
                synthetic = self._result_from_intent(intent, side)
                base = intent.symbol.split("/")[0] if "/" in intent.symbol else ""
                quote = intent.symbol.split("/")[1] if "/" in intent.symbol else ""
                self._on_fill_confirmed(intent, side, synthetic, base=base, quote=quote)
                return
        if intent.attempts >= max_misses:
            intent.status = OrderIntentStatus.MANUAL_REVIEW
            logger.critical(
                "event=order_manual_review cid=%s attempts=%d — operator action required",
                intent.client_order_id,
                intent.attempts,
            )
        else:
            intent.status = OrderIntentStatus.RECONCILIATION
        self.intents.update(intent)

    def _result_from_intent(self, intent: OrderIntent, side: Side) -> OrderResult:
        return OrderResult(
            id=intent.exchange_order_id or "", symbol=intent.symbol, side=side,
            type=OrderType.MARKET, amount=intent.normalized_base or intent.base_amount or 0.0,
            price=intent.average, status="closed", filled=intent.filled or 0.0,
            remaining=0.0, average=intent.average, client_order_id=intent.client_order_id,
        )
