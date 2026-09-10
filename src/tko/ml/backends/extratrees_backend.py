"""ExtraTrees backend — small bagging ensemble."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tko.ml.backends.base import Backend


class ExtraTreesBackend(Backend):
    name = "extratrees"

    def __init__(
        self,
        *,
        n_estimators: int = 40,
        max_depth: int = 4,
        n_jobs: int = 1,
        random_state: int = 42,
    ) -> None:
        self.n_estimators = n_estimators
        self.max_depth = max_depth
        self.n_jobs = n_jobs
        self.random_state = random_state
        self._model: Any = None

    def is_available(self) -> bool:
        try:
            from sklearn.ensemble import ExtraTreesClassifier  # noqa: F401

            return True
        except ImportError:
            return False

    def fit(self, x: Sequence[Sequence[float]], y: Sequence[int]) -> None:
        if not self.is_available():
            raise RuntimeError("sklearn not available")
        from sklearn.ensemble import ExtraTreesClassifier
        import numpy as np

        arr = np.asarray(x, dtype=float)
        labels = np.asarray(y, dtype=int)
        self._model = ExtraTreesClassifier(
            n_estimators=self.n_estimators,
            max_depth=self.max_depth,
            n_jobs=self.n_jobs,
            random_state=self.random_state,
        )
        self._model.fit(arr, labels)

    def predict_proba(self, x: Sequence[Sequence[float]]) -> list[list[float]]:
        if self._model is None:
            raise RuntimeError("model not fitted")
        import numpy as np

        proba = self._model.predict_proba(np.asarray(x, dtype=float))
        return [list(map(float, row)) for row in proba]
