"""Empirical evaluation helpers — scorecard, bootstrap CI, integrity sidecar."""
from pathlib import Path

import numpy as np

from src.champion.integrity import verify_integrity_sidecar
from src.champion.manifest import ChampionManifest
from src.champion.registry import ChampionRegistry
from src.evaluation.scorecard import block_bootstrap_mean_ci, build_scorecard, calibration_reliability


def test_build_scorecard_basic():
    pnls = [1.0, -0.5, 0.2, 0.3, -0.1]
    sc = build_scorecard(pnls, regimes=["TREND_UP"] * 3 + ["RANGE"] * 2, observation_days=5)
    assert sc.sample_count == 5
    assert sc.trade_count == 5
    assert sc.net_pnl == sum(pnls)
    assert "TREND_UP" in sc.regime_coverage


def test_block_bootstrap_ci():
    rng = np.random.default_rng(0)
    pnls = rng.normal(0.01, 0.05, size=100)
    ci = block_bootstrap_mean_ci(pnls, block_size=5, n_boot=50, seed=1)
    assert ci["n_boot"] == 50
    assert ci["ci_low"] <= ci["mean"] <= ci["ci_high"]


def test_calibration_not_claimed():
    scores = [0.1, 0.2, 0.8, 0.9]
    outcomes = [0, 0, 1, 1]
    r = calibration_reliability(scores, outcomes)
    assert r["calibrated"] is False
    assert r["n"] == 4


def test_registry_integrity_sidecar(tmp_path: Path):
    reg = ChampionRegistry(store_dir=str(tmp_path))
    reg.set_champion(ChampionManifest.current_production(code_commit="t"))
    path = tmp_path / "champion_registry.json"
    assert path.exists()
    assert verify_integrity_sidecar(path)
    # tamper
    path.write_text(path.read_text() + "\n", encoding="utf-8")
    assert not verify_integrity_sidecar(path)
