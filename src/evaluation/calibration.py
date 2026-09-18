"""
Scientific, leakage-safe calibration framework for TKO.

Contract:
  fit → transform → validate
  Multi-candidate selection on dedicated selection segment only
  Untouched OOS evaluation
  Reproducible CalibrationArtifact (identity hash excludes timestamps)
  No order authority

References (methodology): Platt; isotonic; beta (Kull); temperature (Guo);
Fonseca & Lopes PD time-series; Brier/ECE as diagnostics.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

CALIBRATION_CODE_VERSION = "tko-cal-v2"

# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def _as_prob(p: Sequence[float]) -> np.ndarray:
    a = np.asarray(p, dtype=np.float64)
    return np.clip(a, 0.0, 1.0)


def brier_score(y_true: Sequence[float], p_pred: Sequence[float]) -> float:
    y = np.asarray(y_true, dtype=np.float64)
    p = _as_prob(p_pred)
    if y.size == 0:
        return 1.0
    return float(np.mean((p - y) ** 2))


def log_loss(y_true: Sequence[float], p_pred: Sequence[float], eps: float = 1e-7) -> float:
    y = np.asarray(y_true, dtype=np.float64)
    p = np.clip(_as_prob(p_pred), eps, 1.0 - eps)
    if y.size == 0:
        return 10.0
    return float(-np.mean(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))


def expected_calibration_error(
    y_true: Sequence[float], p_pred: Sequence[float], n_bins: int = 10, *, quantile: bool = False
) -> float:
    y = np.asarray(y_true, dtype=np.float64)
    p = _as_prob(p_pred)
    if y.size == 0:
        return 1.0
    if quantile:
        edges = np.unique(np.quantile(p, np.linspace(0, 1, n_bins + 1)))
        if len(edges) < 2:
            return 0.0
    else:
        edges = np.linspace(0.0, 1.0, n_bins + 1)
    ece, n = 0.0, y.size
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        mask = (p >= lo) & (p <= hi if i == len(edges) - 2 else p < hi)
        if not np.any(mask):
            continue
        ece += abs(float(p[mask].mean()) - float(y[mask].mean())) * (mask.sum() / n)
    return float(ece)


def maximum_calibration_error(
    y_true: Sequence[float], p_pred: Sequence[float], n_bins: int = 10
) -> float:
    y = np.asarray(y_true, dtype=np.float64)
    p = _as_prob(p_pred)
    if y.size == 0:
        return 1.0
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    mce = 0.0
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        mask = (p >= lo) & (p < hi if i < n_bins - 1 else p <= hi)
        if not np.any(mask):
            continue
        mce = max(mce, abs(float(p[mask].mean()) - float(y[mask].mean())))
    return float(mce)


def calibration_slope_intercept_linear(
    y_true: Sequence[float], p_pred: Sequence[float]
) -> Tuple[float, float]:
    """Auxiliary: y ≈ intercept + slope * p (probability scale)."""
    y = np.asarray(y_true, dtype=np.float64)
    p = _as_prob(p_pred)
    if y.size < 5:
        return 0.0, 1.0
    pm, ym = float(p.mean()), float(y.mean())
    var = float(np.sum((p - pm) ** 2))
    if var < 1e-12:
        return ym, 0.0
    slope = float(np.sum((p - pm) * (y - ym)) / var)
    return ym - slope * pm, slope


def calibration_slope_intercept_logit(
    y_true: Sequence[float], p_pred: Sequence[float], eps: float = 1e-6
) -> Tuple[float, float]:
    """
    Primary diagnostic: logit(P(Y=1)) = alpha + beta * logit(p).
    Ideal: alpha≈0, beta≈1.
    """
    y = np.asarray(y_true, dtype=np.float64)
    p = np.clip(_as_prob(p_pred), eps, 1.0 - eps)
    if y.size < 10:
        return 0.0, 1.0
    # Use empirical logits of p; for binary y use regularized
    logit_p = np.log(p / (1.0 - p))
    # IRLS-lite: logistic regression of y on logit_p
    a, b = 0.0, 1.0
    for _ in range(40):
        z = a + b * logit_p
        pr = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
        pr = np.clip(pr, eps, 1 - eps)
        w = pr * (1 - pr)
        # design [1, logit_p]
        err = pr - y
        ga = float(np.sum(err))
        gb = float(np.sum(err * logit_p))
        haa = float(np.sum(w)) + 1e-8
        hab = float(np.sum(w * logit_p))
        hbb = float(np.sum(w * logit_p * logit_p)) + 1e-8
        det = haa * hbb - hab * hab
        if abs(det) < 1e-14:
            break
        da = (hbb * ga - hab * gb) / det
        db = (-hab * ga + haa * gb) / det
        a -= da
        b -= db
        if abs(da) + abs(db) < 1e-9:
            break
    if not (math.isfinite(a) and math.isfinite(b)):
        return 0.0, 1.0
    return float(a), float(b)


# backward-compatible name → linear auxiliary
def calibration_slope_intercept(
    y_true: Sequence[float], p_pred: Sequence[float]
) -> Tuple[float, float]:
    return calibration_slope_intercept_linear(y_true, p_pred)


def full_metrics(y_true: Sequence[float], p_pred: Sequence[float]) -> Dict[str, float]:
    inter_l, slope_l = calibration_slope_intercept_linear(y_true, p_pred)
    inter_g, slope_g = calibration_slope_intercept_logit(y_true, p_pred)
    return {
        "brier": brier_score(y_true, p_pred),
        "log_loss": log_loss(y_true, p_pred),
        "ece": expected_calibration_error(y_true, p_pred),
        "ece_quantile": expected_calibration_error(y_true, p_pred, quantile=True),
        "mce": maximum_calibration_error(y_true, p_pred),
        "intercept_linear": inter_l,
        "slope_linear": slope_l,
        "intercept_logit": inter_g,
        "slope_logit": slope_g,
        # legacy keys
        "intercept": inter_l,
        "slope": slope_l,
        "n": float(len(y_true)),
    }


def paired_block_bootstrap_delta(
    y: Sequence[float],
    p_a: Sequence[float],
    p_b: Sequence[float],
    *,
    block_size: int = 10,
    n_boot: int = 200,
    seed: int = 42,
    alpha: float = 0.05,
) -> Dict[str, float]:
    """Paired block bootstrap CI for delta Brier (brier_a - brier_b)."""
    y_a = np.asarray(y, dtype=np.float64)
    pa = _as_prob(p_a)
    pb = _as_prob(p_b)
    n = len(y_a)
    if n < block_size or n == 0:
        da = brier_score(y_a, pa) - brier_score(y_a, pb)
        return {
            "delta_brier": da,
            "ci_low": da,
            "ci_high": da,
            "n_boot": 0,
            "block_size": block_size,
            "seed": seed,
        }
    loss_a = (pa - y_a) ** 2
    loss_b = (pb - y_a) ** 2
    delta_i = loss_a - loss_b
    rng = np.random.default_rng(seed)
    n_blocks = int(math.ceil(n / block_size))
    means = []
    for _ in range(n_boot):
        starts = rng.integers(0, max(1, n - block_size + 1), size=n_blocks)
        sample = np.concatenate([delta_i[s : s + block_size] for s in starts])[:n]
        means.append(float(sample.mean()))
    arr = np.sort(np.asarray(means))
    return {
        "delta_brier": float(delta_i.mean()),
        "ci_low": float(np.quantile(arr, alpha / 2)),
        "ci_high": float(np.quantile(arr, 1 - alpha / 2)),
        "n_boot": n_boot,
        "block_size": block_size,
        "seed": seed,
        "alpha": alpha,
    }


# ---------------------------------------------------------------------------
# Calibrators
# ---------------------------------------------------------------------------


class BaseCalibrator:
    name: str = "base"
    fitted: bool = False
    status: str = "UNFITTED"  # UNFITTED | FITTED | INVALID

    def fit(self, scores: Sequence[float], y: Sequence[float]) -> "BaseCalibrator":
        raise NotImplementedError

    def transform(self, scores: Sequence[float]) -> np.ndarray:
        raise NotImplementedError

    def validate(self) -> bool:
        return self.fitted and self.status == "FITTED"

    def params_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "fitted": self.fitted, "status": self.status}


def _safe_prob_out(p: np.ndarray) -> np.ndarray:
    p = np.asarray(p, dtype=np.float64)
    p = np.where(np.isfinite(p), p, 0.5)
    return np.clip(p, 0.0, 1.0)


def _class_diversity_ok(y: np.ndarray, min_pos: int = 3, min_neg: int = 3) -> bool:
    pos = int(np.sum(y >= 0.5))
    neg = int(len(y) - pos)
    return pos >= min_pos and neg >= min_neg


@dataclass
class IdentityCalibrator(BaseCalibrator):
    name: str = "identity"
    fitted: bool = True
    status: str = "FITTED"  # baseline, never "CALIBRATED"

    def fit(self, scores: Sequence[float], y: Sequence[float]) -> "IdentityCalibrator":
        self.fitted = True
        self.status = "FITTED"
        return self

    def transform(self, scores: Sequence[float]) -> np.ndarray:
        s = np.asarray(scores, dtype=np.float64)
        if s.size == 0:
            return s
        if np.all(np.isfinite(s)) and float(np.nanmin(s)) >= 0 and float(np.nanmax(s)) <= 1:
            return _safe_prob_out(s)
        return _safe_prob_out(1.0 / (1.0 + np.exp(-np.clip(s, -30, 30))))


@dataclass
class PlattCalibrator(BaseCalibrator):
    name: str = "platt"
    a: float = -1.0
    b: float = 0.0
    fitted: bool = False
    status: str = "UNFITTED"
    n_fit: int = 0

    def fit(self, scores: Sequence[float], y: Sequence[float], max_iter: int = 50) -> "PlattCalibrator":
        s = np.asarray(scores, dtype=np.float64)
        yt = np.asarray(y, dtype=np.float64)
        if s.size < 20 or not _class_diversity_ok(yt) or not np.all(np.isfinite(s)):
            self.fitted, self.status = False, "INVALID"
            return self
        a, b = -1.0, 0.0
        for _ in range(max_iter):
            z = a * s + b
            p = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
            p = np.clip(p, 1e-6, 1 - 1e-6)
            err = p - yt
            w = p * (1 - p)
            ga, gb = float(np.sum(err * s)), float(np.sum(err))
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
        if not (math.isfinite(a) and math.isfinite(b)) or abs(a) > 1e6 or abs(b) > 1e6:
            self.fitted, self.status = False, "INVALID"
            return self
        self.a, self.b = float(a), float(b)
        self.fitted, self.status, self.n_fit = True, "FITTED", int(s.size)
        return self

    def transform(self, scores: Sequence[float]) -> np.ndarray:
        s = np.asarray(scores, dtype=np.float64)
        if not self.validate():
            return IdentityCalibrator().transform(s)
        return _safe_prob_out(1.0 / (1.0 + np.exp(-np.clip(self.a * s + self.b, -30, 30))))

    def params_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "fitted": self.fitted, "status": self.status, "a": self.a, "b": self.b, "n_fit": self.n_fit}


@dataclass
class IsotonicCalibrator(BaseCalibrator):
    name: str = "isotonic"
    x_thresholds: np.ndarray = field(default_factory=lambda: np.array([0.0, 1.0]))
    y_values: np.ndarray = field(default_factory=lambda: np.array([0.0, 1.0]))
    fitted: bool = False
    status: str = "UNFITTED"
    n_fit: int = 0

    def fit(self, scores: Sequence[float], y: Sequence[float]) -> "IsotonicCalibrator":
        s = np.asarray(scores, dtype=np.float64)
        yt = np.asarray(y, dtype=np.float64)
        if s.size < 30 or not _class_diversity_ok(yt) or not np.all(np.isfinite(s)):
            self.fitted, self.status = False, "INVALID"
            return self
        n_bins = int(min(20, max(5, s.size // 25)))
        edges = np.unique(np.quantile(s, np.linspace(0, 1, n_bins + 1)))
        if len(edges) < 3:
            edges = np.linspace(float(np.nanmin(s)), float(np.nanmax(s)) + 1e-9, 5)
        centers, means = [], []
        for i in range(len(edges) - 1):
            lo, hi = edges[i], edges[i + 1]
            mask = (s >= lo) & (s <= hi if i == len(edges) - 2 else s < hi)
            if not np.any(mask):
                continue
            centers.append(float(0.5 * (lo + hi)))
            means.append(float(yt[mask].mean()))
        if len(means) < 2:
            self.fitted, self.status = False, "INVALID"
            return self
        m = np.asarray(means, dtype=np.float64)
        for i in range(1, len(m)):
            if m[i] < m[i - 1]:
                m[i] = m[i - 1]
        self.x_thresholds = np.asarray(centers, dtype=np.float64)
        self.y_values = np.clip(m, 0.0, 1.0)
        self.fitted, self.status, self.n_fit = True, "FITTED", int(s.size)
        return self

    def transform(self, scores: Sequence[float]) -> np.ndarray:
        s = np.asarray(scores, dtype=np.float64)
        if not self.validate():
            return IdentityCalibrator().transform(s)
        idx = np.clip(np.searchsorted(self.x_thresholds, s, side="right") - 1, 0, len(self.y_values) - 1)
        return _safe_prob_out(self.y_values[idx])

    def params_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "fitted": self.fitted, "status": self.status, "n_fit": self.n_fit, "n_knots": int(len(self.x_thresholds))}


@dataclass
class BetaCalibrator(BaseCalibrator):
    name: str = "beta"
    a: float = 1.0
    b: float = 1.0
    c: float = 0.0
    fitted: bool = False
    status: str = "UNFITTED"
    n_fit: int = 0

    def fit(self, scores: Sequence[float], y: Sequence[float], max_iter: int = 80) -> "BetaCalibrator":
        s = np.asarray(scores, dtype=np.float64)
        yt = np.asarray(y, dtype=np.float64)
        if s.size < 30 or not _class_diversity_ok(yt):
            self.fitted, self.status = False, "INVALID"
            return self
        if not (float(np.nanmin(s)) >= 0 and float(np.nanmax(s)) <= 1):
            s = 1.0 / (1.0 + np.exp(-np.clip(s, -30, 30)))
        s = np.clip(s, 1e-4, 1.0 - 1e-4)
        lp, lq = np.log(s), np.log(1.0 - s)
        a, b, c = 1.0, 1.0, 0.0
        for _ in range(max_iter):
            z = a * lp + b * lq + c
            p = np.clip(1.0 / (1.0 + np.exp(-np.clip(z, -30, 30))), 1e-6, 1 - 1e-6)
            err = p - yt
            w = p * (1 - p)
            ga, gb, gc = float(np.sum(err * lp)), float(np.sum(err * lq)), float(np.sum(err))
            haa = float(np.sum(w * lp * lp)) + 1e-6
            hbb = float(np.sum(w * lq * lq)) + 1e-6
            hcc = float(np.sum(w)) + 1e-6
            a -= ga / haa
            b -= gb / hbb
            c -= gc / hcc
            if abs(ga / haa) + abs(gb / hbb) + abs(gc / hcc) < 1e-8:
                break
        if not all(math.isfinite(x) for x in (a, b, c)) or max(abs(a), abs(b), abs(c)) > 1e4:
            self.fitted, self.status = False, "INVALID"
            return self
        self.a, self.b, self.c = float(a), float(b), float(c)
        self.fitted, self.status, self.n_fit = True, "FITTED", int(s.size)
        return self

    def transform(self, scores: Sequence[float]) -> np.ndarray:
        s = np.asarray(scores, dtype=np.float64)
        if not self.validate():
            return IdentityCalibrator().transform(s)
        if not (float(np.nanmin(s)) >= 0 and float(np.nanmax(s)) <= 1):
            s = 1.0 / (1.0 + np.exp(-np.clip(s, -30, 30)))
        s = np.clip(s, 1e-4, 1.0 - 1e-4)
        z = self.a * np.log(s) + self.b * np.log(1.0 - s) + self.c
        return _safe_prob_out(1.0 / (1.0 + np.exp(-np.clip(z, -30, 30))))

    def params_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "fitted": self.fitted, "status": self.status, "a": self.a, "b": self.b, "c": self.c, "n_fit": self.n_fit}


@dataclass
class TemperatureCalibrator(BaseCalibrator):
    name: str = "temperature"
    T: float = 1.0
    fitted: bool = False
    status: str = "UNFITTED"
    n_fit: int = 0

    def fit(self, scores: Sequence[float], y: Sequence[float]) -> "TemperatureCalibrator":
        s = np.asarray(scores, dtype=np.float64)
        yt = np.asarray(y, dtype=np.float64)
        if s.size < 20 or not _class_diversity_ok(yt):
            self.fitted, self.status = False, "INVALID"
            return self
        if float(np.nanmin(s)) >= 0 and float(np.nanmax(s)) <= 1:
            s = np.clip(s, 1e-6, 1 - 1e-6)
            logits = np.log(s / (1 - s))
        else:
            logits = s
        best_T, best_ll = 1.0, 1e9
        for T in np.linspace(0.5, 5.0, 46):
            if T <= 0:
                continue
            p = 1.0 / (1.0 + np.exp(-np.clip(logits / T, -30, 30)))
            ll = log_loss(yt, p)
            if ll < best_ll:
                best_ll, best_T = ll, float(T)
        if best_T <= 0 or not math.isfinite(best_T):
            self.fitted, self.status = False, "INVALID"
            return self
        self.T = best_T
        self.fitted, self.status, self.n_fit = True, "FITTED", int(s.size)
        return self

    def transform(self, scores: Sequence[float]) -> np.ndarray:
        s = np.asarray(scores, dtype=np.float64)
        if not self.validate() or self.T <= 0:
            return IdentityCalibrator().transform(s)
        if float(np.nanmin(s)) >= 0 and float(np.nanmax(s)) <= 1:
            s = np.clip(s, 1e-6, 1 - 1e-6)
            logits = np.log(s / (1 - s))
        else:
            logits = s
        return _safe_prob_out(1.0 / (1.0 + np.exp(-np.clip(logits / self.T, -30, 30))))

    def params_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "fitted": self.fitted, "status": self.status, "T": self.T, "n_fit": self.n_fit}


@dataclass
class EdgeCalibrator:
    """Expected net edge from composite. Status is explicit — never silent heuristic claim."""

    bin_edges: np.ndarray = field(default_factory=lambda: np.linspace(-1, 1, 11))
    bin_means: np.ndarray = field(default_factory=lambda: np.zeros(10))
    fitted: bool = False
    status: str = "UNCALIBRATED_HEURISTIC"  # or CALIBRATED_OOS
    n_fit: int = 0

    def fit(self, composites: Sequence[float], realized_net_pnl_pct: Sequence[float]) -> "EdgeCalibrator":
        c = np.asarray(composites, dtype=np.float64)
        r = np.asarray(realized_net_pnl_pct, dtype=np.float64)
        if c.size < 50 or not np.all(np.isfinite(c)) or not np.all(np.isfinite(r)):
            self.fitted = False
            self.status = "UNCALIBRATED_HEURISTIC"
            return self
        edges = np.linspace(-1.0, 1.0, 11)
        means = []
        for i in range(10):
            mask = (c >= edges[i]) & (c < edges[i + 1] if i < 9 else c <= edges[i + 1])
            means.append(float(r[mask].mean()) if np.any(mask) else 0.0)
        self.bin_edges = edges
        self.bin_means = np.asarray(means, dtype=np.float64)
        self.fitted = True
        self.status = "CALIBRATED_OOS"
        self.n_fit = int(c.size)
        return self

    def expected_net_edge_pct(self, composite: float) -> Tuple[float, str]:
        if not self.fitted or self.status != "CALIBRATED_OOS":
            return float(composite) * 2.5 * 0.7, "UNCALIBRATED_HEURISTIC"
        x = float(np.clip(composite, -1.0, 1.0))
        idx = int(np.clip(np.searchsorted(self.bin_edges, x, side="right") - 1, 0, len(self.bin_means) - 1))
        return float(self.bin_means[idx]), "CALIBRATED_OOS"


def _make_candidates() -> List[BaseCalibrator]:
    return [
        IdentityCalibrator(),
        PlattCalibrator(),
        IsotonicCalibrator(),
        BetaCalibrator(),
        TemperatureCalibrator(),
    ]


# ---------------------------------------------------------------------------
# Artifact + integrity
# ---------------------------------------------------------------------------


@dataclass
class CalibrationArtifact:
    artifact_version: str = "1"
    calibrator_type: str = "identity"
    calibrator_parameters: Dict[str, Any] = field(default_factory=dict)
    fitted_at: float = 0.0
    n_fit: int = 0
    n_select: int = 0
    n_oos: int = 0
    dataset_hash: str = ""
    feature_schema_hash: str = ""
    model_identity_hash: str = ""
    calibration_code_version: str = CALIBRATION_CODE_VERSION
    split_meta: Dict[str, Any] = field(default_factory=dict)
    metrics_raw: Dict[str, float] = field(default_factory=dict)
    metrics_selected: Dict[str, float] = field(default_factory=dict)
    metrics_oos: Dict[str, float] = field(default_factory=dict)
    candidate_metrics: Dict[str, Dict[str, float]] = field(default_factory=dict)
    delta_metrics: Dict[str, float] = field(default_factory=dict)
    uncertainty: Dict[str, float] = field(default_factory=dict)
    regime_metrics: Dict[str, Any] = field(default_factory=dict)
    calibration_status: str = "UNCALIBRATED"  # CALIBRATED | UNCALIBRATED | INVALID
    identity_hash: str = ""
    notes: List[str] = field(default_factory=list)
    seed: int = 42

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def is_calibrated(self) -> bool:
        return self.calibration_status == "CALIBRATED"

    def compute_identity_hash(self) -> str:
        payload = {
            "calibrator_type": self.calibrator_type,
            "calibrator_parameters": self.calibrator_parameters,
            "n_fit": self.n_fit,
            "n_select": self.n_select,
            "n_oos": self.n_oos,
            "dataset_hash": self.dataset_hash,
            "feature_schema_hash": self.feature_schema_hash,
            "model_identity_hash": self.model_identity_hash,
            "calibration_code_version": self.calibration_code_version,
            "split_meta": self.split_meta,
            "metrics_selected": self.metrics_selected,
            "candidate_keys": sorted(self.candidate_metrics.keys()),
            "seed": self.seed,
            "calibration_status": self.calibration_status,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    def seal(self) -> "CalibrationArtifact":
        self.identity_hash = self.compute_identity_hash()
        return self


def verify_artifact(
    art: CalibrationArtifact,
    *,
    expected_feature_schema_hash: str = "",
    expected_model_identity_hash: str = "",
    expected_dataset_hash: str = "",
) -> Tuple[bool, str]:
    if not art.identity_hash:
        return False, "MISSING_IDENTITY_HASH"
    if art.compute_identity_hash() != art.identity_hash:
        return False, "HASH_MISMATCH"
    if expected_feature_schema_hash and art.feature_schema_hash != expected_feature_schema_hash:
        return False, "FEATURE_SCHEMA_MISMATCH"
    if expected_model_identity_hash and art.model_identity_hash != expected_model_identity_hash:
        return False, "MODEL_IDENTITY_MISMATCH"
    if expected_dataset_hash and art.dataset_hash != expected_dataset_hash:
        return False, "DATASET_HASH_MISMATCH"
    if art.calibration_status == "INVALID":
        return False, "STATUS_INVALID"
    return True, "OK"


# ---------------------------------------------------------------------------
# Selector
# ---------------------------------------------------------------------------


@dataclass
class CalibratorSelector:
    min_brier_improve: float = 0.002
    min_n: int = 40
    seed: int = 42

    def select(
        self,
        scores_fit: Sequence[float],
        y_fit: Sequence[float],
        scores_sel: Sequence[float],
        y_sel: Sequence[float],
        *,
        scores_oos: Optional[Sequence[float]] = None,
        y_oos: Optional[Sequence[float]] = None,
        feature_schema_hash: str = "",
        dataset_hash: str = "",
        model_identity_hash: str = "",
        split_meta: Optional[Dict[str, Any]] = None,
    ) -> Tuple[BaseCalibrator, CalibrationArtifact]:
        yf = np.asarray(y_fit, dtype=np.float64)
        ys = np.asarray(y_sel, dtype=np.float64)
        sf = np.asarray(scores_fit, dtype=np.float64)
        ss = np.asarray(scores_sel, dtype=np.float64)
        id_cal = IdentityCalibrator()
        raw_m = full_metrics(ys, id_cal.transform(ss))
        candidate_metrics: Dict[str, Dict[str, float]] = {"identity": raw_m}

        best: BaseCalibrator = id_cal
        best_m = raw_m
        best_name = "identity"

        if len(sf) < self.min_n or len(ss) < max(20, self.min_n // 2):
            art = self._build_artifact(
                best, raw_m, raw_m, candidate_metrics, len(sf), len(ss), 0,
                feature_schema_hash, dataset_hash, model_identity_hash, split_meta or {},
                status="UNCALIBRATED", notes=["INSUFFICIENT_N"],
            )
            return best, art

        for cal in _make_candidates():
            if cal.name == "identity":
                continue
            try:
                cal.fit(sf, yf)
            except Exception:
                continue
            if not cal.validate():
                continue
            p = cal.transform(ss)
            if not np.all(np.isfinite(p)):
                continue
            m = full_metrics(ys, p)
            candidate_metrics[cal.name] = m
            if self._better(m, best_m):
                best, best_m, best_name = cal, m, cal.name

        notes: List[str] = []
        status = "UNCALIBRATED"
        if best_name != "identity":
            if raw_m["brier"] - best_m["brier"] < self.min_brier_improve:
                best, best_m, best_name = id_cal, raw_m, "identity"
                notes.append("NO_MEANINGFUL_IMPROVE_KEEP_IDENTITY")
            else:
                status = "CALIBRATED"
                notes.append(f"SELECTED_{best_name}")
        else:
            notes.append("KEEP_IDENTITY")

        # OOS evaluation only (never used for selection)
        n_oos = 0
        metrics_oos: Dict[str, float] = {}
        uncertainty: Dict[str, float] = {}
        delta_metrics: Dict[str, float] = {}
        if scores_oos is not None and y_oos is not None and len(scores_oos) > 0:
            so = np.asarray(scores_oos, dtype=np.float64)
            yo = np.asarray(y_oos, dtype=np.float64)
            n_oos = len(yo)
            p_sel = best.transform(so)
            p_id = id_cal.transform(so)
            metrics_oos = full_metrics(yo, p_sel)
            delta_metrics = {
                "delta_brier_oos": metrics_oos["brier"] - full_metrics(yo, p_id)["brier"],
                "delta_log_loss_oos": metrics_oos["log_loss"] - full_metrics(yo, p_id)["log_loss"],
                "delta_ece_oos": metrics_oos["ece"] - full_metrics(yo, p_id)["ece"],
            }
            uncertainty = paired_block_bootstrap_delta(yo, p_sel, p_id, seed=self.seed)

        art = self._build_artifact(
            best, raw_m, best_m, candidate_metrics, len(sf), len(ss), n_oos,
            feature_schema_hash, dataset_hash, model_identity_hash, split_meta or {},
            status=status, notes=notes, metrics_oos=metrics_oos,
            delta_metrics=delta_metrics, uncertainty=uncertainty,
        )
        return best, art

    @staticmethod
    def _better(m: Dict[str, float], best: Dict[str, float]) -> bool:
        if m["brier"] < best["brier"] - 1e-12:
            return True
        if abs(m["brier"] - best["brier"]) < 1e-12 and m["log_loss"] < best["log_loss"] - 1e-12:
            return True
        if (
            abs(m["brier"] - best["brier"]) < 1e-12
            and abs(m["log_loss"] - best["log_loss"]) < 1e-12
            and m["ece"] < best["ece"] - 1e-12
        ):
            return True
        return False

    def _build_artifact(
        self,
        cal: BaseCalibrator,
        raw_m: Dict[str, float],
        sel_m: Dict[str, float],
        cand: Dict[str, Dict[str, float]],
        n_fit: int,
        n_sel: int,
        n_oos: int,
        feature_schema_hash: str,
        dataset_hash: str,
        model_identity_hash: str,
        split_meta: Dict[str, Any],
        status: str,
        notes: List[str],
        metrics_oos: Optional[Dict[str, float]] = None,
        delta_metrics: Optional[Dict[str, float]] = None,
        uncertainty: Optional[Dict[str, float]] = None,
    ) -> CalibrationArtifact:
        params = cal.params_dict() if hasattr(cal, "params_dict") else {"name": cal.name}
        art = CalibrationArtifact(
            artifact_version="1",
            calibrator_type=cal.name,
            calibrator_parameters=params,
            fitted_at=time.time(),
            n_fit=n_fit,
            n_select=n_sel,
            n_oos=n_oos,
            dataset_hash=dataset_hash,
            feature_schema_hash=feature_schema_hash,
            model_identity_hash=model_identity_hash,
            calibration_code_version=CALIBRATION_CODE_VERSION,
            split_meta=split_meta,
            metrics_raw=raw_m,
            metrics_selected=sel_m,
            metrics_oos=metrics_oos or {},
            candidate_metrics=cand,
            delta_metrics=delta_metrics or {},
            uncertainty=uncertainty or {},
            calibration_status=status,
            notes=notes,
            seed=self.seed,
        )
        return art.seal()
