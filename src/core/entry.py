"""TKO-Core entry — sole trading authority. Owns Autopilot + Risk + Execution + IPC server."""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict

# Bootstrap sys.path before importing src.* (dev mode)
if not getattr(sys, "frozen", False):
    try:
        _dev_root = Path(__file__).resolve().parents[2]
        if str(_dev_root) not in sys.path:
            sys.path.insert(0, str(_dev_root))
    except IndexError:
        pass

from src.utils.paths import install_dir, is_frozen  # noqa: E402

ROOT = install_dir()

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
            mode = str(req.get("mode", "LIVE")).upper()
            try:
                from src.execution.production_policy import require_live
                mode = require_live(mode)
                auto.exec_mgr.set_mode(mode)
            except Exception as e:
                return {"ok": False, "error": str(e)}
            auto.start()
            return {"ok": True, "running": True, "mode": "LIVE"}
        if cmd == "stop":
            auto.stop()
            return {"ok": True, "running": False}
        if cmd == "kill":
            auto.kill()
            return {"ok": True, "kill": True}
        if cmd == "credential_status":
            from src.security.credentials import NAMESPACE_TG, NAMESPACE_TOKO, CredentialStore
            toko = CredentialStore(NAMESPACE_TOKO).validate()
            tg = CredentialStore(NAMESPACE_TG).validate()
            return {
                "ok": True,
                "toko": toko,
                "telegram": tg,
                "rest_key_present": bool(auto.rest.api_key),
                "tg_status": getattr(auto.tg, "last_status", ""),
            }
        if cmd == "delete_credentials":
            from src.security.credentials import NAMESPACE_TG, NAMESPACE_TOKO, CredentialStore
            scope = str(req.get("scope", "all"))
            if scope in ("all", "toko"):
                CredentialStore(NAMESPACE_TOKO).delete()
                auto.rest.api_key = ""
                auto.rest.api_secret = ""
            if scope in ("all", "telegram"):
                CredentialStore(NAMESPACE_TG).delete()
            return {"ok": True}
        if cmd == "configure":
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
        if cmd in ("order", "buy", "sell", "submit_order", "place_order"):
            return {"ok": False, "error": "orders_only_via_core_risk_not_gui"}
        return {"ok": False, "error": f"unknown_cmd:{cmd}"}

    return handler


def main() -> int:
    from src.core.autopilot import Autopilot
    from src.ipc.server import IpcServer
    from src.ipc.token import TokenError, ensure_ipc_token

    logger.info("install_dir=%s frozen=%s", ROOT, is_frozen())
    try:
        path = ensure_ipc_token()
        logger.info("IPC token ready at %s", path)
    except TokenError as e:
        logger.error("IPC bootstrap failed: %s", e)
        return 2

    auto = Autopilot()  # frozen-aware config/state paths
    server = IpcServer(build_handler(auto))
    try:
        server.start()
    except Exception as e:
        logger.error("IPC server failed: %s", e)
        return 3

    logger.info(
        "TKO-Core ready (mode=%s). config=%s Ctrl+C to stop.",
        auto.exec_mgr.mode,
        getattr(auto.control, "config_path", "?"),
    )
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
