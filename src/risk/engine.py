"""
Risk Engine — absolute ALLOW/DENY authority before order submission.

Production LIVE only at execution layer. This module never submits orders.

Fail-closed: invalid/missing/stale risk state → DENY / NO TRADE.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class RiskMode(str, Enum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    REDUCE_ONLY = "REDUCE_ONLY"
    HALT = "HALT"


class RiskStateHealth(str, Enum):
    VALID = "VALID"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"
    RECONCILING = "RECONCILING"
    UNRECONCILED = "UNRECONCILED"


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
    max_drawdown_pct: float = 0.15
    warn_drawdown_pct: float = 0.08
    reduce_drawdown_pct: float = 0.12
    max_symbol_concentration: float = 0.50
    max_orders_per_day: int = 50
    max_orders_per_minute: int = 8
    consecutive_loss_cooldown_sec: float = 300.0
    max_realized_vol: float = 0.0
    # Session day in Asia/Jakarta (WIB) — Tokocrypto Indonesia operations
    session_tz_offset_hours: int = 7
    # Spot: shorting not supported unless explicitly enabled
    allow_spot_short: bool = False
    # Reservation TTL seconds if submit never commits/releases
    reservation_ttl_sec: float = 30.0


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
    size_multiplier: float = 1.0
    orders_today: int = 0
    state_health: str = "UNRECONCILED"
    reservation_id: Optional[str] = None
    is_risk_reducing: bool = False


@dataclass
class RiskEvent:
    ts: float
    kind: str
    detail: str
    symbol: str = ""
    risk_mode: str = ""
    decision: str = ""
    client_id: str = ""


@dataclass
class _Reservation:
    id: str
    ts: float
    symbol: str
    notional: float
    committed: bool = False


class RiskEngine:
    def __init__(self, limits: Optional[RiskLimits] = None):
        self.limits = limits or RiskLimits()
        self._lock = threading.RLock()
        self.kill_switch = False
        self.circuit_breaker = False
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        self.day_start_ts = time.time()
        self._day_key = self._session_day_key()
        self.api_errors: List[float] = []
        # signed position notional: +long inventory (spot), -short if allowed
        self.positions: Dict[str, float] = {}
        self.last_market_ts: Dict[str, float] = {}
        self.ws_connected = True
        self.orderbook_synced = True
        self.clock_offset_ms = 0.0
        self.equity = 0.0
        self.equity_peak = 0.0
        self.risk_mode = RiskMode.NORMAL
        self._order_ts: Deque[float] = deque(maxlen=500)
        self.orders_today = 0
        self._cooldown_until = 0.0
        self._events: Deque[RiskEvent] = deque(maxlen=500)
        self._last_realized_vol: float = 0.0
        self.state_health = RiskStateHealth.UNRECONCILED
        self._reservations: Dict[str, _Reservation] = {}
        self._res_seq = 0
        self._hwm_explicit_reset = False

    # ----- session / day -----

    def _session_day_key(self, now: Optional[float] = None) -> str:
        """Authoritative trading day key in configured offset (default WIB UTC+7)."""
        ts = now if now is not None else time.time()
        offset = self.limits.session_tz_offset_hours * 3600
        return time.strftime("%Y-%m-%d", time.gmtime(ts + offset))

    def _roll_day_if_needed(self) -> None:
        key = self._session_day_key()
        if key != self._day_key:
            self._day_key = key
            self.day_start_ts = time.time()
            self.daily_pnl = 0.0
            self.orders_today = 0
            self.api_errors = []
            self._event("DAY_ROLL", key)

    # ----- control surface -----

    def activate_kill_switch(self, reason: str = "manual") -> None:
        with self._lock:
            self.kill_switch = True
            self.risk_mode = RiskMode.HALT
            self._event("KILL_SWITCH", reason, decision="DENY")
            logger.critical("KILL SWITCH ACTIVATED: %s", reason)

    def reset_kill_switch(
        self,
        operator_approved: bool = False,
        approval_ref: str = "",
        require_reconciled: bool = True,
    ) -> bool:
        """Privileged reset — does not restore trading by itself if state invalid."""
        with self._lock:
            if not operator_approved:
                self._event("KILL_SWITCH_RESET_DENIED", "operator_approved=false")
                return False
            if require_reconciled and self.state_health != RiskStateHealth.VALID:
                self._event(
                    "KILL_SWITCH_RESET_DENIED",
                    f"state_health={self.state_health.value}",
                    decision="DENY",
                )
                return False
            self.kill_switch = False
            self._event("KILL_SWITCH_RESET", f"op={approval_ref}", decision="RESET")
            self._refresh_mode_from_drawdown()
            return True

    def trip_circuit_breaker(self, reason: str) -> None:
        with self._lock:
            self.circuit_breaker = True
            self.risk_mode = RiskMode.HALT
            self._event("CIRCUIT_BREAKER", reason, decision="DENY")
            logger.error("Circuit breaker tripped: %s", reason)

    def reset_circuit_breaker(
        self,
        operator_approved: bool = False,
        approval_ref: str = "",
        require_reconciled: bool = True,
    ) -> bool:
        with self._lock:
            if not operator_approved:
                self._event("CIRCUIT_BREAKER_RESET_DENIED", "operator_approved=false")
                return False
            if require_reconciled and self.state_health != RiskStateHealth.VALID:
                self._event(
                    "CIRCUIT_BREAKER_RESET_DENIED",
                    f"state_health={self.state_health.value}",
                )
                return False
            # Fundamental conditions must still be clear
            if self.drawdown_pct() >= self.limits.max_drawdown_pct:
                self._event("CIRCUIT_BREAKER_RESET_DENIED", "drawdown_still_breach")
                return False
            if self.daily_pnl <= -self.limits.max_daily_loss_usdt:
                self._event("CIRCUIT_BREAKER_RESET_DENIED", "daily_loss_still_breach")
                return False
            self.circuit_breaker = False
            self._event("CIRCUIT_BREAKER_RESET", f"op={approval_ref}", decision="RESET")
            self._refresh_mode_from_drawdown()
            return True

    def mark_reconciliation_start(self) -> None:
        with self._lock:
            self.state_health = RiskStateHealth.RECONCILING
            self._event("RECONCILE_START", "")

    def mark_reconciliation_failed(self, reason: str = "failed") -> None:
        with self._lock:
            self.state_health = RiskStateHealth.INVALID
            self.risk_mode = RiskMode.HALT
            self._event("RECONCILE_FAIL", reason, decision="DENY")

    def apply_authoritative_snapshot(
        self,
        equity: float,
        positions: Dict[str, float],
        daily_pnl: Optional[float] = None,
        equity_peak: Optional[float] = None,
    ) -> bool:
        """
        Apply REST-reconciled account state. Returns False if inputs invalid → INVALID.
        HWM: peak = max(existing_peak, equity, equity_peak arg); never lowers peak on equity drop.
        """
        with self._lock:
            if not self._finite_nonneg(equity):
                self.state_health = RiskStateHealth.INVALID
                self._event("INVALID_EQUITY", str(equity), decision="DENY")
                return False
            clean_pos: Dict[str, float] = {}
            for sym, n in (positions or {}).items():
                if not self._finite(n):
                    self.state_health = RiskStateHealth.INVALID
                    self._event("INVALID_POSITION", f"{sym}={n}", decision="DENY")
                    return False
                if abs(n) > 0:
                    clean_pos[sym] = float(n)
            self.equity = float(equity)
            # Never lower HWM on restart unless explicit authorized reset
            peak = self.equity_peak
            if equity_peak is not None:
                if not self._finite_nonneg(equity_peak):
                    self.state_health = RiskStateHealth.INVALID
                    self._event("INVALID_HWM", str(equity_peak), decision="DENY")
                    return False
                peak = max(peak, float(equity_peak))
            peak = max(peak, float(equity))
            self.equity_peak = peak
            self.positions = clean_pos
            if daily_pnl is not None:
                if not self._finite(daily_pnl):
                    self.state_health = RiskStateHealth.INVALID
                    self._event("INVALID_PNL", str(daily_pnl), decision="DENY")
                    return False
                self.daily_pnl = float(daily_pnl)
            self.state_health = RiskStateHealth.VALID
            self._refresh_mode_from_drawdown()
            self._event("RECONCILE_OK", f"eq={equity:.4f} peak={peak:.4f}", decision="OK")
            return True

    def record_api_error(self, status_code: Optional[int] = None) -> None:
        with self._lock:
            now = time.time()
            self.api_errors.append(now)
            cutoff = now - self.limits.api_error_window_sec
            self.api_errors = [t for t in self.api_errors if t >= cutoff]
            if status_code == 418:
                self.kill_switch = True
                self.risk_mode = RiskMode.HALT
                self._event("IP_BAN_418", "kill_switch", decision="DENY")
            if len(self.api_errors) >= self.limits.max_api_errors_window:
                self.circuit_breaker = True
                self.risk_mode = RiskMode.HALT
                self._event("CIRCUIT_BREAKER", "api_errors", decision="DENY")

    def mark_unknown_order(self, client_id: str = "") -> None:
        with self._lock:
            self.state_health = RiskStateHealth.UNKNOWN
            self.risk_mode = RiskMode.HALT
            self._event("UNKNOWN_ORDER", "reconcile_required", client_id=client_id, decision="DENY")

    def update_market_ts(self, symbol: str) -> None:
        with self._lock:
            self.last_market_ts[symbol] = time.time()

    def set_ws_connected(self, connected: bool) -> None:
        with self._lock:
            self.ws_connected = bool(connected)

    def set_orderbook_synced(self, synced: bool) -> None:
        with self._lock:
            self.orderbook_synced = bool(synced)

    def set_clock_offset_ms(self, offset_ms: float) -> None:
        with self._lock:
            if not self._finite(offset_ms):
                self.state_health = RiskStateHealth.INVALID
                self._event("INVALID_CLOCK", str(offset_ms), decision="DENY")
                return
            self.clock_offset_ms = float(offset_ms)

    def set_realized_vol(self, vol: float) -> None:
        with self._lock:
            if not self._finite(vol) or vol < 0:
                self.state_health = RiskStateHealth.INVALID
                self._event("INVALID_VOL", str(vol), decision="DENY")
                return
            self._last_realized_vol = float(vol)

    def update_equity(self, equity: float) -> None:
        with self._lock:
            if not self._finite_nonneg(equity):
                self.state_health = RiskStateHealth.INVALID
                self._event("INVALID_EQUITY", str(equity), decision="DENY")
                return
            self.equity = float(equity)
            if self.equity > self.equity_peak:
                self.equity_peak = self.equity
            self._refresh_mode_from_drawdown()

    def update_position(self, symbol: str, notional: float) -> None:
        with self._lock:
            if not self._finite(notional):
                self.state_health = RiskStateHealth.INVALID
                self._event("INVALID_POSITION", f"{symbol}={notional}", decision="DENY")
                return
            n = float(notional)
            if abs(n) <= 0:
                self.positions.pop(symbol, None)
            else:
                self.positions[symbol] = n

    def update_pnl(self, realized_delta: float) -> None:
        with self._lock:
            if not self._finite(realized_delta):
                self.state_health = RiskStateHealth.INVALID
                self._event("INVALID_PNL", str(realized_delta), decision="DENY")
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
            if self.daily_pnl <= -self.limits.max_daily_loss_usdt:
                self.circuit_breaker = True
                self.risk_mode = RiskMode.HALT
                self._event("CIRCUIT_BREAKER", "max_daily_loss", decision="DENY")

    def drawdown_pct(self) -> float:
        if self.equity_peak <= 0:
            return 0.0
        return max(0.0, (self.equity_peak - self.equity) / self.equity_peak)

    def size_multiplier(self) -> float:
        with self._lock:
            if self.risk_mode == RiskMode.HALT or self.kill_switch or self.circuit_breaker:
                return 0.0
            if self.risk_mode == RiskMode.REDUCE_ONLY:
                return 0.0
            if self.risk_mode == RiskMode.WARNING:
                return 0.5
            if self.consecutive_losses >= 3:
                return 0.5
            if self.consecutive_losses >= 2:
                return 0.75
            return 1.0

    def events(self) -> List[RiskEvent]:
        with self._lock:
            return list(self._events)

    # ----- reduce-only authoritative -----

    def is_risk_reducing(
        self,
        symbol: str,
        side: int,
        notional: float,
    ) -> Tuple[bool, str]:
        """
        Spot inventory model: position > 0 = long.
        BUY always increases long risk.
        SELL reduces only up to current long; excess SELL is not fully reducing.
        Flat + SELL rejected unless allow_spot_short.
        """
        if not self._finite(notional) or notional <= 0:
            return False, "INVALID_NOTIONAL"
        pos = float(self.positions.get(symbol, 0.0))
        if side == 0:  # BUY
            if pos < 0 and self.limits.allow_spot_short:
                # covering short
                return True, "COVER_SHORT"
            return False, "BUY_INCREASES_RISK"
        # SELL
        if pos > 0:
            if notional <= pos + 1e-12:
                return True, "SELL_REDUCES_LONG"
            return False, "SELL_EXCEEDS_LONG"
        if pos == 0:
            if self.limits.allow_spot_short:
                return False, "OPEN_SHORT"  # opening short = risk increasing
            return False, "SPOT_SHORT_NOT_ALLOWED"
        # pos < 0 short: SELL increases short
        return False, "SELL_INCREASES_SHORT"

    # ----- admission / reservation -----

    def admit(
        self,
        symbol: str,
        side: int,
        notional: float,
        available_balance: float,
        current_position_notional: Optional[float] = None,
        client_id: str = "",
    ) -> RiskStatus:
        """
        Atomic check + rate/exposure reservation.
        Caller must commit_reservation() after successful exchange accept,
        or release_reservation() if submit aborted before send.
        UNKNOWN after send → mark_unknown_order (capacity not released as free).
        """
        with self._lock:
            self._purge_expired_reservations()
            st = self._check_unlocked(
                symbol,
                side,
                notional,
                available_balance,
                current_position_notional,
                client_id=client_id,
            )
            if not st.allowed:
                return st
            self._res_seq += 1
            rid = f"r{self._res_seq}_{int(time.time() * 1000)}"
            self._reservations[rid] = _Reservation(
                id=rid, ts=time.time(), symbol=symbol, notional=float(notional)
            )
            # Reserve rate slot optimistically
            self._order_ts.append(time.time())
            self.orders_today += 1
            st.reservation_id = rid
            self._event(
                "ADMIT",
                f"notional={notional}",
                symbol=symbol,
                decision="ALLOW",
                client_id=client_id,
            )
            return st

    def commit_reservation(self, reservation_id: Optional[str]) -> None:
        with self._lock:
            if not reservation_id:
                return
            r = self._reservations.pop(reservation_id, None)
            if r:
                r.committed = True
                self._event("RESERVE_COMMIT", reservation_id)

    def release_reservation(self, reservation_id: Optional[str], *, safe: bool = True) -> None:
        """
        Release only if submit aborted before exchange send.
        If safe=False (UNKNOWN), do not restore rate capacity.
        """
        with self._lock:
            if not reservation_id:
                return
            r = self._reservations.pop(reservation_id, None)
            if not r:
                return
            if safe:
                # restore one rate unit if possible
                if self.orders_today > 0:
                    self.orders_today -= 1
                self._event("RESERVE_RELEASE", reservation_id)
            else:
                self._event("RESERVE_HOLD_UNKNOWN", reservation_id, decision="DENY")

    def check(
        self,
        symbol: str,
        side: int,
        notional: float,
        available_balance: float,
        current_position_notional: float = 0.0,
        reduce_only_intent: bool = False,
    ) -> RiskStatus:
        """Backward-compatible check without reservation (prefer admit())."""
        with self._lock:
            # reduce_only_intent is IGNORED for allow decision — authoritative computation only
            return self._check_unlocked(
                symbol, side, notional, available_balance, current_position_notional
            )

    def _check_unlocked(
        self,
        symbol: str,
        side: int,
        notional: float,
        available_balance: float,
        current_position_notional: Optional[float] = None,
        client_id: str = "",
    ) -> RiskStatus:
        self._roll_day_if_needed()
        blocked: List[str] = []
        dd = self.drawdown_pct()
        mult = 0.0
        if not (self.kill_switch or self.circuit_breaker or self.risk_mode == RiskMode.HALT):
            if self.risk_mode == RiskMode.WARNING:
                mult = 0.5
            elif self.risk_mode == RiskMode.NORMAL:
                mult = 1.0
                if self.consecutive_losses >= 3:
                    mult = 0.5
                elif self.consecutive_losses >= 2:
                    mult = 0.75

        # State health gate
        if self.state_health in (
            RiskStateHealth.INVALID,
            RiskStateHealth.UNKNOWN,
            RiskStateHealth.UNRECONCILED,
            RiskStateHealth.RECONCILING,
        ):
            blocked.append(f"STATE_{self.state_health.value}")

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

        if not self._finite(notional):
            blocked.append("INVALID_NOTIONAL")
            notional = 0.0
        elif notional < 0:
            blocked.append("NEGATIVE_NOTIONAL")
        elif notional == 0:
            blocked.append("ZERO_NOTIONAL")

        if not self._finite(available_balance) or available_balance < 0:
            blocked.append("INVALID_BALANCE")

        if notional > self.limits.max_order_value_usdt:
            blocked.append("MAX_ORDER_VALUE")

        pos = float(self.positions.get(symbol, 0.0))
        if current_position_notional is not None and self._finite(current_position_notional):
            # prefer explicit if provided and finite
            pos_for_limit = abs(float(current_position_notional))
        else:
            pos_for_limit = abs(pos)

        if pos_for_limit + notional > self.limits.max_position_usdt:
            blocked.append("MAX_POSITION")

        reserved = sum(r.notional for r in self._reservations.values())
        total_exp = sum(abs(v) for v in self.positions.values()) + notional + reserved
        if total_exp > self.limits.max_exposure_usdt:
            blocked.append("MAX_EXPOSURE")

        existing_exp = sum(abs(v) for v in self.positions.values())
        if existing_exp > 0 and total_exp > 0 and self.limits.max_symbol_concentration > 0:
            sym_after = pos_for_limit + notional
            conc = sym_after / total_exp
            if not self._finite(conc) or conc > self.limits.max_symbol_concentration + 1e-12:
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
                self.circuit_breaker = True
                self.risk_mode = RiskMode.HALT
                self._event("CIRCUIT_BREAKER", "max_drawdown", decision="DENY")

        reducing, red_reason = self.is_risk_reducing(symbol, side, notional)
        if self.risk_mode == RiskMode.REDUCE_ONLY and not reducing:
            blocked.append("REDUCE_ONLY_MODE")
            blocked.append(red_reason)

        # Spot short policy
        if side == 1 and pos <= 0 and not self.limits.allow_spot_short:
            if pos == 0:
                blocked.append("SPOT_SHORT_NOT_ALLOWED")
            elif not reducing:
                blocked.append("SPOT_SHORT_NOT_ALLOWED")

        if self.orders_today >= self.limits.max_orders_per_day:
            blocked.append("MAX_ORDERS_PER_DAY")
        recent = sum(1 for t in self._order_ts if time.time() - t <= 60.0)
        # include pending reservations in minute window
        recent += sum(1 for r in self._reservations.values() if time.time() - r.ts <= 60.0)
        if recent >= self.limits.max_orders_per_minute:
            blocked.append("MAX_ORDERS_PER_MINUTE")

        if self.limits.max_realized_vol > 0 and self._last_realized_vol > self.limits.max_realized_vol:
            blocked.append("VOLATILITY_HALT")

        allowed = len(blocked) == 0
        reason = ";".join(blocked) if blocked else "OK"
        if not allowed:
            self._event("DENIED", reason, symbol=symbol, decision="DENY", client_id=client_id)

        return RiskStatus(
            allowed=allowed,
            reason=reason,
            blocked_by=blocked,
            daily_pnl=self.daily_pnl,
            consecutive_losses=self.consecutive_losses,
            exposure=sum(abs(v) for v in self.positions.values()),
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
            state_health=self.state_health.value,
            is_risk_reducing=reducing,
        )

    def _purge_expired_reservations(self) -> None:
        now = time.time()
        expired = [k for k, r in self._reservations.items() if now - r.ts > self.limits.reservation_ttl_sec]
        for k in expired:
            self._reservations.pop(k, None)
            self._event("RESERVE_EXPIRE", k)

    def _refresh_mode_from_drawdown(self) -> None:
        if self.kill_switch or self.circuit_breaker:
            self.risk_mode = RiskMode.HALT
            return
        if self.state_health != RiskStateHealth.VALID:
            # keep halt-like posture when not valid
            return
        dd = self.drawdown_pct()
        prev = self.risk_mode
        if dd >= self.limits.max_drawdown_pct:
            self.risk_mode = RiskMode.HALT
        elif dd >= self.limits.reduce_drawdown_pct:
            self.risk_mode = RiskMode.REDUCE_ONLY
        elif dd >= self.limits.warn_drawdown_pct:
            self.risk_mode = RiskMode.WARNING
        else:
            self.risk_mode = RiskMode.NORMAL
        if self.risk_mode != prev:
            self._event("MODE", f"{prev.value}->{self.risk_mode.value}")

    def _event(
        self,
        kind: str,
        detail: str,
        symbol: str = "",
        decision: str = "",
        client_id: str = "",
    ) -> None:
        self._events.append(
            RiskEvent(
                ts=time.time(),
                kind=kind,
                detail=detail,
                symbol=symbol,
                risk_mode=self.risk_mode.value,
                decision=decision,
                client_id=client_id,
            )
        )

    @staticmethod
    def _finite(x: float) -> bool:
        try:
            v = float(x)
            return not (math.isnan(v) or math.isinf(v))
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _finite_nonneg(x: float) -> bool:
        try:
            v = float(x)
            return not (math.isnan(v) or math.isinf(v)) and v >= 0
        except (TypeError, ValueError):
            return False
