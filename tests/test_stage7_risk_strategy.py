"""Stage 7 — Risk & Strategy hard safety boundary regression."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from tko.core.config import Settings
from tko.core.types import Signal
from tko.risk.engine import RiskEngine
from tko.risk.pnl_tracker import DailyPnLTracker
from tko.strategy.btc import TradeDecision


def _s(**kw) -> Settings:
    base = dict(
        max_order_notional=1_000_000,
        max_daily_notional=5_000_000,
        max_position_pct=50,
        min_quote_balance=1,
        max_open_positions=3,
        max_daily_loss_pct=5.0,
        daily_equity_baseline=1_000_000,
        stop_loss_pct=3.0,
        take_profit_pct=5.0,
    )
    base.update(kw)
    return Settings(**base)


def _risk(tmp_path: Path, s: Settings | None = None, pnl=None) -> RiskEngine:
    s = s or _s()
    pnl = pnl or DailyPnLTracker(tmp_path / "pnl.jsonl", timezone_name="UTC")
    return RiskEngine(s, tmp_path, pnl)


def test_max_order_notional_exact_and_over(tmp_path: Path):
    risk = _risk(tmp_path, _s(max_order_notional=1_000_000, max_position_pct=100, min_quote_balance=1))
    dec = risk.evaluate_buy(free_quote=10_000_000, last_price=1000, open_positions=0)
    assert dec.approved
    assert dec.size_quote == pytest.approx(1_000_000)


def test_max_daily_notional_blocks_at_limit(tmp_path: Path):
    s = _s(max_order_notional=500_000, max_daily_notional=1_000_000)
    pnl = DailyPnLTracker(tmp_path / "pnl.jsonl", timezone_name="UTC")
    pnl.record_trade(side="buy", symbol="X", notional=1_000_000, pnl=0)
    risk = _risk(tmp_path, s, pnl)
    dec = risk.evaluate_buy(free_quote=10_000_000, last_price=1000, open_positions=0)
    assert not dec.approved


def test_max_open_positions_blocks(tmp_path: Path):
    risk = _risk(tmp_path, _s(max_open_positions=2))
    dec = risk.evaluate_buy(free_quote=10_000_000, last_price=1000, open_positions=2)
    assert not dec.approved
    assert "position" in dec.reason.lower()


def test_daily_loss_activates_kill_and_blocks_buy(tmp_path: Path):
    s = _s(max_daily_loss_pct=5.0, daily_equity_baseline=1_000_000)
    pnl = DailyPnLTracker(tmp_path / "pnl.jsonl", timezone_name="UTC")
    pnl.record_trade(side="sell", symbol="X", notional=100, pnl=-60_000)
    risk = _risk(tmp_path, s, pnl)
    risk.set_equity_baseline_if_empty(1_000_000)
    assert risk.check_daily_limits_or_kill() is not None
    assert risk.kill_switch_active()
    dec = risk.evaluate_entry(symbol="BTC/IDR", quote_free=10_000_000, last_price=1000, signal=Signal.BUY)
    assert not dec.approved
    assert "kill" in dec.reason.lower()


def test_kill_switch_blocks_buy_allows_force_sell(tmp_path: Path):
    risk = _risk(tmp_path)
    risk.activate_kill_switch("test")
    assert risk.kill_switch_active()
    buy = risk.evaluate_buy(free_quote=10_000_000, last_price=1000, open_positions=0)
    assert not buy.approved
    sell = risk.evaluate_sell(free_base=0.5, entry_price=1000, last_price=900, signal_sell=False)
    assert sell.approved and "kill" in sell.reason.lower()
    assert sell.size_base == pytest.approx(0.5)


def test_kill_persists_across_engine_instances(tmp_path: Path):
    r1 = _risk(tmp_path)
    r1.activate_kill_switch("persist")
    r2 = _risk(tmp_path)
    assert r2.kill_switch_active()


def test_trade_decision_buy_accepted(tmp_path: Path):
    risk = _risk(tmp_path)
    td = TradeDecision(Signal.BUY, "test", 0.8, 1000.0)
    dec = risk.evaluate_entry(
        symbol="BTC/IDR", quote_free=10_000_000, last_price=1000, signal=td, open_positions=0
    )
    assert dec.approved


def test_trade_decision_hold_blocked(tmp_path: Path):
    risk = _risk(tmp_path)
    td = TradeDecision(Signal.HOLD, "noop", 0.0, 1000.0)
    dec = risk.evaluate_entry(
        symbol="BTC/IDR", quote_free=10_000_000, last_price=1000, signal=td
    )
    assert not dec.approved


def test_trade_decision_sell_blocked_on_entry(tmp_path: Path):
    risk = _risk(tmp_path)
    td = TradeDecision(Signal.SELL, "exit", 0.7, 1000.0)
    dec = risk.evaluate_entry(
        symbol="BTC/IDR", quote_free=10_000_000, last_price=1000, signal=td
    )
    assert not dec.approved


def test_nan_inf_price_blocked(tmp_path: Path):
    risk = _risk(tmp_path)
    for bad in (float("nan"), float("inf"), -1.0, 0.0):
        dec = risk.evaluate_buy(free_quote=10_000_000, last_price=bad, open_positions=0)
        assert not dec.approved, f"price={bad} should block"


def test_nan_free_quote_blocked(tmp_path: Path):
    risk = _risk(tmp_path)
    dec = risk.evaluate_buy(free_quote=float("nan"), last_price=1000, open_positions=0)
    assert not dec.approved


def test_invalid_strength_blocked(tmp_path: Path):
    risk = _risk(tmp_path)
    td = TradeDecision(Signal.BUY, "bad", float("nan"), 1000.0)
    dec = risk.evaluate_entry(
        symbol="BTC/IDR", quote_free=10_000_000, last_price=1000, signal=td
    )
    assert not dec.approved


def test_reservation_blocks_over_budget(tmp_path: Path):
    risk = _risk(tmp_path, _s(max_daily_notional=1_000_000, max_order_notional=800_000))
    ok, _ = risk.try_reserve_notional(800_000, reservation_id="a")
    assert ok
    ok2, reason = risk.try_reserve_notional(300_000, reservation_id="b")
    assert not ok2
    assert risk.reserved_notional() == pytest.approx(800_000)


def test_reservation_release_on_reject(tmp_path: Path):
    risk = _risk(tmp_path)
    risk.try_reserve_notional(500_000, reservation_id="cid")
    risk.release_reservation("cid")
    assert risk.reserved_notional() == pytest.approx(0.0)


def test_reservation_commit_full(tmp_path: Path):
    risk = _risk(tmp_path)
    risk.try_reserve_notional(500_000, reservation_id="cid")
    risk.commit_reservation(
        "cid", side="buy", symbol="BTC/IDR", actual_notional=480_000, pnl=0.0
    )
    assert risk.reserved_notional() == pytest.approx(0.0)
    assert risk.pnl.today_notional() == pytest.approx(480_000)


def test_reservation_partial_rereserve_fail_closed(tmp_path: Path):
    s = _s(max_daily_notional=1_000_000, max_order_notional=1_000_000)
    risk = _risk(tmp_path, s)
    risk.try_reserve_notional(800_000, reservation_id="cid")
    risk.commit_partial_and_rereserve(
        "cid", side="buy", symbol="BTC/IDR",
        filled_notional=400_000, remaining_reserve=500_000,
    )
    assert risk.effective_daily_used() <= 1_000_000 + 1e-6


def test_reservation_never_negative(tmp_path: Path):
    risk = _risk(tmp_path)
    risk.release_reservation("missing")
    assert risk.reserved_notional() >= 0


def test_concurrent_reservation_cannot_double_spend(tmp_path: Path):
    risk = _risk(tmp_path, _s(max_daily_notional=1_000_000, max_order_notional=1_000_000))
    results: list[bool] = []
    lock = threading.Lock()

    def worker(i: int):
        ok, _ = risk.try_reserve_notional(600_000, reservation_id=f"t{i}")
        with lock:
            results.append(ok)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert sum(1 for r in results if r) == 1
    assert risk.reserved_notional() == pytest.approx(600_000)


def test_take_profit_exit(tmp_path: Path):
    risk = _risk(tmp_path, _s(take_profit_pct=5.0))
    dec = risk.evaluate_sell(free_base=1.0, entry_price=1000, last_price=1060, signal_sell=False)
    assert dec.approved
    assert "take profit" in dec.reason.lower()


def test_stop_loss_exit(tmp_path: Path):
    risk = _risk(tmp_path, _s(stop_loss_pct=3.0))
    dec = risk.evaluate_sell(free_base=1.0, entry_price=1000, last_price=960, signal_sell=False)
    assert dec.approved
    assert "stop loss" in dec.reason.lower()


def test_sell_capped_by_max_order_notional(tmp_path: Path):
    risk = _risk(tmp_path, _s(max_order_notional=500_000))
    dec = risk.evaluate_sell(free_base=1.0, entry_price=900, last_price=1_000_000, signal_sell=True)
    assert dec.approved
    assert dec.size_base * 1_000_000 <= 500_000 + 1e-6


def test_strategy_signal_still_requires_risk_approval(tmp_path: Path):
    s = _s(max_daily_notional=100_000, max_order_notional=100_000)
    pnl = DailyPnLTracker(tmp_path / "pnl.jsonl", timezone_name="UTC")
    pnl.record_trade(side="buy", symbol="X", notional=100_000, pnl=0)
    risk = _risk(tmp_path, s, pnl)
    td = TradeDecision(Signal.BUY, "strong", 1.0, 1000.0)
    dec = risk.evaluate_entry(
        symbol="BTC/IDR", quote_free=10_000_000, last_price=1000, signal=td
    )
    assert not dec.approved
