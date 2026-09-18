"""
Model / data / calibration drift monitoring (detection only — no auto model swap).

Fail-closed recommendation: escalate to operator; never auto-promote or open risk.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import numpy as np


@dataclass
class DriftReport:
    kind: str
    score: float
    threshold: float
    drifted: bool
    n: int
    notes: List[str] = field(default_factory=list)


def population_stability_index(
    expected: np.ndarray,
    actual: np.ndarray,
    n_bins: int = 10,
    eps: float = 1e-6,
) -> float:
    """PSI between two univariate samples (feature drift diagnostic)."""
    expected = np.asarray(expected, dtype=np.float64)
    actual = np.asarray(actual, dtype=np.float64)
    if expected.size < 20 or actual.size < 20:
        return 0.0
    qs = np.linspace(0, 1, n_bins + 1)
    edges = np.unique(np.quantile(expected, qs))
    if len(edges) < 3:
        return 0.0
    psi = 0.0
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        e = np.mean((expected >= lo) & (expected <= hi if i == len(edges) - 2 else expected < hi))
        a = np.mean((actual >= lo) & (actual <= hi if i == len(edges) - 2 else actual < hi))
        e = max(e, eps)
        a = max(a, eps)
        psi += (a - e) * np.log(a / e)
    return float(psi)


def brier_drift(reference_brier: float, recent_brier: float, threshold: float = 0.05) -> DriftReport:
    delta = float(recent_brier - reference_brier)
    return DriftReport(
        kind="calibration_brier",
        score=delta,
        threshold=threshold,
        drifted=delta > threshold,
        n=0,
        notes=["operator_review_required"] if delta > threshold else [],
    )


def feature_drift_psi(baseline: np.ndarray, recent: np.ndarray, threshold: float = 0.25) -> DriftReport:
    psi = population_stability_index(baseline, recent)
    return DriftReport(
        kind="feature_psi",
        score=psi,
        threshold=threshold,
        drifted=psi > threshold,
        n=int(min(len(baseline), len(recent))),
        notes=["operator_review_required"] if psi > threshold else [],
    )
