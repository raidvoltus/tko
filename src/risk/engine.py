"""Risk Engine - mandatory gate before any order submission."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class RiskLimits:
    max_order_value_usdt: float = 100.0
    max_position_usdt: float = 500.0
    max_exposure_usdt: float = 1000.0
    max_daily_loss_usdt: float = 50.0
    max_consecutive_losses: int = 5
    min_balance_usdt: float = 10.0
    max_stale_market_sec: float = 30.0
    max_clock_drift_ms: float = 3000.0
    max_api_errors_window: int = 10
    api_error_window_sec: float = 60.0


@dataclass
class RiskStatus:
    allowed: bool
    reason: str = ""
    blocked_by: List[str] = field(default_factory=list)
    daily_pnl: float = 0.0
    consecutive_losses: int = 0
    exposure: float = 0.0
    circuit_breaker: bool = False
    kill_switch: bool = False
    market_stale: bool = False
    ws_disconnected: bool = False
    orderbook_desync: bool = False
    clock_drift: bool = False


class RiskEngine:
    def __init__(self, limits: Optional[RiskLimits] = None):
        self.limits = limits or RiskLimits()
        self.kill_switch = False
        self.circuit_breaker = False
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        self.day_start_ts = time.time()
        self.api_errors: List[float] = []
        self.positions: Dict[str, float] = {}  # symbol -> notional
        self.last_market_ts: Dict[str, float] = {}
        self.ws_connected = True
        self.orderbook_synced = True
        self.clock_offset_ms = 0.0

    def activate_kill_switch(self, reason: str = "manual") -> None:
        self.kill_switch = True
        logger.critical("KILL SWITCH ACTIVATED: %s", reason)

    def reset_kill_switch(self) -> None:
        self.kill_switch = False
        logger.warning("Kill switch reset")

    def trip_circuit_breaker(self, reason: str) -> None:
        self.circuit_breaker = True
        logger.error("Circuit breaker tripped: %s", reason)

    def reset_circuit_breaker(self) -> None:
        self.circuit_breaker = False

    def record_api_error(self) -> None:
        now = time.time()
        self.api_errors.append(now)
        cutoff = now - self.limits.api_error_window_sec
        self.api_errors = [t for t in self.api_errors if t >= cutoff]
        if len(self.api_errors) >= self.limits.max_api_errors_window:
            self.trip_circuit_breaker("too many API errors")

    def update_market_ts(self, symbol: str) -> None:
        self.last_market_ts[symbol] = time.time()

    def update_pnl(self, realized_delta: float) -> None:
        self.daily_pnl += realized_delta
        if realized_delta < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

    def check(
        self,
        symbol: str,
        side: int,
        notional: float,
        available_balance: float,
        current_position_notional: float = 0.0,
    ) -> RiskStatus:
        blocked: List[str] = []

        if self.kill_switch:
            blocked.append("KILL_SWITCH")
        if self.circuit_breaker:
            blocked.append("CIRCUIT_BREAKER")
        if not self.ws_connected:
            blocked.append("WS_DISCONNECTED")
        if not self.orderbook_synced:
            blocked.append("ORDERBOOK_DESYNC")

        # market stale
        last = self.last_market_ts.get(symbol, 0)
        market_stale = (time.time() - last) > self.limits.max_stale_market_sec if last else True
        if market_stale:
            blocked.append("MARKET_STALE")

        # clock drift
        clock_drift = abs(self.clock_offset_ms) > self.limits.max_clock_drift_ms
        if clock_drift:
            blocked.append("CLOCK_DRIFT")

        if notional > self.limits.max_order_value_usdt:
            blocked.append("MAX_ORDER_VALUE")
        if current_position_notional + notional > self.limits.max_position_usdt:
            blocked.append("MAX_POSITION")
        total_exp = sum(self.positions.values()) + notional
        if total_exp > self.limits.max_exposure_usdt:
            blocked.append("MAX_EXPOSURE")
        if self.daily_pnl <= -self.limits.max_daily_loss_usdt:
            blocked.append("MAX_DAILY_LOSS")
        if self.consecutive_losses >= self.limits.max_consecutive_losses:
            blocked.append("MAX_CONSECUTIVE_LOSSES")
        if available_balance < self.limits.min_balance_usdt:
            blocked.append("MIN_BALANCE")

        allowed = len(blocked) == 0
        reason = ";".join(blocked) if blocked else "OK"
        return RiskStatus(
            allowed=allowed,
            reason=reason,
            blocked_by=blocked,
            daily_pnl=self.daily_pnl,
            consecutive_losses=self.consecutive_losses,
            exposure=sum(self.positions.values()),
            circuit_breaker=self.circuit_breaker,
            kill_switch=self.kill_switch,
            market_stale=market_stale,
            ws_disconnected=not self.ws_connected,
            orderbook_desync=not self.orderbook_synced,
            clock_drift=clock_drift,
        )
