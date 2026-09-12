"""Simple Telegram notifier (sync) + optional /kill command poll."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from tko.core.credentials import TelegramCredentials

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, creds: TelegramCredentials | None, *, audit: object | None = None) -> None:
        self._creds = creds
        self._audit = audit
        self._offset: int | None = None

    @property
    def enabled(self) -> bool:
        return self._creds is not None

    def send(self, text: str) -> bool:
        if not self._creds:
            return False
        if self._audit is not None:
            try:
                self._audit.record("NOTIFY", reason=text[:200])  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001,S110
                pass
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

    def poll_kill_command(self, kill_path: Path) -> bool:
        if not self._creds:
            return False
        token = self._creds.bot_token.get_secret_value()
        allowed = str(self._creds.chat_id)
        url = f"https://api.telegram.org/bot{token}/getUpdates"
        params: dict[str, str | int] = {"timeout": 0, "limit": 20}
        if self._offset is not None:
            params["offset"] = self._offset
        qs = urllib.parse.urlencode(params)
        try:
            req = urllib.request.Request(f"{url}?{qs}", method="GET")
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.debug("telegram poll failed: %s", exc)
            return False
        if not body.get("ok"):
            return False
        killed = False
        for upd in body.get("result") or []:
            self._offset = int(upd["update_id"]) + 1
            msg = upd.get("message") or upd.get("edited_message") or {}
            chat = msg.get("chat") or {}
            chat_id = str(chat.get("id") or "")
            text = (msg.get("text") or "").strip()
            if chat_id != allowed:
                continue
            if text.lower().startswith("/kill"):
                kill_path.parent.mkdir(parents=True, exist_ok=True)
                kill_path.write_text("telegram /kill", encoding="utf-8")
                logger.warning("event=telegram_kill_received chat_id=%s", chat_id)
                self.send("TKO: kill switch activated via /kill")
                if self._audit is not None:
                    try:
                        self._audit.record("KILL_SWITCH", reason="telegram_/kill")  # type: ignore[attr-defined]
                    except Exception:  # noqa: BLE001,S110
                        pass
                killed = True
        return killed
