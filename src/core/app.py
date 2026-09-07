"""Main application controller - wires all subsystems."""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.execution.filters import SymbolFilters, parse_filters
from src.execution.manager import ExecutionManager
from src.ml.sklearn_model import SklearnModel
from src.risk.engine import RiskEngine, RiskLimits
from src.telegram.notifier import TelegramNotifier
from src.tokocrypto.rest import RestClient
from src.tokocrypto.websocket import MarketWebSocket, UserDataStream
from src.utils.secure_config import SecureConfig

logger = logging.getLogger(__name__)


class AppController:
    def __init__(self, config_dir: Optional[Path] = None):
        self.config = SecureConfig(config_dir)
        self.config.load()
        self.risk = RiskEngine(RiskLimits())
        self.rest = RestClient()
        self.exec_mgr = ExecutionManager(self.rest, self.risk, mode="PAPER")
        self.tg = TelegramNotifier()
        self.ml: Optional[SklearnModel] = None
        self.ws: Optional[MarketWebSocket] = None
        self.user_stream: Optional[UserDataStream] = None
        self.bot_running = False
        self.logs: List[str] = []
        self.market_snapshot: Dict[str, Any] = {}
        self.balance_snapshot: Dict[str, Any] = {}
        self.symbol = "BTC_USDT"
        self._lock = threading.Lock()
        self._load_ml()

    def _log(self, msg: str, level: str = "INFO") -> None:
        line = f"{time.strftime('%H:%M:%S')} [{level}] {msg}"
        self.logs.append(line)
        if len(self.logs) > 200:
            self.logs = self.logs[-100:]
        logger.log(getattr(logging, level, logging.INFO), msg)

    def _load_ml(self) -> None:
        model_dir = Path(__file__).resolve().parents[2] / "models" / "default"
        self.ml = SklearnModel()
        if model_dir.exists():
            ok = self.ml.load(str(model_dir))
            self._log(f"ML load: {'OK' if ok else 'FAILED/missing'}")
        else:
            self._log("No ML model present - running without ML signals")

    def save_config(
        self,
        api_key: str,
        api_secret: str,
        tg_token: str,
        tg_chat: str,
        mode: str = "PAPER",
    ) -> None:
        data = self.config.load()
        if api_key and not api_key.startswith("*"):
            data["api_key"] = api_key
        if api_secret and not api_secret.startswith("*"):
            data["api_secret"] = api_secret
        if tg_token and not tg_token.startswith("*"):
            data["tg_token"] = tg_token
        if tg_chat:
            data["tg_chat"] = tg_chat
        data["mode"] = mode
        self.config.save(data)
        # apply
        self.rest.api_key = data.get("api_key", "")
        self.rest.api_secret = data.get("api_secret", "")
        self.tg.configure(data.get("tg_token", ""), data.get("tg_chat", ""))
        self.exec_mgr.set_mode(mode)
        self._log("Config saved")

    def test_tokocrypto(self) -> tuple:
        data = self.config.load()
        self.rest.api_key = data.get("api_key", "")
        self.rest.api_secret = data.get("api_secret", "")
        try:
            body = self.rest.server_time()
            if body.get("success") or "timestamp" in str(body) or "serverTime" in str(body) or body:
                return True, f"Server responded: {str(body)[:120]}"
            return False, str(body)[:200]
        except Exception as e:
            return False, str(e)

    def test_telegram(self) -> bool:
        data = self.config.load()
        self.tg.configure(data.get("tg_token", ""), data.get("tg_chat", ""))
        return self.tg.test_connection()

    def start_bot(self, mode: str = "PAPER") -> None:
        if self.bot_running:
            return
        data = self.config.load()
        self.rest.api_key = data.get("api_key", "")
        self.rest.api_secret = data.get("api_secret", "")
        self.tg.configure(data.get("tg_token", ""), data.get("tg_chat", ""))
        self.tg.start()
        try:
            self.exec_mgr.set_mode(mode)
        except Exception as e:
            self._log(str(e), "ERROR")
            return
        self.bot_running = True
        self.risk.ws_connected = True
        self._log(f"Bot started in {mode}")
        self.tg.notify_system("Bot started", f"Mode={mode}")

        # load symbols / filters (best effort)
        try:
            info = self.rest.exchange_info()
            symbols = info.get("data") or info.get("symbols") or []
            if isinstance(symbols, list):
                for s in symbols:
                    if isinstance(s, dict) and s.get("symbol") == self.symbol:
                        sf = parse_filters(s)
                        self.exec_mgr.filters[self.symbol] = sf
                        break
        except Exception as e:
            self._log(f"exchange_info failed: {e}", "WARN")

        # start market WS
        self.ws = MarketWebSocket(
            [self.symbol],
            on_message=self._on_market_msg,
            on_error=lambda e: self._log(f"WS error: {e}", "ERROR"),
            on_close=lambda: setattr(self.risk, "ws_connected", False),
            on_open=lambda: setattr(self.risk, "ws_connected", True),
        )
        self.ws.start()

        # user stream if credentials present
        if self.rest.api_key and self.rest.api_secret:
            try:
                st, body = self.rest.create_listen_token()
                token = None
                if st.value == "ACK":
                    data_body = body.get("data") or body
                    token = data_body.get("listenToken") or data_body.get("listenKey")
                if not token:
                    st, body = self.rest.create_listen_key()
                    data_body = body.get("data") or body
                    token = data_body.get("listenKey")
                if token:
                    self.user_stream = UserDataStream(token, on_message=self._on_user_msg)
                    self.user_stream.start()
                    self._log("User data stream started")
            except Exception as e:
                self._log(f"User stream failed: {e}", "WARN")

    def stop_bot(self) -> None:
        self.bot_running = False
        if self.ws:
            self.ws.stop()
        if self.user_stream:
            self.user_stream.stop()
        self._log("Bot stopped")
        self.tg.notify_system("Bot stopped")

    def kill_switch(self) -> None:
        self.risk.activate_kill_switch("UI emergency")
        self.stop_bot()
        self.tg.notify_system("EMERGENCY KILL SWITCH ACTIVATED")

    def _on_market_msg(self, data: Dict) -> None:
        self.risk.update_market_ts(self.symbol)
        # simplified parse
        stream = data.get("stream", "")
        payload = data.get("data") or data
        with self._lock:
            if "c" in payload:  # ticker last price
                self.market_snapshot["last"] = payload.get("c")
                self.market_snapshot["bid"] = payload.get("b")
                self.market_snapshot["ask"] = payload.get("a")
            self.market_snapshot["symbol"] = self.symbol
            self.market_snapshot["status"] = "LIVE" if self.risk.ws_connected else "DISCONNECTED"
            self.market_snapshot["raw_ts"] = time.time()

    def _on_user_msg(self, data: Dict) -> None:
        # order update events → telegram notification only on real execution
        etype = data.get("e") or data.get("eventType") or ""
        if "execution" in str(etype).lower() or "order" in str(etype).lower() or "ORDER" in str(data):
            self._log(f"User event: {str(data)[:120]}")
            # extract and notify (best-effort)
            order_id = str(data.get("i") or data.get("orderId") or "")
            side = str(data.get("S") or data.get("side") or "")
            status = str(data.get("X") or data.get("status") or "")
            qty = str(data.get("l") or data.get("executedQty") or "")
            price = str(data.get("L") or data.get("price") or "")
            symbol = str(data.get("s") or data.get("symbol") or self.symbol)
            event = "BUY_FILLED" if "BUY" in side.upper() else "SELL_FILLED"
            if "PARTIAL" in status.upper():
                event = event.replace("FILLED", "PARTIAL")
            self.tg.notify_trade(
                event=event,
                symbol=symbol,
                side=side,
                quantity=qty,
                price=price,
                order_id=order_id,
                status=status,
                mode=self.exec_mgr.mode,
            )

    def get_snapshot(self) -> Dict[str, Any]:
        conn = "ONLINE" if (self.ws and self.ws.connected) else ("DISCONNECTED" if self.bot_running else "IDLE")
        ml_status = "LOADED" if (self.ml and self.ml.is_loaded) else "MISSING"
        risk_st = self.risk.check(self.symbol, 0, 0, 1000)
        return {
            "connection": conn,
            "mode": self.exec_mgr.mode,
            "bot_status": "RUNNING" if self.bot_running else ("KILL" if self.risk.kill_switch else "STOPPED"),
            "balance": self.balance_snapshot or {"total": "—", "available": "—", "locked": "—"},
            "market": {
                "symbol": self.symbol,
                "last": self.market_snapshot.get("last", "—"),
                "bid": self.market_snapshot.get("bid", "—"),
                "ask": self.market_snapshot.get("ask", "—"),
                "spread": "—",
                "change": "—",
                "status": self.market_snapshot.get("status", "—"),
            },
            "signal": {
                "signal": "WAIT",
                "prob": "—",
                "model": self.ml.name if self.ml else "none",
                "version": self.ml.version if self.ml else "—",
                "model_status": ml_status,
                "last_pred": "—",
            },
            "positions": [],
            "orders": [f"{o.client_id[:12]} {o.state.value} {o.symbol}" for o in list(self.exec_mgr.orders.values())[-5:]],
            "risk": {
                "daily_pnl": self.risk.daily_pnl,
                "exposure": sum(self.risk.positions.values()),
                "status": "OK" if risk_st.allowed else risk_st.reason,
                "circuit": self.risk.circuit_breaker,
                "kill": self.risk.kill_switch,
            },
            "system": {
                "rest": "OK",
                "ws": "CONNECTED" if (self.ws and self.ws.connected) else "DOWN",
                "user_stream": "CONNECTED" if (self.user_stream and self.user_stream.connected) else "DOWN",
                "clock_offset": f"{self.risk.clock_offset_ms:.0f}ms",
                "rate": "—",
            },
            "telegram": {
                "status": self.tg.last_status,
                "last": time.strftime("%H:%M:%S", time.localtime(self.tg.last_notification_ts)) if self.tg.last_notification_ts else "—",
                "errors": self.tg.last_error or "—",
            },
            "logs": self.logs[-12:],
        }
