"""TKO-GUI entry — IPC client only. No direct exchange orders."""
from __future__ import annotations

import logging
import sys
from typing import Any, Dict

# Bootstrap sys.path only in dev (not frozen PyInstaller)
if not getattr(sys, "frozen", False):
    from pathlib import Path

    try:
        _dev_root = Path(__file__).resolve().parents[2]
        if str(_dev_root) not in sys.path:
            sys.path.insert(0, str(_dev_root))
    except (IndexError, AttributeError):
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("tko.gui")


def _require_tkinter() -> None:
    try:
        import _tkinter  # noqa: F401
        import tkinter  # noqa: F401
    except ModuleNotFoundError as e:
        raise SystemExit(
            "TKO-GUI requires tkinter/_tkinter.\n"
            "This frozen build is missing Tcl/Tk runtime files.\n"
            "Rebuild with packaging/tko-gui.spec on official Python (with Tcl/Tk),\n"
            "and ship the full onedir folder TKO-GUI/ (not only the .exe).\n"
            f"Original error: {e}"
        ) from e


class GuiIpcBridge:
    def __init__(self):
        from src.ipc.client import IpcClient
        from src.ipc.token import TokenError, ensure_ipc_token

        try:
            ensure_ipc_token()
        except TokenError as e:
            logger.error("Token bootstrap failed: %s", e)
        self.client = IpcClient()
        try:
            self.client.connect_auth()
            self.ipc_ok = True
        except Exception as e:
            logger.error("IPC auth failed: %s", e)
            self.ipc_ok = False

    def _req(self, **kwargs) -> Dict[str, Any]:
        if not self.ipc_ok:
            return {"ok": False, "error": "ipc_not_ready"}
        try:
            return self.client.request(kwargs)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def save_config(self, api_key, api_secret, tg_token, tg_chat, mode="LIVE"):
        r = self._req(
            cmd="configure",
            api_key=api_key if not str(api_key).startswith("*") else "",
            api_secret=api_secret if not str(api_secret).startswith("*") else "",
            tg_token=tg_token if not str(tg_token).startswith("*") else "",
            tg_chat=tg_chat,
        )
        return r

    def test_tokocrypto(self):
        r = self._req(cmd="test_toko")
        return bool(r.get("ok")), r.get("data") or r.get("error", "fail")

    def test_telegram(self):
        r = self._req(cmd="test_telegram")
        return bool(r.get("ok"))

    def start_bot(self, mode="LIVE"):
        return self._req(cmd="start", mode=mode)

    def stop_bot(self):
        return self._req(cmd="stop")

    def kill_switch(self):
        return self._req(cmd="kill")

    def get_snapshot(self):
        r = self._req(cmd="snapshot")
        if r.get("ok") and isinstance(r.get("data"), dict):
            return r["data"]
        return {
            "connection": "IPC_ERROR",
            "mode": "—",
            "bot_status": r.get("error", "NO_CORE"),
            "balance": {"total": "—", "available": "—", "locked": "—"},
            "market": {"symbol": "—", "last": "—", "bid": "—", "ask": "—", "status": "—"},
            "signal": {
                "signal": "—",
                "prob": "—",
                "model": "—",
                "version": "—",
                "model_status": "—",
                "last_pred": "—",
            },
            "positions": [],
            "orders": [],
            "risk": {"daily_pnl": "—", "exposure": "—", "status": "—", "circuit": False, "kill": False},
            "system": {"rest": "—", "ws": "—", "user_stream": "—", "clock_offset": "—", "rate": "—"},
            "telegram": {"status": "—", "last": "—", "errors": "—"},
            "logs": [str(r.get("error", "core offline"))],
        }


def main() -> int:
    _require_tkinter()
    from src.gui.main_window import MainWindow

    bridge = GuiIpcBridge()
    if not bridge.ipc_ok:
        logger.error("GUI starting without IPC auth — trading unavailable")
    win = MainWindow(bridge)
    win.load_config_into_form({})
    win.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
