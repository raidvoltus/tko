"""Runtime lifecycle state machine — trading authorization gate (Stage 4).

INV-20..INV-47: only READY may authorize LIVE trading.
KILL is terminal for the current process session.
DEGRADED may not jump directly to READY.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class LifecycleState(str, Enum):
    STARTING = "STARTING"
    RECONCILING = "RECONCILING"
    READY = "READY"
    DEGRADED = "DEGRADED"
    KILL = "KILL"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    HALTED = "HALTED"


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
        {
            LifecycleState.RECONCILING,
            LifecycleState.HALTED,
            LifecycleState.STOPPING,
        }
    ),
    LifecycleState.RECONCILING: frozenset(
        {
            LifecycleState.READY,
            LifecycleState.HALTED,
            LifecycleState.DEGRADED,
            LifecycleState.STOPPING,
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
            LifecycleState.RECONCILING,
            LifecycleState.KILL,
            LifecycleState.HALTED,
            LifecycleState.STOPPING,
        }
    ),
    LifecycleState.KILL: frozenset(
        {
            LifecycleState.STOPPING,
            LifecycleState.HALTED,
        }
    ),
    LifecycleState.STOPPING: frozenset({LifecycleState.STOPPED}),
    LifecycleState.STOPPED: frozenset(),
    LifecycleState.HALTED: frozenset(
        {
            LifecycleState.STOPPING,
            LifecycleState.RECONCILING,
        }
    ),
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
            if self._kill_sticky and target == LifecycleState.READY:
                logger.warning(
                    "event=lifecycle_transition_rejected from=%s to=READY reason=kill_sticky",
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
            if target == LifecycleState.READY and self._kill_sticky:
                logger.warning("event=lifecycle_force_rejected to=READY reason=kill_sticky")
                return
            if target == LifecycleState.READY and self._state == LifecycleState.KILL:
                logger.warning("event=lifecycle_force_rejected from=KILL to=READY")
                return
            if target == LifecycleState.READY and self._state == LifecycleState.DEGRADED:
                logger.warning(
                    "event=lifecycle_force_rejected from=DEGRADED to=READY must_reconcile_first"
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

    def mark_recon_ok(self) -> None:
        with self._lock:
            self._last_successful_recon_ts = time.time()

    def mark_exchange_contact(self) -> None:
        with self._lock:
            self._last_exchange_contact_ts = time.time()

    def mark_tick(self) -> None:
        with self._lock:
            self._last_tick_ts = time.time()

    def assert_trading_allowed(self) -> None:
        if not self.trading_authorized:
            snap = self.snapshot()
            raise RuntimeError(
                f"trading not authorized: state={snap.state.value} reason={snap.reason}"
            )

    def request_stop(self) -> None:
        """Signal-safe: immediately disable trading and enter STOPPING."""
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
