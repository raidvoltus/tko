"""TKO-Core entry — sole trading authority. Owns Autopilot + Risk + Execution + IPC server."""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict

# Ensure project root on path when frozen or script
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("tko.core")


def build_handler(auto):
    def handler(req: Dict[str, Any]) -> Dict[str, Any]:
        cmd = str(req.get("cmd", ""))
        if cmd == "ping":
            return {"ok": True, "pong": True, "mode": auto.exec_mgr.mode, "running": auto.running}
        if cmd == "snapshot":
            return {"ok": True, "data": auto.snapshot_for_gui()}
        if cmd == "start":
            mode = str(req.get("mode", "PAPER")).upper()
            if mode not in ("PAPER", "SHADOW", "LIVE"):
                return {"ok": False, "error": "invalid_mode"}
            try:
                auto.exec_mgr.set_mode(mode)
            except Exception as e:
                return {"ok": False, "error": str(e)}
            auto.start()
            return {"ok": True, "running": True, "mode": mode}
        if cmd == "stop":
            auto.stop()
            return {"ok": True, "running": False}
        if cmd == "kill":
            auto.kill()
            return {"ok": True, "kill": True}
        if cmd == "configure":
            # GUI may send credentials over authenticated IPC only
            auto.configure_credentials(
                str(req.get("api_key", "")),
                str(req.get("api_secret", "")),
                str(req.get("tg_token", "")),
                str(req.get("tg_chat", "")),
            )
            return {"ok": True}
        if cmd == "test_toko":
            try:
                body = auto.rest.server_time()
                return {"ok": True, "data": str(body)[:200]}
            except Exception as e:
                return {"ok": False, "error": str(e)}
        if cmd == "test_telegram":
            ok = auto.tg.test_connection()
            return {"ok": ok}
        # Explicitly reject any direct order command from GUI
        if cmd in ("order", "buy", "sell", "submit_order", "place_order"):
            return {"ok": False, "error": "orders_only_via_core_risk_not_gui"}
        return {"ok": False, "error": f"unknown_cmd:{cmd}"}

    return handler


def main() -> int:
    from src.core.autopilot import Autopilot
    from src.ipc.server import IpcServer
    from src.ipc.token import TokenError, ensure_ipc_token

    try:
        path = ensure_ipc_token()
        logger.info("IPC token ready at %s", path)
    except TokenError as e:
        logger.error("IPC bootstrap failed: %s", e)
        return 2

    auto = Autopilot(root=str(ROOT))
    server = IpcServer(build_handler(auto))
    try:
        server.start()
    except Exception as e:
        logger.error("IPC server failed: %s", e)
        return 3

    logger.info("TKO-Core ready (mode=%s). Ctrl+C to stop.", auto.exec_mgr.mode)
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        logger.info("Shutting down Core")
    finally:
        auto.stop()
        server.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
