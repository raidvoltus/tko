"""Champion–Challenger framework tests — no orders."""
from src.champion.manifest import ChampionManifest, ChallengerManifest, compute_config_hash
from src.champion.registry import ChampionRegistry
from src.champion.states import ChallengerState, PromotionDecision
from src.champion.promotion import PromotionGate, Scorecard
from src.evaluation.walk_forward import generate_walk_forward_splits
from src.decision.edge import cost_adjusted_edge, strategy_score_to_gross_edge
from src.decision.governor import Governor
from src.decision.strategies import StrategyEngine
import numpy as np


def test_champion_manifest_identity():
    m = ChampionManifest.current_production(code_commit="abc123")
    assert m.champion_id
    assert m.identity_hash()
    assert m.risk_policy_version == "v1-risk-engine"


def test_no_direct_candidate_to_promoted():
    reg = ChampionRegistry()
    ch = ChallengerManifest(
        challenger_id="c1",
        challenger_type="STRATEGY",
        parent_champion_id="tko-champion-v1",
        feature_version="v2",
        strategy_version="v2-test",
        regime_version="v1",
        config_hash="x",
        code_commit="abc",
    )
    reg.register_challenger(ch)
    assert not reg.transition("c1", ChallengerState.PROMOTED)
    assert reg.transition("c1", ChallengerState.BACKTESTED)
    assert reg.transition("c1", ChallengerState.VALIDATED)
    assert reg.transition("c1", ChallengerState.SHADOW_EVALUATED)
    assert reg.transition("c1", ChallengerState.ELIGIBLE)


def test_promotion_gate_insufficient_evidence():
    gate = PromotionGate()
    out = gate.evaluate(Scorecard(), Scorecard(sample_count=10), cost_adjusted=True)
    assert out["decision"] == PromotionDecision.INSUFFICIENT_EVIDENCE.value
    assert out["promotion_eligible"] is False


def test_promotion_gate_requires_cost_adjusted():
    gate = PromotionGate()
    out = gate.evaluate(
        Scorecard(sample_count=500, trade_count=50, observation_days=30),
        Scorecard(sample_count=500, trade_count=50, observation_days=30, net_pnl=10),
        cost_adjusted=False,
    )
    assert out["decision"] == PromotionDecision.INSUFFICIENT_EVIDENCE.value


def test_rollback():
    reg = ChampionRegistry()
    a = ChampionManifest.current_production(code_commit="a")
    b = ChampionManifest.current_production(code_commit="b")
    # distinct ids
    object.__setattr__(b, "champion_id", "tko-champion-v2") if False else None
    reg.set_champion(a)
    # create new champion with different id via replace
    from dataclasses import replace
    b2 = replace(a, champion_id="tko-champion-v2", code_commit="b")
    reg.set_champion(b2, reason="test")
    assert reg.champion.champion_id == "tko-champion-v2"
    assert reg.rollback()
    assert reg.champion.champion_id == "tko-champion-v1"


def test_walk_forward_purge():
    splits = generate_walk_forward_splits(1000, 200, 50, 50, 10, step=50)
    assert len(splits) >= 1
    for s in splits:
        s.validate()
        assert s.train_end <= s.purge_end <= s.valid_end <= s.test_end


def test_cost_adjusted_edge():
    e = cost_adjusted_edge(1.0, fee_pct=0.1, spread_pct=0.1, slippage_pct=0.1, impact_pct=0.1, exec_risk_pct=0.1, vol_risk_pct=0.1)
    assert abs(e.net_edge_pct - 0.4) < 1e-9
    assert e.calibrated is False


def test_governor_unknown_no_trade():
    eng = StrategyEngine()
    c = np.linspace(100, 101, 20)
    sc = eng.evaluate(c)
    # force unknown via empty-ish
    from src.decision.strategies import StrategyScores
    unknown = StrategyScores(
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, "UNKNOWN", 0.0, (), "test"
    )
    g = Governor()
    d = g.decide(unknown, data_valid=True)
    assert d.action == "NO_TRADE"


def test_governor_risk_blocked():
    eng = StrategyEngine()
    c = np.linspace(100, 120, 80)
    sc = eng.evaluate(c, rsi=55)
    g = Governor()
    d = g.decide(sc, risk_blocked=True, edge=cost_adjusted_edge(2.0))
    assert d.action == "NO_TRADE"
