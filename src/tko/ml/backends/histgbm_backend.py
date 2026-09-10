"""HistGradientBoosting backend — sklearn native, no extra deps."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tko.ml.backends.base import Backend


class HistGBMBackend(Backend):
    name = "histgbm"

    def __init__(
        self,
        *,
        max_iter: int = 40,
        max_depth: int = 4,
        random_state: int = 42,
    ) -> None:
        self.max_iter = max_iter
        self.max_depth = max_depth
        self.random_state = random_state
        self._model: Any = None

    def is_available(self) -> bool:
        try:
            from sklearn.ensemble import (  # noqa: F401
                HistGradientBoostingClassifier,
            )

            return True
        except ImportError:
            return False

    def fit(self, x: Sequence[Sequence[float]], y: Sequence[int]) -> None:
        if not self.is_available():
            raise RuntimeError("sklearn not available")
        from sklearn.ensemble import HistGradientBoostingClassifier
        import numpy as np

        arr = np.asarray(x, dtype=float)
        labels = np.asarray(y, dtype=int)
        self._model = HistGradientBoostingClassifier(
            max_iter=self.max_iter,
            max_depth=self.max_depth,
            random_state=self.random_state,
        )
        self._model.fit(arr, labels)

    def predict_proba(self, x: Sequence[Sequence[float]]) -> list[list[float]]:
        if self._model is None:
            raise RuntimeError("model not fitted")
        import numpy as np

        proba = self._model.predict_proba(np.asarray(x, dtype=float))
        return [list(map(float, row)) for row in proba]
