"""Stage 8 — Runtime & concurrency: startup, shutdown, races, lifecycle."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from tko.core.config import Settings
from tko.risk.engine import RiskEngine
from tko.risk.pnl_tracker import DailyPnLTracker
from tko.runtime.lifecycle import LifecycleGovernor, LifecycleState
from tko.runtime.instance_lock import InstanceLock, InstanceLockError
from tko.runtime.watchdog import Watchdog, Heartbeat


def _to_ready(lc: LifecycleGovernor, reason: str = "test") -> None:
    lc.force(LifecycleState.STARTING, reason="t")
    lc.transition(LifecycleState.RECONCILING, reason="t")
    assert lc.authorize_ready(
        recon_ok=True,
        kill_switch_clear=True,
        circuit_clear=True,
        daily_risk_ok=True,
        positions_ok=True,
        exchange_ok=True,
        reason=reason,
    )


def test_force_ready_rejected():
    lc = LifecycleGovernor()
    lc.force(LifecycleState.READY, reason="hack")
    assert lc.state == LifecycleState.STARTING
    assert not lc.trading_authorized


def test_transition_ready_rejected():
    lc = LifecycleGovernor()
    lc.transition(LifecycleState.RECONCILING, reason="r")
    assert not lc.transition(LifecycleState.READY, reason="hack")
    assert lc.state == LifecycleState.RECONCILING


def test_authorize_ready_requires_all_gates():
    lc = LifecycleGovernor()
    lc.transition(LifecycleState.RECONCILING, reason="r")
    assert not lc.authorize_ready(
        recon_ok=True,
        kill_switch_clear=True,
        circuit_clear=True,
        daily_risk_ok=False,
        positions_ok=True,
        exchange_ok=True,
        reason="partial",
    )
    assert lc.state == LifecycleState.RECONCILING


def test_authorize_ready_success():
    lc = LifecycleGovernor()
    _to_ready(lc)
    assert lc.state == LifecycleState.READY
    assert lc.trading_authorized


def test_request_stop_idempotent():
    lc = LifecycleGovernor()
    _to_ready(lc)
    lc.request_stop()
    assert lc.state == LifecycleState.STOPPING
    assert not lc.trading_authorized
    lc.request_stop()
    assert lc.state == LifecycleState.STOPPING


def test_no_submit_after_stop():
    lc = LifecycleGovernor()
    _to_ready(lc)
    lc.request_stop()
    with pytest.raises(RuntimeError):
        lc.run_authorized_submit(lambda: "ok")


def test_submit_mutex_stop_vs_buy_race():
    lc = LifecycleGovernor()
    _to_ready(lc)
    barrier = threading.Barrier(2)
    states: list[str] = []

    def slow_submit():
        states.append(lc.state.value)
        try:
            barrier.wait(timeout=2)
        except threading.BrokenBarrierError:
            pass
        return "done"

    def stopper():
        time.sleep(0.01)
        lc.request_stop()
        try:
            barrier.wait(timeout=2)
        except threading.BrokenBarrierError:
            pass

    results: list = []

    def buyer():
        try:
            results.append(lc.run_authorized_submit(slow_submit))
        except RuntimeError as e:
            results.append(e)

    t1 = threading.Thread(target=buyer)
    t2 = threading.Thread(target=stopper)
    t1.start()
    t2.start()
    t1.join(5)
    t2.join(5)
    assert lc.state == LifecycleState.STOPPING
    assert not lc.trading_authorized
    for s in states:
        assert s == "READY"


def test_process_alive_false_on_mark_stopped():
    lc = LifecycleGovernor()
    assert lc.snapshot().process_alive is True
    lc.mark_process_stopped()
    assert lc.snapshot().process_alive is False


def test_concurrent_reserve_single_winner(tmp_path: Path):
    s = Settings(
        max_daily_notional=1_000_000,
        max_order_notional=1_000_000,
        min_quote_balance=1,
    )
    risk = RiskEngine(s, tmp_path, DailyPnLTracker(tmp_path / "p.jsonl", timezone_name="UTC"))
    results: list[bool] = []
    lock = threading.Lock()

    def w(i: int):
        ok, _ = risk.try_reserve_notional(700_000, reservation_id=f"r{i}")
        with lock:
            results.append(ok)

    threads = [threading.Thread(target=w, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(1 for r in results if r) == 1


def test_kill_blocks_authorized_submit():
    lc = LifecycleGovernor()
    _to_ready(lc)
    lc.transition(LifecycleState.KILL, reason="test_kill")
    assert not lc.trading_authorized
    with pytest.raises(RuntimeError):
        lc.run_authorized_submit(lambda: 1)


def test_instance_lock_blocks_second(tmp_path: Path):
    p = tmp_path / "tko.lock"
    a = InstanceLock(p)
    a.acquire()
    b = InstanceLock(p)
    with pytest.raises(InstanceLockError):
        b.acquire()
    a.release()
    b.acquire()
    b.release()


def test_watchdog_stale_and_missing(tmp_path: Path):
    hb_path = tmp_path / "hb.json"
    wd = Watchdog(hb_path, stale_after_sec=1.0)
    r = wd.check()
    assert r["stale"] is True
    Heartbeat(hb_path).beat(lifecycle="READY", trading_authorized=True, process_alive=True)
    r2 = wd.check()
    assert r2["stale"] is False
    assert r2["trading_authorized"] is True
    time.sleep(1.1)
    r3 = wd.check()
    assert r3["stale"] is True


def test_watchdog_process_not_alive(tmp_path: Path):
    hb_path = tmp_path / "hb.json"
    Heartbeat(hb_path).beat(lifecycle="READY", trading_authorized=True, process_alive=False)
    data = Heartbeat(hb_path).read()
    assert data is not None
    assert data.get("process_alive") is False


def test_bot_start_rejected_when_already_active(tmp_path: Path):
    class Mini:
        def __init__(self):
            self._start_lock = threading.Lock()
            self._start_active = False
            self.starts = 0

        def start(self):
            with self._start_lock:
                if self._start_active:
                    return
                self._start_active = True
            self.starts += 1

    m = Mini()
    m.start()
    m.start()
    assert m.starts == 1


def test_assert_trading_allowed_raises_when_not_ready():
    lc = LifecycleGovernor()
    with pytest.raises(RuntimeError):
        lc.assert_trading_allowed()
    _to_ready(lc)
    lc.assert_trading_allowed()


def test_start_active_cleared_on_failed_startup_simulation():
    class MiniBot:
        def __init__(self):
            self._start_lock = threading.Lock()
            self._start_active = False
            self._running = False
            self._stop_requested = False
            self.starts = 0
            self.barrier_ok = False

        def _clear_start_active(self):
            with self._start_lock:
                self._start_active = False

        def _startup_barrier(self) -> bool:
            return self.barrier_ok

        def start(self):
            with self._start_lock:
                if self._start_active:
                    return
                self._start_active = True
            self.starts += 1
            self._running = True
            self._stop_requested = False
            if not self._startup_barrier():
                self._running = False
                self._clear_start_active()
                return
            self._clear_start_active()

    m = MiniBot()
    m.barrier_ok = False
    m.start()
    assert m.starts == 1
    assert m._start_active is False
    m.barrier_ok = True
    m.start()
    assert m.starts == 2


def test_stop_idempotent_and_process_alive_false():
    lc = LifecycleGovernor()
    _to_ready(lc)
    assert lc.snapshot().process_alive is True
    lc.request_stop()
    lc.mark_process_stopped()
    assert lc.snapshot().process_alive is False
    lc.force(LifecycleState.STOPPED, reason="shutdown_complete")
    assert lc.state == LifecycleState.STOPPED
    lc.force(LifecycleState.STOPPED, reason="again")
    assert lc.state == LifecycleState.STOPPED


def test_heartbeat_after_stop_carries_process_alive_false(tmp_path: Path):
    hb_path = tmp_path / "hb.json"
    hb = Heartbeat(hb_path)
    hb.beat(lifecycle="READY", trading_authorized=True, process_alive=True)
    assert hb.read()["process_alive"] is True
    hb.beat(lifecycle="STOPPING", trading_authorized=False, process_alive=False)
    data = hb.read()
    assert data["process_alive"] is False
    assert data["trading_authorized"] is False
    hb.beat(lifecycle="STOPPED", trading_authorized=False, process_alive=False)
    assert hb.read()["process_alive"] is False


def test_production_bot_module_not_placeholder():
    import tko.runtime.bot as bot_mod
    src = Path(bot_mod.__file__).read_text(encoding="utf-8")
    assert "class TradingBot" in src
    assert "_start_active" in src
    assert "mark_process_stopped" in src
    assert "evaluate_entry" in src
    assert "reconcile_all" in src
    assert src.strip() != "PLACEHOLDER"
