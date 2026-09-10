"""Map IPC commands to Core runtime without bypassing Stage 4–9 gates.

Rules:
* status is read-only snapshot.
* stop / kill go through existing lifecycle APIs.
* start is rejected if already active; never force READY.
* reconcile cannot invent fills — only reports that Core must re-run barrier.
* No order submission path exists here.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class CoreHandle(Protocol):
    """Minimal surface the IPC dispatcher needs from TradingBot / runtime."""

    @property
    def lifecycle(self) -> Any: ...

    def request_shutdown(self) -> None: ...

    def status_dict(self) -> dict[str, Any]: ...


def build_status(core: CoreHandle | None, *, started_at: float, worker_id: str) -> dict[str, Any]:
    if core is None:
        return {
            "ok": True,
            "state": {
                "lifecycle": "UNKNOWN",
                "ready": False,
                "trading_authorized": False,
                "process_alive": False,
                "position": {},
                "balance": {},
                "pnl": {},
                "recon_status": "UNKNOWN",
                "risk_status": "UNKNOWN",
                "kill_status": "UNKNOWN",
                "heartbeat": time.time(),
                "started_at": started_at,
                "worker_id": worker_id,
                "pid": os.getpid(),
                "core_present": False,
            },
        }
    try:
        return {"ok": True, "state": core.status_dict()}
    except Exception as exc:
        logger.warning("status snapshot failed: %s", exc)
        return {"ok": False, "error": "status_unavailable"}


def dispatch(
    core: CoreHandle | None,
    cmd: str,
    args: dict[str, Any] | None = None,
    *,
    started_at: float = 0.0,
    worker_id: str = "",
) -> dict[str, Any]:
    args = args or {}
    cmd = (cmd or "").strip().lower()

    if cmd in ("status", "ping"):
        return build_status(core, started_at=started_at, worker_id=worker_id)

    if cmd == "stop":
        if core is None:
            return {"ok": False, "error": "core_not_bound"}
        core.request_shutdown()
        return {"ok": True, "accepted": "stop"}

    if cmd == "kill":
        if core is None:
            return {"ok": False, "error": "core_not_bound"}
        try:
            from tko.runtime.lifecycle import LifecycleState

            core.lifecycle.force(LifecycleState.KILL, reason="ipc_kill")
            core.request_shutdown()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "accepted": "kill"}

    if cmd == "start":
        if core is None:
            return {"ok": False, "error": "core_not_bound"}
        snap = core.lifecycle.snapshot()
        if snap.trading_authorized or snap.state.value in ("READY", "RECONCILING", "STARTING"):
            return {"ok": True, "accepted": "already_running", "lifecycle": snap.state.value}
        return {
            "ok": False,
            "error": "start_requires_core_process",
            "detail": "Start TKO-Core service/process; GUI cannot authorize READY",
        }

    if cmd == "reconcile":
        if core is None:
            return {"ok": False, "error": "core_not_bound"}
        snap = core.lifecycle.snapshot()
        return {
            "ok": True,
            "accepted": "reconcile_owned_by_core",
            "lifecycle": snap.state.value,
            "trading_authorized": snap.trading_authorized,
            "note": "Restart Core or wait for autonomous recovery; GUI cannot bypass authorize_ready",
        }

    return {"ok": False, "error": f"unknown_cmd:{cmd}"}
