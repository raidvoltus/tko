"""Telegram notifier — secrets never logged; no order authority."""
from __future__ import annotations

import hashlib
import logging
import queue
import threading
import time
from typing import Optional

import requests

from src.security.redact import redact

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, bot_token: str = "", chat_id: str = ""):
        self.bot_token = bot_token
        self.chat_id = str(chat_id) if chat_id else ""
        self.last_status = "NOT_CONFIGURED"
        self.last_error = ""
        self.last_notification_ts = 0.0
        self._queue: queue.Queue = queue.Queue(maxsize=100)
        self._sent_keys: set = set()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.allowed_chat_ids: set = set()
        if self.chat_id:
            self.allowed_chat_ids.add(self.chat_id)

    def configure(self, bot_token: str, chat_id: str) -> None:
        self.bot_token = bot_token or ""
        self.chat_id = str(chat_id) if chat_id else ""
        self.allowed_chat_ids = {self.chat_id} if self.chat_id else set()
        if self.bot_token and self.chat_id:
            self.last_status = "CONFIGURED"
        else:
            self.last_status = "NOT_CONFIGURED"

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
            self.last_status = "NOT_CONFIGURED"
            self.last_error = "missing token or chat_id"
            return False
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/getMe"
            r = requests.get(url, timeout=10)
            if r.status_code == 200 and r.json().get("ok"):
                self.last_status = "CONNECTED"
                self.last_error = ""
                return True
            self.last_status = "ERROR"
            self.last_error = redact(r.text[:200])
            return False
        except Exception as e:
            self.last_status = "ERROR"
            self.last_error = redact(str(e))
            return False

    def is_authorized(self, chat_id: str) -> bool:
        return str(chat_id) in self.allowed_chat_ids

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
        mode: str = "LIVE",
        extra: str = "",
    ) -> None:
        key_src = f"{event}|{order_id}|{status}|{quantity}|{price}"
        key = hashlib.sha256(key_src.encode()).hexdigest()[:24]
        if key in self._sent_keys:
            return
        self._sent_keys.add(key)
        if len(self._sent_keys) > 500:
            self._sent_keys = set(list(self._sent_keys)[-200:])
        pnl_s = f" pnl={realized_pnl}" if realized_pnl is not None else ""
        text = (
            f"[TKO {mode}] {event} {symbol} {side} qty={quantity} px={price} "
            f"id={order_id}{pnl_s} {status} {extra}"
        ).strip()
        self._enqueue(text)

    def notify_system(self, title: str, detail: str = "") -> None:
        self._enqueue(f"[TKO] {title}\n{redact(detail)}".strip())

    def notify_account_status(self, account_dict: dict) -> None:
        """Authoritative account state only — no fabricated balances."""
        status = account_dict.get("status", "UNKNOWN")
        if status not in ("VALID",):
            self._enqueue(f"[TKO] ACCOUNT STATE: {status}")
            return
        lines = [f"[TKO] ACCOUNT {status}"]
        for h in account_dict.get("holdings") or []:
            lines.append(
                f"{h.get('asset')}: free={h.get('free')} locked={h.get('locked')} total={h.get('total')}"
            )
        self._enqueue("\n".join(lines)[:3500])

    def _enqueue(self, text: str) -> None:
        try:
            self._queue.put_nowait(redact(text)[:4000])
        except queue.Full:
            logger.warning("Telegram queue full — drop message")

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
                time.sleep(backoff)
                try:
                    self._queue.put_nowait(text)
                except queue.Full:
                    pass
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
                self.last_status = "CONNECTED"
                self.last_notification_ts = time.time()
                self.last_error = ""
                return True
            self.last_status = "ERROR"
            self.last_error = redact(r.text[:200])
            return False
        except Exception as e:
            self.last_status = "ERROR"
            self.last_error = redact(str(e))
            return False
