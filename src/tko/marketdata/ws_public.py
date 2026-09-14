"""Public market-data WebSocket with heartbeat, reconnect, stale detection.

Tokocrypto WSS (symbolType=1 MBX style):
  base: wss://stream-cloud.tokocrypto.site/stream
  combined: /stream?streams=<s1>/<s2>
  raw: /ws/<streamName>

This module is the preferred live market path. REST remains recovery/snapshot only.
No credentials. No order endpoints.
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import quote

logger = logging.getLogger(__name__)

DEFAULT_PUBLIC_WS_BASE = "wss://stream-cloud.tokocrypto.site"
DEFAULT_STALE_SEC = 15.0
DEFAULT_PING_INTERVAL_SEC = 20.0
MAX_BACKOFF_SEC = 60.0


@dataclass
class StreamHealth:
    connected: bool = False
    last_message_ts: float = 0.0
    last_error: str = ""
    reconnects: int = 0
    subscriptions: list[str] = field(default_factory=list)

    def is_fresh(self, *, max_age_sec: float = DEFAULT_STALE_SEC) -> bool:
        if not self.connected or self.last_message_ts <= 0:
            return False
        return (time.time() - self.last_message_ts) <= max_age_sec


def _symbol_stream_name(symbol: str, channel: str = "ticker") -> str:
    """BTC/USDT -> btcusdt@ticker (Tokocrypto MBX-style lowercase no separator)."""
    s = symbol.replace("/", "").replace("_", "").lower()
    return f"{s}@{channel}"


class PublicMarketStream:
    """Threaded public WS client. Fail-closed: stale => not healthy."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_PUBLIC_WS_BASE,
        stale_sec: float = DEFAULT_STALE_SEC,
        on_message: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.stale_sec = stale_sec
        self.on_message = on_message
        self._streams: list[str] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self.health = StreamHealth()
        self._last_prices: dict[str, float] = {}
        self._ws: Any = None

    def subscribe_symbols(self, symbols: list[str], *, channel: str = "ticker") -> None:
        names = [_symbol_stream_name(s, channel) for s in symbols if s]
        with self._lock:
            for n in names:
                if n not in self._streams:
                    self._streams.append(n)
            self.health.subscriptions = list(self._streams)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, name="tko-ws-public", daemon=True)
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

    def last_price(self, symbol: str) -> float | None:
        key = symbol.replace("/", "").replace("_", "").upper()
        with self._lock:
            for k, v in self._last_prices.items():
                if k.replace("/", "").replace("_", "").upper() == key:
                    return v
            return self._last_prices.get(symbol)

    def _combined_url(self) -> str:
        with self._lock:
            streams = list(self._streams)
        if not streams:
            streams = ["!miniTicker@arr"]
        joined = "/".join(quote(s, safe="@") for s in streams)
        return f"{self.base_url}/stream?streams={joined}"

    def _run_loop(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            try:
                self._connect_once()
                attempt = 0
            except Exception as exp:  # noqa: BLE001
                self.health.connected = False
                self.health.last_error = f"{type(exp).__name__}:{exp}"
                logger.warning("event=ws_public_error err=%s", self.health.last_error[:200])
            if self._stop.is_set():
                break
            attempt += 1
            self.health.reconnects += 1
            backoff = min(MAX_BACKOFF_SEC, (2 ** min(attempt, 6)) + random.uniform(0, 1.5))
            logger.info("event=ws_public_reconnect backoff=%.1fs attempt=%d", backoff, attempt)
            self._stop.wait(backoff)

    def _connect_once(self) -> None:
        try:
            import websocket  # type: ignore
        except ImportError as exp:
            raise RuntimeError(
                "websocket-client package required for PublicMarketStream "
                "(pip install websocket-client)"
            ) from exp

        url = self._combined_url()
        logger.info("event=ws_public_connecting url=%s streams=%d", url.split("?")[0], len(self._streams))

        def on_message(_ws: Any, message: str) -> None:
            self.health.last_message_ts = time.time()
            self.health.connected = True
            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                return
            data = payload.get("data", payload) if isinstance(payload, dict) else payload
            if isinstance(data, dict):
                self._ingest_ticker(data)
                if self.on_message:
                    try:
                        self.on_message(data)
                    except Exception as exp:  # noqa: BLE001
                        logger.warning("event=ws_on_message_handler_error err=%s", exp)

        def on_error(_ws: Any, error: Exception) -> None:
            self.health.last_error = str(error)[:200]
            self.health.connected = False

        def on_close(_ws: Any, *_args: Any) -> None:
            self.health.connected = False

        def on_open(_ws: Any) -> None:
            self.health.connected = True
            self.health.last_message_ts = time.time()
            logger.info("event=ws_public_open")

        ws = websocket.WebSocketApp(
            url,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open,
        )
        self._ws = ws
        ws.run_forever(ping_interval=DEFAULT_PING_INTERVAL_SEC, ping_timeout=10)

    def _ingest_ticker(self, data: dict[str, Any]) -> None:
        sym = str(data.get("s") or data.get("symbol") or "")
        price = data.get("c") or data.get("lastPrice") or data.get("p")
        if not sym or price is None:
            return
        try:
            px = float(price)
        except (TypeError, ValueError):
            return
        if px <= 0:
            return
        with self._lock:
            self._last_prices[sym] = px
