"""Scikit-learn baseline model (RandomForest / LogisticRegression)."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

from .base import MLModel, ModelMetadata

logger = logging.getLogger(__name__)


class SklearnModel(MLModel):
    def __init__(
        self,
        name: str = "sklearn_rf",
        version: str = "1.0.0",
        model_type: str = "rf",  # rf | lr
        seed: Optional[int] = 42,
    ):
        super().__init__(name=name, version=version)
        self.model_type = model_type
        self.seed = seed
        if model_type == "lr":
            self._model = LogisticRegression(
                max_iter=500, random_state=seed, solver="lbfgs"
            )
        else:
            self._model = RandomForestClassifier(
                n_estimators=100,
                max_depth=8,
                random_state=seed,
                n_jobs=1,  # CPU, Win7 friendly
            )

    def train(
        self,
        X: np.ndarray,
        y: np.ndarray,
        feature_names: List[str],
        X_val: Optional[np.ndarray] = None,
        y_val: Optional[np.ndarray] = None,
        **kwargs,
    ) -> Dict[str, float]:
        self.feature_schema = list(feature_names)
        self._model.fit(X, y)
        metrics = self._evaluate(X, y, prefix="train")
        if X_val is not None and y_val is not None:
            metrics.update(self._evaluate(X_val, y_val, prefix="val"))
        self.metadata = ModelMetadata(
            name=self.name,
            version=self.version,
            framework="sklearn",
            feature_schema=self.feature_schema,
            created_at=datetime.utcnow().isoformat() + "Z",
            train_samples=len(y),
            metrics=metrics,
            seed=self.seed,
        )
        self.is_loaded = True
        return metrics

    def predict(self, X: np.ndarray) -> np.ndarray:
        if not self.is_loaded or self._model is None:
            raise RuntimeError("Model not loaded")
        return self._model.predict(X)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if not self.is_loaded or self._model is None:
            raise RuntimeError("Model not loaded")
        return self._model.predict_proba(X)

    def _evaluate(self, X: np.ndarray, y: np.ndarray, prefix: str) -> Dict[str, float]:
        pred = self._model.predict(X)
        proba = self._model.predict_proba(X)
        metrics = {
            f"{prefix}_accuracy": float(accuracy_score(y, pred)),
            f"{prefix}_precision": float(precision_score(y, pred, average="weighted", zero_division=0)),
            f"{prefix}_recall": float(recall_score(y, pred, average="weighted", zero_division=0)),
            f"{prefix}_f1": float(f1_score(y, pred, average="weighted", zero_division=0)),
        }
        try:
            if proba.shape[1] == 2:
                metrics[f"{prefix}_roc_auc"] = float(roc_auc_score(y, proba[:, 1]))
        except Exception:
            pass
        cm = confusion_matrix(y, pred)
        metrics[f"{prefix}_cm"] = cm.tolist()  # type: ignore
        return metrics
