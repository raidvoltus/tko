"""
Risk Engine — absolute authority before any order submission.

Research-aligned controls (CPU-only, fail-closed):
- Kill switch / circuit breaker (robson, enterprise-crypto, riskkit)
- Daily loss + consecutive losses
- High-water-mark drawdown tiers → REDUCE_ONLY / HALT
- Order rate & max trades/day (overtrading guard)
- Position concentration vs total exposure
- Stale market / WS / orderbook / clock drift
- NaN/Inf/zero/negative notional rejection
- Risk event audit trail (in-memory bounded)

Does NOT place orders. ExecutionManager must call check() and honor denied.
"""
from __future__ import annotations

import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class RiskMode(str, Enum):
    """Operational risk posture (not execution mode)."""

    NORMAL = "NORMAL"
    WARNING = "WARNING"      # elevated drawdown — size advice reduced
    REDUCE_ONLY = "REDUCE_ONLY"  # only allow reducing exposure (SELL if long-side book)
    HALT = "HALT"            # no new risk-increasing orders


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
    # Drawdown from equity high-water mark (fraction, e.g. 0.10 = 10%)
    max_drawdown_pct: float = 0.15
    warn_drawdown_pct: float = 0.08
    reduce_drawdown_pct: float = 0.12
    # Concentration: single symbol notional / total exposure after order
    max_symbol_concentration: float = 0.50
    # Overtrading
    max_orders_per_day: int = 50
    max_orders_per_minute: int = 8
    # Cooldown after consecutive losses (seconds); 0 = disabled
    consecutive_loss_cooldown_sec: float = 300.0
    # Optional realized vol halt (caller supplies vol_20); 0 = disabled
    max_realized_vol: float = 0.0


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
    risk_mode: str = "NORMAL"
    drawdown_pct: float = 0.0
    size_multiplier: float = 1.0  # advisory only — does not place orders
    orders_today: int = 0


@dataclass
class RiskEvent:
    ts: float
    kind: str
    detail: str


