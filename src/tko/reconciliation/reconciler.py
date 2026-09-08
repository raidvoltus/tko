"""Reconcile UNKNOWN intents and position store vs exchange balances."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from tko.audit.audit_log import AuditLog
from tko.execution.intent import IntentStore, OrderIntent, OrderIntentStatus
from tko.risk.position_store import PositionStore

logger = logging.getLogger(__name__)


@dataclass
class ReconcileResult:
    intents_checked: int = 0
    intents_confirmed: int = 0
    intents_manual_review: int = 0
    positions_adjusted: int = 0
    notes: list[str] = field(default_factory=list)


class Reconciler:
    def __init__(
        self,
        *,
        client: Any,
        intents: IntentStore,
        positions: PositionStore,
        audit: AuditLog | None = None,
        max_unknown_checks: int = 5,
        min_dust: float = 1e-8,
    ) -> None:
        self.client = client
        self.intents = intents
        self.positions = positions
        self.audit = audit
        self.max_unknown_checks = max_unknown_checks
        self.min_dust = min_dust
        self._unknown_hits: dict[str, int] = {}

    def reconcile_all(
        self,
        free_map: dict[str, float] | None = None,
        *,
        on_confirmed: Callable[[OrderIntent], None] | None = None,
    ) -> ReconcileResult:
        result = ReconcileResult()
        for intent in list(self.intents.unresolved_unknown()):
            result.intents_checked += 1
            cid = intent.client_order_id or ""
            found = None
            try:
                found = self.client.find_order_by_client_id(intent.symbol, cid)
            except Exception as exc:
                logger.warning("reconcile find_order failed %s: %s", cid, exc)
            if found:
                intent.status = OrderIntentStatus.CONFIRMED
                intent.exchange_order_id = str(found.get("id") or "")
                intent.filled = float(found.get("filled") or 0)
                avg = found.get("average") or found.get("price")
                intent.average = float(avg) if avg is not None else None
                self.intents.update(intent)
                result.intents_confirmed += 1
                result.notes.append(f"confirmed {cid}")
                self._unknown_hits.pop(cid, None)
                if self.audit:
                    self.audit.record(
                        "RECONCILE_RESULT",
                        symbol=intent.symbol,
                        side=intent.side,
                        client_order_id=cid,
                        exchange_order_id=intent.exchange_order_id,
                        reason="confirmed",
                        quantity=intent.filled,
                        price=intent.average,
                    )
                if on_confirmed:
                    try:
                        on_confirmed(intent)
                    except Exception as exc:
                        logger.warning("on_confirmed hook failed: %s", exc)
                continue

            hits = self._unknown_hits.get(cid, 0) + 1
            self._unknown_hits[cid] = hits
            if hits >= self.max_unknown_checks:
                try:
                    intent.status = OrderIntentStatus.MANUAL_REVIEW
                except Exception:
                    intent.status = OrderIntentStatus.RETRY_ELIGIBLE
                self.intents.update(intent)
                result.intents_manual_review += 1
                result.notes.append(f"manual_review {cid} after {hits} checks")
                if self.audit:
                    self.audit.record(
                        "RECONCILE_RESULT",
                        symbol=intent.symbol,
                        side=intent.side,
                        client_order_id=cid,
                        reason=f"not_found_after_{hits}",
                    )
            else:
                intent.status = OrderIntentStatus.RECONCILIATION
                self.intents.update(intent)
                result.notes.append(f"still_unknown {cid} checks={hits}")

        if free_map is not None:
            notes = self.positions.reconcile_with_balances(free_map, min_dust=self.min_dust)
            result.positions_adjusted = len(notes)
            result.notes.extend(notes)
            if notes and self.audit:
                self.audit.record(
                    "RECONCILE_RESULT",
                    reason="position_balance_mismatch",
                    extra={"notes": notes},
                )

        if self.audit and result.intents_checked == 0 and result.positions_adjusted == 0:
            self.audit.record("RECONCILE_RESULT", reason="noop")

        logger.info(
            "event=reconcile_done checked=%d confirmed=%d manual=%d pos_adj=%d",
            result.intents_checked,
            result.intents_confirmed,
            result.intents_manual_review,
            result.positions_adjusted,
        )
        return result
