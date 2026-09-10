"""Stage 7: strategy signal + risk gates, reservations, exits (production path)."""

from __future__ import annotations

import math
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tko.core.config import Settings
from tko.core.types import Signal
from tko.risk.engine import RiskEngine
from tko.risk.pnl_tracker import DailyPnLTracker


def _settings(**kw) -> Settings:
    base = dict(
        max_order_notional=500_000,
        max_daily_notional=5_000_000,
        max_position_pct=100,
        min_quote_balance=1,
        max_open_positions=10,
        max_daily_loss_pct=5.0,
        daily_equity_baseline=1_000_000,
        take_profit_pct=2.0,
        stop_loss_pct=1.0,
    )
    base.update(kw)
    return Settings(**base)


def _risk(tmp_path: Path, **kw) -> RiskEngine:
    s = _settings(**kw)
    return RiskEngine(s, tmp_path, DailyPnLTracker(tmp_path / "pnl.jsonl", timezone_name="UTC"))


def test_max_order_notional_exact_and_over(tmp_path: Path):
    risk = _risk(tmp_path, max_order_notional=100_000)
    dec = risk.size_buy_only(free_quote=10_000_000, last_price=1000, open_positions=0, quote_asset="IDR")
    assert dec.approved
    assert dec.size_quote <= 100_000 + 1e-6


def test_max_daily_notional_blocks_at_limit(tmp_path: Path):
    risk = _risk(tmp_path, max_daily_notional=200_000, max_order_notional=500_000)
    risk.pnl.record_trade(side="buy", symbol="BTC/IDR", notional=200_000, pnl=0)
    dec = risk.size_buy_only(free_quote=10_000_000, last_price=1000, open_positions=0, quote_asset="IDR")
    assert not dec.approved
    assert "daily notional" in dec.reason.lower()


def test_max_open_positions_blocks(tmp_path: Path):
    risk = _risk(tmp_path, max_open_positions=2)
    dec = risk.size_buy_only(free_quote=10_000_000, last_price=1000, open_positions=2)
    assert not dec.approved


def test_daily_loss_activates_kill_and_blocks_buy(tmp_path: Path):
    risk = _risk(tmp_path, max_daily_loss_pct=5.0, daily_equity_baseline=1_000_000)
    risk.set_equity_baseline_if_empty(1_000_000)
    risk.pnl.record_trade(side="sell", symbol="BTC/IDR", notional=100, pnl=-60_000)
    reason = risk.check_daily_limits_or_kill()
    assert reason is not None
    assert risk.kill_switch_active()
    dec = risk.evaluate_entry(
        symbol="BTC/IDR", quote_free=10_000_000, last_price=1000, signal=Signal.BUY
    )
    assert not dec.approved


def test_kill_switch_blocks_buy_allows_force_sell(tmp_path: Path):
    risk = _risk(tmp_path)
    risk.activate_kill_switch("test")
    buy = risk.evaluate_entry(
        symbol="BTC/IDR", quote_free=10_000_000, last_price=1000, signal=Signal.BUY
    )
    assert not buy.approved
    sell = risk.evaluate_exit(symbol="BTC/IDR", base_free=0.01, last_price=1000, entry_price=900)
    assert sell.approved  # force sell under kill


def test_kill_persists_across_engine_instances(tmp_path: Path):
    risk = _risk(tmp_path)
    risk.activate_kill_switch("persist")
    risk2 = _risk(tmp_path)
    assert risk2.kill_switch_active()


def test_trade_decision_buy_accepted(tmp_path: Path):
    risk = _risk(tmp_path)
    dec = risk.evaluate_entry(
        symbol="BTC/IDR", quote_free=10_000_000, last_price=1000, signal=Signal.BUY
    )
    assert dec.approved


def test_trade_decision_hold_blocked(tmp_path: Path):
    risk = _risk(tmp_path)
    dec = risk.evaluate_entry(
        symbol="BTC/IDR", quote_free=10_000_000, last_price=1000, signal=Signal.HOLD
    )
    assert not dec.approved


def test_trade_decision_sell_blocked_on_entry(tmp_path: Path):
    risk = _risk(tmp_path)
    dec = risk.evaluate_entry(
        symbol="BTC/IDR", quote_free=10_000_000, last_price=1000, signal=Signal.SELL
    )
    assert not dec.approved


def test_nan_inf_price_blocked(tmp_path: Path):
    risk = _risk(tmp_path)
    for bad in (float("nan"), float("inf"), float("-inf")):
        dec = risk.size_buy_only(free_quote=10_000_000, last_price=bad, open_positions=0)
        assert not dec.approved


def test_nan_free_quote_blocked(tmp_path: Path):
    risk = _risk(tmp_path)
    dec = risk.size_buy_only(free_quote=float("nan"), last_price=1000, open_positions=0)
    assert not dec.approved


def test_invalid_strength_blocked(tmp_path: Path):
    risk = _risk(tmp_path)

    class Bad:
        signal = Signal.BUY
        strength = float("nan")

    dec = risk.evaluate_entry(
        symbol="BTC/IDR", quote_free=10_000_000, last_price=1000, signal=Bad()
    )
    assert not dec.approved


