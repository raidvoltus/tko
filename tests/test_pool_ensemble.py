"""Tests for ModelPool, Ensemble, and Diversity — Stage 5.1."""

from __future__ import annotations

from collections.abc import Sequence

from tko.ml.backends.base import Backend
from tko.ml.diversity import (
    prediction_correlation,
    select_diverse,
)
from tko.ml.ensemble import NnlsEnsemble, SoftVotingEnsemble
from tko.ml.governor import (
    ComputationalGovernor,
    GovernorConfig,
    ResourceSnapshot,
)
from tko.ml.pool import ModelPool


class _DummyBackend(Backend):
    """Deterministic backend for unit tests."""

    def __init__(self, name: str, fail_fit: bool = False) -> None:
        self.name = name
        self._fail_fit = fail_fit
        self._fitted = False

    def is_available(self) -> bool:
        return True

    def fit(self, x: Sequence[Sequence[float]], y: Sequence[int]) -> None:
        if self._fail_fit:
            raise RuntimeError("intentional fit failure")
        self._fitted = True

    def predict_proba(self, x: Sequence[Sequence[float]]) -> list[list[float]]:
        # Constant proba so shape is easy to assert
        return [[0.2, 0.5, 0.3] for _ in x]


def test_pool_predict_shape() -> None:
    backends = [_DummyBackend("a"), _DummyBackend("b"), _DummyBackend("c")]
    pool = ModelPool(backends, max_models=5)
    X = [[1.0, 2.0], [3.0, 4.0]]
    y = [0, 1]
    pool.fit(X, y)
    preds = pool.predict_proba(X)
    assert len(preds) == 3
    assert len(preds[0]) == 2  # n_samples
    assert len(preds[0][0]) == 3  # n_classes


def test_pool_skips_failed_backend() -> None:
    backends = [
        _DummyBackend("good"),
        _DummyBackend("bad", fail_fit=True),
        _DummyBackend("ok2"),
    ]
    pool = ModelPool(backends, max_models=5)
    pool.fit([[1.0]], [0])
    active = pool.active_backends()
    names = [b.name for b in active]
    assert "bad" not in names
    assert "good" in names
    assert "ok2" in names


def test_pool_safe_exit_returns_empty() -> None:
    gov = ComputationalGovernor(
        GovernorConfig(enabled=True, safe_exit_on_pressure=True)
    )
    gov.observe(ResourceSnapshot(ram_used_pct=0.99, latency_ms=10.0))
    assert gov.should_hold_only()

    backends = [_DummyBackend("a"), _DummyBackend("b")]
    pool = ModelPool(backends, governor=gov, max_models=5)
    pool.fit([[1.0]], [0])
    assert pool.active_backends() == []
    assert pool.predict_proba([[1.0]]) == []


def test_nnls_weights_non_negative_sum_one() -> None:
    ens = NnlsEnsemble()
    # 2 models, 3 samples, 2 classes
    preds_val = [
        [[0.1, 0.9], [0.2, 0.8], [0.3, 0.7]],
        [[0.4, 0.6], [0.5, 0.5], [0.6, 0.4]],
    ]
    y_val = [1, 1, 0]
    ens.fit(preds_val, y_val)
    assert len(ens.weights) == 2
    assert all(w >= 0.0 for w in ens.weights)
    assert abs(sum(ens.weights) - 1.0) < 1e-6


def test_soft_voting_fallback() -> None:
    ens = SoftVotingEnsemble()
    preds_val = [
        [[0.1, 0.9], [0.2, 0.8]],
        [[0.4, 0.6], [0.5, 0.5]],
    ]
    ens.fit(preds_val, [1, 0])
    out = ens.predict_proba(preds_val)
    assert len(out) == 2
    assert abs(out[0][0] + out[0][1] - 1.0) < 1e-6


def test_diversity_identical_predictions_dropped() -> None:
    preds = {
        "m1": [0.1, 0.2, 0.3, 0.4],
        "m2": [0.1, 0.2, 0.3, 0.4],  # identical → high correlation
        "m3": [0.9, 0.8, 0.1, 0.0],
    }
    corr = prediction_correlation(preds["m1"], preds["m2"])
    assert abs(corr - 1.0) < 1e-6
    selected = select_diverse(preds, threshold=0.80, max_per_family=1)
    # m1 and m2 are redundant; only one of them + m3 should remain
    assert "m3" in selected
    assert len(selected) <= 2
    assert not ("m1" in selected and "m2" in selected)
