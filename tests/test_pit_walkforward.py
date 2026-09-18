"""PIT dataset + walk-forward scorecard runner tests."""
import numpy as np
import pytest

from src.evaluation.pit_dataset import (
    assert_feature_label_alignment,
    build_pit_from_closes,
    schema_hash,
)
from src.evaluation.runner import WalkForwardRunner, compare_champion_challenger
from src.champion.promotion import PromotionGate


def _series(n=400, seed=0):
    rng = np.random.default_rng(seed)
    # geometric random walk
    rets = rng.normal(0.0002, 0.01, size=n)
    closes = 100 * np.exp(np.cumsum(rets))
    return closes


def test_schema_hash_stable():
    h1 = schema_hash()
    h2 = schema_hash()
    assert h1 == h2
    assert len(h1) == 32


def test_pit_build_and_no_lookahead_names():
    ds = build_pit_from_closes("BTC_USDT", _series())
    ds.assert_no_lookahead_labels_in_features()
    assert_feature_label_alignment(ds, horizon=1)
    assert_feature_label_alignment(ds, horizon=5)
    assert ds.n == 400
    assert "ret_1" in ds.features
    assert "fwd_ret_1" in ds.labels


def test_pit_slice_lengths():
    ds = build_pit_from_closes("BTC_USDT", _series(100))
    sub = ds.slice(10, 50)
    assert sub.n == 40
    assert len(sub.features["ret_1"]) == 40


def test_lookahead_feature_name_rejected():
    ds = build_pit_from_closes("BTC_USDT", _series(80))
    ds.features["fwd_bad"] = np.zeros(ds.n)
    with pytest.raises(AssertionError):
        ds.assert_no_lookahead_labels_in_features()


def test_walkforward_fills_scorecard():
    ds = build_pit_from_closes("BTC_USDT", _series(500))
    runner = WalkForwardRunner(entry_threshold=0.05)
    report = runner.run(ds, train_size=150, valid_size=40, test_size=40, purge_size=5, step=40)
    assert report.feature_schema_hash == ds.feature_schema_hash
    assert len(report.folds) >= 1
    assert report.aggregate.sample_count > 0
    assert report.cost_model["calibrated"] is False
    assert "cost_adjusted=True" in report.notes[0]
    d = report.to_dict()
    assert "aggregate" in d and "folds" in d


def test_champion_challenger_scorecards_for_gate():
    ds = build_pit_from_closes("BTC_USDT", _series(500, seed=1))
    r = WalkForwardRunner(entry_threshold=0.05)
    rep_a = r.run(ds, train_size=150, valid_size=40, test_size=40, purge_size=5, step=40)
    # challenger: higher threshold → fewer trades (different scorecard)
    r2 = WalkForwardRunner(entry_threshold=0.25)
    rep_b = r2.run(ds, train_size=150, valid_size=40, test_size=40, purge_size=5, step=40)
    cards = compare_champion_challenger(rep_a, rep_b)
    gate = PromotionGate()
    out = gate.evaluate(
        cards["champion"],
        cards["challenger"],
        cost_adjusted=True,
        operator_approved=False,
    )
    assert out["promotion_eligible"] is False or out["decision"] in (
        "KEEP_CHAMPION",
        "INSUFFICIENT_EVIDENCE",
        "REJECT",
        "PROMOTE",
    )
    # without operator, never auto PROMOTE
    if out["decision"] == "PROMOTE":
        pytest.fail("must not PROMOTE without operator_approved")
