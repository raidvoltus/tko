"""Risk hard caps and sizing-only helper (not production BUY authorization)."""

from pathlib import Path

from tko.core.config import Settings
from tko.risk.engine import RiskEngine
from tko.risk.pnl_tracker import DailyPnLTracker


def _risk(tmp_path: Path) -> RiskEngine:
    s = Settings()
    return RiskEngine(s, tmp_path, pnl_tracker=DailyPnLTracker(tmp_path / "pnl.jsonl"))


def test_size_respects_max_position_pct(tmp_path: Path):
    risk = _risk(tmp_path)
    dec = risk.size_buy_only(free_quote=10_000_000, last_price=1000, open_positions=0, quote_asset="IDR")
    assert dec.approved
    assert dec.size_quote <= 10_000_000 * (risk.s.max_position_pct / 100.0) + 1e-6


def test_max_open_positions_blocks(tmp_path: Path):
    risk = _risk(tmp_path)
    # force open_positions at limit
    risk.s.max_open_positions = 1
    dec = risk.size_buy_only(free_quote=10_000_000, last_price=1000, open_positions=1, quote_asset="IDR")
    assert not dec.approved
    assert "max open" in dec.reason.lower()


def test_size_respects_max_order_notional(tmp_path: Path):
    risk = _risk(tmp_path)
    risk.s.max_order_notional = 1_000_000
    dec = risk.size_buy_only(free_quote=10_000_000, last_price=1000, open_positions=0, quote_asset="IDR")
    assert dec.approved
    assert dec.size_quote <= 1_000_000 + 1e-6
