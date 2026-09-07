"""WebSocket market data + user data stream for Tokocrypto (Win7 compatible)."""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

import websocket  # websocket-client

logger = logging.getLogger(__name__)

# MBX style streams (symbolType=1)
STREAM_BASE = "wss://stream-cloud.tokocrypto.site/stream"


class MarketWebSocket:
    """Combined trade / depth / ticker stream with auto-reconnect."""

    def __init__(
        self,
        symbols: List[str],
        on_message: Optional[Callable[[Dict], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
        on_close: Optional[Callable[[], None]] = None,
        on_open: Optional[Callable[[], None]] = None,
    ):
        self.symbols = [s.replace("_", "").lower() for s in symbols]
        self.on_message = on_message
        self.on_error = on_error
        self.on_close = on_close
        self.on_open = on_open
        self._ws: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._connected = False
        self._last_msg_ts = 0.0
        self._reconnect_delay = 1.0
        self._max_reconnect_delay = 60.0

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def last_message_age(self) -> float:
        if self._last_msg_ts == 0:
            return float("inf")
        return time.time() - self._last_msg_ts

    def _build_url(self) -> str:
        # Combined stream example: /stream?streams=btcusdt@trade/btcusdt@depth
        streams = []
        for s in self.symbols:
            streams.append(f"{s}@trade")
            streams.append(f"{s}@depth")
            streams.append(f"{s}@ticker")
        stream_param = "/".join(streams)
        return f"{STREAM_BASE}?streams={stream_param}"

    def _on_open(self, ws):
        self._connected = True
        self._reconnect_delay = 1.0
        logger.info("Market WebSocket opened")
        if self.on_open:
            self.on_open()

    def _on_message(self, ws, message: str):
        self._last_msg_ts = time.time()
        try:
            data = json.loads(message)
            if self.on_message:
                self.on_message(data)
        except Exception as e:
            logger.warning("WS message parse error: %s", e)

    def _on_error(self, ws, error):
        self._connected = False
        logger.error("Market WebSocket error: %s", error)
        if self.on_error:
            self.on_error(error if isinstance(error, Exception) else Exception(str(error)))

    def _on_close(self, ws, close_status_code, close_msg):
        self._connected = False
        logger.warning("Market WebSocket closed: %s %s", close_status_code, close_msg)
        if self.on_close:
            self.on_close()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=5)

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            url = self._build_url()
            self._ws = websocket.WebSocketApp(
                url,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )
            try:
                self._ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as e:
                logger.exception("WS run_forever exception: %s", e)
            if self._stop.is_set():
                break
            logger.info("Reconnecting market WS in %.1fs", self._reconnect_delay)
            time.sleep(self._reconnect_delay)
            self._reconnect_delay = min(self._reconnect_delay * 2, self._max_reconnect_delay)


class UserDataStream:
    """User data stream using listenToken or listenKey."""

    def __init__(
        self,
        listen_key_or_token: str,
        on_message: Optional[Callable[[Dict], None]] = None,
        use_token: bool = True,
    ):
        self.listen_key = listen_key_or_token
        self.on_message = on_message
        self.use_token = use_token
        self._ws: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._connected = False
        self._last_msg_ts = 0.0

    @property
    def connected(self) -> bool:
        return self._connected

    def _url(self) -> str:
        # Legacy listenKey style
        return f"{STREAM_BASE}/ws/{self.listen_key}"

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass

    def _run(self) -> None:
        while not self._stop.is_set():
            self._ws = websocket.WebSocketApp(
                self._url(),
                on_open=lambda ws: setattr(self, "_connected", True),
                on_message=self._on_msg,
                on_error=lambda ws, e: logger.error("UserData WS error: %s", e),
                on_close=lambda ws, *a: setattr(self, "_connected", False),
            )
            try:
                self._ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as e:
                logger.exception("UserData run_forever: %s", e)
            if self._stop.is_set():
                break
            time.sleep(3)

    def _on_msg(self, ws, message: str):
        self._last_msg_ts = time.time()
        try:
            data = json.loads(message)
            if self.on_message:
                self.on_message(data)
        except Exception as e:
            logger.warning("UserData parse error: %s", e)
