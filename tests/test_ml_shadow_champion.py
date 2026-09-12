"""Stage ML Shadow + Champion/Challenger — Stage 5.1 authority untouched."""

from __future__ import annotations

from pathlib import Path

import pytest

from tko.core.config import Settings
from tko.core.types import Signal
from tko.ml.artifacts import publish_model_bundle
from tko.ml.audit_ml import MlAuditLog
from tko.ml.champion import ChampionRecord, ChampionRegistry
from tko.ml.challenger import ChallengerRegistry
from tko.ml.compare import compare_champion_challenger
from tko.ml.drift import evaluate_drift
from tko.ml.observation import ObservationConfig, ObservationWindow
from tko.ml.paper import PaperConfig, PaperLedger, PaperMode, simulate_round_trip
from tko.ml.promotion import PromoState, PromotionEngine
from tko.ml.shadow_engine import ShadowEngine, ShadowInput
from tko.risk.engine import RiskEngine
from tko.risk.pnl_tracker import DailyPnLTracker
from tko.strategy.btc import TradeDecision


def test_paper_isolated_from_production(tmp_path: Path):
    ledger = PaperLedger(tmp_path / "paper.jsonl")
    t = simulate_round_trip(
        symbol="BTC/IDR", side="BUY", entry_price=100.0, exit_price=101.0,
        signal_ts=1.0, market_ts=1.0, model_id="m1", model_role="challenger",
    )
    ledger.record(t)
    assert len(ledger.read_all()) == 1
    assert not (tmp_path / "positions.json").exists()
    assert not (tmp_path / "intents.json").exists()


def test_shadow_mode_never_live(tmp_path: Path):
    eng = ShadowEngine(
        paper_path=tmp_path / "p.jsonl",
        observation_path=tmp_path / "o.jsonl",
        shadow_log_path=tmp_path / "s.jsonl",
    )
    assert eng.MODE == PaperMode.LIVE_DISABLED
    out = eng.evaluate(
        ShadowInput(
            symbol="BTC/IDR", primary_signal="BUY", market_ts=1.0, signal_ts=1.0,
            entry_price=100.0, exit_price=102.0, champion_conf=0.8, challenger_conf=0.4,
            champion_id="c1", challenger_id="x1",
        )
    )
    assert out["mode"] == "SHADOW"
    assert "LIVE" not in out["mode"]


def test_stale_market_no_shadow_decision(tmp_path: Path):
    eng = ShadowEngine(
        paper_path=tmp_path / "p.jsonl",
        observation_path=tmp_path / "o.jsonl",
        shadow_log_path=tmp_path / "s.jsonl",
    )
    out = eng.evaluate(
        ShadowInput(
            symbol="BTC/IDR", primary_signal="BUY", market_ts=0.0, signal_ts=1.0,
            entry_price=100.0, champion_conf=0.9, challenger_conf=0.9,
        )
    )
    assert out.get("ok") is False


def test_champion_immutable_pointer(tmp_path: Path):
    reg = ChampionRegistry(tmp_path / "champ.jsonl")
    r1 = ChampionRecord(
        model_id="a", version="1", artifact_hash="h1", artifact_dir="",
        dataset_version="d1", feature_version="f1", training_ts=1.0,
        evaluation_window="w", gate_results={}, promotion_ts=1.0, status="CHAMPION_ACTIVE",
    )
    reg.set_champion(r1)
    assert reg.get_active().model_id == "a"
    r2 = ChampionRecord(
        model_id="b", version="2", artifact_hash="h2", artifact_dir="",
        dataset_version="d2", feature_version="f1", training_ts=2.0,
        evaluation_window="w", gate_results={}, promotion_ts=2.0, status="CHAMPION_ACTIVE",
    )
    reg.set_champion(r2)
    assert reg.get_active().model_id == "b"
    hist = reg.history()
    assert any(h.status == "CHAMPION_RETIRED" and h.model_id == "a" for h in hist)


def test_challenger_artifact_hash_immutable(tmp_path: Path):
    reg = ChallengerRegistry(tmp_path / "ch.jsonl")
    c = reg.register(artifact_hash="abc", version="1", status="REGISTERED")
    reg.update_status(c.model_id, "SHADOW_ELIGIBLE")
    latest = reg.latest(c.model_id)
    assert latest.artifact_hash == "abc"
    assert latest.status == "SHADOW_ELIGIBLE"


def test_insufficient_observation_blocks_promotion(tmp_path: Path):
    w = ObservationWindow(tmp_path / "o.jsonl", ObservationConfig(min_samples=50))
    st = w.status()
    assert st["decision"] == "INSUFFICIENT_EVIDENCE"
    assert st["ready"] is False


def test_gates_reject_weak_challenger():
    res = compare_champion_challenger(
        champion_id="champ",
        challenger_id="chall",
        champion_pnls=[0.01] * 40,
        challenger_pnls=[-0.02] * 40,
        baseline_pnls=[0.01] * 40,
        min_trades=20,
        n_trials=10,
        regime_ok=False,
    )
    assert res.decision == "CHALLENGER_REJECTED"


def test_gates_promotion_eligible_strong_challenger():
    base = [0.001] * 50
    chall = [0.02] * 50
    champ = [0.005] * 50
    res = compare_champion_challenger(
        champion_id="champ",
        challenger_id="chall",
        champion_pnls=champ,
        challenger_pnls=chall,
        baseline_pnls=base,
        min_trades=20,
        n_trials=1,
        regime_ok=True,
    )
    assert res.decision in ("PROMOTION_ELIGIBLE", "CHALLENGER_REJECTED", "INSUFFICIENT_EVIDENCE")


