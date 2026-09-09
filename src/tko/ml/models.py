"""Simple sklearn classifiers + joblib persistence. Optional dependency."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class ClassifierProto(Protocol):
    def fit(self, x: Any, y: Any) -> Any: ...
    def predict(self, x: Any) -> Any: ...
    def predict_proba(self, x: Any) -> Any: ...


def sklearn_available() -> bool:
    try:
        import sklearn  # noqa: F401

        return True
    except ImportError:
        return False


def make_classifier(kind: str = "logreg") -> ClassifierProto:
    if not sklearn_available():
        raise ImportError(
            "scikit-learn not installed. Install with: pip install 'tko[ml]'"
        )
    from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    kind = kind.lower().strip()
    if kind == "rf":
        return RandomForestClassifier(
            n_estimators=100, max_depth=6, min_samples_leaf=5, random_state=42, n_jobs=1
        )
    if kind in ("gb", "gbm"):
        return GradientBoostingClassifier(
            n_estimators=80, max_depth=3, learning_rate=0.08, random_state=42
        )
    return Pipeline(
        [
            ("scaler", StandardScaler()),
            (
                "clf",
                LogisticRegression(max_iter=500, class_weight="balanced", random_state=42),
            ),
        ]
    )


def save_model(model: Any, path: Path) -> None:
    import joblib

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, path)
    logger.info("event=ml_model_saved path=%s", path)


def load_model(path: Path) -> Any:
    import joblib

    return joblib.load(Path(path))
