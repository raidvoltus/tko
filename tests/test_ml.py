"""ML model integrity tests (marker: ml)."""
from __future__ import annotations

import tempfile

import numpy as np
import pytest

pytestmark = pytest.mark.ml


def test_sklearn_train_predict_save_load(sklearn_mod):
    from src.ml.sklearn_model import SklearnModel

    X = np.random.randn(100, 4)
    y = (X[:, 0] > 0).astype(int)
    features = ["f1", "f2", "f3", "f4"]
    m = SklearnModel(seed=42)
    metrics = m.train(X, y, features)
    assert "train_accuracy" in metrics
    proba = m.predict_proba(X[:5])
    assert proba.shape == (5, 2)

    with tempfile.TemporaryDirectory() as d:
        m.save(d)
        m2 = SklearnModel()
        assert m2.load(d)
        assert m2.is_loaded
        assert m2.feature_schema == features
        p2 = m2.predict(X[:5])
        assert len(p2) == 5


def test_corrupt_model_rejected(sklearn_mod):
    from src.ml.sklearn_model import SklearnModel

    with tempfile.TemporaryDirectory() as d:
        bad = __import__("pathlib").Path(d) / "model.joblib"
        bad.write_bytes(b"not-a-model")
        m = SklearnModel()
        assert m.load(d) is False or not m.is_loaded
