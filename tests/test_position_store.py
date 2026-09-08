"""Tests for position store."""

from __future__ import annotations

from pathlib import Path

from tko.risk.position_store import PositionStore


def test_upsert_and_reload(tmp_path: Path):
    path = tmp_path / "positions.json"
    store = PositionStore(path)
    store.upsert(symbol="BTC/IDR", base="BTC", quote="IDR", amount=0.01, entry_price=1e9, order_id="o1")
    store2 = PositionStore(path)
    pos = store2.get("BTC/IDR")
    assert pos is not None
    assert pos.amount == 0.01
    assert pos.entry_price == 1e9


def test_average_in_and_reduce(tmp_path: Path):
    store = PositionStore(tmp_path / "p.json")
    store.upsert(symbol="BTC/IDR", base="BTC", quote="IDR", amount=1.0, entry_price=100.0)
    store.upsert(symbol="BTC/IDR", base="BTC", quote="IDR", amount=1.0, entry_price=200.0)
    pos = store.get("BTC/IDR")
    assert pos is not None
    assert pos.amount == 2.0
    assert pos.entry_price == 150.0
    store.reduce_or_close("BTC/IDR", 2.0)
    assert store.get("BTC/IDR") is None


def test_reconcile_removes_missing(tmp_path: Path):
    store = PositionStore(tmp_path / "p.json")
    store.upsert(symbol="BTC/IDR", base="BTC", quote="IDR", amount=0.5, entry_price=1.0)
    notes = store.reconcile_with_balances({"ETH": 1.0}, min_dust=1e-8)
    assert store.get("BTC/IDR") is None
    assert notes
