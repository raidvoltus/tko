"""Tests for durable daily PnL tracker."""

from __future__ import annotations

from pathlib import Path

from tko.risk.pnl_tracker import DailyPnLTracker


def test_record_and_persist(tmp_path: Path):
    path = tmp_path / "pnl.jsonl"
    t = DailyPnLTracker(path, timezone_name="UTC")
    t.record_trade(side="buy", symbol="BTC/IDR", notional=1_000_000, pnl=0.0, order_id="1")
    t.record_trade(side="sell", symbol="BTC/IDR", notional=1_100_000, pnl=100_000, order_id="2")
    assert t.today_notional() == 2_100_000
    assert t.today_realized_pnl() == 100_000
    t2 = DailyPnLTracker(path, timezone_name="UTC")
    assert t2.today_realized_pnl() == 100_000
    assert t2.today_notional() == 2_100_000
    assert path.exists()
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2


def test_stats_for_day(tmp_path: Path):
    t = DailyPnLTracker(tmp_path / "p.jsonl", timezone_name="Asia/Jakarta")
    t.record_trade(side="sell", symbol="ETH/IDR", notional=500, pnl=-50, order_id="x")
    s = t.stats_for_day()
    assert s.trade_count == 1
    assert s.realized_pnl == -50
