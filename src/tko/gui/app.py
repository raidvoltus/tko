"""Tkinter control panel — display-only cache + command buttons via IPC.

Invariants:
* GUI close does NOT stop Core.
* Offline/stale heartbeat => READY=UNKNOWN (never cached READY).
* No exchange / credentials imports in this package.
"""

from __future__ import annotations

import logging
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from tko.ipc.transport import IPCClient

logger = logging.getLogger(__name__)

POLL_MS = 1500
STALE_SEC = 5.0


class TKOControlApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("TKO Control")
        self.root.geometry("740x500")
        self.client = IPCClient()
        self.last_hb = 0.0
        self._build()
        self.root.after(200, self._poll)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build(self) -> None:
        f = ttk.Frame(self.root, padding=12)
        f.pack(fill="both", expand=True)
        self.lbl_conn = ttk.Label(f, text="Connecting…", font=("Segoe UI", 12, "bold"))
        self.lbl_conn.grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 10))
        labels = [
            "Lifecycle",
            "READY",
            "Authorized",
            "Position",
            "PnL",
            "Recon",
            "Risk",
            "KILL",
            "Heartbeat",
            "Worker",
            "PID",
        ]
        self.vars = {lab: tk.StringVar(value="—") for lab in labels}
        for i, lab in enumerate(labels, start=1):
            ttk.Label(f, text=lab + ":", width=14).grid(row=i, column=0, sticky="w")
            ttk.Label(f, textvariable=self.vars[lab]).grid(row=i, column=1, sticky="w", columnspan=3)
        bf = ttk.Frame(f)
        bf.grid(row=len(labels) + 2, column=0, columnspan=4, sticky="ew", pady=(16, 0))
        for txt, cmd in (("STOP", "stop"), ("KILL", "kill"), ("STATUS", "status")):
            ttk.Button(bf, text=txt, command=lambda c=cmd: self._send(c)).pack(side="left", padx=4)
        note = ttk.Label(
            f,
            text="Closing this window does not stop Core. Core is independent.",
            foreground="#555",
        )
        note.grid(row=len(labels) + 3, column=0, columnspan=4, sticky="w", pady=(12, 0))

    def _poll(self) -> None:
        def worker() -> None:
            r = self.client.request("status")
            self.root.after(0, lambda: self._apply(r))

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(POLL_MS, self._poll)

    def _apply(self, r: dict) -> None:
        now = time.time()
        if not r.get("ok"):
            self._render_offline(str(r.get("error", "core offline")))
            return
        s = r.get("state") or {}
        hb = float(s.get("heartbeat") or 0)
        self.last_hb = hb
        if hb and (now - hb) > STALE_SEC:
            self._render_offline("heartbeat stale")
            return
        if not s.get("core_present", True) and not s.get("lifecycle"):
            self._render_offline("core not bound")
            return
        self.lbl_conn.configure(text="\u25cf CONNECTED", foreground="green")
        self.vars["Lifecycle"].set(str(s.get("lifecycle", "\u2014")))
        ready = bool(s.get("ready") or s.get("trading_authorized"))
        self.vars["READY"].set("YES" if ready else "NO")
        self.vars["Authorized"].set("YES" if s.get("trading_authorized") else "NO")
        self.vars["Position"].set(str(s.get("position", {})))
        self.vars["PnL"].set(str(s.get("pnl", {})))
        self.vars["Recon"].set(str(s.get("recon_status", "\u2014")))
        self.vars["Risk"].set(str(s.get("risk_status", "\u2014")))
        self.vars["KILL"].set(str(s.get("kill_status", "\u2014")))
        age = (now - hb) if hb else float("nan")
        self.vars["Heartbeat"].set(f"{age:.1f}s ago" if hb else "\u2014")
        self.vars["Worker"].set(str(s.get("worker_id", "\u2014")))
        self.vars["PID"].set(str(s.get("pid", "\u2014")))

    def _render_offline(self, reason: str) -> None:
        self.lbl_conn.configure(text=f"\u25cf CORE OFFLINE / UNKNOWN  ({reason})", foreground="red")
        for k in self.vars:
            self.vars[k].set("\u2014")
        self.vars["READY"].set("UNKNOWN")
        self.vars["Authorized"].set("UNKNOWN")

    def _send(self, cmd: str) -> None:
        if cmd in ("stop", "kill") and not messagebox.askyesno("Confirm", f"Send {cmd.upper()} to Core?"):
            return

        def worker() -> None:
            r = self.client.request(cmd)
            self.root.after(0, lambda: self._after_cmd(cmd, r))

        threading.Thread(target=worker, daemon=True).start()

    def _after_cmd(self, cmd: str, r: dict) -> None:
        if not r.get("ok"):
            messagebox.showerror("Command failed", str(r.get("error", "?")))

    def _on_close(self) -> None:
        # GUI exit must NOT stop Core
        try:
            self.client.disconnect()
        except Exception:  # noqa: BLE001,S110
            pass
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    try:
        TKOControlApp().run()
    except tk.TclError as exc:
        print(f"GUI unavailable (no display): {exc}")
        return 0
    return 0
