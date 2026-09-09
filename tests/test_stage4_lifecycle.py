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


def _gates(**over):
    base = dict(
        recon_ok=True, kill_switch_clear=True, circuit_clear=True,
        daily_risk_ok=True, positions_ok=True, exchange_ok=True,
    )
    base.update(over)
    return base


def test_inv21_only_ready_authorizes():
    g = LifecycleGovernor()
    assert g.state == LifecycleState.STARTING
    assert g.trading_authorized is False
    g.transition(LifecycleState.RECONCILING, reason="t")
    assert g.trading_authorized is False
    assert g.transition(LifecycleState.READY, reason="ok") is False
    assert g.authorize_ready(reason="ok", **_gates())
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


def test_stale_heartbeat(tmp_path: Path):
    path = tmp_path / "hb.json"
    path.write_text(
        json.dumps(
            {
                "ts": time.time() - 120,
                "status": "OK",
                "lifecycle": "READY",
                "trading_authorized": True,
                "process_alive": True,
            }
        ),
        encoding="utf-8",
    )
    wd = Watchdog(path, stale_after_sec=30)
    r = wd.check()
    assert r["ok"] is False
    assert r["trading_authorized"] is False


def test_transition_illegal_rejected():
    g = LifecycleGovernor()
    assert g.transition(LifecycleState.READY, reason="skip") is False
    assert g.state == LifecycleState.STARTING


def test_snapshot_serializable():
    g = LifecycleGovernor()
    snap = g.snapshot()
    d = snap.to_dict()
    assert d["state"] == "STARTING"
    assert d["trading_authorized"] is False
    json.dumps(d)


def test_startup_balance_fail_halts(tmp_path: Path):
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
