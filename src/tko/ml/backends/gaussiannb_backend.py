"""GaussianNB backend — ultra-light probabilistic model."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tko.ml.backends.base import Backend


class GaussianNBBackend(Backend):
    name = "gaussiannb"

    def __init__(self) -> None:
        self._model: Any = None

    def is_available(self) -> bool:
        try:
            from sklearn.naive_bayes import GaussianNB  # noqa: F401

            return True
        except ImportError:
            return False

    def fit(self, x: Sequence[Sequence[float]], y: Sequence[int]) -> None:
        if not self.is_available():
            raise RuntimeError("sklearn not available")
        from sklearn.naive_bayes import GaussianNB
        import numpy as np

        arr = np.asarray(x, dtype=float)
        labels = np.asarray(y, dtype=int)
        self._model = GaussianNB()
        self._model.fit(arr, labels)

    def predict_proba(self, x: Sequence[Sequence[float]]) -> list[list[float]]:
        if self._model is None:
            raise RuntimeError("model not fitted")
        import numpy as np

        proba = self._model.predict_proba(np.asarray(x, dtype=float))
        return [list(map(float, row)) for row in proba]
