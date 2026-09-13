"""Tkinter control panel — display-only cache + command buttons via IPC.

Invariants:
* GUI close does NOT stop Core.
* Offline/stale heartbeat => READY=UNKNOWN (never cached READY).
* No exchange trading from GUI; credentials only written to OS keyring via Setup.
* Setup does not authorize trading — Core remains sole trading authority.
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


class SetupDialog(tk.Toplevel):
    """One-shot credential entry → OS keyring only (no trading, no plaintext files)."""

    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master)
        self.title("TKO Setup — Credentials")
        self.geometry("520x360")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        frm = ttk.Frame(self, padding=14)
        frm.pack(fill="both", expand=True)

        ttk.Label(
            frm,
            text="Disimpan ke OS keyring (Windows Credential Manager).\n"
            "GUI tidak mengeksekusi order. Jalankan TKO-Core.exe run setelah setup.",
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))

        ttk.Label(frm, text="Tokocrypto API Key").grid(row=1, column=0, sticky="w")
        self.api_key = ttk.Entry(frm, width=48)
        self.api_key.grid(row=1, column=1, sticky="ew", pady=4)

        ttk.Label(frm, text="Tokocrypto API Secret").grid(row=2, column=0, sticky="w")
        self.api_secret = ttk.Entry(frm, width=48, show="*")
        self.api_secret.grid(row=2, column=1, sticky="ew", pady=4)

        ttk.Separator(frm).grid(row=3, column=0, columnspan=2, sticky="ew", pady=10)

        ttk.Label(frm, text="Telegram Bot Token (opsional)").grid(row=4, column=0, sticky="w")
        self.tg_token = ttk.Entry(frm, width=48, show="*")
        self.tg_token.grid(row=4, column=1, sticky="ew", pady=4)

        ttk.Label(frm, text="Telegram Chat ID (opsional)").grid(row=5, column=0, sticky="w")
        self.tg_chat = ttk.Entry(frm, width=48)
        self.tg_chat.grid(row=5, column=1, sticky="ew", pady=4)

        bf = ttk.Frame(frm)
        bf.grid(row=6, column=0, columnspan=2, sticky="e", pady=(16, 0))
        ttk.Button(bf, text="Cancel", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(bf, text="Save to keyring", command=self._save).pack(side="right", padx=4)

        self.bind("<Return>", lambda _e: self._save())
        self.api_key.focus_set()

    def _save(self) -> None:
        key = self.api_key.get().strip()
        secret = self.api_secret.get().strip()
        if not key or not secret:
            messagebox.showerror("Setup", "API Key dan API Secret wajib diisi.", parent=self)
            return
        try:
            from tko.core.credentials import CredentialError, save_telegram, save_tokocrypto

            save_tokocrypto(key, secret)
            tg_t = self.tg_token.get().strip()
            tg_c = self.tg_chat.get().strip()
            if tg_t or tg_c:
                if not tg_t or not tg_c:
                    messagebox.showerror(
                        "Setup",
                        "Telegram: isi Bot Token dan Chat ID keduanya, atau kosongkan keduanya.",
                        parent=self,
                    )
                    return
                save_telegram(tg_t, tg_c)
        except CredentialError as exc:
            messagebox.showerror("Setup gagal", str(exc), parent=self)
            return
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Setup gagal", f"Keyring error: {exc}", parent=self)
            return

        try:
            from tko.ipc.protocol import default_token_path, ensure_token

            path = default_token_path()
            ensure_token(path)
            token_msg = f"\nIPC token: {path}"
        except Exception as exc:  # noqa: BLE001
            token_msg = f"\nIPC token belum dibuat: {exc}"

        messagebox.showinfo(
            "Setup OK",
            "Kredensial tersimpan di keyring."
            + token_msg
            + "\n\nLangkah berikutnya:\n"
            "1. Jalankan TKO-Core.exe run (biarkan tetap terbuka)\n"
            "2. Buka GUI lagi — status harus CONNECTED",
            parent=self,
        )
        self.destroy()


class TKOControlApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("TKO Control")
        self.root.geometry("760x540")
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
        ttk.Button(bf, text="SETUP", command=self._open_setup).pack(side="left", padx=4)
        for txt, cmd in (("STOP", "stop"), ("KILL", "kill"), ("STATUS", "status")):
            ttk.Button(bf, text=txt, command=lambda c=cmd: self._send(c)).pack(side="left", padx=4)
        note = ttk.Label(
            f,
            text="SETUP = keyring only (bukan trading). Closing GUI does not stop Core.\n"
            "Token IPC: %PROGRAMDATA%\\TKO\\ipc.token (bukan Program Files).",
            foreground="#555",
        )
        note.grid(row=len(labels) + 3, column=0, columnspan=4, sticky="w", pady=(12, 0))

    def _open_setup(self) -> None:
        SetupDialog(self.root)

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
        s = r.get("state") or r
        if not isinstance(s, dict):
            self._render_offline("bad status payload")
            return
        hb = float(s.get("heartbeat") or 0.0)
        if hb:
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
