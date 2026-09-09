"""Stage 4: autonomous recovery path invariants (full autonomous, fail-closed)."""

from __future__ import annotations

from tko.runtime.lifecycle import LifecycleGovernor, LifecycleState


def _gates(**over):
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


def _to_ready(g, *, reason: str = "test_ready") -> None:
    if g.state == LifecycleState.STARTING:
        g.transition(LifecycleState.RECONCILING, reason="test_recon")
    elif g.state != LifecycleState.RECONCILING:
        g.force(LifecycleState.STARTING, reason="test_reset")
        g.transition(LifecycleState.RECONCILING, reason="test_recon")
    assert g.authorize_ready(reason=reason, **_gates())


def test_halted_cannot_go_direct_ready():
    g = LifecycleGovernor()
    g.force(LifecycleState.HALTED, reason="fail")
    assert g.transition(LifecycleState.READY, reason="nope") is False
    g.force(LifecycleState.READY, reason="force-nope")
    assert g.state == LifecycleState.HALTED
    assert g.trading_authorized is False


def test_halted_recovery_path_to_ready():
    g = LifecycleGovernor()
    g.force(LifecycleState.HALTED, reason="startup_fail")
    assert g.begin_recovery(reason="auto")
    assert g.state == LifecycleState.RECOVERY
    assert g.trading_authorized is False
    assert g.transition(LifecycleState.READY, reason="nope") is False
    assert g.complete_recovery_to_reconciling()
    assert g.state == LifecycleState.RECONCILING
    assert g.authorize_ready(reason="recon_ok", **_gates())
    assert g.trading_authorized is True


def test_halted_no_direct_reconciling():
    g = LifecycleGovernor()
    g.force(LifecycleState.HALTED, reason="x")
    assert g.transition(LifecycleState.RECONCILING, reason="skip") is False
    assert g.state == LifecycleState.HALTED


def test_degraded_may_begin_recovery():
    g = LifecycleGovernor()
    _to_ready(g, reason="ok")
    g.transition(LifecycleState.DEGRADED, reason="tick")
    assert g.begin_recovery(reason="auto")
    assert g.state == LifecycleState.RECOVERY
    assert g.trading_authorized is False


def test_recovery_cannot_force_ready():
    g = LifecycleGovernor()
    g.force(LifecycleState.HALTED, reason="x")
    g.begin_recovery()
    g.force(LifecycleState.READY, reason="nope")
    assert g.state == LifecycleState.RECOVERY
    assert g.trading_authorized is False
