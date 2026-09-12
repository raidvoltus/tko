"""Stage 4 concurrent TOCTOU regression tests."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

from tko.core.config import Settings
from tko.execution.engine import ExecutionEngine
from tko.risk.engine import RiskDecision
from tko.runtime.lifecycle import LifecycleGovernor, LifecycleState


def _to_ready(g, *, reason: str = "test_ready") -> None:
    if g.state == LifecycleState.STARTING:
        g.transition(LifecycleState.RECONCILING, reason="test_recon")
    elif g.state != LifecycleState.RECONCILING:
        g.force(LifecycleState.STARTING, reason="test_reset")
        g.transition(LifecycleState.RECONCILING, reason="test_recon")
    assert g.authorize_ready(
        reason=reason,
        recon_ok=True,
        kill_switch_clear=True,
        circuit_clear=True,
        daily_risk_ok=True,
        positions_ok=True,
        exchange_ok=True,
    )


def test_inv33_concurrent_toctou_create_order_never_reaches_exchange(tmp_path: Path):
    create_calls: list = []
    barrier = threading.Barrier(2, timeout=1)

    def slow_create_order(**kwargs):
        create_calls.append({"kwargs": kwargs, "state": lc.state.value})
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            pass
        return MagicMock(id="x", filled=0.0, average=0.0, status="closed")

    client = MagicMock()
    client.circuit_open = False
    client.create_order.side_effect = slow_create_order
    client.validate_symbol_ready.return_value = (True, "ok")
    constraints = MagicMock()
    constraints.validate_notional.return_value = (True, "ok")
    constraints.normalize_quantity.side_effect = lambda q, market_order=False: q
    constraints.validate_quantity.return_value = (True, "ok")
    client.get_constraints.return_value = constraints

    lc = LifecycleGovernor()
    _to_ready(lc, reason="ok")
    eng = ExecutionEngine(client, Settings(min_quote_balance=1), tmp_path, lifecycle=lc)
    dec = RiskDecision(True, "approved", size_quote=10000, size_base=0.01)

    def buyer():
        eng.buy("BTC/IDR", "BTC", "IDR", dec, last_price=1000.0)

    def stopper():
        time.sleep(0.02)
        lc.request_stop()
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            pass

    t1 = threading.Thread(target=buyer)
    t2 = threading.Thread(target=stopper)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert lc.trading_authorized is False
    assert lc.state == LifecycleState.STOPPING
    for call in create_calls:
        assert call["state"] == "READY", f"create_order ran under state={call['state']}"


def test_inv33_stop_before_submit_blocks(tmp_path: Path):
    client = MagicMock()
    client.circuit_open = False
    client.validate_symbol_ready.return_value = (True, "ok")
    constraints = MagicMock()
    constraints.validate_notional.return_value = (True, "ok")
    constraints.normalize_quantity.side_effect = lambda q, market_order=False: q
    constraints.validate_quantity.return_value = (True, "ok")
    client.get_constraints.return_value = constraints

    lc = LifecycleGovernor()
    _to_ready(lc, reason="ok")
    eng = ExecutionEngine(client, Settings(min_quote_balance=1), tmp_path, lifecycle=lc)
    dec = RiskDecision(True, "approved", size_quote=10000, size_base=0.01)
    lc.request_stop()
    assert eng.buy("BTC/IDR", "BTC", "IDR", dec, last_price=1000.0) is None
    assert client.create_order.call_count == 0


def test_run_authorized_submit_mutex_blocks_stop_during_submit():
    g = LifecycleGovernor()
    _to_ready(g, reason="ok")
    order: list = []
    started = threading.Event()
    release = threading.Event()

    def submit_fn():
        started.set()
        release.wait(timeout=2)
        order.append("done")
        return "OK"

    def stopper():
        started.wait(timeout=2)
        g.request_stop()
        order.append("stopped")

    t = threading.Thread(target=stopper)
    t.start()
    result = g.run_authorized_submit(submit_fn)
    release.set()
    t.join(timeout=2)
    assert result == "OK"
    assert order[0] == "done"
    assert "stopped" in order
    assert g.state == LifecycleState.STOPPING
    assert g.trading_authorized is False
