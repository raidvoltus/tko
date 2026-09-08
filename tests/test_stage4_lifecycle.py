"""Stage 4 lifecycle governor regression tests (INV-20..INV-32)."""

from __future__ import annotations

import json
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
