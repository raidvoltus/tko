"""Logistic Regression backend — lightest linear model."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tko.ml.backends.base import Backend


class LogRegBackend(Backend):
    name = "logreg"

    def __init__(
        self,
        *,
        max_iter: int = 500,
        n_jobs: int = 1,
        random_state: int = 42,
    ) -> None:
        self.max_iter = max_iter
        self.n_jobs = n_jobs
        self.random_state = random_state
        self._model: Any = None

    def is_available(self) -> bool:
        try:
            from sklearn.linear_model import LogisticRegression  # noqa: F401

            return True
        except ImportError:
            return False

    def fit(self, x: Sequence[Sequence[float]], y: Sequence[int]) -> None:
        if not self.is_available():
            raise RuntimeError("sklearn not available")
        from sklearn.linear_model import LogisticRegression
        import numpy as np

        arr = np.asarray(x, dtype=float)
        labels = np.asarray(y, dtype=int)
        self._model = LogisticRegression(
            max_iter=self.max_iter,
            n_jobs=self.n_jobs,
            random_state=self.random_state,
            solver="lbfgs",
        )
        self._model.fit(arr, labels)

    def predict_proba(self, x: Sequence[Sequence[float]]) -> list[list[float]]:
        if self._model is None:
            raise RuntimeError("model not fitted")
        import numpy as np

        proba = self._model.predict_proba(np.asarray(x, dtype=float))
        return [list(map(float, row)) for row in proba]
