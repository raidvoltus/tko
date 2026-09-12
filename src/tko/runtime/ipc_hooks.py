"""Optional IPC binding helpers for TradingBot (Stage 10)."""

from __future__ import annotations

import logging
import os
import secrets
import time
from typing import Any

logger = logging.getLogger(__name__)


def make_status_dict(bot: Any) -> dict:
    snap = bot.lifecycle.snapshot()
    positions: dict = {}
    try:
        store = getattr(bot, "positions_store", None)
        if store is not None and hasattr(store, "all"):
            data = store.all()
            if isinstance(data, dict):
                positions = {str(k): str(v) for k, v in data.items()}
    except Exception:  # noqa: BLE001
        positions = {}
    pnl: dict = {}
    try:
        day = bot.pnl.stats_for_day()
        pnl = {
            "realized": float(getattr(day, "realized_pnl", 0.0) or 0.0),
            "notional": float(getattr(day, "notional_traded", 0.0) or 0.0),
            "day": str(getattr(day, "day", "")),
        }
    except Exception:  # noqa: BLE001
        pnl = {}
    return {
        "lifecycle": snap.state.value,
        "ready": bool(snap.trading_authorized),
        "trading_authorized": bool(snap.trading_authorized),
        "process_alive": bool(snap.process_alive),
        "position": positions,
        "balance": {},
        "pnl": pnl,
        "recon_status": "OK" if snap.last_successful_recon_ts else "PENDING",
        "risk_status": "KILL" if snap.state.value == "KILL" else ("OK" if snap.trading_authorized else snap.state.value),
        "kill_status": "ACTIVE" if snap.state.value == "KILL" else "ARMED",
        "heartbeat": time.time(),
        "started_at": getattr(bot, "_started_at", time.time()),
        "worker_id": getattr(bot, "_worker_id", ""),
        "pid": os.getpid(),
        "core_present": True,
        "reason": (snap.reason or "")[:200],
    }


def start_ipc(bot: Any) -> None:
    from tko.ipc.dispatcher import dispatch
    from tko.ipc.transport import IPCServer

    bot._started_at = time.time()
    bot._worker_id = secrets.token_hex(8)

    def _disp(cmd, args):
        return dispatch(bot, cmd, args or {}, started_at=bot._started_at, worker_id=bot._worker_id)

    try:
        bot._ipc = IPCServer(_disp)
        bot._ipc.start()
        logger.info("event=ipc_bound worker_id=%s", bot._worker_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("event=ipc_start_failed err=%s", exc)
        bot._ipc = None


def stop_ipc(bot: Any) -> None:
    ipc = getattr(bot, "_ipc", None)
    if ipc is not None:
        try:
            ipc.stop()
        except Exception as exc:  # noqa: BLE001
            logger.warning("event=ipc_stop_failed err=%s", exc)
        bot._ipc = None
