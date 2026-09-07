"""ML model integrity tests."""
import os
import tempfile

import numpy as np

from src.ml.sklearn_model import SklearnModel


def test_train_predict_save_load():
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


def test_corrupt_model_rejected():
    X = np.random.randn(20, 2)
    y = np.zeros(20, dtype=int)
    m = SklearnModel()
    m.train(X, y, ["a", "b"])
    with tempfile.TemporaryDirectory() as d:
        m.save(d)
        # corrupt
        with open(os.path.join(d, "model.pkl"), "ab") as f:
            f.write(b"CORRUPT")
        m2 = SklearnModel()
        assert not m2.load(d)
        assert not m2.is_loaded
