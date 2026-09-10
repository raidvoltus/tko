"""Runtime lifecycle state machine — trading authorization gate (Stage 4).

INV-20..INV-60: only READY may authorize LIVE trading.
KILL is a hard safety state: no direct KILL->READY.
Autonomous recovery: KILL|HALTED|DEGRADED -> RECOVERY -> RECONCILING -> READY only after validation.
READY only via authorize_ready() with ALL validation gates True (INV-51).
transition(READY) and force(READY) are always rejected.
Only authorize_ready() may enter READY.
Submit handoff is race-safe via _submit_mutex (concurrent TOCTOU closed).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class LifecycleState(str, Enum):
    STARTING = "STARTING"
    RECONCILING = "RECONCILING"
    READY = "READY"
    DEGRADED = "DEGRADED"
    KILL = "KILL"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    HALTED = "HALTED"
    RECOVERY = "RECOVERY"


TERMINAL_SESSION_STATES = frozenset(
    {
        LifecycleState.KILL,
        LifecycleState.STOPPING,
        LifecycleState.STOPPED,
        LifecycleState.HALTED,
    }
)

_TRANSITIONS: dict[LifecycleState, frozenset[LifecycleState]] = {
    LifecycleState.STARTING: frozenset(
        {LifecycleState.RECONCILING, LifecycleState.HALTED, LifecycleState.STOPPING}
    ),
    LifecycleState.RECONCILING: frozenset(
        {
            LifecycleState.HALTED,
            LifecycleState.DEGRADED,
            LifecycleState.STOPPING,
            LifecycleState.KILL,
        }
    ),
    LifecycleState.READY: frozenset(
        {
            LifecycleState.DEGRADED,
            LifecycleState.KILL,
            LifecycleState.RECONCILING,
            LifecycleState.HALTED,
            LifecycleState.STOPPING,
        }
    ),
    LifecycleState.DEGRADED: frozenset(
        {
            LifecycleState.RECOVERY,
            LifecycleState.KILL,
            LifecycleState.HALTED,
            LifecycleState.STOPPING,
            LifecycleState.RECONCILING,
        }
    ),
    LifecycleState.KILL: frozenset(
        {LifecycleState.RECOVERY, LifecycleState.STOPPING, LifecycleState.HALTED}
    ),
    LifecycleState.HALTED: frozenset(
        {LifecycleState.RECOVERY, LifecycleState.STOPPING, LifecycleState.KILL}
    ),
    LifecycleState.RECOVERY: frozenset(
        {
            LifecycleState.RECONCILING,
            LifecycleState.HALTED,
            LifecycleState.KILL,
            LifecycleState.STOPPING,
        }
    ),
    LifecycleState.STOPPING: frozenset({LifecycleState.STOPPED}),
    LifecycleState.STOPPED: frozenset(),
}


@dataclass
class LifecycleSnapshot:
    state: LifecycleState
    trading_authorized: bool
    process_alive: bool
    last_error: str
    last_successful_recon_ts: float | None
    last_exchange_contact_ts: float | None
    last_tick_ts: float | None
    updated_at: float
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "trading_authorized": self.trading_authorized,
            "process_alive": self.process_alive,
            "last_error": self.last_error[:500] if self.last_error else "",
            "last_successful_recon_ts": self.last_successful_recon_ts,
            "last_exchange_contact_ts": self.last_exchange_contact_ts,
            "last_tick_ts": self.last_tick_ts,
            "updated_at": self.updated_at,
            "reason": self.reason[:300],
        }


class LifecycleGovernor:
    """Single authority for runtime lifecycle + trading authorization."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state = LifecycleState.STARTING
        self._last_error = ""
        self._reason = "init"
        self._last_successful_recon_ts: float | None = None
        self._last_exchange_contact_ts: float | None = None
        self._last_tick_ts: float | None = None
        self._process_alive = True
        self._kill_sticky = False
        self._submit_mutex = threading.Lock()

    @property
    def state(self) -> LifecycleState:
        with self._lock:
            return self._state

    @property
    def trading_authorized(self) -> bool:
        with self._lock:
            return self._state == LifecycleState.READY and not self._kill_sticky

    def is_terminal_safety_state(self) -> bool:
        with self._lock:
            return self._state in TERMINAL_SESSION_STATES or self._kill_sticky

    def snapshot(self) -> LifecycleSnapshot:
        with self._lock:
            return LifecycleSnapshot(
                state=self._state,
                trading_authorized=self._state == LifecycleState.READY and not self._kill_sticky,
                process_alive=self._process_alive,
                last_error=self._last_error,
                last_successful_recon_ts=self._last_successful_recon_ts,
                last_exchange_contact_ts=self._last_exchange_contact_ts,
                last_tick_ts=self._last_tick_ts,
                updated_at=time.time(),
                reason=self._reason,
            )

    def transition(self, target: LifecycleState, *, reason: str = "") -> bool:
        with self._lock:
            if target == LifecycleState.READY:
                logger.warning(
                    "event=lifecycle_transition_rejected from=%s to=READY reason=use_authorize_ready",
                    self._state.value,
                )
                return False
            allowed = _TRANSITIONS.get(self._state, frozenset())
            if target not in allowed and target != self._state:
                logger.warning(
                    "event=lifecycle_transition_rejected from=%s to=%s reason=%s",
                    self._state.value,
                    target.value,
                    reason[:200],
                )
                return False
            prev = self._state
            self._state = target
            self._reason = reason[:300]
            if target == LifecycleState.KILL:
                self._kill_sticky = True
            if target in (LifecycleState.HALTED, LifecycleState.DEGRADED, LifecycleState.KILL):
                if reason:
                    self._last_error = reason[:500]
            logger.info(
                "event=runtime_%s from=%s reason=%s",
                target.value.lower(),
                prev.value,
                reason[:200],
            )
            return True

    def force(self, target: LifecycleState, *, reason: str = "") -> None:
        with self._lock:
            if target == LifecycleState.READY:
                logger.warning(
                    "event=lifecycle_force_rejected from=%s to=READY reason=use_authorize_ready",
                    self._state.value,
                )
                return
            prev = self._state
            self._state = target
            self._reason = reason[:300]
            if target == LifecycleState.KILL:
                self._kill_sticky = True
            if reason:
                self._last_error = reason[:500]
            logger.info(
                "event=runtime_%s from=%s reason=%s forced=1",
                target.value.lower(),
                prev.value,
                reason[:200],
            )

    def mark_process_stopped(self) -> None:
        """Mark process not alive for heartbeat/watchdog (shutdown path)."""
        with self._lock:
            self._process_alive = False
            logger.info("event=process_alive_false reason=shutdown")

    def mark_process_alive(self) -> None:
        with self._lock:
            self._process_alive = True

    def mark_recon_ok(self) -> None:
        with self._lock:
            self._last_successful_recon_ts = time.time()

    def mark_exchange_contact(self) -> None:
        with self._lock:
            self._last_exchange_contact_ts = time.time()

    def mark_tick(self) -> None:
        with self._lock:
            self._last_tick_ts = time.time()

    def begin_recovery(self, *, reason: str = "") -> bool:
        with self._lock:
            if self._state not in (
                LifecycleState.KILL,
                LifecycleState.HALTED,
                LifecycleState.DEGRADED,
            ):
                return False
            prev = self._state
            self._state = LifecycleState.RECOVERY
            self._reason = reason[:300] or "recovery"
            logger.info("event=runtime_recovery from=%s reason=%s", prev.value, self._reason)
            return True

    def complete_recovery_to_reconciling(self, *, reason: str = "") -> bool:
        with self._lock:
            if self._state != LifecycleState.RECOVERY:
                return False
            self._state = LifecycleState.RECONCILING
            self._reason = reason[:300] or "recovery_recon"
            logger.info("event=runtime_reconciling from=RECOVERY reason=%s", self._reason)
            return True

    def authorize_ready(
        self,
        *,
        recon_ok: bool,
        kill_switch_clear: bool,
        circuit_clear: bool,
        daily_risk_ok: bool,
        positions_ok: bool,
        exchange_ok: bool,
        reason: str = "",
    ) -> bool:
        with self._lock:
            if self._state != LifecycleState.RECONCILING:
                logger.warning(
                    "event=lifecycle_authorize_ready_rejected state=%s",
                    self._state.value,
                )
                return False
            gates = {
                "recon_ok": recon_ok,
                "kill_switch_clear": kill_switch_clear,
                "circuit_clear": circuit_clear,
                "daily_risk_ok": daily_risk_ok,
                "positions_ok": positions_ok,
                "exchange_ok": exchange_ok,
            }
            failed = [k for k, v in gates.items() if not v]
            if failed:
                logger.warning(
                    "event=lifecycle_authorize_ready_rejected state=RECONCILING failed_gates=%s reason=%s",
                    ",".join(failed),
                    reason[:200],
                )
                return False
            prev = self._state
            was_sticky = self._kill_sticky
            self._kill_sticky = False
            self._state = LifecycleState.READY
            self._reason = reason[:300]
            self._last_successful_recon_ts = time.time()
            logger.info(
                "event=runtime_ready from=%s reason=%s cleared_kill_sticky=%s gates=all_pass",
                prev.value,
                reason[:200],
                was_sticky,
            )
            return True

    def request_stop(self) -> None:
        with self._submit_mutex:
            with self._lock:
                if self._state in (LifecycleState.STOPPED, LifecycleState.STOPPING):
                    return
                prev = self._state
                self._state = LifecycleState.STOPPING
                self._reason = "shutdown_requested"
                logger.info(
                    "event=runtime_stopping from=%s reason=shutdown_requested",
                    prev.value,
                )

    def run_authorized_submit(self, submit_fn: Callable[[], T]) -> T:
        with self._submit_mutex:
            with self._lock:
                if self._state != LifecycleState.READY or self._kill_sticky:
                    raise RuntimeError(
                        f"lifecycle rejects submit: state={self._state.value}"
                    )
            return submit_fn()

    def try_run_authorized_submit(self, submit_fn: Callable[[], T]) -> T | None:
        try:
            return self.run_authorized_submit(submit_fn)
        except RuntimeError:
            return None
