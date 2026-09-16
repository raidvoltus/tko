"""XGBoost model - version pinned for Win7 (1.5.2)."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np

try:
    import xgboost as xgb
    HAS_XGB = True
except ImportError:
    HAS_XGB = False
    xgb = None  # type: ignore

from sklearn.metrics import (
    accuracy_score,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

from .base import MLModel, ModelMetadata

logger = logging.getLogger(__name__)


class XGBoostModel(MLModel):
    def __init__(
        self,
        name: str = "xgboost",
        version: str = "1.0.0",
        seed: Optional[int] = 42,
    ):
        super().__init__(name=name, version=version)
        self.seed = seed
        if not HAS_XGB:
            logger.warning("xgboost not installed; XGBoostModel will be unavailable")
            self._model = None
            return
        self._model = xgb.XGBClassifier(
            n_estimators=100,
            max_depth=6,
            learning_rate=0.1,
            objective="binary:logistic",
            use_label_encoder=False,
            eval_metric="logloss",
            random_state=seed,
            n_jobs=1,  # CPU only
            tree_method="hist",
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
        if not HAS_XGB or self._model is None:
            raise RuntimeError("xgboost not available")
        self.feature_schema = list(feature_names)
        eval_set = [(X_val, y_val)] if X_val is not None else None
        self._model.fit(
            X, y,
            eval_set=eval_set,
            verbose=False,
        )
        metrics = self._evaluate(X, y, "train")
        if X_val is not None and y_val is not None:
            metrics.update(self._evaluate(X_val, y_val, "val"))
        self.metadata = ModelMetadata(
            name=self.name,
            version=self.version,
            framework="xgboost",
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
        return metrics
