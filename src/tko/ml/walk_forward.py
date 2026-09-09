"""Walk-forward classification validation (time-series safe)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tko.ml.models import make_classifier, sklearn_available


@dataclass
class WalkForwardFold:
    train_size: int
    test_size: int
    accuracy: float
    n_pred: int


@dataclass
class WalkForwardResult:
    folds: list[WalkForwardFold] = field(default_factory=list)
    mean_accuracy: float = 0.0
    kind: str = "logreg"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "mean_accuracy": self.mean_accuracy,
            "n_folds": len(self.folds),
            "folds": [
                {
                    "train_size": f.train_size,
                    "test_size": f.test_size,
                    "accuracy": f.accuracy,
                    "n_pred": f.n_pred,
                }
                for f in self.folds
            ],
        }


def walk_forward_classify(
    x: list[list[float]],
    y: list[int],
    *,
    kind: str = "logreg",
    train_bars: int = 200,
    test_bars: int = 40,
    step_bars: int = 40,
    min_classes: int = 2,
) -> WalkForwardResult:
    if not sklearn_available():
        raise ImportError("scikit-learn required for walk_forward_classify")
    pairs = [(xi, yi) for xi, yi in zip(x, y) if yi in (-1, 1)]
    if len(pairs) < train_bars + test_bars:
        return WalkForwardResult(kind=kind)
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    folds: list[WalkForwardFold] = []
    start = 0
    n = len(xs)
    while start + train_bars + test_bars <= n:
        tr_x = xs[start : start + train_bars]
        tr_y = ys[start : start + train_bars]
        te_x = xs[start + train_bars : start + train_bars + test_bars]
        te_y = ys[start + train_bars : start + train_bars + test_bars]
        if len(set(tr_y)) < min_classes:
            start += step_bars
            continue
        clf = make_classifier(kind)
        clf.fit(tr_x, tr_y)
        pred = list(clf.predict(te_x))
        correct = sum(1 for a, b in zip(pred, te_y) if a == b)
        acc = correct / len(te_y) if te_y else 0.0
        folds.append(
            WalkForwardFold(
                train_size=len(tr_x), test_size=len(te_y), accuracy=acc, n_pred=len(pred)
            )
        )
        start += step_bars
    mean_acc = sum(f.accuracy for f in folds) / len(folds) if folds else 0.0
    return WalkForwardResult(folds=folds, mean_accuracy=mean_acc, kind=kind)
