"""Tests for absolute notional hard caps and daily loss."""

from __future__ import annotations

from pathlib import Path

from tko.core.config import Settings
from tko.risk.engine import RiskEngine
from tko.risk.pnl_tracker import DailyPnLTracker


def test_max_order_notional_caps_size(tmp_path: Path):
    s = Settings(
        max_order_notional=1_000_000,
        max_daily_notional=50_000_000,
        max_position_pct=100,
        min_quote_balance=1,
    )
    risk = RiskEngine(s, tmp_path, DailyPnLTracker(tmp_path / "pnl.jsonl", timezone_name="UTC"))
    dec = risk.evaluate_buy(free_quote=10_000_000, last_price=1000, open_positions=0, quote_asset="IDR")
    assert dec.approved
    assert dec.size_quote <= 1_000_000


def test_max_daily_notional_blocks(tmp_path: Path):
    s = Settings(max_order_notional=5_000_000, max_daily_notional=1_000_000, min_quote_balance=1, max_position_pct=50)
    pnl = DailyPnLTracker(tmp_path / "pnl.jsonl", timezone_name="UTC")
    pnl.record_trade(side="buy", symbol="BTC/IDR", notional=1_000_000, pnl=0)
    risk = RiskEngine(s, tmp_path, pnl)
    dec = risk.evaluate_buy(free_quote=10_000_000, last_price=1000, open_positions=0, quote_asset="IDR")
    assert not dec.approved
    assert "daily notional" in dec.reason.lower()


def test_daily_loss_activates_kill(tmp_path: Path):
    s = Settings(max_daily_loss_pct=5.0, daily_equity_baseline=1_000_000, min_quote_balance=1)
    pnl = DailyPnLTracker(tmp_path / "pnl.jsonl", timezone_name="UTC")
    pnl.record_trade(side="sell", symbol="BTC/IDR", notional=100, pnl=-60_000)
    risk = RiskEngine(s, tmp_path, pnl)
    risk.set_equity_baseline_if_empty(1_000_000)
    reason = risk.check_daily_limits_or_kill()
    assert reason is not None
    assert risk.kill_switch_active()
