"""User data stream via POST /open/v1/user-listen-token + WS API subscribe.

Documented Tokocrypto flow (PDF):
  REST: POST /open/v1/user-listen-token (SIGNED) → {token, expirationTime}
  WS API base: wss://ws-api.tokocrypto.site:443/ws-api/v3
  Method: userDataStream.subscribe.listenToken
  Tokens do NOT auto-renew; renew before expirationTime
  On expiry without renewal → eventStreamTerminated

REST reconciliation remains authority for fills/balances.
This stream is an accelerator for event-driven updates — never sole source of truth.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_REST_BASE = "https://www.tokocrypto.com"
DEFAULT_WS_API = "wss://ws-api.tokocrypto.site:443/ws-api/v3"
DEFAULT_RECV_WINDOW = 5000
RENEW_BEFORE_SEC = 600.0  # renew 10 minutes before expiry
MAX_BACKOFF_SEC = 60.0


@dataclass
class UserStreamHealth:
    connected: bool = False
    token_expires_at_ms: int = 0
    last_event_ts: float = 0.0
    last_error: str = ""
    reconnects: int = 0
    terminated: bool = False

    def token_valid(self) -> bool:
        if self.token_expires_at_ms <= 0:
            return False
        return time.time() * 1000 < float(self.token_expires_at_ms)

    def is_healthy(self, *, max_event_age_sec: float = 120.0) -> bool:
        if not self.connected or self.terminated:
            return False
        if not self.token_valid():
            return False
        if self.last_event_ts <= 0:
            return True
        return (time.time() - self.last_event_ts) <= max_event_age_sec


class UserListenTokenClient:
    """SIGNED REST helper for listen token create (no order endpoints)."""

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        rest_base: str = DEFAULT_REST_BASE,
        recv_window: int = DEFAULT_RECV_WINDOW,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.rest_base = rest_base.rstrip("/")
        self.recv_window = min(int(recv_window), 60000)

    def create_listen_token(self) -> tuple[str, int]:
        """Returns (token, expirationTime_ms). Raises on failure."""
        path = "/open/v1/user-listen-token"
        params: dict[str, Any] = {
            "timestamp": int(time.time() * 1000),
            "recvWindow": self.recv_window,
        }
        query = urllib.parse.urlencode(params)
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        url = f"{self.rest_base}{path}?{query}&signature={signature}"
        req = urllib.request.Request(
            url,
            method="POST",
            headers={
                "X-MBX-APIKEY": self.api_key,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data=b"",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exp:
            err_body = exp.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"user-listen-token HTTP {exp.code}: {err_body}") from exp
        except urllib.error.URLError as exp:
            raise RuntimeError(f"user-listen-token network: {exp}") from exp

        payload = json.loads(body)
        if int(payload.get("code", -1)) != 0:
            raise RuntimeError(f"user-listen-token rejected: {payload}")
        data = payload.get("data") or {}
        token = str(data.get("token") or "")
        exp_ms = int(data.get("expirationTime") or 0)
        if not token or exp_ms <= 0:
            raise RuntimeError(f"user-listen-token malformed: {payload}")
        return token, exp_ms


class UserDataStream:
    """Background WS API session for user events + token renewal."""

    def __init__(
        self,
        token_client: UserListenTokenClient,
        *,
        ws_api_url: str = DEFAULT_WS_API,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.token_client = token_client
        self.ws_api_url = ws_api_url
        self.on_event = on_event
        self.health = UserStreamHealth()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._token = ""
        self._ws: Any = None
        self._req_id = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, name="tko-user-stream", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:  # noqa: BLE001,S110
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        self.health.connected = False

    def _next_id(self) -> str:
        self._req_id += 1
        return f"tko-uds-{self._req_id}-{int(time.time())}"

    def _run_loop(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            try:
                self._token, exp_ms = self.token_client.create_listen_token()
                self.health.token_expires_at_ms = exp_ms
                self.health.terminated = False
                logger.info(
                    "event=user_listen_token_ok expires_in_sec=%.0f",
                    max(0, (exp_ms / 1000.0) - time.time()),
                )
                self._session_once()
                attempt = 0
            except Exception as exp:  # noqa: BLE001
                self.health.connected = False
                self.health.last_error = f"{type(exp).__name__}:{exp}"
                logger.warning("event=user_stream_error err=%s", self.health.last_error[:200])
            if self._stop.is_set():
                break
            attempt += 1
            self.health.reconnects += 1
            backoff = min(MAX_BACKOFF_SEC, (2 ** min(attempt, 6)) + random.uniform(0, 1.5))
            self._stop.wait(backoff)

    def _session_once(self) -> None:
        try:
            import websocket  # type: ignore
        except ImportError as exp:
            raise RuntimeError("websocket-client required for UserDataStream") from exp

        renew_at = (self.health.token_expires_at_ms / 1000.0) - RENEW_BEFORE_SEC

        def on_message(_ws: Any, message: str) -> None:
            self.health.last_event_ts = time.time()
            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                return
            event = payload.get("event") if isinstance(payload, dict) else None
            if isinstance(event, dict) and event.get("e") == "eventStreamTerminated":
                self.health.terminated = True
                self.health.connected = False
                logger.warning("event=user_stream_terminated — will renew token")
                try:
                    _ws.close()
                except Exception:  # noqa: BLE001,S110
                    pass
                return
            if self.on_event and isinstance(payload, dict):
                try:
                    self.on_event(payload)
                except Exception as exp:  # noqa: BLE001
                    logger.warning("event=user_stream_handler_error err=%s", exp)

        def on_open(ws: Any) -> None:
            self.health.connected = True
            sub = {
                "id": self._next_id(),
                "method": "userDataStream.subscribe.listenToken",
                "params": {"listenToken": self._token},
            }
            ws.send(json.dumps(sub))
            logger.info("event=user_stream_subscribe_sent")

        def on_error(_ws: Any, error: Exception) -> None:
            self.health.last_error = str(error)[:200]
            self.health.connected = False

        def on_close(_ws: Any, *_a: Any) -> None:
            self.health.connected = False

        ws = websocket.WebSocketApp(
            self.ws_api_url,
            on_message=on_message,
            on_open=on_open,
            on_error=on_error,
            on_close=on_close,
        )
        self._ws = ws

        def _renew_watch() -> None:
            while not self._stop.is_set() and self.health.connected and not self.health.terminated:
                if time.time() >= renew_at:
                    logger.info("event=user_stream_pre_expiry_renew")
                    try:
                        ws.close()
                    except Exception:  # noqa: BLE001,S110
                        pass
                    break
                self._stop.wait(5.0)

        watcher = threading.Thread(target=_renew_watch, name="tko-token-renew", daemon=True)
        watcher.start()
        ws.run_forever(ping_interval=20, ping_timeout=10)
