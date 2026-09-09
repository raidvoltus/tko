"""INV-51: force(READY) rejected from every lifecycle state."""

from __future__ import annotations

from tko.runtime.lifecycle import LifecycleGovernor, LifecycleState

ALL_STATES = list(LifecycleState)


def test_force_ready_from_starting_rejected():
    g = LifecycleGovernor()
    assert g.state == LifecycleState.STARTING
    g.force(LifecycleState.READY, reason="bypass")
    assert g.state == LifecycleState.STARTING
    assert g.trading_authorized is False


def test_force_ready_from_stopping_stopped_rejected():
    g = LifecycleGovernor()
    g.force(LifecycleState.STOPPING, reason="s")
    g.force(LifecycleState.READY, reason="bypass")
    assert g.state == LifecycleState.STOPPING
    g.force(LifecycleState.STOPPED, reason="done")
    g.force(LifecycleState.READY, reason="bypass")
    assert g.state == LifecycleState.STOPPED
    assert g.trading_authorized is False


def test_force_ready_from_reconciling_rejected():
    g = LifecycleGovernor()
    g.transition(LifecycleState.RECONCILING, reason="r")
    g.force(LifecycleState.READY, reason="bypass")
    assert g.state == LifecycleState.RECONCILING
    assert g.trading_authorized is False


def test_force_ready_from_halted_kill_degraded_recovery_rejected():
    for st in (
        LifecycleState.HALTED,
        LifecycleState.KILL,
        LifecycleState.DEGRADED,
        LifecycleState.RECOVERY,
    ):
        g = LifecycleGovernor()
        g.force(st, reason="place")
        before = g.state
        g.force(LifecycleState.READY, reason="bypass")
        assert g.state == before
        assert g.trading_authorized is False


def test_only_authorize_ready_reaches_ready():
    g = LifecycleGovernor()
    g.transition(LifecycleState.RECONCILING, reason="r")
    g.force(LifecycleState.READY, reason="nope")
    assert g.state == LifecycleState.RECONCILING
    assert g.authorize_ready(
        reason="ok",
        recon_ok=True,
        kill_switch_clear=True,
        circuit_clear=True,
        daily_risk_ok=True,
        positions_ok=True,
        exchange_ok=True,
    )
    assert g.state == LifecycleState.READY
    assert g.trading_authorized is True


def test_force_ready_rejected_from_every_non_ready_state():
    placements = {
        LifecycleState.STARTING: lambda g: None,
        LifecycleState.RECONCILING: lambda g: g.transition(LifecycleState.RECONCILING, reason="r"),
        LifecycleState.HALTED: lambda g: g.force(LifecycleState.HALTED, reason="h"),
        LifecycleState.KILL: lambda g: g.force(LifecycleState.KILL, reason="k"),
        LifecycleState.STOPPING: lambda g: g.force(LifecycleState.STOPPING, reason="s"),
        LifecycleState.STOPPED: lambda g: (
            g.force(LifecycleState.STOPPING, reason="s"),
            g.force(LifecycleState.STOPPED, reason="d"),
        ),
        LifecycleState.DEGRADED: lambda g: g.force(LifecycleState.DEGRADED, reason="d"),
        LifecycleState.RECOVERY: lambda g: g.force(LifecycleState.RECOVERY, reason="r"),
    }
    for st, place in placements.items():
        g = LifecycleGovernor()
        place(g)
        before = g.state
        g.force(LifecycleState.READY, reason="bypass_attempt")
        assert g.state == before, f"force(READY) changed {before} -> {g.state}"
        assert g.trading_authorized is False
        assert g.state != LifecycleState.READY
