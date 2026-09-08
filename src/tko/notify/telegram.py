"""Simple Telegram notifier (sync)."""

from __future__ import annotations

import logging
import urllib.error
import urllib.parse
import urllib.request

from tko.core.credentials import TelegramCredentials

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, creds: TelegramCredentials | None) -> None:
        self._creds = creds

    @property
    def enabled(self) -> bool:
        return self._creds is not None

    def send(self, text: str) -> bool:
        if not self._creds:
            return False
        token = self._creds.bot_token.get_secret_value()
        chat_id = self._creds.chat_id
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode(
            {"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": "1"}
        ).encode()
        try:
            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=15) as resp:
                return 200 <= resp.status < 300
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning("Telegram send failed: %s", type(exc).__name__)
            return False
