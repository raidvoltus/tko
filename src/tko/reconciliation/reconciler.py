"""Single reconciliation authority for UNKNOWN/RECONCILIATION intents."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from tko.audit.audit_log import AuditLog
from tko.exchange.order_response import (
    InvalidOrderResponse,
    OrderLookupResult,
    OrderLookupStatus,
    validate_order_payload,
)
from tko.execution.intent import IntentStore, OrderIntent, OrderIntentStatus
from tko.risk.position_store import PositionDiscrepancy, PositionStore

logger = logging.getLogger(__name__)

DEFAULT_MAX_RECON_MISSES = 5


@dataclass
class ReconcileResult:
    intents_checked: int = 0
    intents_confirmed: int = 0
    intents_governor: int = 0
    intents_still_reconciling: int = 0
    positions_adjusted: int = 0
    position_discrepancies: list[PositionDiscrepancy] = field(default_factory=list)
    safe_to_trade: bool = True
    notes: list[str] = field(default_factory=list)


class Reconciler:
    """Sole lifecycle authority for ambiguous order recovery (INV-16)."""

    def __init__(
        self,
        *,
        client: Any,
        intents: IntentStore,
        positions: PositionStore | None = None,
        audit: AuditLog | None = None,
        max_unknown_checks: int = DEFAULT_MAX_RECON_MISSES,
        min_dust: float = 1e-8,
    ) -> None:
        self.client = client
        self.intents = intents
        self.positions = positions
        self.audit = audit
        self.max_unknown_checks = max(1, int(max_unknown_checks))
        self.min_dust = min_dust

    def reconcile_intent(
        self,
        intent: OrderIntent,
        *,
        on_confirmed: Callable[[OrderIntent], None] | None = None,
    ) -> OrderIntent:
        intent.status = OrderIntentStatus.RECONCILIATION
        self.intents.update(intent)

        cid = intent.client_order_id or ""
        try:
            lookup = self.client.find_order_by_client_id(intent.symbol, cid)
        except Exception as exc:  # noqa: BLE001
            intent.error_category = "RECON_QUERY_FAILED"
            intent.error_message = str(exc)[:300]
            self.intents.update(intent)
            logger.warning("event=reconcile_query_failed cid=%s err=%s", cid, exc)
            return intent

        if not isinstance(lookup, OrderLookupResult):
            if lookup is None:
                lookup = OrderLookupResult.not_found()
            elif isinstance(lookup, dict):
                lookup = OrderLookupResult.found(lookup)
            else:
                intent.error_category = "RECON_QUERY_FAILED"
                intent.error_message = f"unexpected lookup type: {type(lookup).__name__}"
                self.intents.update(intent)
                return intent

        if lookup.status == OrderLookupStatus.QUERY_FAILED:
            intent.error_category = "RECON_QUERY_FAILED"
            intent.error_message = (lookup.error or "query failed")[:300]
            self.intents.update(intent)
            logger.warning("event=reconcile_query_failed cid=%s err=%s", cid, lookup.error)
            return intent

        if lookup.status == OrderLookupStatus.FOUND and lookup.order is not None:
            try:
                validated = validate_order_payload(
                    lookup.order,
                    expected_client_order_id=cid,
                    require_client_id_match=True,
                )
            except InvalidOrderResponse as exc:
                intent.error_category = "INVALID_RESPONSE"
                intent.error_message = str(exc)[:300]
                self.intents.update(intent)
                logger.warning("event=reconcile_invalid_response cid=%s err=%s", cid, exc)
                return intent

            intent.exchange_order_id = validated.id
            # Monotonic fill watermark: never decrease on out-of-order exchange snapshots
            incoming_filled = float(validated.filled or 0.0)
            prior_filled = float(intent.filled or 0.0)
            prior_accounted = float(getattr(intent, "accounted_filled", 0.0) or 0.0)
            filled_qty = max(incoming_filled, prior_filled, prior_accounted)
            intent.filled = filled_qty
            intent.average = (
                validated.average if validated.average is not None else validated.price
            )
            st = (validated.status or "").lower()
            remaining = float(validated.remaining or 0.0)
            # If exchange reports lower remaining after we already saw higher fill,
            # recompute remaining from amount when available.
            try:
                amount = float(validated.amount or 0.0)
                if amount > 0 and filled_qty > incoming_filled:
                    remaining = max(0.0, amount - filled_qty)
            except (TypeError, ValueError):
                pass

            TERMINAL_FAIL = frozenset({
                "canceled", "cancelled", "rejected", "expired", "expired_in_match",
            })
            OPEN_PARTIAL = frozenset({
                "open", "partial", "partially_filled", "new", "accepted", "pending",
            })
            FILLED_OK = frozenset({
                "closed", "filled", "done", "complete", "completed",
            })

            if not st:
                intent.status = OrderIntentStatus.UNKNOWN
                intent.error_category = "UNKNOWN_STATUS"
                intent.error_message = "exchange status missing/empty — reconciliation required"
                self.intents.update(intent)
                logger.warning("event=reconcile_unknown_status cid=%s status=<empty>", cid)
                return intent

            if st in TERMINAL_FAIL and filled_qty <= 1e-12:
                intent.status = OrderIntentStatus.REJECTED
                intent.error_category = f"EXCHANGE_{st.upper()}"
                intent.error_message = f"exchange status={st}"
                self.intents.update(intent)
                if self.audit:
                    self.audit.record(
                        "RECONCILE_RESULT",
                        symbol=intent.symbol,
                        side=intent.side,
                        client_order_id=cid,
                        exchange_order_id=intent.exchange_order_id,
                        reason=f"rejected_{st}",
                        quantity=0.0,
                    )
                logger.info(
                    "event=reconcile_exchange_rejected cid=%s status=%s", cid, st
                )
                return intent

            if st in OPEN_PARTIAL:
                intent.status = OrderIntentStatus.PARTIALLY_FILLED
                intent.error_category = ""
                intent.error_message = f"partial remaining={remaining}"
            elif st in FILLED_OK or (st in TERMINAL_FAIL and filled_qty > 1e-12):
                intent.status = OrderIntentStatus.CONFIRMED
                intent.error_category = ""
                intent.error_message = ""
            else:
                intent.status = OrderIntentStatus.UNKNOWN
                intent.error_category = "UNKNOWN_STATUS"
                intent.error_message = f"unmapped exchange status={st!r} — reconciliation required"
                self.intents.update(intent)
                logger.warning(
                    "event=reconcile_unknown_status cid=%s status=%r filled=%.8f",
                    cid, st, filled_qty,
                )
                return intent
            self.intents.update(intent)
            if self.audit:
                self.audit.record(
                    "RECONCILE_RESULT",
                    symbol=intent.symbol,
                    side=intent.side,
                    client_order_id=cid,
                    exchange_order_id=intent.exchange_order_id,
                    reason=(
                        "confirmed"
                        if intent.status == OrderIntentStatus.CONFIRMED
                        else "partial"
                    ),
                    quantity=intent.filled,
                    price=intent.average,
                )
            if filled_qty > 1e-12 and on_confirmed:
                try:
                    on_confirmed(intent)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("on_confirmed hook failed: %s", exc)
            return intent

        if lookup.status == OrderLookupStatus.NOT_FOUND:
            intent.attempts = int(intent.attempts or 0) + 1
            if intent.attempts >= self.max_unknown_checks:
                intent.status = OrderIntentStatus.GOVERNOR_AUTONOMOUS
                intent.error_category = "ORDER_NOT_FOUND"
                intent.error_message = (
                    f"not found after {intent.attempts} successful lookups; "
                    "duplicate-blocked until account-level resolution"
                )
                self.intents.update(intent)
                logger.critical(
                    "event=order_governor_autonomous cid=%s attempts=%d "
                    "invariant=blocks_duplicate_until_resolved",
                    cid,
                    intent.attempts,
                )
                if self.audit:
                    self.audit.record(
                        "RECONCILE_RESULT",
                        symbol=intent.symbol,
                        side=intent.side,
                        client_order_id=cid,
                        reason=f"governor_after_{intent.attempts}",
                    )
                return intent

            intent.status = OrderIntentStatus.RECONCILIATION
            self.intents.update(intent)
        return intent

    def reconcile_all(
        self,
        free_map: dict[str, float] | None = None,
        *,
        on_confirmed: Callable[[OrderIntent], None] | None = None,
    ) -> ReconcileResult:
        result = ReconcileResult()
        pending_fn = getattr(self.intents, "unresolved_for_recovery", None)
        pending = list(pending_fn() if callable(pending_fn) else self.intents.unresolved_unknown())
        for intent in pending:
            if intent.status == OrderIntentStatus.SUBMITTING:
                intent.status = OrderIntentStatus.UNKNOWN
                intent.error_category = intent.error_category or "RECOVERY"
                intent.error_message = (
                    intent.error_message or "promoted from SUBMITTING on recovery"
                )
                self.intents.update(intent)
            result.intents_checked += 1
            updated = self.reconcile_intent(intent, on_confirmed=on_confirmed)
            if updated.status == OrderIntentStatus.CONFIRMED:
                result.intents_confirmed += 1
                result.notes.append(f"confirmed {updated.client_order_id}")
            elif updated.status == OrderIntentStatus.PARTIALLY_FILLED:
                result.intents_still_reconciling += 1
                result.notes.append(f"partial {updated.client_order_id}")
            elif updated.status == OrderIntentStatus.GOVERNOR_AUTONOMOUS:
                result.intents_governor += 1
                result.notes.append(f"governor {updated.client_order_id}")
            else:
                result.intents_still_reconciling += 1
                result.notes.append(f"reconciling {updated.client_order_id}")

        if free_map is not None and self.positions is not None:
            disc = self.positions.detect_discrepancies(free_map, min_dust=self.min_dust)
            result.position_discrepancies = list(disc)
            if disc:
                result.safe_to_trade = False
                for d in disc:
                    result.notes.append(d.note)
                    logger.critical(
                        "event=position_discrepancy symbol=%s store=%.8f free=%.8f",
                        d.symbol, d.store_amount, d.exchange_free,
                    )
            notes = self.positions.reconcile_with_balances(free_map, min_dust=self.min_dust)
            result.positions_adjusted = len(notes)
            result.notes.extend(notes)
            residual = self.positions.detect_discrepancies(free_map, min_dust=self.min_dust)
            if residual:
                result.safe_to_trade = False
                result.position_discrepancies = list(residual)
            elif disc:
                result.safe_to_trade = True
                result.notes.append("position_store_aligned_to_exchange")

        if result.intents_governor > 0:
            result.notes.append(f"governor_blocked_intents={result.intents_governor}")

        logger.info(
            "event=reconcile_done checked=%d confirmed=%d governor=%d still=%d "
            "safe_to_trade=%s discrepancies=%d",
            result.intents_checked,
            result.intents_confirmed,
            result.intents_governor,
            result.intents_still_reconciling,
            result.safe_to_trade,
            len(result.position_discrepancies),
        )
        return result
