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
from tko.execution.fill_journal import FillEvent, FillJournal, make_event_id
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
        self.fill_journal = FillJournal(state_dir / "fill_events.jsonl")
        self.reconciler = Reconciler(
            client=client,
            intents=self.intents,
            positions=self.positions_store,
            max_unknown_checks=5,
        )
        self._hydrate_positions_memory()
        # S5-Recovery: rebuild in-memory reservations from durable BUY intents
        if self.risk is not None:
            try:
                holding = self.intents.buy_intents_holding_budget()
                n = self.risk.rehydrate_reservations_from_intents(holding)
                if n:
                    logger.info("event=startup_reservation_rehydrate count=%d", n)
            except Exception as exc:
                logger.warning("reservation rehydrate failed: %s", exc)
        # S5-B5: replay any journaled fills not yet applied (crash recovery)
        try:
            self._replay_unapplied_fills()
        except Exception as exc:
            logger.warning("fill journal replay failed: %s", exc)

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
        """Reconcile UNKNOWN/RECON/PARTIAL and crash-orphaned SUBMITTING intents."""
        pending = list(self.intents.unresolved_for_recovery())
        for intent in pending:
            # Promote SUBMITTING → UNKNOWN so recon authority owns the lifecycle
            if intent.status == OrderIntentStatus.SUBMITTING:
                intent.status = OrderIntentStatus.UNKNOWN
                intent.error_category = intent.error_category or "RECOVERY"
                intent.error_message = (
                    intent.error_message or "promoted from SUBMITTING on recovery"
                )
                self.intents.update(intent)
                logger.warning(
                    "event=submitting_promoted_to_unknown cid=%s",
                    intent.client_order_id,
                )
            self._reconcile(intent)

    def _precheck_notional(self, notional: float, *, side: str) -> tuple[bool, str]:
        if notional <= 0:
            return False, "notional is zero"
        if self.s.max_order_notional > 0 and notional > float(self.s.max_order_notional) + 1e-9:
            return False, f"notional {notional:.4f} exceeds max_order_notional {self.s.max_order_notional:.4f}"
        if self.risk is not None and self.s.max_daily_notional > 0:
            # S5-B1: count outstanding reservations so concurrent paths cannot overshoot
            used = self.risk.effective_daily_used()
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
        # S5-B1: atomic reserve BEFORE SUBMITTING / LIVE POST
        if self.risk is not None and float(quote_amt) > 0:
            ok_r, r_reason = self.risk.try_reserve_notional(
                float(quote_amt), reservation_id=intent.client_order_id
            )
            if not ok_r:
                intent.status = OrderIntentStatus.REJECTED
                intent.error_category = "RISK_RESERVE"
                intent.error_message = r_reason[:300]
                self.intents.update(intent)
                logger.error("event=notional_reserve_failed symbol=%s reason=%s", symbol, r_reason)
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
            if self.risk is not None:
                self.risk.release_reservation(intent.client_order_id)
            return None
        except TokocryptoError as exc:
            intent.error_category = exc.category.value
            intent.error_message = str(exc)[:300]
            if exc.ambiguous or is_ambiguous(exc.category):
                # UNKNOWN: KEEP reservation (order may exist on exchange)
                intent.status = OrderIntentStatus.UNKNOWN
                self.intents.update(intent)
                self._reconcile(intent)
                if intent.status == OrderIntentStatus.CONFIRMED:
                    return self._result_from_intent(intent, side)
                return None
            # Definitive reject: RELEASE reservation
            intent.status = OrderIntentStatus.REJECTED
            self.intents.update(intent)
            if self.risk is not None:
                self.risk.release_reservation(intent.client_order_id)
            if self.metrics is not None:
                try:
                    self.metrics.record_order(success=False)  # type: ignore[attr-defined]
                except Exception:
                    pass
            return None
        except Exception as exc:
            # Ambiguous exception: KEEP reservation, go UNKNOWN
            intent.status = OrderIntentStatus.UNKNOWN
            intent.error_category = ErrorCategory.UNKNOWN_ERROR.value
            intent.error_message = str(exc)[:300]
            self.intents.update(intent)
            self._reconcile(intent)
            if intent.status == OrderIntentStatus.CONFIRMED:
                return self._result_from_intent(intent, side)
            return None
        # S5-W2: distinguish full fill vs partial / still-open
        intent.exchange_order_id = result.id
        intent.filled = float(result.filled or 0.0)
        intent.average = result.average
        st = (result.status or "").lower()
        remaining = float(result.remaining or 0.0)
        is_partial = remaining > 1e-12 and st in (
            "open", "partial", "partially_filled", "new", "accepted", "pending",
        )
        if is_partial:
            intent.status = OrderIntentStatus.PARTIALLY_FILLED
            self.intents.update(intent)
            logger.warning(
                "event=partial_fill cid=%s filled=%.8f remaining=%.8f status=%s",
                intent.client_order_id, intent.filled, remaining, result.status,
            )
            if self.audit is not None:
                try:
                    self.audit.record(  # type: ignore[attr-defined]
                        "ORDER_PARTIAL", symbol=intent.symbol, side=side.value,
                        client_order_id=intent.client_order_id or "",
                        exchange_order_id=result.id, quantity=result.filled,
                        price=result.average, reason=f"remaining={remaining}",
                    )
                except Exception:
                    pass
            self._on_fill_confirmed(
                intent, side, result, base=base, quote=quote, partial=True, remaining=remaining
            )
            return result

        intent.status = OrderIntentStatus.CONFIRMED
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
        self._on_fill_confirmed(intent, side, result, base=base, quote=quote, partial=False)
        return result

    def _on_fill_confirmed(
        self,
        intent: OrderIntent,
        side: Side,
        result: OrderResult,
        *,
        base: str,
        quote: str,
        partial: bool = False,
        remaining: float = 0.0,
    ) -> None:
        """Exactly-once fill accounting via durable fill journal barrier (S5-B5).

        Order of operations:
          1. compute delta from cumulative vs accounted_filled
          2. try_record into fill journal (fsync) — first durable barrier
          3. apply position / watermark / PnL / reservation
          4. mark_applied in journal

        Crash after (2) and before (4): startup replays unapplied events.
        Crash before (2): no durable trace → safe to recompute delta from exchange.
        """
        avg = result.average or intent.last_price or 0.0
        cumulative = float(result.filled or intent.normalized_base or intent.base_amount or 0.0)
        previously = float(getattr(intent, "accounted_filled", 0.0) or 0.0)
        delta = max(0.0, cumulative - previously)

        if delta <= 1e-12:
            if not partial and self.risk is not None and side == Side.BUY:
                self.risk.release_reservation(intent.client_order_id or "")
            logger.info(
                "event=fill_noop_already_accounted cid=%s cum=%.8f accounted=%.8f",
                intent.client_order_id, cumulative, previously,
            )
            return

        event_id = make_event_id(intent.client_order_id or "", cumulative)
        notional = float(delta * avg) if avg > 0 else 0.0
        event = FillEvent(
            event_id=event_id,
            client_order_id=intent.client_order_id or "",
            symbol=intent.symbol,
            side=side.value,
            delta=delta,
            cumulative=cumulative,
            average=avg,
            notional=notional,
            order_id=result.id or "",
            partial=partial,
            remaining=float(remaining or 0.0),
            quote_amount=float(intent.quote_amount or 0.0),
        )

        # Durable barrier FIRST — if already journaled, only ensure applied
        is_new = self.fill_journal.try_record(event)
        if not is_new:
            if not self.fill_journal.is_applied(event_id):
                self._apply_fill_event(event, intent=intent, base=base, quote=quote)
            else:
                # Fully done previously; sync watermark if lagging
                if previously < cumulative:
                    intent.accounted_filled = cumulative
                    intent.filled = cumulative
                    self.intents.update(intent)
            return

        self._apply_fill_event(event, intent=intent, base=base, quote=quote)

    def _apply_fill_event(
        self,
        event: FillEvent,
        *,
        intent: OrderIntent | None = None,
        base: str = "",
        quote: str = "",
    ) -> None:
        """Idempotent side-effect application for a journaled fill event."""
        if self.fill_journal.is_applied(event.event_id):
            return

        if intent is None:
            intent = self.intents.by_client_id(event.client_order_id)
        if "/" in event.symbol and (not base or not quote):
            parts = event.symbol.split("/", 1)
            base = base or parts[0]
            quote = quote or parts[1]

        side = Side.BUY if event.side == "buy" else Side.SELL
        delta = float(event.delta)
        avg = float(event.average)
        cumulative = float(event.cumulative)

        if side == Side.BUY and delta > 1e-12:
            existing = self.positions.get(event.symbol)
            if existing and existing.amount > 0:
                total = existing.amount + delta
                wavg = (
                    (existing.entry_price * existing.amount) + (avg * delta)
                ) / total if total > 0 else avg
                self.positions[event.symbol] = PositionState(
                    symbol=event.symbol, base=base, quote=quote,
                    amount=total, entry_price=wavg, opened_at=existing.opened_at,
                )
            else:
                self.positions[event.symbol] = PositionState(
                    symbol=event.symbol, base=base, quote=quote,
                    amount=delta, entry_price=avg,
                )
            self.positions_store.upsert(
                symbol=event.symbol, base=base, quote=quote,
                amount=delta, entry_price=avg,
                order_id=event.order_id, client_order_id=event.client_order_id,
                fill_event_id=event.event_id,
            )

            if intent is not None:
                intent.accounted_filled = max(
                    float(getattr(intent, "accounted_filled", 0.0) or 0.0), cumulative
                )
                intent.filled = max(float(intent.filled or 0.0), cumulative)
                if avg > 0:
                    intent.average = avg
                self.intents.update(intent)

            if self.risk is not None:
                if event.partial:
                    quote_total = float(event.quote_amount or 0.0)
                    accounted_notional = float(cumulative * avg) if avg > 0 else 0.0
                    residual = max(0.0, quote_total - accounted_notional)
                    if residual <= 0 and event.remaining > 0 and avg > 0:
                        residual = event.remaining * avg
                    self.risk.commit_partial_and_rereserve(
                        event.client_order_id,
                        side="buy",
                        symbol=event.symbol,
                        filled_notional=float(event.notional),
                        remaining_reserve=residual,
                        pnl=0.0,
                        order_id=event.order_id,
                        client_order_id=event.client_order_id,
                        fill_event_id=event.event_id,
                    )
                else:
                    self.risk.commit_reservation(
                        event.client_order_id,
                        side="buy",
                        symbol=event.symbol,
                        actual_notional=float(event.notional),
                        pnl=0.0,
                        order_id=event.order_id,
                        client_order_id=event.client_order_id,
                        fill_event_id=event.event_id,
                    )
        elif side == Side.SELL and delta > 1e-12:
            entry = self.load_entry_price(event.symbol) or avg
            notional = delta * avg
            pnl = (avg - entry) * delta if entry > 0 else 0.0
            self.positions_store.reduce_or_close(event.symbol, delta)
            if event.symbol in self.positions:
                left = self.positions[event.symbol].amount - delta
                if left <= 1e-12:
                    self.positions.pop(event.symbol, None)
                else:
                    self.positions[event.symbol].amount = left
            if intent is not None:
                intent.accounted_filled = max(
                    float(getattr(intent, "accounted_filled", 0.0) or 0.0), cumulative
                )
                intent.filled = max(float(intent.filled or 0.0), cumulative)
                self.intents.update(intent)
            if self.risk is not None:
                self.risk.record_fill(
                    side="sell",
                    symbol=event.symbol,
                    notional=notional,
                    pnl=pnl,
                    order_id=event.order_id,
                    client_order_id=event.client_order_id,
                    fill_event_id=event.event_id,
                )

        self.fill_journal.mark_applied(event.event_id)
        logger.info(
            "event=fill_applied id=%s delta=%.8f cum=%.8f partial=%s",
            event.event_id, delta, cumulative, event.partial,
        )

    def _replay_unapplied_fills(self) -> None:
        """Crash recovery: apply any journaled fill events not yet marked applied."""
        pending = self.fill_journal.unapplied_events()
        if not pending:
            return
        logger.warning("event=fill_journal_replay count=%d", len(pending))
        for event in pending:
            intent = self.intents.by_client_id(event.client_order_id)
            self._apply_fill_event(event, intent=intent)

    def _reconcile(self, intent: OrderIntent, *, max_misses: int | None = None) -> None:
        if max_misses is not None:
            self.reconciler.max_unknown_checks = max_misses

        def _on_confirmed(i: OrderIntent) -> None:
            side = Side.BUY if i.side == "buy" else Side.SELL
            synthetic = self._result_from_intent(i, side)
            base = i.symbol.split("/")[0] if "/" in i.symbol else ""
            quote = i.symbol.split("/")[1] if "/" in i.symbol else ""
            is_partial = i.status == OrderIntentStatus.PARTIALLY_FILLED
            rem = float(synthetic.remaining or 0.0)
            self._on_fill_confirmed(
                i, side, synthetic, base=base, quote=quote,
                partial=is_partial, remaining=rem,
            )

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
        """Build OrderResult from intent without inventing a full-fill when partial."""
        amount = float(intent.normalized_base or intent.base_amount or 0.0)
        filled = float(intent.filled or 0.0)
        if intent.status == OrderIntentStatus.PARTIALLY_FILLED:
            remaining = max(0.0, amount - filled) if amount > 0 else 0.0
            status = "open"
        elif intent.status == OrderIntentStatus.CONFIRMED:
            remaining = 0.0
            status = "closed"
        else:
            remaining = max(0.0, amount - filled) if amount > 0 else 0.0
            status = "unknown"
        return OrderResult(
            id=intent.exchange_order_id or "",
            symbol=intent.symbol,
            side=side,
            type=OrderType.MARKET,
            amount=amount,
            price=intent.average,
            status=status,
            filled=filled,
            remaining=remaining,
            average=intent.average,
            client_order_id=intent.client_order_id,
        )
