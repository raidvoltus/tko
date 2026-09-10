"""Ensemble layer — NNLS weighted voting with soft-voting fallback.

Weights are non-negative and normalised to sum 1.0.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def _normalise(weights: list[float]) -> list[float]:
    total = sum(max(0.0, w) for w in weights)
    if total <= 0.0:
        n = len(weights)
        return [1.0 / n] * n if n else []
    return [max(0.0, w) / total for w in weights]


class SoftVotingEnsemble:
    """Simple average of model probabilities (equal weights)."""

    def __init__(self) -> None:
        self.weights: list[float] = []

    def fit(
        self,
        preds_val: Sequence[Sequence[Sequence[float]]],
        y_val: Sequence[int],
    ) -> None:
        n = len(preds_val)
        self.weights = [1.0 / n] * n if n else []

    def predict_proba(
        self,
        preds: Sequence[Sequence[Sequence[float]]],
    ) -> list[list[float]]:
        """Weighted average of per-model proba matrices."""
        if not preds:
            return []
        n_models = len(preds)
        n_samples = len(preds[0])
        n_classes = len(preds[0][0]) if n_samples else 0
        w = self.weights if len(self.weights) == n_models else [1.0 / n_models] * n_models
        w = _normalise(list(w))

        out: list[list[float]] = []
        for i in range(n_samples):
            row = [0.0] * n_classes
            for m in range(n_models):
                for c in range(n_classes):
                    row[c] += w[m] * float(preds[m][i][c])
            out.append(row)
        return out


class NnlsEnsemble:
    """Non-negative least squares ensemble (scipy) with soft-voting fallback."""

    def __init__(self) -> None:
        self.weights: list[float] = []
        self._used_nnls = False

    def fit(
        self,
        preds_val: Sequence[Sequence[Sequence[float]]],
        y_val: Sequence[int],
    ) -> None:
        """Learn non-negative weights from validation predictions.

        preds_val: list of (n_samples, n_classes) proba matrices.
        We use the probability of the positive class (index 1) when available.
        """
        n_models = len(preds_val)
        if n_models == 0:
            self.weights = []
            return

        # Prefer NNLS when scipy is present
        try:
            import numpy as np
            from scipy.optimize import nnls  # type: ignore[import-untyped]

            n_samples = len(preds_val[0])
            # Design matrix: columns = models, target = y (as float)
            # Use class-1 probability if multi-class
            X = np.zeros((n_samples, n_models), dtype=float)
            for m in range(n_models):
                for i in range(n_samples):
                    row = preds_val[m][i]
                    X[i, m] = float(row[1]) if len(row) > 1 else float(row[0])
            y = np.asarray(y_val, dtype=float)
            coef, _ = nnls(X, y)
            self.weights = _normalise([float(c) for c in coef])
            self._used_nnls = True
            return
        except Exception:
            pass

        # Fallback: equal weights
        self.weights = [1.0 / n_models] * n_models
        self._used_nnls = False

    def predict_proba(
        self,
        preds: Sequence[Sequence[Sequence[float]]],
    ) -> list[list[float]]:
        if not preds:
            return []
        n_models = len(preds)
        n_samples = len(preds[0])
        n_classes = len(preds[0][0]) if n_samples else 0
        w = (
            self.weights
            if len(self.weights) == n_models
            else [1.0 / n_models] * n_models
        )
        w = _normalise(list(w))

        out: list[list[float]] = []
        for i in range(n_samples):
            row = [0.0] * n_classes
            for m in range(n_models):
                for c in range(n_classes):
                    row[c] += w[m] * float(preds[m][i][c])
            out.append(row)
        return out

    def status(self) -> dict[str, Any]:
        return {
            "weights": list(self.weights),
            "used_nnls": self._used_nnls,
        }
