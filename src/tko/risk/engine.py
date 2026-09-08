"""Risk limits, durable PnL, hard caps, and kill switch."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from tko.core.config import Settings
from tko.risk.pnl_tracker import DailyPnLTracker

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RiskDecision:
    approved: bool
    reason: str
    size_quote: float
    size_base: float


class RiskEngine:
    def __init__(
        self,
        settings: Settings,
        state_dir: Path,
        pnl_tracker: DailyPnLTracker | None = None,
        audit: object | None = None,
    ) -> None:
        self.s = settings
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.pnl = pnl_tracker or DailyPnLTracker(
            state_dir / "pnl_ledger.jsonl",
            timezone_name=settings.risk_timezone,
        )
        self.audit = audit
        self._equity_baseline = float(settings.daily_equity_baseline or 0.0)

    def _risk_block(self, reason: str) -> None:
        if self.audit is not None:
            try:
                self.audit.record("RISK_BLOCK", reason=reason)  # type: ignore[attr-defined]
            except Exception:
                pass

    def set_equity_baseline_if_empty(self, equity: float) -> None:
        if self._equity_baseline <= 0 and equity > 0:
            self._equity_baseline = float(equity)
            logger.info("event=equity_baseline_set value=%.8f", self._equity_baseline)

    def kill_switch_active(self) -> bool:
        return (self.state_dir / "KILL").exists() or Path(self.s.kill_switch_file).exists()

    def activate_kill_switch(self, reason: str = "stop") -> None:
        p = self.state_dir / "KILL"
        p.write_text(reason[:500], encoding="utf-8")
        logger.warning("KILL SWITCH ACTIVATED reason=%s", reason)
        if self.audit is not None:
            try:
                self.audit.record("KILL_SWITCH", reason=reason[:300])  # type: ignore[attr-defined]
            except Exception:
                pass

    def clear_kill_switch(self) -> None:
        for p in (self.state_dir / "KILL", Path(self.s.kill_switch_file)):
            if p.exists():
                p.unlink(missing_ok=True)

    def daily_loss_breached(self) -> bool:
        if self.s.max_daily_loss_pct <= 0 or self._equity_baseline <= 0:
            return False
        pnl = self.pnl.today_realized_pnl()
        loss_pct = (-pnl / self._equity_baseline) * 100.0 if pnl < 0 else 0.0
        return loss_pct >= abs(self.s.max_daily_loss_pct)

    def check_daily_limits_or_kill(self) -> str | None:
        if self.daily_loss_breached():
            pnl = self.pnl.today_realized_pnl()
            reason = (
                f"daily loss limit breached: pnl={pnl:.4f} "
                f"limit={self.s.max_daily_loss_pct}% of baseline={self._equity_baseline:.4f}"
            )
            self.activate_kill_switch(reason)
            return reason
        return None

    def evaluate_buy(
        self,
        free_quote: float,
        last_price: float,
        open_positions: int,
        *,
        quote_asset: str | None = None,
    ) -> RiskDecision:
        if self.kill_switch_active():
            self._risk_block("kill switch active")
            return RiskDecision(False, "kill switch active", 0.0, 0.0)
        breach = self.check_daily_limits_or_kill()
        if breach:
            return RiskDecision(False, breach, 0.0, 0.0)
        min_q = self.s.min_balance_for_quote(quote_asset or self.s.quote_asset)
        if free_quote < min_q:
            return RiskDecision(False, f"quote balance {free_quote:.8f} < min {min_q:.8f}", 0.0, 0.0)
        if open_positions >= self.s.max_open_positions:
            return RiskDecision(False, "max open positions reached", 0.0, 0.0)
        if last_price <= 0:
            return RiskDecision(False, "invalid price", 0.0, 0.0)
        size_quote = free_quote * (self.s.max_position_pct / 100.0)
        size_quote = min(size_quote, free_quote * 0.95)
        if self.s.max_order_notional > 0:
            size_quote = min(size_quote, float(self.s.max_order_notional))
        if self.s.max_daily_notional > 0:
            used = self.pnl.today_notional()
            remaining = float(self.s.max_daily_notional) - used
            if remaining <= 0:
                return RiskDecision(
                    False,
                    f"max daily notional reached ({used:.4f}/{self.s.max_daily_notional:.4f})",
                    0.0, 0.0,
                )
            size_quote = min(size_quote, remaining)
        if size_quote < min_q * 0.5:
            return RiskDecision(False, "computed size too small", 0.0, 0.0)
        return RiskDecision(True, "approved", size_quote, size_quote / last_price)

    def evaluate_sell(
        self,
        free_base: float,
        entry_price: float | None,
        last_price: float,
        signal_sell: bool,
        *,
        estimated_notional: float | None = None,
    ) -> RiskDecision:
        if self.kill_switch_active():
            if free_base > 0:
                return RiskDecision(True, "kill switch — force sell", 0.0, free_base)
            return RiskDecision(False, "kill switch active", 0.0, 0.0)
        if free_base <= 0:
            return RiskDecision(False, "no base balance", 0.0, 0.0)
        if last_price <= 0:
            return RiskDecision(False, "invalid price", 0.0, 0.0)
        notional = estimated_notional if estimated_notional is not None else free_base * last_price
        if self.s.max_order_notional > 0 and notional > self.s.max_order_notional:
            free_base = min(free_base, float(self.s.max_order_notional) / last_price)
        if entry_price and entry_price > 0:
            pnl_pct = (last_price - entry_price) / entry_price * 100.0
            if pnl_pct >= self.s.take_profit_pct:
                return RiskDecision(True, f"take profit {pnl_pct:.2f}%", 0.0, free_base)
            if pnl_pct <= -abs(self.s.stop_loss_pct):
                return RiskDecision(True, f"stop loss {pnl_pct:.2f}%", 0.0, free_base)
        if signal_sell:
            return RiskDecision(True, "strategy SELL signal", 0.0, free_base)
        return RiskDecision(False, "hold position", 0.0, 0.0)

    def record_fill(
        self, *, side: str, symbol: str, notional: float, pnl: float = 0.0,
        order_id: str = "", client_order_id: str = "",
    ) -> None:
        self.pnl.record_trade(
            side=side, symbol=symbol, notional=notional, pnl=pnl,
            order_id=order_id, client_order_id=client_order_id,
        )
        self.check_daily_limits_or_kill()
