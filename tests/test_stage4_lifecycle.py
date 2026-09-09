"""Stage 4 lifecycle governor regression tests (INV-20..INV-47)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tko.core.config import Settings
from tko.runtime.lifecycle import LifecycleGovernor, LifecycleState
from tko.runtime.watchdog import Heartbeat, Watchdog


def test_inv21_only_ready_authorizes():
    g = LifecycleGovernor()
    assert g.state == LifecycleState.STARTING
    assert g.trading_authorized is False
    g.transition(LifecycleState.RECONCILING, reason="t")
    assert g.trading_authorized is False
    g.transition(LifecycleState.READY, reason="ok")
    assert g.trading_authorized is True
    g.transition(LifecycleState.HALTED, reason="fail")
    assert g.trading_authorized is False


def test_inv25_stopping_blocks():
    g = LifecycleGovernor()
    g.force(LifecycleState.READY, reason="x")
    g.force(LifecycleState.STOPPING, reason="sig")
    assert g.trading_authorized is False
    g.force(LifecycleState.STOPPED, reason="done")
    assert g.trading_authorized is False


def test_inv26_halted_blocks():
    g = LifecycleGovernor()
    g.force(LifecycleState.HALTED, reason="err")
    assert g.trading_authorized is False
    with pytest.raises(RuntimeError, match="not authorized"):
        g.assert_trading_allowed()


def test_inv27_heartbeat_not_readiness(tmp_path: Path):
    hb = Heartbeat(tmp_path / "hb.json")
    hb.beat(status="OK", lifecycle="HALTED", trading_authorized=False)
    data = hb.read()
    assert data is not None
    assert data["process_alive"] is True
    assert data["trading_authorized"] is False
    assert hb.is_trading_authorized() is False


def test_inv28_watchdog_no_trade_api(tmp_path: Path):
    wd = Watchdog(tmp_path / "hb.json", stale_after_sec=1)
    assert not hasattr(wd, "create_order")
    assert not hasattr(wd, "buy")
    result = wd.check()
    assert result["trading_authorized"] is False


def test_malformed_heartbeat(tmp_path: Path):
    path = tmp_path / "hb.json"
    path.write_text("not-json", encoding="utf-8")
    wd = Watchdog(path, stale_after_sec=60)
    r = wd.check()
    assert r["ok"] is False
    assert r["trading_authorized"] is False


def test_stale_heartbeat(tmp_path: Path):
    hb = Heartbeat(tmp_path / "hb.json")
    hb.beat(status="READY", lifecycle="READY", trading_authorized=True)
    data = json.loads((tmp_path / "hb.json").read_text())
    data["ts"] = data["ts"] - 9999
    (tmp_path / "hb.json").write_text(json.dumps(data))
    wd = Watchdog(tmp_path / "hb.json", stale_after_sec=10)
    r = wd.check()
    assert r["stale"] is True
    assert r["trading_authorized"] is False


def test_startup_exchange_fail_no_ready(tmp_path: Path):
    from tko.runtime.bot import TradingBot

    with patch("tko.runtime.bot.load_tokocrypto") as lc, patch(
        "tko.runtime.bot.load_telegram", return_value=None
    ):
        from tko.core.credentials import TokocryptoCredentials
        from tko.core.types import SecretStr

        lc.return_value = TokocryptoCredentials(
            SecretStr("valid_api_key_xxxxx"), SecretStr("valid_api_secret_yyyy")
        )
        bot = TradingBot(Settings(min_quote_balance=1, loop_interval_sec=5), tmp_path)
        bot.client.connect = MagicMock(side_effect=RuntimeError("connect fail"))
        ok = bot._startup_barrier()
        assert ok is False
        assert bot.lifecycle.state == LifecycleState.HALTED
        assert bot.lifecycle.trading_authorized is False


def test_startup_balance_fail_halted(tmp_path: Path):
    from tko.runtime.bot import TradingBot

    with patch("tko.runtime.bot.load_tokocrypto") as lc, patch(
        "tko.runtime.bot.load_telegram", return_value=None
    ):
        from tko.core.credentials import TokocryptoCredentials
        from tko.core.types import SecretStr

        lc.return_value = TokocryptoCredentials(
            SecretStr("valid_api_key_xxxxx"), SecretStr("valid_api_secret_yyyy")
        )
        bot = TradingBot(Settings(min_quote_balance=1, loop_interval_sec=5), tmp_path)
        bot.client.connect = MagicMock()
        bot.client.fetch_balance = MagicMock(side_effect=RuntimeError("balance fail"))
        ok = bot._startup_barrier()
        assert ok is False
        assert bot.lifecycle.state == LifecycleState.HALTED
        assert bot.lifecycle.trading_authorized is False


def test_double_stop_idempotent(tmp_path: Path):
    from tko.runtime.bot import TradingBot

    with patch("tko.runtime.bot.load_tokocrypto") as lc, patch(
        "tko.runtime.bot.load_telegram", return_value=None
    ):
        from tko.core.credentials import TokocryptoCredentials
        from tko.core.types import SecretStr

        lc.return_value = TokocryptoCredentials(
            SecretStr("valid_api_key_xxxxx"), SecretStr("valid_api_secret_yyyy")
        )
        bot = TradingBot(Settings(min_quote_balance=1), tmp_path)
        bot.client.close = MagicMock()
        bot.stop()
        bot.stop()
        assert bot.lifecycle.state == LifecycleState.STOPPED
        assert bot.lifecycle.trading_authorized is False


def test_restart_unknown_intent_blocks(tmp_path: Path):
    from tko.execution.intent import IntentStore, OrderIntentStatus

    path = tmp_path / "order_intents.json"
    store = IntentStore(path)
    intent = store.create(symbol="BTC/IDR", side="buy", quote_amount=1)
    intent.status = OrderIntentStatus.UNKNOWN
    store.update(intent)
    store2 = IntentStore(path)
    assert store2.has_blocking_intent("BTC/IDR", "buy") is True


def test_restart_governor_blocks(tmp_path: Path):
    from tko.execution.intent import IntentStore, OrderIntentStatus

    path = tmp_path / "order_intents.json"
    store = IntentStore(path)
    intent = store.create(symbol="ETH/IDR", side="sell", quote_amount=1)
    intent.status = OrderIntentStatus.GOVERNOR_AUTONOMOUS
    store.update(intent)
    store2 = IntentStore(path)
    assert store2.has_blocking_intent("ETH/IDR", "sell") is True


def test_instance_lock_single_owner(tmp_path: Path):
    from tko.runtime.instance_lock import InstanceLock, InstanceLockError

    path = tmp_path / "tko.lock"
    lock1 = InstanceLock(path)
    lock1.acquire()
    lock2 = InstanceLock(path)
    with pytest.raises(InstanceLockError):
        lock2.acquire()
    lock1.release()
    lock2.acquire()
    lock2.release()


def test_startup_success_to_ready(tmp_path: Path):
    from tko.runtime.bot import TradingBot

    with patch("tko.runtime.bot.load_tokocrypto") as lc, patch(
        "tko.runtime.bot.load_telegram", return_value=None
    ):
        from tko.core.credentials import TokocryptoCredentials
        from tko.core.types import SecretStr

        lc.return_value = TokocryptoCredentials(
            SecretStr("valid_api_key_xxxxx"), SecretStr("valid_api_secret_yyyy")
        )
        bot = TradingBot(Settings(min_quote_balance=1), tmp_path)
        bot.client.connect = MagicMock()
        bot.client.fetch_balance = MagicMock(return_value={})
        bot.client._circuit_open = False
        bot.client._rate_limit_until = 0.0
        bot.reconciler.reconcile_all = MagicMock(return_value=MagicMock())
        ok = bot._startup_barrier()
        assert ok is True
        assert bot.lifecycle.state == LifecycleState.READY
        assert bot.lifecycle.trading_authorized is True


def test_tick_respects_halted(tmp_path: Path):
    from tko.runtime.bot import TradingBot

    with patch("tko.runtime.bot.load_tokocrypto") as lc, patch(
        "tko.runtime.bot.load_telegram", return_value=None
    ):
        from tko.core.credentials import TokocryptoCredentials
        from tko.core.types import SecretStr

        lc.return_value = TokocryptoCredentials(
            SecretStr("valid_api_key_xxxxx"), SecretStr("valid_api_secret_yyyy")
        )
        bot = TradingBot(Settings(min_quote_balance=1), tmp_path)
        bot.lifecycle.force(LifecycleState.HALTED, reason="test")
        bot.client.fetch_balance = MagicMock()
        bot._tick()
        bot.client.fetch_balance.assert_not_called()


def test_windows_restart_policy_notes():
    from tko.runtime.windows_service import restart_policy_notes

    notes = restart_policy_notes()
    assert "IgnoreNew" in notes or "MultipleInstances" in notes
    assert "InstanceLock" in notes


def test_inv34_kill_cannot_transition_to_ready():
    g = LifecycleGovernor()
    g.force(LifecycleState.READY, reason="x")
    assert g.transition(LifecycleState.KILL, reason="k")
    assert g.transition(LifecycleState.READY, reason="nope") is False
    assert g.trading_authorized is False
    g.force(LifecycleState.READY, reason="force-nope")
    assert g.state == LifecycleState.KILL
    assert g.trading_authorized is False


def test_inv35_degraded_cannot_direct_ready():
    g = LifecycleGovernor()
    g.force(LifecycleState.READY, reason="x")
    g.transition(LifecycleState.DEGRADED, reason="d")
    assert g.transition(LifecycleState.READY, reason="nope") is False
    assert g.state == LifecycleState.DEGRADED
    assert g.trading_authorized is False


def test_inv36_degraded_recovery_requires_reconciling():
    g = LifecycleGovernor()
    g.force(LifecycleState.READY, reason="x")
    g.transition(LifecycleState.DEGRADED, reason="d")
    assert g.transition(LifecycleState.RECONCILING, reason="recover")
    assert g.transition(LifecycleState.READY, reason="ok")
    assert g.trading_authorized is True


def test_inv37_kill_sticky():
    g = LifecycleGovernor()
    g.force(LifecycleState.READY, reason="x")
    g.transition(LifecycleState.KILL, reason="k")
    assert g.is_terminal_safety_state()
    assert g.trading_authorized is False
    g.force(LifecycleState.READY, reason="x")
    assert g.trading_authorized is False


def test_inv33_47_toctou_submit_blocked(tmp_path: Path):
    from tko.execution.engine import ExecutionEngine
    from tko.risk.engine import RiskDecision

    client = MagicMock()
    client.circuit_open = False
    client.validate_symbol_ready.return_value = (True, "ok")
    constraints = MagicMock()
    constraints.validate_notional.return_value = (True, "ok")
    constraints.normalize_quantity.side_effect = lambda q, market_order=False: q
    constraints.validate_quantity.return_value = (True, "ok")
    client.get_constraints.return_value = constraints

    lc = LifecycleGovernor()
    lc.force(LifecycleState.READY, reason="ok")
    eng = ExecutionEngine(client, Settings(min_quote_balance=1), tmp_path, lifecycle=lc)
    dec = RiskDecision(True, "approved", size_quote=10000, size_base=0.01)

    lc.request_stop()
    assert lc.trading_authorized is False
    assert eng.buy("BTC/IDR", "BTC", "IDR", dec, last_price=1000.0) is None
    assert client.create_order.call_count == 0


def test_inv42_stopping_disables_trading_immediately():
    g = LifecycleGovernor()
    g.force(LifecycleState.READY, reason="x")
    assert g.trading_authorized is True
    g.request_stop()
    assert g.state == LifecycleState.STOPPING
    assert g.trading_authorized is False


def test_inv41_stop_idempotent(tmp_path: Path):
    from tko.runtime.bot import TradingBot

    with patch("tko.runtime.bot.load_tokocrypto") as lc, patch(
        "tko.runtime.bot.load_telegram", return_value=None
    ):
        from tko.core.credentials import TokocryptoCredentials
        from tko.core.types import SecretStr

        lc.return_value = TokocryptoCredentials(
            SecretStr("valid_api_key_xxxxx"), SecretStr("valid_api_secret_yyyy")
        )
        bot = TradingBot(Settings(min_quote_balance=1), tmp_path)
        bot.client.close = MagicMock()
        bot.stop()
        bot.stop()
        bot.stop()
        assert bot.lifecycle.state == LifecycleState.STOPPED


def test_inv43_watchdog_cannot_authorize_ready(tmp_path: Path):
    wd = Watchdog(tmp_path / "hb.json")
    assert not hasattr(wd, "lifecycle")
    assert not hasattr(wd, "force")
    r = wd.check()
    assert r.get("trading_authorized") is False


def test_inv44_xml_contains_ignore_new():
    from tko.runtime.windows_service import ServicePlan, build_task_xml

    plan = ServicePlan(
        task_name="TkoBot",
        exe_path=Path(r"C:\tko\tko.exe"),
        work_dir=Path(r"C:\tko"),
        command_line=r'cmd.exe /c cd /d "C:\tko" && "C:\tko\tko.exe" run',
    )
    xml = build_task_xml(plan)
    assert "MultipleInstancesPolicy>IgnoreNew" in xml
    assert "RestartOnFailure" in xml
    assert "<Count>3</Count>" in xml
    assert "<Interval>PT1M</Interval>" in xml


def test_inv38_signal_requests_stop(tmp_path: Path):
    g = LifecycleGovernor()
    g.force(LifecycleState.READY, reason="x")
    g.request_stop()
    assert g.state == LifecycleState.STOPPING
    assert g.trading_authorized is False


def test_inv39_signal_during_startup_no_ready(tmp_path: Path):
    from tko.runtime.bot import TradingBot

    with patch("tko.runtime.bot.load_tokocrypto") as lc, patch(
        "tko.runtime.bot.load_telegram", return_value=None
    ):
        from tko.core.credentials import TokocryptoCredentials
        from tko.core.types import SecretStr

        lc.return_value = TokocryptoCredentials(
            SecretStr("valid_api_key_xxxxx"), SecretStr("valid_api_secret_yyyy")
        )
        bot = TradingBot(Settings(min_quote_balance=1), tmp_path)
        bot.lifecycle.request_stop()
        bot.client.connect = MagicMock()
        bot.client.fetch_balance = MagicMock(return_value={})
        ok = bot._startup_barrier()
        bot.lifecycle.request_stop()
        assert bot.lifecycle.trading_authorized is False


def test_inv33_concurrent_toctou_create_order_never_reaches_exchange(tmp_path: Path):
    """Race: concurrent request_stop vs submit; create_order never under STOPPING."""
    from tko.execution.engine import ExecutionEngine
    from tko.risk.engine import RiskDecision

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
    lc.force(LifecycleState.READY, reason="ok")
    eng = ExecutionEngine(client, Settings(min_quote_balance=1), tmp_path, lifecycle=lc)
    dec = RiskDecision(True, "approved", size_quote=10000, size_base=0.01)

    results: list = []

    def buyer():
        results.append(eng.buy("BTC/IDR", "BTC", "IDR", dec, last_price=1000.0))

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
    from tko.execution.engine import ExecutionEngine
    from tko.risk.engine import RiskDecision

    client = MagicMock()
    client.circuit_open = False
    client.validate_symbol_ready.return_value = (True, "ok")
    constraints = MagicMock()
    constraints.validate_notional.return_value = (True, "ok")
    constraints.normalize_quantity.side_effect = lambda q, market_order=False: q
    constraints.validate_quantity.return_value = (True, "ok")
    client.get_constraints.return_value = constraints

    lc = LifecycleGovernor()
    lc.force(LifecycleState.READY, reason="ok")
    eng = ExecutionEngine(client, Settings(min_quote_balance=1), tmp_path, lifecycle=lc)
    dec = RiskDecision(True, "approved", size_quote=10000, size_base=0.01)
    lc.request_stop()
    assert eng.buy("BTC/IDR", "BTC", "IDR", dec, last_price=1000.0) is None
    assert client.create_order.call_count == 0


def test_run_authorized_submit_mutex_blocks_stop_during_submit():
    """While submit_fn runs under mutex, request_stop blocks until it finishes."""
    g = LifecycleGovernor()
    g.force(LifecycleState.READY, reason="ok")
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
