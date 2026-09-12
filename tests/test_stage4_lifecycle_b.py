"""Stage 4 lifecycle governor regression tests part B (INV-34..44)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from tko.core.config import Settings
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


def _gates(**over):
    base = {
        "recon_ok": True, "kill_switch_clear": True, "circuit_clear": True,
        "daily_risk_ok": True, "positions_ok": True, "exchange_ok": True,
    }
    base.update(over)
    return base


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
    _to_ready(g, reason="x")
    assert g.transition(LifecycleState.KILL, reason="k")
    assert g.transition(LifecycleState.READY, reason="nope") is False
    assert g.trading_authorized is False
    g.force(LifecycleState.READY, reason="force-nope")
    assert g.state == LifecycleState.KILL
    assert g.trading_authorized is False


def test_inv35_degraded_cannot_direct_ready():
    g = LifecycleGovernor()
    _to_ready(g, reason="x")
    g.transition(LifecycleState.DEGRADED, reason="d")
    assert g.transition(LifecycleState.READY, reason="nope") is False
    assert g.state == LifecycleState.DEGRADED
    assert g.trading_authorized is False


def test_inv36_degraded_recovery_requires_reconciling():
    g = LifecycleGovernor()
    _to_ready(g, reason="x")
    g.transition(LifecycleState.DEGRADED, reason="d")
    assert g.transition(LifecycleState.RECONCILING, reason="recover")
    assert g.transition(LifecycleState.READY, reason="ok") is False
    assert g.authorize_ready(reason="ok", **_gates())
    assert g.trading_authorized is True


def test_inv37_kill_sticky():
    g = LifecycleGovernor()
    g.force(LifecycleState.KILL, reason="loss")
    assert g._kill_sticky is True
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
    lc.force(LifecycleState.HALTED, reason="x")
    eng = ExecutionEngine(client, Settings(min_quote_balance=1), tmp_path, lifecycle=lc)
    dec = RiskDecision(True, "approved", size_quote=10000, size_base=0.01)
    assert eng.buy("BTC/IDR", "BTC", "IDR", dec, last_price=1000.0) is None
    assert client.create_order.call_count == 0


def test_inv42_stopping_disables_trading_immediately():
    g = LifecycleGovernor()
    _to_ready(g, reason="x")
    g.request_stop()
    assert g.state == LifecycleState.STOPPING
    assert g.trading_authorized is False


def test_inv44_xml_contains_ignore_new():
    from tko.runtime.windows_service import ServicePlan, build_task_xml

    plan = ServicePlan(
        task_name="TKO",
        exe_path=Path("C:/tko/tko.exe"),
        work_dir=Path("C:/tko"),
        command_line='cmd.exe /c cd /d "C:\\tko" && "C:\\tko\\tko.exe" run',
    )
    xml = build_task_xml(plan)
    assert "IgnoreNew" in xml
    assert "RestartOnFailure" in xml


def test_inv38_signal_requests_stop(tmp_path: Path):
    g = LifecycleGovernor()
    _to_ready(g, reason="x")
    g.request_stop()
    assert g.state == LifecycleState.STOPPING


def test_inv43_watchdog_cannot_authorize_ready(tmp_path: Path):
    from tko.runtime.watchdog import Watchdog

    wd = Watchdog(tmp_path / "hb.json", stale_after_sec=60)
    assert not hasattr(wd, "authorize_ready")
    assert not hasattr(wd, "transition")
