"""Probability calibration utilities and Brier score."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass
class CalibrationReport:
    brier: float
    n: int
    method: str = "identity"
    ece: float = 0.0  # expected calibration error (binned)

    def to_dict(self) -> dict:
        return {"brier": self.brier, "n": self.n, "method": self.method, "ece": self.ece}


def brier_score(y_true: Sequence[int], y_prob: Sequence[float]) -> float:
    if not y_true or len(y_true) != len(y_prob):
        return 1.0
    s = 0.0
    for yt, yp in zip(y_true, y_prob):
        p = min(1.0, max(0.0, float(yp)))
        s += (p - float(yt)) ** 2
    return s / len(y_true)


def expected_calibration_error(
    y_true: Sequence[int], y_prob: Sequence[float], *, n_bins: int = 10
) -> float:
    if not y_true:
        return 1.0
    bins = [[] for _ in range(n_bins)]
    for yt, yp in zip(y_true, y_prob):
        p = min(0.999, max(0.0, float(yp)))
        b = min(n_bins - 1, int(p * n_bins))
        bins[b].append((int(yt), p))
    ece = 0.0
    n = len(y_true)
    for bucket in bins:
        if not bucket:
            continue
        acc = sum(y for y, _ in bucket) / len(bucket)
        conf = sum(p for _, p in bucket) / len(bucket)
        ece += (len(bucket) / n) * abs(acc - conf)
    return ece


def identity_calibrate(probs: Sequence[float]) -> list[float]:
    return [min(1.0, max(0.0, float(p))) for p in probs]


@dataclass
class PlattCalibrator:
    """Simple logistic (Platt) calibrator fitted on OOS scores."""

    a: float = 1.0
    b: float = 0.0
    fitted: bool = False

    def fit(self, scores: Sequence[float], y: Sequence[int], *, iters: int = 50) -> None:
        # Newton-ish 1D logistic on score → P(y=1)
        import math
        a, b = 1.0, 0.0
        for _ in range(iters):
            g_a = g_b = 0.0
            h_aa = h_ab = h_bb = 0.0
            for s, yt in zip(scores, y):
                z = a * float(s) + b
                # sigmoid
                if z >= 0:
                    ez = math.exp(-z)
                    p = 1.0 / (1.0 + ez)
                else:
                    ez = math.exp(z)
                    p = ez / (1.0 + ez)
                diff = p - float(yt)
                g_a += diff * float(s)
                g_b += diff
                w = p * (1.0 - p)
                h_aa += w * float(s) * float(s)
                h_ab += w * float(s)
                h_bb += w
            # solve 2x2
            det = h_aa * h_bb - h_ab * h_ab
            if abs(det) < 1e-12:
                break
            da = (h_bb * g_a - h_ab * g_b) / det
            db = (h_aa * g_b - h_ab * g_a) / det
            a -= da
            b -= db
        self.a, self.b, self.fitted = a, b, True

    def transform(self, scores: Sequence[float]) -> list[float]:
        import math
        out = []
        for s in scores:
            z = self.a * float(s) + self.b
            if z >= 0:
                ez = math.exp(-z)
                p = 1.0 / (1.0 + ez)
            else:
                ez = math.exp(z)
                p = ez / (1.0 + ez)
            out.append(p)
        return out


def evaluate_calibration(
    y_true: Sequence[int], y_prob: Sequence[float], *, method: str = "identity"
) -> CalibrationReport:
    return CalibrationReport(
        brier=brier_score(y_true, y_prob),
        n=len(y_true),
        method=method,
        ece=expected_calibration_error(y_true, y_prob),
    )
