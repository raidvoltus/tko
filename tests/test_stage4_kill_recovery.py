"""Stage 4: KILL hard safety + autonomous recovery (never KILL->READY direct)."""

from __future__ import annotations

from tko.runtime.lifecycle import LifecycleGovernor, LifecycleState


def _gates(**over):
    """All validation gates True unless overridden."""
    base = dict(
        recon_ok=True,
        kill_switch_clear=True,
        circuit_clear=True,
        daily_risk_ok=True,
        positions_ok=True,
        exchange_ok=True,
    )
    base.update(over)
    return base


def test_kill_cannot_go_direct_ready():
    g = LifecycleGovernor()
    g.force(LifecycleState.KILL, reason="daily_loss")
    assert g.transition(LifecycleState.READY, reason="nope") is False
    g.force(LifecycleState.READY, reason="force-nope")
    assert g.state == LifecycleState.KILL
    assert g.trading_authorized is False
    assert g._kill_sticky is True


def test_kill_recovery_path_to_ready():
    g = LifecycleGovernor()
    g.force(LifecycleState.KILL, reason="circuit")
    assert g.begin_recovery(reason="auto")
    assert g.state == LifecycleState.RECOVERY
    assert g._kill_sticky is True
    assert g.trading_authorized is False
    assert g.transition(LifecycleState.READY, reason="nope") is False
    assert g.complete_recovery_to_reconciling()
    assert g.state == LifecycleState.RECONCILING
    assert g.transition(LifecycleState.READY, reason="nope") is False
    assert g.authorize_ready(reason="validated", **_gates())
    assert g.state == LifecycleState.READY
    assert g.trading_authorized is True
    assert g._kill_sticky is False


def test_authorize_ready_only_from_reconciling():
    g = LifecycleGovernor()
    g.force(LifecycleState.KILL, reason="x")
    assert g.authorize_ready(**_gates()) is False
    g.begin_recovery()
    assert g.authorize_ready(**_gates()) is False
    g.complete_recovery_to_reconciling()
    assert g.authorize_ready(**_gates())


def test_authorize_ready_rejects_missing_risk_gate():
    g = LifecycleGovernor()
    g.force(LifecycleState.HALTED, reason="x")
    g.begin_recovery()
    g.complete_recovery_to_reconciling()
    assert g.authorize_ready(**_gates(daily_risk_ok=False)) is False
    assert g.state == LifecycleState.RECONCILING
    assert g.trading_authorized is False
    assert g.authorize_ready(**_gates()) is True


def test_transition_ready_always_rejected():
    g = LifecycleGovernor()
    g.force(LifecycleState.STARTING, reason="x")
    g.transition(LifecycleState.RECONCILING, reason="r")
    assert g.transition(LifecycleState.READY, reason="nope") is False
    assert g.state == LifecycleState.RECONCILING


def test_submit_blocked_until_authorize_ready():
    g = LifecycleGovernor()
    g.force(LifecycleState.KILL, reason="x")
    g.begin_recovery()
    g.complete_recovery_to_reconciling()
    raised = False
    try:
        g.run_authorized_submit(lambda: "POST")
    except RuntimeError:
        raised = True
    assert raised
    g.authorize_ready(**_gates())
    assert g.run_authorized_submit(lambda: "POST") == "POST"


def test_halted_recovery_still_works_with_authorize_ready():
    g = LifecycleGovernor()
    g.force(LifecycleState.HALTED, reason="fail")
    assert g.begin_recovery()
    assert g.complete_recovery_to_reconciling()
    assert g.authorize_ready(**_gates())
    assert g.trading_authorized


def test_recovery_backoff_has_no_hard_stop():
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "src" / "tko" / "runtime" / "bot.py"
    text = src.read_text(encoding="utf-8")
    assert "_max_recovery_attempts" not in text
    assert "_recovery_backoff_sec" in text
    assert "check_daily_limits_or_kill" in text
    barrier_idx = text.find("def _startup_barrier")
    auth_idx = text.find("authorize_ready(", barrier_idx)
    risk_idx = text.find("check_daily_limits_or_kill", barrier_idx)
    assert 0 < risk_idx < auth_idx


def test_inv51_authorize_requires_all_gates():
    g = LifecycleGovernor()
    g.force(LifecycleState.STARTING, reason="t")
    g.transition(LifecycleState.RECONCILING, reason="r")
    for key in ("recon_ok", "kill_switch_clear", "circuit_clear", "daily_risk_ok", "positions_ok", "exchange_ok"):
        assert g.authorize_ready(**_gates(**{key: False})) is False
        assert g.state == LifecycleState.RECONCILING
    assert g.authorize_ready(**_gates()) is True
    assert g.trading_authorized is True
