"""Stage 5 execution safety: reservations visible to sizing helper."""

from pathlib import Path

from tko.core.config import Settings
from tko.risk.engine import RiskEngine
from tko.risk.pnl_tracker import DailyPnLTracker


def test_size_buy_only_sees_outstanding_reservations(tmp_path: Path):
    s = Settings()
    s.max_daily_notional = 5_000_000
    risk = RiskEngine(s, tmp_path, pnl_tracker=DailyPnLTracker(tmp_path / "pnl.jsonl"))
    ok, _ = risk.try_reserve_notional(4_000_000, reservation_id="r1")
    assert ok
    dec = risk.size_buy_only(free_quote=10_000_000, last_price=1000, open_positions=0, quote_asset="IDR")
    # Remaining daily budget should constrain size
    assert dec.approved
    assert dec.size_quote <= 1_000_000 + 1e-3
    ok2, reason = risk.try_reserve_notional(2_000_000, reservation_id="r2")
    assert not ok2
    dec2 = risk.size_buy_only(free_quote=10_000_000, last_price=1000, open_positions=0, quote_asset="IDR")
    # May still approve a smaller slice of residual headroom
    if dec2.approved:
        assert dec2.size_quote <= 1_000_000 + 1e-3
