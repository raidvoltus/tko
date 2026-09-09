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
from tko.reconciliation.reconciler import Reconciler
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
        lifecycle: object | None = None,
    ) -> None:
        self.client = client
        self.s = settings
        self.risk = risk
        self.audit = audit
        self.metrics = metrics
        self.lifecycle = lifecycle
        self.positions_store = positions_store or PositionStore(state_dir / "positions.json")
        self.positions: dict[str, PositionState] = {}
        self.intents = IntentStore(state_dir / "order_intents.json")
        self.reconciler = Reconciler(
            client=client,
            intents=self.intents,
            positions=self.positions_store,
            max_unknown_checks=5,
        )
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

    def _run_live_create_order(self, **kwargs):  # type: ignore[no-untyped-def]
        """Race-safe LIVE POST: MUST go through LifecycleGovernor.run_authorized_submit.

        Fail-closed: no lifecycle, or lifecycle without run_authorized_submit → no POST.
        Never: trading_authorized boolean check then direct create_order (TOCTOU bypass).
        """
        lc = self.lifecycle
        if lc is None:
            raise RuntimeError(
                "lifecycle required for LIVE create_order (fail-closed: no authorization gate)"
            )
        run = getattr(lc, "run_authorized_submit", None)
        if run is None or not callable(run):
            raise RuntimeError(
                "lifecycle.run_authorized_submit required for LIVE create_order (fail-closed)"
            )
        return run(lambda: self.client.create_order(**kwargs))

    def buy(self, symbol: str, base: str, quote: str, decision: RiskDecision, last_price: float) -> OrderResult | None:
        if not decision.approved or decision.size_quote <= 0:
            return None
        if self.client.circuit_open:
            logger.error("BUY blocked: circuit breaker open")
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
        intent = self.intents.create_if_absent(
            symbol=symbol, side="buy", quote_amount=float(quote_amt),
            last_price=last_price, reason=decision.reason, strategy="btc",
        )
        if intent is None:
            logger.warning("event=order_retry_blocked symbol=%s side=buy reason=active_intent", symbol)
            return None
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
        intent = self.intents.create_if_absent(
            symbol=symbol, side="sell", base_amount=float(raw_qty),
            last_price=last_price, reason=decision.reason, strategy="btc",
        )
        if intent is None:
            logger.warning("event=order_retry_blocked symbol=%s side=sell reason=active_intent", symbol)
            return None
        intent.normalized_base = float(norm)
        intent.status = OrderIntentStatus.NORMALIZED
        self.intents.update(intent)
        return self._submit(intent, side=Side.SELL, base_amount=float(norm), quote_amount=None, base=base or "", quote=quote or "")

    def _submit(self, intent: OrderIntent, *, side: Side, base_amount: float, quote_amount: float | None, base: str, quote: str) -> OrderResult | None:
        intent.status = OrderIntentStatus.SUBMITTING
        intent.attempts += 1
        self.intents.update(intent)
        try:
            result = self._run_live_create_order(
                symbol=intent.symbol, side=side, amount=base_amount,
                order_type=OrderType.MARKET, client_order_id=intent.client_order_id,
                quote_amount=quote_amount if side == Side.BUY else None,
            )
        except RuntimeError as exc:
            logger.error("event=lifecycle_blocks_submit cid=%s err=%s", intent.client_order_id, exc)
            intent.status = OrderIntentStatus.REJECTED
            intent.error_category = "LIFECYCLE"
            intent.error_message = str(exc)[:300]
            self.intents.update(intent)
            return None
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

    def _reconcile(self, intent: OrderIntent, *, max_misses: int | None = None) -> None:
        if max_misses is not None:
            self.reconciler.max_unknown_checks = max_misses

        def _on_confirmed(i: OrderIntent) -> None:
            side = Side.BUY if i.side == "buy" else Side.SELL
            synthetic = self._result_from_intent(i, side)
            base = i.symbol.split("/")[0] if "/" in i.symbol else ""
            quote = i.symbol.split("/")[1] if "/" in i.symbol else ""
            self._on_fill_confirmed(i, side, synthetic, base=base, quote=quote)

        self.reconciler.reconcile_intent(intent, on_confirmed=_on_confirmed)
        refreshed = self.intents.by_client_id(intent.client_order_id)
        if refreshed is not None:
            intent.status = refreshed.status
            intent.exchange_order_id = refreshed.exchange_order_id
            intent.filled = refreshed.filled
            intent.average = refreshed.average
            intent.attempts = refreshed.attempts
            intent.error_category = refreshed.error_category
            intent.error_message = refreshed.error_message

    def _result_from_intent(self, intent: OrderIntent, side: Side) -> OrderResult:
        return OrderResult(
            id=intent.exchange_order_id or "", symbol=intent.symbol, side=side,
            type=OrderType.MARKET, amount=intent.normalized_base or intent.base_amount or 0.0,
            price=intent.average, status="closed", filled=intent.filled or 0.0,
            remaining=0.0, average=intent.average, client_order_id=intent.client_order_id,
        )
