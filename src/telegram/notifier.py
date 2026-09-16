"""Telegram trade notifications - notification only, never trading authority."""
from __future__ import annotations

import hashlib
import logging
import queue
import threading
import time
from typing import Optional, Set

import requests

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, bot_token: str = "", chat_id: str = ""):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._sent_keys: Set[str] = set()
        self._queue: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.last_status = "OFFLINE"
        self.last_error = ""
        self.last_notification_ts = 0.0

    def configure(self, bot_token: str, chat_id: str) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def test_connection(self) -> bool:
        if not self.bot_token or not self.chat_id:
            self.last_status = "OFFLINE"
            self.last_error = "missing token or chat_id"
            return False
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/getMe"
            r = requests.get(url, timeout=10)
            if r.status_code == 200 and r.json().get("ok"):
                self.last_status = "ONLINE"
                self.last_error = ""
                return True
            self.last_status = "OFFLINE"
            self.last_error = r.text[:200]
            return False
        except Exception as e:
            self.last_status = "OFFLINE"
            self.last_error = str(e)
            return False

    def notify_trade(
        self,
        event: str,
        symbol: str,
        side: str,
        quantity: str,
        price: str,
        order_id: str,
        client_id: str = "",
        status: str = "",
        realized_pnl: Optional[float] = None,
        mode: str = "PAPER",
        extra: str = "",
    ) -> None:
        """Queue a trade notification with deduplication."""
        key_src = f"{event}|{order_id}|{status}|{quantity}|{price}"
        key = hashlib.sha256(key_src.encode()).hexdigest()[:24]
        if key in self._sent_keys:
            return
        self._sent_keys.add(key)
        # prevent unbounded growth
        if len(self._sent_keys) > 5000:
            self._sent_keys = set(list(self._sent_keys)[-2000:])

        emoji = {
            "BUY_SUBMITTED": "🟢",
            "BUY_PARTIAL": "🟡",
            "BUY_FILLED": "🟢",
            "SELL_SUBMITTED": "🔴",
            "SELL_PARTIAL": "🟡",
            "SELL_FILLED": "🔴",
            "CANCELED": "⚪",
            "REJECTED": "⛔",
            "EXPIRED": "⚪",
            "UNKNOWN": "⚠️",
            "RECONCILE": "🔄",
        }.get(event, "ℹ️")

        lines = [
            f"{emoji} {event.replace('_', ' ')}",
            "",
            f"Symbol: {symbol}",
            f"Quantity: {quantity}",
            f"Execution Price: {price}",
            f"Order ID: {order_id}",
        ]
        if client_id:
            lines.append(f"Client ID: {client_id}")
        if status:
            lines.append(f"Status: {status}")
        if realized_pnl is not None:
            lines.append(f"Realized P&L: {realized_pnl:+.4f}")
        lines.append(f"Mode: {mode}")
        if extra:
            lines.append(extra)
        lines.append(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        text = "\n".join(lines)
        self._queue.put(text)

    def notify_system(self, title: str, detail: str = "") -> None:
        text = f"ℹ️ {title}\n{detail}".strip()
        self._queue.put(text)

    def _worker(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                text = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            ok = self._send(text)
            if ok:
                backoff = 1.0
            else:
                # re-queue with backoff
                time.sleep(backoff)
                self._queue.put(text)
                backoff = min(backoff * 2, 60.0)

    def _send(self, text: str) -> bool:
        if not self.bot_token or not self.chat_id:
            return False
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            r = requests.post(
                url,
                json={"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True},
                timeout=15,
            )
            if r.status_code == 200:
                self.last_status = "ONLINE"
                self.last_notification_ts = time.time()
                self.last_error = ""
                return True
            self.last_error = r.text[:200]
            return False
        except Exception as e:
            self.last_error = str(e)
            return False
