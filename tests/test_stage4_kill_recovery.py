"""Stage 4: KILL hard safety + autonomous recovery (never KILL->READY direct)."""

from __future__ import annotations

from tko.runtime.lifecycle import LifecycleGovernor, LifecycleState


def test_kill_cannot_go_direct_ready():
    g = LifecycleGovernor()
    g.force(LifecycleState.KILL, reason="daily_loss")
    assert g.transition(LifecycleState.READY, reason="nope") is False
    g.force(LifecycleState.READY, reason="force-nope")
    assert g.state == LifecycleState.KILL
    assert g.trading_authorized is False
    assert g._kill_sticky is True


def test_kill_recovery_path_to_ready():
    """KILL -> RECOVERY -> RECONCILING -> authorize_ready -> READY."""
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
    assert g.authorize_ready(reason="validated")
    assert g.state == LifecycleState.READY
    assert g.trading_authorized is True
    assert g._kill_sticky is False


def test_authorize_ready_only_from_reconciling():
    g = LifecycleGovernor()
    g.force(LifecycleState.KILL, reason="x")
    assert g.authorize_ready() is False
    g.begin_recovery()
    assert g.authorize_ready() is False
    g.complete_recovery_to_reconciling()
    assert g.authorize_ready()


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
    g.authorize_ready()
    assert g.run_authorized_submit(lambda: "POST") == "POST"


def test_halted_recovery_still_works_with_authorize_ready():
    g = LifecycleGovernor()
    g.force(LifecycleState.HALTED, reason="fail")
    assert g.begin_recovery()
    assert g.complete_recovery_to_reconciling()
    assert g.authorize_ready()
    assert g.trading_authorized
