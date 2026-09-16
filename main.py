#!/usr/bin/env python3
"""
TKO unified launcher (dev).

Production Windows layout:
  TKO-Core.exe  -> src.core.entry  (IPC server + trading authority)
  TKO-GUI.exe   -> src.gui.entry   (IPC client only)

Dev convenience: run Core IPC + in-process GUI bridge (legacy single-process).
For strict GUI/Core isolation use:
  python -m src.core.entry
  python -m src.gui.entry
"""
from __future__ import annotations

import logging
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


def main():
    # Prefer documenting split mode; keep single-process bridge for quick PAPER testing
    from src.core.autopilot import Autopilot
    from src.gui.main_window import MainWindow
    from src.ipc.token import ensure_ipc_token
    from src.utils.secure_config import SecureConfig

    try:
        ensure_ipc_token()
    except Exception as e:
        logging.warning("IPC token bootstrap: %s", e)

    auto = Autopilot(root=ROOT)

    class Bridge:
        def __init__(self, autopilot):
            self.auto = autopilot
            self.secure = SecureConfig()
            self.secure.load()

        def save_config(self, api_key, api_secret, tg_token, tg_chat, mode="PAPER"):
            data = self.secure.load()
            if api_key and not str(api_key).startswith("*"):
                data["api_key"] = api_key
            if api_secret and not str(api_secret).startswith("*"):
                data["api_secret"] = api_secret
            if tg_token and not str(tg_token).startswith("*"):
                data["tg_token"] = tg_token
            if tg_chat:
                data["tg_chat"] = tg_chat
            data["mode"] = mode
            self.secure.save(data)
            self.auto.configure_credentials(
                data.get("api_key", ""),
                data.get("api_secret", ""),
                data.get("tg_token", ""),
                data.get("tg_chat", ""),
            )
            try:
                self.auto.exec_mgr.set_mode(mode)
            except Exception as e:
                logging.error("set_mode: %s", e)

        def test_tokocrypto(self):
            data = self.secure.load()
            self.auto.rest.api_key = data.get("api_key", "")
            self.auto.rest.api_secret = data.get("api_secret", "")
            try:
                body = self.auto.rest.server_time()
                return True, str(body)[:120]
            except Exception as e:
                return False, str(e)

        def test_telegram(self):
            data = self.secure.load()
            self.auto.tg.configure(data.get("tg_token", ""), data.get("tg_chat", ""))
            return self.auto.tg.test_connection()

        def start_bot(self, mode="PAPER"):
            data = self.secure.load()
            self.auto.configure_credentials(
                data.get("api_key", ""),
                data.get("api_secret", ""),
                data.get("tg_token", ""),
                data.get("tg_chat", ""),
            )
            try:
                self.auto.exec_mgr.set_mode(mode)
            except Exception as e:
                logging.error("%s", e)
                return
            self.auto.start()

        def stop_bot(self):
            self.auto.stop()

        def kill_switch(self):
            self.auto.kill()

        def get_snapshot(self):
            return self.auto.snapshot_for_gui()

        def load_config_dict(self):
            return self.secure.load()

    bridge = Bridge(auto)
    win = MainWindow(bridge)
    win.load_config_into_form(bridge.load_config_dict())
    win.run()


if __name__ == "__main__":
    main()
