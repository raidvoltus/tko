"""
Probability / score calibration for trading signals.

References (methodology, not copied code):
- Platt scaling (logistic map raw score → probability)
- Isotonic regression / PAV for monotone calibration
- ECE, Brier score (Niculescu-Mizil & Caruana; financial ML calibration practice)

CPU-only, no sklearn required. Calibrated=True only after fit on held-out data.
Does not place orders.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np


def brier_score(y_true: Sequence[float], p_pred: Sequence[float]) -> float:
    y = np.asarray(y_true, dtype=np.float64)
    p = np.clip(np.asarray(p_pred, dtype=np.float64), 0.0, 1.0)
    if y.size == 0:
        return 1.0
    return float(np.mean((p - y) ** 2))


def expected_calibration_error(
    y_true: Sequence[float],
    p_pred: Sequence[float],
    n_bins: int = 10,
) -> float:
    y = np.asarray(y_true, dtype=np.float64)
    p = np.clip(np.asarray(p_pred, dtype=np.float64), 0.0, 1.0)
    if y.size == 0:
        return 1.0
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    n = y.size
    for i in range(n_bins):
        lo, hi = bins[i], bins[i + 1]
        mask = (p >= lo) & (p < hi if i < n_bins - 1 else p <= hi)
        if not np.any(mask):
            continue
        ece += abs(float(p[mask].mean()) - float(y[mask].mean())) * (mask.sum() / n)
    return float(ece)


def reliability_table(
    y_true: Sequence[float],
    p_pred: Sequence[float],
    n_bins: int = 10,
) -> List[Dict[str, float]]:
    y = np.asarray(y_true, dtype=np.float64)
    p = np.clip(np.asarray(p_pred, dtype=np.float64), 0.0, 1.0)
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    rows: List[Dict[str, float]] = []
    for i in range(n_bins):
        lo, hi = bins[i], bins[i + 1]
        mask = (p >= lo) & (p < hi if i < n_bins - 1 else p <= hi)
        if not np.any(mask):
            continue
        rows.append(
            {
                "bin_lo": float(lo),
                "bin_hi": float(hi),
                "count": float(mask.sum()),
                "avg_pred": float(p[mask].mean()),
                "avg_outcome": float(y[mask].mean()),
            }
        )
    return rows


@dataclass
class PlattCalibrator:
    """Two-parameter logistic: p = 1/(1+exp(a*s+b)) on raw scores s."""

    a: float = -1.0
    b: float = 0.0
    fitted: bool = False
    n_fit: int = 0
    ece_before: float = 1.0
    ece_after: float = 1.0
    brier_after: float = 1.0

    def fit(self, scores: Sequence[float], y: Sequence[float], max_iter: int = 50) -> "PlattCalibrator":
        s = np.asarray(scores, dtype=np.float64)
        yt = np.asarray(y, dtype=np.float64)
        if s.size < 20:
            self.fitted = False
            return self
        # Newton on logistic loss
        a, b = -1.0, 0.0
        for _ in range(max_iter):
            z = a * s + b
            p = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
            p = np.clip(p, 1e-6, 1 - 1e-6)
            err = p - yt
            w = p * (1 - p)
            # gradient
            ga = float(np.sum(err * s))
            gb = float(np.sum(err))
            # hessian
            haa = float(np.sum(w * s * s)) + 1e-8
            hab = float(np.sum(w * s))
            hbb = float(np.sum(w)) + 1e-8
            det = haa * hbb - hab * hab
            if abs(det) < 1e-12:
                break
            da = (hbb * ga - hab * gb) / det
            db = (-hab * ga + haa * gb) / det
            a -= da
            b -= db
            if abs(da) + abs(db) < 1e-8:
                break
        self.a, self.b = float(a), float(b)
        self.fitted = True
        self.n_fit = int(s.size)
        raw_p = 1.0 / (1.0 + np.exp(-np.clip(s, -30, 30)))  # naive map if score~logit
        # if scores already in [0,1], treat as raw p
        if float(np.nanmin(s)) >= 0 and float(np.nanmax(s)) <= 1:
            raw_p = np.clip(s, 1e-6, 1 - 1e-6)
        self.ece_before = expected_calibration_error(yt, raw_p)
        cal = self.transform(s)
        self.ece_after = expected_calibration_error(yt, cal)
        self.brier_after = brier_score(yt, cal)
        return self

    def transform(self, scores: Sequence[float]) -> np.ndarray:
        s = np.asarray(scores, dtype=np.float64)
        if not self.fitted:
            # identity-ish for [0,1] scores
            if s.size and float(np.nanmin(s)) >= 0 and float(np.nanmax(s)) <= 1:
                return np.clip(s, 0.0, 1.0)
            return 1.0 / (1.0 + np.exp(-np.clip(s, -30, 30)))
        z = self.a * s + self.b
        return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


@dataclass
class IsotonicCalibrator:
    """
    Pool-adjacent-violators (PAV) monotone map from score → empirical frequency.
    Prefer when n is large; Platt when n is small (<~200).
    """

    x_thresholds: np.ndarray = field(default_factory=lambda: np.array([0.0, 1.0]))
    y_values: np.ndarray = field(default_factory=lambda: np.array([0.0, 1.0]))
    fitted: bool = False
    n_fit: int = 0
    ece_after: float = 1.0

    def fit(self, scores: Sequence[float], y: Sequence[float]) -> "IsotonicCalibrator":
        s = np.asarray(scores, dtype=np.float64)
        yt = np.asarray(y, dtype=np.float64)
        if s.size < 30:
            self.fitted = False
            return self
        # Quantile bins + monotone non-decreasing projection (PAV on bin means)
        n_bins = int(min(20, max(5, s.size // 25)))
        qs = np.linspace(0, 1, n_bins + 1)
        edges = np.quantile(s, qs)
        edges = np.unique(edges)
        if len(edges) < 3:
            edges = np.linspace(float(s.min()), float(s.max()) + 1e-9, 5)
        centers = []
        means = []
        for i in range(len(edges) - 1):
            lo, hi = edges[i], edges[i + 1]
            mask = (s >= lo) & (s <= hi if i == len(edges) - 2 else s < hi)
            if not np.any(mask):
                continue
            centers.append(float(0.5 * (lo + hi)))
            means.append(float(yt[mask].mean()))
        if len(means) < 2:
            self.fitted = False
            return self
        # enforce non-decreasing
        m = np.asarray(means, dtype=np.float64)
        for i in range(1, len(m)):
            if m[i] < m[i - 1]:
                m[i] = m[i - 1]
        self.x_thresholds = np.asarray(centers, dtype=np.float64)
        self.y_values = np.clip(m, 0.0, 1.0)
        self.fitted = True
        self.n_fit = int(s.size)
        self.ece_after = expected_calibration_error(yt, self.transform(s))
        return self

    def transform(self, scores: Sequence[float]) -> np.ndarray:
        s = np.asarray(scores, dtype=np.float64)
        if not self.fitted:
            return np.clip(s, 0.0, 1.0) if s.size and np.nanmin(s) >= 0 and np.nanmax(s) <= 1 else np.full_like(s, 0.5)
        # right-constant step: find last threshold <= s
        idx = np.searchsorted(self.x_thresholds, s, side="right") - 1
        idx = np.clip(idx, 0, len(self.y_values) - 1)
        return self.y_values[idx]


@dataclass
class EdgeCalibrator:
    """
    Map strategy composite [-1,1] → expected net edge % using bin means of realized PnL.
    Not a guarantee of future edge; marks calibrated only after fit.
    """

    bin_edges: np.ndarray = field(default_factory=lambda: np.linspace(-1, 1, 11))
    bin_means: np.ndarray = field(default_factory=lambda: np.zeros(10))
    fitted: bool = False
    n_fit: int = 0

    def fit(self, composites: Sequence[float], realized_net_pnl_pct: Sequence[float]) -> "EdgeCalibrator":
        c = np.asarray(composites, dtype=np.float64)
        r = np.asarray(realized_net_pnl_pct, dtype=np.float64)
        if c.size < 50:
            self.fitted = False
            return self
        edges = np.linspace(-1.0, 1.0, 11)
        means = []
        for i in range(10):
            mask = (c >= edges[i]) & (c < edges[i + 1] if i < 9 else c <= edges[i + 1])
            means.append(float(r[mask].mean()) if np.any(mask) else 0.0)
        self.bin_edges = edges
        self.bin_means = np.asarray(means, dtype=np.float64)
        self.fitted = True
        self.n_fit = int(c.size)
        return self

    def expected_net_edge_pct(self, composite: float) -> float:
        if not self.fitted:
            # fallback heuristic (uncalibrated)
            return float(composite) * 2.5 * 0.7  # rough cost-aware shrink
        x = float(np.clip(composite, -1.0, 1.0))
        idx = int(np.searchsorted(self.bin_edges, x, side="right") - 1)
        idx = int(np.clip(idx, 0, len(self.bin_means) - 1))
        return float(self.bin_means[idx])
