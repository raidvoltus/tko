"""Institutional hardening: ledger, clock, recovery, drift, property-style risk invariants."""
from pathlib import Path

import numpy as np

from src.audit.clock import ClockDiscipline
from src.audit.ledger import AuditLedger
from src.audit.lineage import chain_lineage
from src.ops.drift import brier_drift, feature_drift_psi
from src.ops.recovery import RecoveryController, RecoveryPhase
from src.risk.engine import RiskEngine, RiskLimits, RiskStateHealth
from src.validation.independent import IndependentValidator


def test_ledger_hash_chain(tmp_path: Path):
    led = AuditLedger(path=str(tmp_path / "audit.jsonl"))
    led.append("RISK_DENY", {"reason": "KILL"})
    led.append("ADMIT", {"symbol": "BTC_USDT"})
    assert led.verify_chain()
    # tamper memory chain
    led._events[0].payload["reason"] = "HACK"
    assert not led.verify_chain()


def test_ledger_reload_integrity(tmp_path: Path):
    p = tmp_path / "a.jsonl"
    led = AuditLedger(path=str(p))
    led.append("A", {"x": 1})
    led.append("B", {"x": 2})
    tip = led.tip_hash()
    led2 = AuditLedger(path=str(p))
    assert led2.verify_chain()
    assert led2.tip_hash() == tip


def test_clock_discipline():
    c = ClockDiscipline(max_drift_ms=100)
    c.set_exchange_offset_ms(50)
    s = c.snapshot()
    assert s.healthy
    c.set_exchange_offset_ms(5000)
    assert not c.snapshot().healthy
    t0 = c.now_mono()
    assert c.elapsed_mono(t0) >= 0


def test_lineage_chain():
    L = chain_lineage("ds1", "feat1", model_hash="m1", calibration_hash="c1")
    assert len(L["nodes"]) >= 3
    assert L["root"]


def test_recovery_fail_closed():
    r = RecoveryController()
    r.begin_restart()
    r.begin_reconcile()
    cert = r.mark_reconcile_failed()
    assert cert.eligible_to_trade is False
    assert r.phase == RecoveryPhase.NO_TRADE


def test_recovery_ready_requires_invariants():
    r = RecoveryController()
    r.begin_restart()
    r.begin_reconcile()
    cert = r.rebuild_and_verify(
        risk_state_valid=True,
        streams_healthy=True,
        kill_switch=False,
        circuit_breaker=False,
        ledger_chain_ok=True,
    )
    assert cert.eligible_to_trade
    r2 = RecoveryController()
    r2.begin_restart()
    bad = r2.rebuild_and_verify(
        risk_state_valid=False,
        streams_healthy=True,
        kill_switch=False,
        circuit_breaker=False,
    )
    assert not bad.eligible_to_trade


def test_drift_detection():
    rng = np.random.default_rng(0)
    base = rng.normal(0, 1, 500)
    recent = rng.normal(2, 1, 500)  # shifted
    rep = feature_drift_psi(base, recent, threshold=0.1)
    assert rep.drifted
    bd = brier_drift(0.1, 0.2, threshold=0.05)
    assert bd.drifted


def test_independent_validator_no_strategy_import():
    import src.validation.independent as mod

    text = Path(mod.__file__).read_text()
    assert "from src.decision" not in text and "new_order" not in text
    assert "new_order" not in text
    v = IndependentValidator(min_samples=5)
    out = v.evaluate([0.1, -0.05, 0.2, -0.01, 0.03])
    assert out.pass_min_samples
    assert out.n == 5


def _risk_ready(**kw):
    lim = RiskLimits(max_stale_market_sec=9999, **kw)
    r = RiskEngine(lim)
    r.ws_connected = True
    r.orderbook_synced = True
    r.update_market_ts("BTC_USDT")
    assert r.apply_authoritative_snapshot(10_000.0, {}, daily_pnl=0.0)
    return r


def test_property_risk_deny_when_unreconciled():
    r = RiskEngine(RiskLimits(max_stale_market_sec=9999))
    r.ws_connected = True
    r.orderbook_synced = True
    r.update_market_ts("BTC_USDT")
    st = r.check("BTC_USDT", 0, 10, 1000)
    assert not st.allowed


def test_property_kill_always_denies():
    r = _risk_ready()
    r.activate_kill_switch("chaos")
    for notional in (1, 10, 50):
        assert not r.check("BTC_USDT", 0, notional, 1000).allowed


def test_property_admit_reservation_concurrency():
    import threading

    r = _risk_ready(max_orders_per_minute=5, max_orders_per_day=100)
    results = []

    def w():
        results.append(r.admit("BTC_USDT", 0, 5, 1000).allowed)

    th = [threading.Thread(target=w) for _ in range(12)]
    for t in th:
        t.start()
    for t in th:
        t.join()
    assert sum(1 for x in results if x) <= 5


def test_chaos_ws_disconnect_denies():
    r = _risk_ready()
    r.set_ws_connected(False)
    assert not r.check("BTC_USDT", 0, 10, 1000).allowed


def test_chaos_stale_market_denies():
    r = RiskEngine(RiskLimits(max_stale_market_sec=0.01))
    r.apply_authoritative_snapshot(10_000.0, {})
    r.ws_connected = True
    r.orderbook_synced = True
    # no market ts update → stale
    assert not r.check("BTC_USDT", 0, 10, 1000).allowed


def test_chaos_unknown_order_blocks():
    r = _risk_ready()
    r.mark_unknown_order("c1")
    assert r.state_health == RiskStateHealth.UNKNOWN
    assert not r.check("BTC_USDT", 0, 10, 1000).allowed


def test_audit_modules_no_execution():
    for mod in ("src/audit/ledger.py", "src/audit/clock.py", "src/ops/recovery.py", "src/ops/drift.py"):
        text = Path(mod).read_text()
        assert "new_order" not in text
        assert "RestClient" not in text