class RiskEngine:
    def __init__(self, limits: Optional[RiskLimits] = None):
        self.limits = limits or RiskLimits()
        self.kill_switch = False
        self.circuit_breaker = False
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        self.day_start_ts = time.time()
        self._day_key = time.strftime("%Y-%m-%d", time.gmtime())
        self.api_errors: List[float] = []
        self.positions: Dict[str, float] = {}  # symbol -> notional (abs exposure)
        self.last_market_ts: Dict[str, float] = {}
        self.ws_connected = True
        self.orderbook_synced = True
        self.clock_offset_ms = 0.0
        # Equity high-water mark for drawdown
        self.equity = 0.0
        self.equity_peak = 0.0
        self.risk_mode = RiskMode.NORMAL
        # Order rate tracking
        self._order_ts: Deque[float] = deque(maxlen=500)
        self.orders_today = 0
        self._cooldown_until = 0.0
        self._events: Deque[RiskEvent] = deque(maxlen=200)
        self._last_realized_vol: float = 0.0

    # ----- control surface -----

    def activate_kill_switch(self, reason: str = "manual") -> None:
        self.kill_switch = True
        self.risk_mode = RiskMode.HALT
        self._event("KILL_SWITCH", reason)
        logger.critical("KILL SWITCH ACTIVATED: %s", reason)

    def reset_kill_switch(self) -> None:
        self.kill_switch = False
        self._event("KILL_SWITCH_RESET", "operator")
        logger.warning("Kill switch reset")
        self._refresh_mode_from_drawdown()

    def trip_circuit_breaker(self, reason: str) -> None:
        self.circuit_breaker = True
        self.risk_mode = RiskMode.HALT
        self._event("CIRCUIT_BREAKER", reason)
        logger.error("Circuit breaker tripped: %s", reason)

    def reset_circuit_breaker(self) -> None:
        self.circuit_breaker = False
        self._event("CIRCUIT_BREAKER_RESET", "operator")
        self._refresh_mode_from_drawdown()

    def record_api_error(self) -> None:
        now = time.time()
        self.api_errors.append(now)
        cutoff = now - self.limits.api_error_window_sec
        self.api_errors = [t for t in self.api_errors if t >= cutoff]
        if len(self.api_errors) >= self.limits.max_api_errors_window:
            self.trip_circuit_breaker("too many API errors")

    def update_market_ts(self, symbol: str) -> None:
        self.last_market_ts[symbol] = time.time()

    def set_ws_connected(self, connected: bool) -> None:
        self.ws_connected = bool(connected)

    def set_orderbook_synced(self, synced: bool) -> None:
        self.orderbook_synced = bool(synced)

    def set_clock_offset_ms(self, offset_ms: float) -> None:
        self.clock_offset_ms = float(offset_ms)

    def set_realized_vol(self, vol: float) -> None:
        """Optional realized volatility input (e.g. 20-bar). Used only if max_realized_vol > 0."""
        self._last_realized_vol = float(vol) if vol is not None else 0.0

    def update_equity(self, equity: float) -> None:
        """Update mark-to-market equity for drawdown tracking."""
        if equity is None or (isinstance(equity, float) and (math.isnan(equity) or math.isinf(equity))):
            return
        eq = float(equity)
        if eq < 0:
            return
        self.equity = eq
        if eq > self.equity_peak:
            self.equity_peak = eq
        self._refresh_mode_from_drawdown()

    def update_position(self, symbol: str, notional: float) -> None:
        if notional is None or (isinstance(notional, float) and (math.isnan(notional) or math.isinf(notional))):
            return
        n = abs(float(notional))
        if n <= 0:
            self.positions.pop(symbol, None)
        else:
            self.positions[symbol] = n

    def update_pnl(self, realized_delta: float) -> None:
        if realized_delta is None or (
            isinstance(realized_delta, float) and (math.isnan(realized_delta) or math.isinf(realized_delta))
        ):
            return
        self._roll_day_if_needed()
        self.daily_pnl += float(realized_delta)
        if realized_delta < 0:
            self.consecutive_losses += 1
            if (
                self.limits.consecutive_loss_cooldown_sec > 0
                and self.consecutive_losses >= self.limits.max_consecutive_losses
            ):
                self._cooldown_until = time.time() + self.limits.consecutive_loss_cooldown_sec
                self._event("COOLDOWN", f"losses={self.consecutive_losses}")
        else:
            self.consecutive_losses = 0
        # Auto trip on daily loss breach
        if self.daily_pnl <= -self.limits.max_daily_loss_usdt:
            self.trip_circuit_breaker("max_daily_loss")

    def record_order_attempt(self) -> None:
        """Call after an order is submitted (approved path) for rate accounting."""
        self._roll_day_if_needed()
        now = time.time()
        self._order_ts.append(now)
        self.orders_today += 1

    def drawdown_pct(self) -> float:
        if self.equity_peak <= 0:
            return 0.0
        return max(0.0, (self.equity_peak - self.equity) / self.equity_peak)

    def size_multiplier(self) -> float:
        """Advisory size scale [0,1] from risk posture — does not authorize orders."""
        mode = self.risk_mode
        if mode == RiskMode.HALT or self.kill_switch or self.circuit_breaker:
            return 0.0
        if mode == RiskMode.REDUCE_ONLY:
            return 0.0  # no increase
        if mode == RiskMode.WARNING:
            return 0.5
        # consecutive loss ladder
        if self.consecutive_losses >= 3:
            return 0.5
        if self.consecutive_losses >= 2:
            return 0.75
        return 1.0

    def events(self) -> List[RiskEvent]:
        return list(self._events)

    # ----- core gate -----

    def check(
        self,
        symbol: str,
        side: int,
        notional: float,
        available_balance: float,
        current_position_notional: float = 0.0,
        reduce_only_intent: bool = False,
    ) -> RiskStatus:
        """
        Pre-trade gate. Fail-closed.

        side: 0=BUY, 1=SELL (Tokocrypto convention used in ExecutionManager)
        reduce_only_intent: if True, order is treated as risk-reducing (allowed in REDUCE_ONLY mode)
        """
        self._roll_day_if_needed()
        blocked: List[str] = []
        dd = self.drawdown_pct()
        mult = self.size_multiplier()

        if self.kill_switch:
            blocked.append("KILL_SWITCH")
        if self.circuit_breaker:
            blocked.append("CIRCUIT_BREAKER")
        if self.risk_mode == RiskMode.HALT:
            blocked.append("RISK_MODE_HALT")
        if not self.ws_connected:
            blocked.append("WS_DISCONNECTED")
        if not self.orderbook_synced:
            blocked.append("ORDERBOOK_DESYNC")

        last = self.last_market_ts.get(symbol, 0)
        market_stale = (time.time() - last) > self.limits.max_stale_market_sec if last else True
        if market_stale:
            blocked.append("MARKET_STALE")

        clock_drift = abs(self.clock_offset_ms) > self.limits.max_clock_drift_ms
        if clock_drift:
            blocked.append("CLOCK_DRIFT")

        if time.time() < self._cooldown_until:
            blocked.append("LOSS_COOLDOWN")

        # notional validity
        if notional is None or (isinstance(notional, float) and (math.isnan(notional) or math.isinf(notional))):
            blocked.append("INVALID_NOTIONAL")
            notional = 0.0
        elif notional < 0:
            blocked.append("NEGATIVE_NOTIONAL")
        elif notional == 0:
            blocked.append("ZERO_NOTIONAL")

        if notional > self.limits.max_order_value_usdt:
            blocked.append("MAX_ORDER_VALUE")
        if current_position_notional + notional > self.limits.max_position_usdt:
            blocked.append("MAX_POSITION")

        total_exp = sum(self.positions.values()) + notional
        if total_exp > self.limits.max_exposure_usdt:
            blocked.append("MAX_EXPOSURE")

        # concentration — only when book already has exposure (first fill exempt)
        existing_exp = sum(self.positions.values())
        if existing_exp > 0 and total_exp > 0 and self.limits.max_symbol_concentration > 0:
            sym_after = current_position_notional + notional
            conc = sym_after / total_exp
            if conc > self.limits.max_symbol_concentration + 1e-12:
                blocked.append("MAX_SYMBOL_CONCENTRATION")

        if self.daily_pnl <= -self.limits.max_daily_loss_usdt:
            blocked.append("MAX_DAILY_LOSS")
        if self.consecutive_losses >= self.limits.max_consecutive_losses:
            blocked.append("MAX_CONSECUTIVE_LOSSES")
        if available_balance < self.limits.min_balance_usdt:
            blocked.append("MIN_BALANCE")

        if dd >= self.limits.max_drawdown_pct:
            blocked.append("MAX_DRAWDOWN")
            if not self.circuit_breaker:
                self.trip_circuit_breaker("max_drawdown")

        # REDUCE_ONLY: block risk-increasing orders
        if self.risk_mode == RiskMode.REDUCE_ONLY and not reduce_only_intent:
            blocked.append("REDUCE_ONLY_MODE")

        # Overtrading
        if self.orders_today >= self.limits.max_orders_per_day:
            blocked.append("MAX_ORDERS_PER_DAY")
        recent = sum(1 for t in self._order_ts if time.time() - t <= 60.0)
        if recent >= self.limits.max_orders_per_minute:
            blocked.append("MAX_ORDERS_PER_MINUTE")

        # Optional vol halt
        if (
            self.limits.max_realized_vol > 0
            and self._last_realized_vol > self.limits.max_realized_vol
        ):
            blocked.append("VOLATILITY_HALT")

        allowed = len(blocked) == 0
        reason = ";".join(blocked) if blocked else "OK"
        if not allowed:
            self._event("DENIED", reason)

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
            risk_mode=self.risk_mode.value,
            drawdown_pct=dd,
            size_multiplier=mult,
            orders_today=self.orders_today,
        )

    # ----- internals -----

    def _roll_day_if_needed(self) -> None:
        key = time.strftime("%Y-%m-%d", time.gmtime())
        if key != self._day_key:
            self._day_key = key
            self.day_start_ts = time.time()
            self.daily_pnl = 0.0
            self.orders_today = 0
            self.api_errors = []
            self._event("DAY_ROLL", key)

    def _refresh_mode_from_drawdown(self) -> None:
        if self.kill_switch or self.circuit_breaker:
            self.risk_mode = RiskMode.HALT
            return
        dd = self.drawdown_pct()
        if dd >= self.limits.max_drawdown_pct:
            self.risk_mode = RiskMode.HALT
        elif dd >= self.limits.reduce_drawdown_pct:
            self.risk_mode = RiskMode.REDUCE_ONLY
            self._event("MODE_REDUCE_ONLY", f"dd={dd:.4f}")
        elif dd >= self.limits.warn_drawdown_pct:
            self.risk_mode = RiskMode.WARNING
        else:
            self.risk_mode = RiskMode.NORMAL

    def _event(self, kind: str, detail: str) -> None:
        self._events.append(RiskEvent(ts=time.time(), kind=kind, detail=detail))
