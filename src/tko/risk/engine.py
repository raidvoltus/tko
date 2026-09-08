"""Risk limits and kill switch."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from tko.core.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RiskDecision:
    approved: bool
    reason: str
    size_quote: float  # amount in quote currency to spend (BUY) or 0
    size_base: float  # amount in base to sell (SELL) or 0


class RiskEngine:
    def __init__(self, settings: Settings, state_dir: Path) -> None:
        self.s = settings
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._daily_pnl_pct = 0.0
        self._open_positions = 0

    def kill_switch_active(self) -> bool:
        return (self.state_dir / "KILL").exists() or Path(self.s.kill_switch_file).exists()

    def activate_kill_switch(self) -> None:
        p = self.state_dir / "KILL"
        p.write_text("stop", encoding="utf-8")
        logger.warning("KILL SWITCH ACTIVATED")

    def clear_kill_switch(self) -> None:
        for p in (self.state_dir / "KILL", Path(self.s.kill_switch_file)):
            if p.exists():
                p.unlink(missing_ok=True)

    def evaluate_buy(
        self,
        free_quote: float,
        last_price: float,
        open_positions: int,
    ) -> RiskDecision:
        if self.kill_switch_active():
            return RiskDecision(False, "kill switch active", 0.0, 0.0)
        if free_quote < self.s.min_quote_balance:
            return RiskDecision(
                False,
                f"quote balance {free_quote:.0f} < min {self.s.min_quote_balance:.0f}",
                0.0,
                0.0,
            )
        if open_positions >= self.s.max_open_positions:
            return RiskDecision(False, "max open positions reached", 0.0, 0.0)
        if self._daily_pnl_pct <= -abs(self.s.max_daily_loss_pct):
            return RiskDecision(False, "max daily loss reached", 0.0, 0.0)
        if last_price <= 0:
            return RiskDecision(False, "invalid price", 0.0, 0.0)

        size_quote = free_quote * (self.s.max_position_pct / 100.0)
        size_quote = min(size_quote, free_quote * 0.95)
        if size_quote < self.s.min_quote_balance * 0.5:
            return RiskDecision(False, "computed size too small", 0.0, 0.0)

        size_base = size_quote / last_price
        return RiskDecision(True, "approved", size_quote, size_base)

    def evaluate_sell(
        self,
        free_base: float,
        entry_price: float | None,
        last_price: float,
        signal_sell: bool,
    ) -> RiskDecision:
        if self.kill_switch_active():
            if free_base > 0:
                return RiskDecision(True, "kill switch — force sell", 0.0, free_base)
            return RiskDecision(False, "kill switch active", 0.0, 0.0)
        if free_base <= 0:
            return RiskDecision(False, "no base balance", 0.0, 0.0)
        if last_price <= 0:
            return RiskDecision(False, "invalid price", 0.0, 0.0)

        if entry_price and entry_price > 0:
            pnl_pct = (last_price - entry_price) / entry_price * 100.0
            if pnl_pct >= self.s.take_profit_pct:
                return RiskDecision(True, f"take profit {pnl_pct:.2f}%", 0.0, free_base)
            if pnl_pct <= -abs(self.s.stop_loss_pct):
                return RiskDecision(True, f"stop loss {pnl_pct:.2f}%", 0.0, free_base)

        if signal_sell:
            return RiskDecision(True, "strategy SELL signal", 0.0, free_base)

        return RiskDecision(False, "hold position", 0.0, 0.0)

    def record_pnl_pct(self, pct: float) -> None:
        self._daily_pnl_pct += pct
