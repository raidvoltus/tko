"""Stage 7 risk/strategy: signal-aware evaluate_entry is sole BUY authorization."""

from pathlib import Path

import pytest

from tko.core.config import Settings
from tko.core.types import Signal
from tko.risk.engine import RiskEngine
from tko.risk.pnl_tracker import DailyPnLTracker


def _risk(tmp_path: Path) -> RiskEngine:
    s = Settings()
    return RiskEngine(s, tmp_path, pnl_tracker=DailyPnLTracker(tmp_path / "pnl.jsonl"))


def test_evaluate_entry_requires_buy_signal(tmp_path: Path):
    risk = _risk(tmp_path)
    dec = risk.evaluate_entry(
        symbol="BTC/IDR",
        quote_free=10_000_000,
        last_price=1000,
        signal=None,
        open_positions=0,
    )
    assert not dec.approved
    assert "signal" in dec.reason.lower()


def test_evaluate_entry_rejects_non_buy(tmp_path: Path):
    risk = _risk(tmp_path)
    dec = risk.evaluate_entry(
        symbol="BTC/IDR",
        quote_free=10_000_000,
        last_price=1000,
        signal=Signal.SELL,
        open_positions=0,
    )
    assert not dec.approved


def test_evaluate_entry_approves_buy_signal(tmp_path: Path):
    risk = _risk(tmp_path)
    dec = risk.evaluate_entry(
        symbol="BTC/IDR",
        quote_free=10_000_000,
        last_price=1000,
        signal=Signal.BUY,
        open_positions=0,
    )
    assert dec.approved
    assert dec.size_quote > 0


def test_size_buy_only_approves_without_signal(tmp_path: Path):
    """Sizing helper has no signal gate — must not be used for live authorization."""
    risk = _risk(tmp_path)
    dec = risk.size_buy_only(free_quote=10_000_000, last_price=1000, open_positions=0)
    assert dec.approved


def test_size_buy_only_respects_max_open(tmp_path: Path):
    risk = _risk(tmp_path)
    risk.s.max_open_positions = 2
    dec = risk.size_buy_only(free_quote=10_000_000, last_price=1000, open_positions=2)
    assert not dec.approved


def test_size_buy_only_rejects_nan(tmp_path: Path):
    risk = _risk(tmp_path)
    for bad in (float("nan"), float("inf"), float("-inf")):
        dec = risk.size_buy_only(free_quote=10_000_000, last_price=bad, open_positions=0)
        assert not dec.approved
    dec = risk.size_buy_only(free_quote=float("nan"), last_price=1000, open_positions=0)
    assert not dec.approved


def test_evaluate_buy_removed_cannot_authorize(tmp_path: Path):
    """Production must not use evaluate_buy as BUY authorization."""
    risk = _risk(tmp_path)
    with pytest.raises(RuntimeError, match="not a production authorization path"):
        risk.evaluate_buy(free_quote=10_000_000, last_price=1000, open_positions=0)
