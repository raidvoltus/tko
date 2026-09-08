"""Metrics and backup tests."""

import time
from pathlib import Path

from tko.runtime.backup import backup_state
from tko.runtime.metrics import MetricsStore
from tko.runtime.watchdog import Heartbeat, Watchdog


def test_metrics_and_backup(tmp_path: Path):
    m = MetricsStore(tmp_path / "metrics.json")
    m.record_order(success=True, latency_ms=12.0)
    m.update_pnl("2026-09-08", 100.0, 1000.0)
    snap = m.snapshot()
    assert snap.orders_success == 1
    assert snap.daily_pnl == 100.0

    state = tmp_path / "state"
    state.mkdir()
    (state / "positions.json").write_text("{}", encoding="utf-8")
    out = backup_state(state, tmp_path / "backups")
    assert out.exists()
    assert out.suffix == ".zip"


def test_heartbeat_watchdog(tmp_path: Path):
    hb_path = tmp_path / "heartbeat.json"
    Heartbeat(hb_path).beat(status="OK")
    assert Watchdog(hb_path, stale_after_sec=9999).check_once() is True
    time.sleep(0.05)
    assert Watchdog(hb_path, stale_after_sec=0).check_once() is False
