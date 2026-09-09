"""Stage 4 lifecycle governor invariants INV-20..INV-47 (+ recovery)."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tko.core.config import Settings
from tko.execution.engine import ExecutionEngine
from tko.risk.engine import RiskDecision
from tko.runtime.lifecycle import LifecycleGovernor, LifecycleState
from tko.runtime.windows_service import build_task_xml


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
    assert g.transition(LifecycleState.READY, reason="ok") is False  # must use authorize_ready
    assert g.authorize_ready(reason="ok", **_gates())
    assert g.trading_authorized is True
    g.transition(LifecycleState.HALTED, reason="fail")
    assert g.trading_authorized is False


def test_inv22_kill_sticky_blocks_ready():
    g = LifecycleGovernor()
    g.force(LifecycleState.KILL, reason="loss")
    assert g.trading_authorized is False
    g.force(LifecycleState.READY, reason="nope")
    assert g.state == LifecycleState.KILL
    assert g.trading_authorized is False


def test_inv33_submit_mutex_blocks_when_not_ready():
    g = LifecycleGovernor()
    with pytest.raises(RuntimeError):
        g.run_authorized_submit(lambda: "x")


def test_inv33_submit_ok_when_ready():
    g = LifecycleGovernor()
    g.transition(LifecycleState.RECONCILING, reason="r")
    assert g.authorize_ready(**_gates())
    assert g.run_authorized_submit(lambda: 42) == 42


def test_inv36_degraded_recovery_requires_reconciling():
    g = LifecycleGovernor()
    g.force(LifecycleState.READY, reason="x")
    g.transition(LifecycleState.DEGRADED, reason="d")
    assert g.transition(LifecycleState.RECONCILING, reason="recover")
    assert g.transition(LifecycleState.READY, reason="ok") is False
    assert g.authorize_ready(reason="ok", **_gates())
    assert g.trading_authorized is True


def test_request_stop_takes_mutex():
    g = LifecycleGovernor()
    g.transition(LifecycleState.RECONCILING, reason="r")
    g.authorize_ready(**_gates())
    g.request_stop()
    assert g.state == LifecycleState.STOPPING
    with pytest.raises(RuntimeError):
        g.run_authorized_submit(lambda: "post")


def test_build_task_xml_ignore_new():
    xml = build_task_xml(command="tko.exe", working_dir="C:\\tko", task_name="TKO")
    assert "IgnoreNew" in xml
    assert "RestartOnFailure" in xml
