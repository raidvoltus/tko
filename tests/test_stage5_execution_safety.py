"""Stage 5 execution safety: atomic daily-notional reservation + UNKNOWN/GOVERNOR blocking."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

from tko.core.config import Settings
from tko.execution.intent import BLOCKS_DUPLICATE, IntentStore, OrderIntentStatus
from tko.risk.engine import RiskEngine
from tko.risk.pnl_tracker import DailyPnLTracker


def _settings(**kwargs) -> Settings:
    base = dict(
        max_order_notional=500_000,
        max_daily_notional=1_000_000,
        max_position_pct=100,
        min_quote_balance=1,
        max_open_positions=10,
    )
    base.update(kwargs)
    return Settings(**base)


def test_atomic_reserve_blocks_concurrent_overshoot(tmp_path: Path):
    """S5-B1: two concurrent reserves of 600k against 1M limit → exactly one succeeds."""
    s = _settings()
    pnl = DailyPnLTracker(tmp_path / "pnl.jsonl", timezone_name="UTC")
    risk = RiskEngine(s, tmp_path, pnl)

    barrier = threading.Barrier(2)

    def worker(rid: str) -> bool:
        barrier.wait(timeout=5)
        ok, _ = risk.try_reserve_notional(600_000, reservation_id=rid)
        return ok

    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(worker, f"cid-{i}") for i in range(2)]
        results = [f.result(timeout=5) for f in futs]

    assert sum(results) == 1, f"exactly one reserve must succeed, got {results}"
    assert risk.reserved_notional() == pytest.approx(600_000)
    ok3, reason = risk.try_reserve_notional(600_000, reservation_id="cid-3")
    assert not ok3
    assert "blocked" in reason.lower() or "exceed" in reason.lower()


def test_reserve_then_commit_frees_budget(tmp_path: Path):
    s = _settings(max_daily_notional=1_000_000)
    risk = RiskEngine(s, tmp_path, DailyPnLTracker(tmp_path / "pnl.jsonl", timezone_name="UTC"))
    ok, _ = risk.try_reserve_notional(800_000, reservation_id="A")
    assert ok
    assert risk.effective_daily_used() == pytest.approx(800_000)
    risk.commit_reservation(
        "A", side="buy", symbol="BTC/IDR", actual_notional=750_000, order_id="ex-1"
    )
    assert risk.reserved_notional() == 0
    assert risk.pnl.today_notional() == pytest.approx(750_000)
    ok2, _ = risk.try_reserve_notional(200_000, reservation_id="B")
    assert ok2
    ok3, _ = risk.try_reserve_notional(100_000, reservation_id="C")
    assert not ok3


def test_release_reservation_restores_budget(tmp_path: Path):
    s = _settings(max_daily_notional=1_000_000)
    risk = RiskEngine(s, tmp_path, DailyPnLTracker(tmp_path / "pnl.jsonl", timezone_name="UTC"))
    assert risk.try_reserve_notional(900_000, reservation_id="X")[0]
    risk.release_reservation("X")
    assert risk.reserved_notional() == 0
    assert risk.try_reserve_notional(900_000, reservation_id="Y")[0]


def test_evaluate_buy_sees_outstanding_reservations(tmp_path: Path):
    s = _settings(max_daily_notional=1_000_000, max_order_notional=1_000_000)
    risk = RiskEngine(s, tmp_path, DailyPnLTracker(tmp_path / "pnl.jsonl", timezone_name="UTC"))
    risk.try_reserve_notional(900_000, reservation_id="held")
    dec = risk.evaluate_buy(free_quote=10_000_000, last_price=1000, open_positions=0, quote_asset="IDR")
    if dec.approved:
        assert dec.size_quote <= 100_000 + 1e-6
    risk.try_reserve_notional(100_000, reservation_id="held2")
    dec2 = risk.evaluate_buy(free_quote=10_000_000, last_price=1000, open_positions=0, quote_asset="IDR")
    assert not dec2.approved
    assert "daily notional" in dec2.reason.lower()


def test_governor_blocks_duplicate_intent(tmp_path: Path):
    """S5-B2: GOVERNOR_AUTONOMOUS remains in BLOCKS_DUPLICATE."""
    assert OrderIntentStatus.GOVERNOR_AUTONOMOUS in BLOCKS_DUPLICATE
    assert OrderIntentStatus.UNKNOWN in BLOCKS_DUPLICATE

    store = IntentStore(tmp_path / "intents.json")
    intent = store.create(symbol="BTC/IDR", side="buy", quote_amount=100_000, last_price=1000)
    intent.status = OrderIntentStatus.GOVERNOR_AUTONOMOUS
    intent.error_category = "ORDER_NOT_FOUND"
    store.update(intent)

    blocked = store.create_if_absent(
        symbol="BTC/IDR", side="buy", quote_amount=50_000, last_price=1000
    )
    assert blocked is None
    assert store.has_blocking_intent("BTC/IDR", "buy")
    gov = store.governor_intents()
    assert len(gov) == 1
    assert gov[0].client_order_id == intent.client_order_id


def test_unknown_blocks_duplicate_intent(tmp_path: Path):
    store = IntentStore(tmp_path / "intents.json")
    intent = store.create(symbol="BTC/IDR", side="buy", quote_amount=100_000, last_price=1000)
    intent.status = OrderIntentStatus.UNKNOWN
    store.update(intent)
    assert store.create_if_absent(symbol="BTC/IDR", side="buy", quote_amount=1, last_price=1) is None


def test_evaluate_entry_exit_aliases(tmp_path: Path):
    from tko.core.types import Signal

    s = _settings()
    risk = RiskEngine(s, tmp_path, DailyPnLTracker(tmp_path / "pnl.jsonl", timezone_name="UTC"))
    dec = risk.evaluate_entry(
        symbol="BTC/IDR", quote_free=5_000_000, last_price=1_000_000, signal=Signal.BUY
    )
    assert dec.approved
    assert dec.size_quote > 0

    dec2 = risk.evaluate_exit(
        symbol="BTC/IDR", base_free=0.01, last_price=1_100_000, entry_price=1_000_000
    )
    assert isinstance(dec2.approved, bool)


def test_concurrent_reserve_stress(tmp_path: Path):
    """Many concurrent small reserves must not exceed max_daily_notional."""
    limit = 1_000_000
    chunk = 50_000
    s = _settings(max_daily_notional=limit, max_order_notional=chunk)
    risk = RiskEngine(s, tmp_path, DailyPnLTracker(tmp_path / "pnl.jsonl", timezone_name="UTC"))
    n_workers = 40
    barrier = threading.Barrier(n_workers)

    def worker(i: int) -> bool:
        barrier.wait(timeout=10)
        ok, _ = risk.try_reserve_notional(chunk, reservation_id=f"w-{i}")
        return ok

    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futs = [pool.submit(worker, i) for i in range(n_workers)]
        successes = [f.result(timeout=10) for f in as_completed(futs)]

    n_ok = sum(1 for x in successes if x)
    assert n_ok == limit // chunk
    assert risk.reserved_notional() == pytest.approx(limit)