def test_promotion_atomic_and_no_duplicate(tmp_path: Path):
    champ_reg = ChampionRegistry(tmp_path / "champ.jsonl")
    chall_reg = ChallengerRegistry(tmp_path / "chall.jsonl")
    art = tmp_path / "art"
    publish_model_bundle(
        art, model_bytes=b"x", metadata={}, metrics={}, calibration={}, feature_schema={"v": 1}
    )
    from tko.ml.artifacts import sha256_file
    h = sha256_file(art / "model.joblib")
    c = chall_reg.register(
        artifact_hash=h, artifact_dir=str(art), status="PROMOTION_ELIGIBLE", version="1"
    )
    eng = PromotionEngine(
        champion_reg=champ_reg,
        challenger_reg=chall_reg,
        lock_path=tmp_path / "lock.json",
        state_path=tmp_path / "promo_state.json",
    )
    eng.transition(c.model_id, PromoState.TRAINED)
    eng.transition(c.model_id, PromoState.GATE_EVALUATION)
    eng.transition(c.model_id, PromoState.SHADOW_ELIGIBLE)
    eng.transition(c.model_id, PromoState.SHADOW_ACTIVE)
    eng.transition(c.model_id, PromoState.CHALLENGER_VALIDATED)
    eng.transition(c.model_id, PromoState.PROMOTION_ELIGIBLE)
    out = eng.promote_challenger(c.model_id)
    assert out["ok"] is True
    assert champ_reg.get_active().model_id == c.model_id
    out2 = eng.promote_challenger(c.model_id)
    assert out2["ok"] is False


def test_rollback_to_previous_or_ml_off(tmp_path: Path):
    champ_reg = ChampionRegistry(tmp_path / "champ.jsonl")
    chall_reg = ChallengerRegistry(tmp_path / "chall.jsonl")
    eng = PromotionEngine(
        champion_reg=champ_reg,
        challenger_reg=chall_reg,
        lock_path=tmp_path / "lock.json",
        state_path=tmp_path / "promo_state.json",
    )
    r1 = ChampionRecord(
        model_id="old", version="1", artifact_hash="h", artifact_dir="",
        dataset_version="d", feature_version="f", training_ts=1.0,
        evaluation_window="w", gate_results={}, promotion_ts=1.0, status="CHAMPION_ACTIVE",
    )
    champ_reg.set_champion(r1)
    r2 = ChampionRecord(
        model_id="new", version="2", artifact_hash="h2", artifact_dir="",
        dataset_version="d", feature_version="f", training_ts=2.0,
        evaluation_window="w", gate_results={}, promotion_ts=2.0, status="CHAMPION_ACTIVE",
    )
    champ_reg.set_champion(r2)
    out = eng.rollback_to_previous(reason="test")
    assert out["ok"] is True
    assert champ_reg.get_active().model_id == "old"


def test_drift_invalidates_candidate():
    rep = evaluate_drift(
        ref_preds=[0.5] * 20,
        cur_preds=[0.9] * 20,
        feature_schema_ok=True,
        market_fresh=True,
        pred_shift_threshold=0.1,
    )
    assert rep.prediction_drift is True
    assert rep.ok is False


def test_schema_mismatch_reject():
    rep = evaluate_drift(feature_schema_ok=False, market_fresh=True)
    assert rep.schema_mismatch is True
    assert rep.ok is False


def test_audit_trail(tmp_path: Path):
    log = MlAuditLog(tmp_path / "audit.jsonl")
    log.emit("gate_evaluation", model_id="m1", reason="ok", gate_results={"eligible": False})
    log.emit("rejection", model_id="m1", reason="dsr")
    rows = log.read_all()
    assert len(rows) == 2
    assert rows[0]["event"] == "gate_evaluation"


def test_ml_cannot_authorize_or_execute(tmp_path: Path):
    risk = RiskEngine(
        Settings(min_quote_balance=1, market_data_max_age_sec=0, max_open_positions=1),
        tmp_path,
        DailyPnLTracker(tmp_path / "pnl.jsonl", timezone_name="UTC"),
    )
    with pytest.raises(RuntimeError, match="not a production authorization path"):
        risk.evaluate_buy(free_quote=1e6, last_price=1000, open_positions=0)
    import inspect
    from tko.ml import shadow_engine as se
    src = inspect.getsource(se)
    assert "create_order" not in src
    assert "run_authorized_submit" not in src


def test_settings_still_ml_off():
    s = Settings(min_quote_balance=1)
    assert s.ml_filter_enabled is False
    assert s.ml_governor_enabled is False


def test_illegal_promo_transition_safe_fail(tmp_path: Path):
    eng = PromotionEngine(
        champion_reg=ChampionRegistry(tmp_path / "c.jsonl"),
        challenger_reg=ChallengerRegistry(tmp_path / "h.jsonl"),
        lock_path=tmp_path / "lock.json",
        state_path=tmp_path / "st.json",
    )
    eng.transition("m1", PromoState.TRAINED)
    st = eng.transition("m1", PromoState.PROMOTED, reason="skip")
    assert st == PromoState.SAFE_FAIL
