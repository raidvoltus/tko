"""Single reconciliation authority for UNKNOWN/RECONCILIATION intents."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from tko.audit.audit_log import AuditLog
from tko.exchange.order_response import (
    InvalidOrderResponse,
    OrderLookupResult,
    OrderLookupStatus,
    validate_order_payload,
)
from tko.execution.intent import IntentStore, OrderIntent, OrderIntentStatus
from tko.risk.position_store import PositionStore

logger = logging.getLogger(__name__)

DEFAULT_MAX_RECON_MISSES = 5


@dataclass
class ReconcileResult:
    intents_checked: int = 0
    intents_confirmed: int = 0
    intents_governor: int = 0
    intents_still_reconciling: int = 0
    positions_adjusted: int = 0
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
        except Exception as exc:
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
                    require_client_id_match=False,
                )
            except InvalidOrderResponse as exc:
                intent.error_category = "INVALID_RESPONSE"
                intent.error_message = str(exc)[:300]
                self.intents.update(intent)
                logger.warning("event=reconcile_invalid_response cid=%s err=%s", cid, exc)
                return intent

            intent.exchange_order_id = validated.id
            intent.filled = validated.filled
            intent.average = (
                validated.average if validated.average is not None else validated.price
            )
            st = (validated.status or "").lower()
            remaining = float(validated.remaining or 0.0)
            filled_qty = float(validated.filled or 0.0)

            # S6-A: terminal exchange failure statuses — do not invent success
            terminal_fail = {
                "canceled", "cancelled", "rejected", "expired", "expired_in_match",
            }
            if st in terminal_fail and filled_qty <= 1e-12:
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

            if remaining > 1e-12 and st in (
                "open", "partial", "partially_filled", "new", "accepted", "pending",
            ):
                intent.status = OrderIntentStatus.PARTIALLY_FILLED
                intent.error_category = ""
                intent.error_message = f"partial remaining={remaining}"
            else:
                # FILLED / closed / canceled-with-fill / etc. → account filled qty once
                intent.status = OrderIntentStatus.CONFIRMED
                intent.error_category = ""
                intent.error_message = ""
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
            # S6-B: only account when there is executed quantity
            if filled_qty > 1e-12 and on_confirmed:
                try:
                    on_confirmed(intent)
                except Exception as exc:
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
            notes = self.positions.reconcile_with_balances(free_map, min_dust=self.min_dust)
            result.positions_adjusted = len(notes)
            result.notes.extend(notes)

        logger.info(
            "event=reconcile_done checked=%d confirmed=%d governor=%d still=%d",
            result.intents_checked,
            result.intents_confirmed,
            result.intents_governor,
            result.intents_still_reconciling,
        )
        return result
