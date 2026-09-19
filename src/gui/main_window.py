"""Simple single-dashboard GUI - Tkinter, Windows 7 compatible."""
from __future__ import annotations

import logging
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Dict

logger = logging.getLogger(__name__)


class MainWindow:
    def __init__(self, app_controller: Any):
        self.ctrl = app_controller
        self.root = tk.Tk()
        self.root.title("TOKOCRYPTO TRADING BOT")
        self.root.geometry("980x720")
        self.root.minsize(800, 600)

        self._build_config_frame()
        self._build_dashboard()
        self._update_job = None
        self._running_ui = True

    def _build_config_frame(self) -> None:
        frm = ttk.LabelFrame(self.root, text="Configuration", padding=6)
        frm.pack(fill=tk.X, padx=6, pady=4)

        ttk.Label(frm, text="API Key").grid(row=0, column=0, sticky=tk.W)
        self.api_key_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.api_key_var, width=40).grid(row=0, column=1, padx=4)

        ttk.Label(frm, text="API Secret").grid(row=0, column=2, sticky=tk.W)
        self.api_secret_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.api_secret_var, width=40, show="*").grid(row=0, column=3, padx=4)

        ttk.Label(frm, text="Telegram Token").grid(row=1, column=0, sticky=tk.W)
        self.tg_token_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.tg_token_var, width=40, show="*").grid(row=1, column=1, padx=4)

        ttk.Label(frm, text="Telegram Chat ID").grid(row=1, column=2, sticky=tk.W)
        self.tg_chat_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.tg_chat_var, width=40).grid(row=1, column=3, padx=4)

        btn_row = ttk.Frame(frm)
        btn_row.grid(row=2, column=0, columnspan=4, pady=4)
        ttk.Button(btn_row, text="Save Configuration", command=self._on_save).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_row, text="Test Tokocrypto", command=self._on_test_toko).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_row, text="Test Telegram", command=self._on_test_tg).pack(side=tk.LEFT, padx=3)

        mode_frm = ttk.Frame(frm)
        mode_frm.grid(row=3, column=0, columnspan=4, sticky=tk.W)
        ttk.Label(mode_frm, text="Trading Mode:").pack(side=tk.LEFT)
        self.mode_var = tk.StringVar(value="LIVE")
        for m in ("LIVE",):
            ttk.Radiobutton(mode_frm, text=m, variable=self.mode_var, value=m).pack(side=tk.LEFT, padx=4)

        ctrl_row = ttk.Frame(frm)
        ctrl_row.grid(row=4, column=0, columnspan=4, pady=4)
        ttk.Button(ctrl_row, text="START BOT", command=self._on_start).pack(side=tk.LEFT, padx=3)
        ttk.Button(ctrl_row, text="STOP BOT", command=self._on_stop).pack(side=tk.LEFT, padx=3)
        ttk.Button(ctrl_row, text="EMERGENCY KILL SWITCH", command=self._on_kill).pack(side=tk.LEFT, padx=3)

    def _build_dashboard(self) -> None:
        # Status bar
        self.status_frame = ttk.Frame(self.root)
        self.status_frame.pack(fill=tk.X, padx=6)
        self.lbl_conn = ttk.Label(self.status_frame, text="Connection: ● UNKNOWN", foreground="gray")
        self.lbl_conn.pack(side=tk.LEFT, padx=6)
        self.lbl_mode = ttk.Label(self.status_frame, text="Mode: LIVE")
        self.lbl_mode.pack(side=tk.LEFT, padx=6)
        self.lbl_bot = ttk.Label(self.status_frame, text="Bot: STOPPED")
        self.lbl_bot.pack(side=tk.LEFT, padx=6)

        # Notebook-like sections in a scrollable text for simplicity + responsiveness on Win7
        self.dash = tk.Text(self.root, height=36, wrap=tk.WORD, font=("Consolas", 9))
        self.dash.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)
        self.dash.configure(state=tk.DISABLED)

    def _set_dash(self, text: str) -> None:
        self.dash.configure(state=tk.NORMAL)
        self.dash.delete("1.0", tk.END)
        self.dash.insert(tk.END, text)
        self.dash.configure(state=tk.DISABLED)

    def _on_save(self) -> None:
        self.ctrl.save_config(
            api_key=self.api_key_var.get().strip(),
            api_secret=self.api_secret_var.get().strip(),
            tg_token=self.tg_token_var.get().strip(),
            tg_chat=self.tg_chat_var.get().strip(),
            mode=self.mode_var.get(),
        )
        messagebox.showinfo("Saved", "Configuration saved (encrypted).")

    def _on_test_toko(self) -> None:
        ok, msg = self.ctrl.test_tokocrypto()
        messagebox.showinfo("Tokocrypto", msg if ok else f"FAIL: {msg}")

    def _on_test_tg(self) -> None:
        ok = self.ctrl.test_telegram()
        messagebox.showinfo("Telegram", "OK" if ok else "FAIL - check token/chat id")

    def _on_start(self) -> None:
        mode = self.mode_var.get()
        if mode == "LIVE":
            if not messagebox.askyesno("Confirm LIVE", "Enable LIVE trading? Real orders will be sent."):
                return
        self.ctrl.start_bot(mode)
        self.lbl_bot.config(text="Bot: RUNNING")

    def _on_stop(self) -> None:
        self.ctrl.stop_bot()
        self.lbl_bot.config(text="Bot: STOPPED")

    def _on_kill(self) -> None:
        if messagebox.askyesno("KILL SWITCH", "Activate emergency kill switch?"):
            self.ctrl.kill_switch()
            self.lbl_bot.config(text="Bot: KILL SWITCH")

    def refresh_dashboard(self, snapshot: Dict[str, Any]) -> None:
        """Called from controller with a full state snapshot."""
        lines = []
        lines.append("=" * 70)
        lines.append("TOKOCRYPTO TRADING BOT — DASHBOARD")
        lines.append("=" * 70)
        conn = snapshot.get("connection", "UNKNOWN")
        color = "green" if conn == "ONLINE" else "red"
        self.lbl_conn.config(text=f"Connection: ● {conn}", foreground=color)
        self.lbl_mode.config(text=f"Mode: {snapshot.get('mode', 'LIVE')}")
        self.lbl_bot.config(text=f"Bot: {snapshot.get('bot_status', 'STOPPED')}")

        lines.append(f"Connection: {conn}   Mode: {snapshot.get('mode')}   Bot: {snapshot.get('bot_status')}")
        lines.append("-" * 70)
        lines.append("TOKOCRYPTO ACCOUNT")
        recon = snapshot.get("reconciliation") or {}
        bal = snapshot.get("balance", {})
        lines.append(f"  ACCOUNT STATE : {snapshot.get('account_state', recon.get('state', '—'))}")
        lines.append(f"  SOURCE        : {bal.get('source', recon.get('source', '—'))}")
        lines.append(
            f"  USDT free={bal.get('available', 'UNKNOWN')}  "
            f"locked={bal.get('locked', 'UNKNOWN')}  total={bal.get('total', 'UNKNOWN')}"
        )
        lines.append(f"  Equity USDT   : {bal.get('equity_usdt', 'UNKNOWN')}")
        if bal.get("unpriced"):
            lines.append(f"  Unpriced      : {bal.get('unpriced')}")
        lines.append("  HOLDINGS:")
        lines.append("-" * 70)
        lines.append("MARKET")
        m = snapshot.get("market", {})
        lines.append(f"  Symbol: {m.get('symbol', '—')}  Last: {m.get('last', '—')}  Bid: {m.get('bid', '—')}  Ask: {m.get('ask', '—')}")
        lines.append(f"  Spread: {m.get('spread', '—')}  24h: {m.get('change', '—')}  Status: {m.get('status', '—')}")
        lines.append("-" * 70)
        lines.append("SIGNAL / ML")
        s = snapshot.get("signal", {})
        lines.append(f"  Signal: {s.get('signal', 'WAIT')}  Prob: {s.get('prob', '—')}  Model: {s.get('model', '—')} v{s.get('version', '—')}")
        lines.append(f"  Model Status: {s.get('model_status', '—')}  Last Pred: {s.get('last_pred', '—')}")
        lines.append("-" * 70)
        lines.append("POSITION")
        for p in snapshot.get("positions", []) or ["  (none)"]:
            lines.append(f"  {p}")
        lines.append("-" * 70)
        lines.append("ORDERS (recent)")
        for o in snapshot.get("orders", [])[:8] or ["  (none)"]:
            lines.append(f"  {o}")
        lines.append("-" * 70)
        lines.append("RISK")
        r = snapshot.get("risk", {})
        lines.append(f"  Daily PnL: {r.get('daily_pnl', '—')}  Exposure: {r.get('exposure', '—')}  Status: {r.get('status', '—')}")
        lines.append(f"  Circuit: {r.get('circuit', False)}  Kill: {r.get('kill', False)}")
        lines.append("-" * 70)
        lines.append("SYSTEM")
        sys_ = snapshot.get("system", {})
        lines.append(f"  REST: {sys_.get('rest', '—')}  WS: {sys_.get('ws', '—')}  UserStream: {sys_.get('user_stream', '—')}")
        lines.append(f"  Clock offset: {sys_.get('clock_offset', '—')}  Rate limit: {sys_.get('rate', '—')}")
        lines.append("-" * 70)
        lines.append("TELEGRAM")
        tg = snapshot.get("telegram", {})
        lines.append(f"  Status: {tg.get('status', 'OFFLINE')}  Last: {tg.get('last', '—')}  Errors: {tg.get('errors', '—')}")
        lines.append("-" * 70)
        lines.append("ACTIVITY LOG")
        for log in snapshot.get("logs", [])[-10:]:
            lines.append(f"  {log}")
        lines.append("=" * 70)
        self._set_dash("\n".join(lines))

    def load_config_into_form(self, cfg: Dict) -> None:
        self.api_key_var.set(cfg.get("api_key", ""))
        # never show real secret; leave blank or masked indicator
        if cfg.get("api_secret"):
            self.api_secret_var.set("********")
        if cfg.get("tg_token"):
            self.tg_token_var.set("********")
        self.tg_chat_var.set(cfg.get("tg_chat", ""))
        self.mode_var.set(cfg.get("mode", "LIVE"))

    def run(self) -> None:
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._schedule_refresh()
        self.root.mainloop()

    def _schedule_refresh(self) -> None:
        if not self._running_ui:
            return
        try:
            snap = self.ctrl.get_snapshot()
            self.refresh_dashboard(snap)
        except Exception as e:
            logger.debug("UI refresh error: %s", e)
        self.root.after(1000, self._schedule_refresh)

    def _on_close(self) -> None:
        self._running_ui = False
        try:
            self.ctrl.stop_bot()
        except Exception:
            pass
        self.root.destroy()