def test_entry_signal_none_blocked(tmp_path: Path):
    risk = _risk(tmp_path)
    dec = risk.evaluate_entry(
        symbol="BTC/IDR", quote_free=10_000_000, last_price=1000, signal=None
    )
    assert not dec.approved
    assert "signal" in dec.reason.lower()


def test_reserve_rejects_nan_inf(tmp_path: Path):
    risk = _risk(tmp_path)
    for bad in (float("nan"), float("inf"), 0.0, -1.0):
        ok, _ = risk.try_reserve_notional(bad, reservation_id="x")
        assert not ok


def test_commit_rejects_nan_notional(tmp_path: Path):
    risk = _risk(tmp_path)
    risk.try_reserve_notional(100_000, reservation_id="c1")
    risk.commit_reservation(
        "c1", side="buy", symbol="BTC/IDR", actual_notional=float("nan")
    )
    # non-finite rejected: reservation cleared without recording bogus notional
    assert risk.reserved_notional() == 0


def test_reservation_blocks_over_budget(tmp_path: Path):
    risk = _risk(tmp_path, max_daily_notional=100_000)
    assert risk.try_reserve_notional(80_000, reservation_id="a")[0]
    ok, reason = risk.try_reserve_notional(30_000, reservation_id="b")
    assert not ok


def test_reservation_release_on_reject(tmp_path: Path):
    risk = _risk(tmp_path, max_daily_notional=100_000)
    assert risk.try_reserve_notional(80_000, reservation_id="r")[0]
    risk.release_reservation("r")
    assert risk.try_reserve_notional(80_000, reservation_id="r2")[0]


def test_reservation_commit_full(tmp_path: Path):
    risk = _risk(tmp_path, max_daily_notional=500_000)
    assert risk.try_reserve_notional(100_000, reservation_id="f")[0]
    risk.commit_reservation("f", side="buy", symbol="BTC/IDR", actual_notional=90_000)
    assert risk.reserved_notional() == 0
    assert risk.pnl.today_notional() == pytest.approx(90_000)


def test_reservation_partial_rereserve_fail_closed(tmp_path: Path):
    risk = _risk(tmp_path, max_daily_notional=100_000)
    assert risk.try_reserve_notional(100_000, reservation_id="p")[0]
    ok, _ = risk.commit_partial_and_rereserve(
        "p",
        side="buy",
        symbol="BTC/IDR",
        filled_notional=40_000,
        remaining_reserve=60_000,
    )
    assert ok
    assert risk.reserved_notional() == pytest.approx(60_000)


def test_reservation_never_negative(tmp_path: Path):
    risk = _risk(tmp_path)
    risk.release_reservation("missing")
    assert risk.reserved_notional() >= 0


def test_concurrent_reservation_cannot_double_spend(tmp_path: Path):
    risk = _risk(tmp_path, max_daily_notional=100_000)
    barrier = threading.Barrier(2)

    def worker(rid: str) -> bool:
        barrier.wait(timeout=5)
        ok, _ = risk.try_reserve_notional(80_000, reservation_id=rid)
        return ok

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [f.result(timeout=5) for f in [pool.submit(worker, f"c{i}") for i in range(2)]]
    assert sum(results) == 1


def test_take_profit_exit(tmp_path: Path):
    risk = _risk(tmp_path, take_profit_pct=2.0)
    dec = risk.evaluate_exit(
        symbol="BTC/IDR", base_free=0.01, last_price=1020, entry_price=1000
    )
    assert dec.approved
    assert "take profit" in dec.reason.lower()


def test_stop_loss_exit(tmp_path: Path):
    risk = _risk(tmp_path, stop_loss_pct=1.0)
    dec = risk.evaluate_exit(
        symbol="BTC/IDR", base_free=0.01, last_price=980, entry_price=1000
    )
    assert dec.approved
    assert "stop loss" in dec.reason.lower()


def test_sell_capped_by_max_order_notional(tmp_path: Path):
    risk = _risk(tmp_path, max_order_notional=50_000)
    dec = risk.evaluate_exit(
        symbol="BTC/IDR", base_free=1.0, last_price=100_000, entry_price=100_000, signal_sell=True
    )
    assert dec.approved
    assert dec.size_base * 100_000 <= 50_000 + 1e-3


def test_strategy_signal_still_requires_risk_approval(tmp_path: Path):
    risk = _risk(tmp_path, max_open_positions=0)
    dec = risk.evaluate_entry(
        symbol="BTC/IDR", quote_free=10_000_000, last_price=1000, signal=Signal.BUY, open_positions=0
    )
    assert not dec.approved


def test_evaluate_buy_removed_cannot_authorize(tmp_path: Path):
    """Production must not use evaluate_buy as BUY authorization."""
    risk = _risk(tmp_path)
    with pytest.raises(RuntimeError, match="not a production authorization path"):
        risk.evaluate_buy(free_quote=10_000_000, last_price=1000, open_positions=0)
