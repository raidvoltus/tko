"""
Scientific calibration framework for TKO trading probabilities.

Methodology (not copied code):
- Platt / logistic scaling (Platt 2000; Fonseca & Lopes 2017 PD)
- Isotonic / monotone bin map (Niculescu-Mizil & Caruana)
- Beta calibration (Kull, Silva Filho & Flach 2017)
- Temperature scaling (Guo et al. 2017 ICML) for logit inputs
- Metrics: Brier, log-loss, ECE, MCE, calibration slope/intercept
- Selection: candidate calibrators → time-ordered validation → pick by proper scores
  (NOT automatic n→method rule)

Calibrator never places orders. RiskEngine remains absolute authority.
Calibrated=True only after successful fit + selection on held-out folds.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def brier_score(y_true: Sequence[float], p_pred: Sequence[float]) -> float:
    y = np.asarray(y_true, dtype=np.float64)
    p = np.clip(np.asarray(p_pred, dtype=np.float64), 0.0, 1.0)
    if y.size == 0:
        return 1.0
    return float(np.mean((p - y) ** 2))


def log_loss(y_true: Sequence[float], p_pred: Sequence[float], eps: float = 1e-7) -> float:
    y = np.asarray(y_true, dtype=np.float64)
    p = np.clip(np.asarray(p_pred, dtype=np.float64), eps, 1.0 - eps)
    if y.size == 0:
        return 10.0
    return float(-np.mean(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))


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


def maximum_calibration_error(
    y_true: Sequence[float],
    p_pred: Sequence[float],
    n_bins: int = 10,
) -> float:
    y = np.asarray(y_true, dtype=np.float64)
    p = np.clip(np.asarray(p_pred, dtype=np.float64), 0.0, 1.0)
    if y.size == 0:
        return 1.0
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    mce = 0.0
    for i in range(n_bins):
        lo, hi = bins[i], bins[i + 1]
        mask = (p >= lo) & (p < hi if i < n_bins - 1 else p <= hi)
        if not np.any(mask):
            continue
        mce = max(mce, abs(float(p[mask].mean()) - float(y[mask].mean())))
    return float(mce)


def calibration_slope_intercept(
    y_true: Sequence[float],
    p_pred: Sequence[float],
) -> Tuple[float, float]:
    """
    Linear regression y ~ a + b * p.
    Perfect calibration ≈ intercept 0, slope 1.
    """
    y = np.asarray(y_true, dtype=np.float64)
    p = np.clip(np.asarray(p_pred, dtype=np.float64), 0.0, 1.0)
    if y.size < 5:
        return 0.0, 1.0
    p_mean = float(p.mean())
    y_mean = float(y.mean())
    var = float(np.sum((p - p_mean) ** 2))
    if var < 1e-12:
        return y_mean, 0.0
    slope = float(np.sum((p - p_mean) * (y - y_mean)) / var)
    intercept = y_mean - slope * p_mean
    return intercept, slope


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


def full_metrics(y_true: Sequence[float], p_pred: Sequence[float]) -> Dict[str, float]:
    inter, slope = calibration_slope_intercept(y_true, p_pred)
    return {
        "brier": brier_score(y_true, p_pred),
        "log_loss": log_loss(y_true, p_pred),
        "ece": expected_calibration_error(y_true, p_pred),
        "mce": maximum_calibration_error(y_true, p_pred),
        "intercept": inter,
        "slope": slope,
        "n": float(len(y_true)),
    }


# ---------------------------------------------------------------------------
# Calibrators
# ---------------------------------------------------------------------------


class BaseCalibrator:
    name: str = "base"
    fitted: bool = False

    def fit(self, scores: Sequence[float], y: Sequence[float]) -> "BaseCalibrator":
        raise NotImplementedError

    def transform(self, scores: Sequence[float]) -> np.ndarray:
        raise NotImplementedError

    def params_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "fitted": self.fitted}


@dataclass
class IdentityCalibrator(BaseCalibrator):
    """Baseline: clip scores already in [0,1]; sigmoid if unbounded."""

    name: str = "identity"
    fitted: bool = True  # always "ready"

    def fit(self, scores: Sequence[float], y: Sequence[float]) -> "IdentityCalibrator":
        self.fitted = True
        return self

    def transform(self, scores: Sequence[float]) -> np.ndarray:
        s = np.asarray(scores, dtype=np.float64)
        if s.size and float(np.nanmin(s)) >= 0 and float(np.nanmax(s)) <= 1:
            return np.clip(s, 0.0, 1.0)
        return 1.0 / (1.0 + np.exp(-np.clip(s, -30, 30)))


@dataclass
class PlattCalibrator(BaseCalibrator):
    """p = sigmoid(a * s + b). Prefer when miscalibration roughly log-linear."""

    name: str = "platt"
    a: float = -1.0
    b: float = 0.0
    fitted: bool = False
    n_fit: int = 0

    def fit(self, scores: Sequence[float], y: Sequence[float], max_iter: int = 50) -> "PlattCalibrator":
        s = np.asarray(scores, dtype=np.float64)
        yt = np.asarray(y, dtype=np.float64)
        if s.size < 20:
            self.fitted = False
            return self
        a, b = -1.0, 0.0
        for _ in range(max_iter):
            z = a * s + b
            p = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
            p = np.clip(p, 1e-6, 1 - 1e-6)
            err = p - yt
            w = p * (1 - p)
            ga = float(np.sum(err * s))
            gb = float(np.sum(err))
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
        return self

    def transform(self, scores: Sequence[float]) -> np.ndarray:
        s = np.asarray(scores, dtype=np.float64)
        if not self.fitted:
            return IdentityCalibrator().transform(s)
        z = self.a * s + self.b
        return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))

    def params_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "fitted": self.fitted, "a": self.a, "b": self.b, "n_fit": self.n_fit}


@dataclass
class IsotonicCalibrator(BaseCalibrator):
    """Monotone quantile-bin map + non-decreasing projection."""

    name: str = "isotonic"
    x_thresholds: np.ndarray = field(default_factory=lambda: np.array([0.0, 1.0]))
    y_values: np.ndarray = field(default_factory=lambda: np.array([0.0, 1.0]))
    fitted: bool = False
    n_fit: int = 0

    def fit(self, scores: Sequence[float], y: Sequence[float]) -> "IsotonicCalibrator":
        s = np.asarray(scores, dtype=np.float64)
        yt = np.asarray(y, dtype=np.float64)
        if s.size < 30:
            self.fitted = False
            return self
        n_bins = int(min(20, max(5, s.size // 25)))
        qs = np.linspace(0, 1, n_bins + 1)
        edges = np.unique(np.quantile(s, qs))
        if len(edges) < 3:
            edges = np.linspace(float(s.min()), float(s.max()) + 1e-9, 5)
        centers, means = [], []
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
        m = np.asarray(means, dtype=np.float64)
        for i in range(1, len(m)):
            if m[i] < m[i - 1]:
                m[i] = m[i - 1]
        self.x_thresholds = np.asarray(centers, dtype=np.float64)
        self.y_values = np.clip(m, 0.0, 1.0)
        self.fitted = True
        self.n_fit = int(s.size)
        return self

    def transform(self, scores: Sequence[float]) -> np.ndarray:
        s = np.asarray(scores, dtype=np.float64)
        if not self.fitted:
            return IdentityCalibrator().transform(s)
        idx = np.searchsorted(self.x_thresholds, s, side="right") - 1
        idx = np.clip(idx, 0, len(self.y_values) - 1)
        return self.y_values[idx]

    def params_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "fitted": self.fitted,
            "n_fit": self.n_fit,
            "n_knots": int(len(self.x_thresholds)),
        }


@dataclass
class BetaCalibrator(BaseCalibrator):
    """
    Beta calibration (Kull et al.): logit(p') = a * log(p) + b * log(1-p) + c
    Flexible; can recover identity when a=b=1, c=0.
    """

    name: str = "beta"
    a: float = 1.0
    b: float = 1.0
    c: float = 0.0
    fitted: bool = False
    n_fit: int = 0

    def fit(self, scores: Sequence[float], y: Sequence[float], max_iter: int = 80) -> "BetaCalibrator":
        s = np.asarray(scores, dtype=np.float64)
        yt = np.asarray(y, dtype=np.float64)
        if s.size < 30:
            self.fitted = False
            return self
        # map unbounded scores to (0,1) first
        if not (float(np.nanmin(s)) >= 0 and float(np.nanmax(s)) <= 1):
            s = 1.0 / (1.0 + np.exp(-np.clip(s, -30, 30)))
        s = np.clip(s, 1e-4, 1.0 - 1e-4)
        lp = np.log(s)
        lq = np.log(1.0 - s)
        a, b, c = 1.0, 1.0, 0.0
        for _ in range(max_iter):
            z = a * lp + b * lq + c
            p = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
            p = np.clip(p, 1e-6, 1 - 1e-6)
            err = p - yt
            w = p * (1 - p)
            # gradients
            ga = float(np.sum(err * lp))
            gb = float(np.sum(err * lq))
            gc = float(np.sum(err))
            # approximate diagonal Hessian
            haa = float(np.sum(w * lp * lp)) + 1e-6
            hbb = float(np.sum(w * lq * lq)) + 1e-6
            hcc = float(np.sum(w)) + 1e-6
            da = ga / haa
            db = gb / hbb
            dc = gc / hcc
            a -= da
            b -= db
            c -= dc
            if abs(da) + abs(db) + abs(dc) < 1e-8:
                break
        self.a, self.b, self.c = float(a), float(b), float(c)
        self.fitted = True
        self.n_fit = int(s.size)
        return self

    def transform(self, scores: Sequence[float]) -> np.ndarray:
        s = np.asarray(scores, dtype=np.float64)
        if not self.fitted:
            return IdentityCalibrator().transform(s)
        if not (float(np.nanmin(s)) >= 0 and float(np.nanmax(s)) <= 1):
            s = 1.0 / (1.0 + np.exp(-np.clip(s, -30, 30)))
        s = np.clip(s, 1e-4, 1.0 - 1e-4)
        z = self.a * np.log(s) + self.b * np.log(1.0 - s) + self.c
        return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))

    def params_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "fitted": self.fitted,
            "a": self.a,
            "b": self.b,
            "c": self.c,
            "n_fit": self.n_fit,
        }


@dataclass
class TemperatureCalibrator(BaseCalibrator):
    """p = sigmoid(logit(s) / T). Guo et al. temperature scaling (binary)."""

    name: str = "temperature"
    T: float = 1.0
    fitted: bool = False
    n_fit: int = 0

    def fit(self, scores: Sequence[float], y: Sequence[float]) -> "TemperatureCalibrator":
        s = np.asarray(scores, dtype=np.float64)
        yt = np.asarray(y, dtype=np.float64)
        if s.size < 20:
            self.fitted = False
            return self
        if float(np.nanmin(s)) >= 0 and float(np.nanmax(s)) <= 1:
            s = np.clip(s, 1e-6, 1 - 1e-6)
            logits = np.log(s / (1 - s))
        else:
            logits = s
        best_T, best_ll = 1.0, 1e9
        for T in np.linspace(0.5, 5.0, 46):
            p = 1.0 / (1.0 + np.exp(-np.clip(logits / T, -30, 30)))
            ll = log_loss(yt, p)
            if ll < best_ll:
                best_ll, best_T = ll, float(T)
        self.T = best_T
        self.fitted = True
        self.n_fit = int(s.size)
        return self

    def transform(self, scores: Sequence[float]) -> np.ndarray:
        s = np.asarray(scores, dtype=np.float64)
        if not self.fitted:
            return IdentityCalibrator().transform(s)
        if float(np.nanmin(s)) >= 0 and float(np.nanmax(s)) <= 1:
            s = np.clip(s, 1e-6, 1 - 1e-6)
            logits = np.log(s / (1 - s))
        else:
            logits = s
        return 1.0 / (1.0 + np.exp(-np.clip(logits / self.T, -30, 30)))

    def params_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "fitted": self.fitted, "T": self.T, "n_fit": self.n_fit}


@dataclass
class EdgeCalibrator:
    """composite [-1,1] → empirical mean net PnL % by bin. Fit only on held-out."""

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
            return float(composite) * 2.5 * 0.7
        x = float(np.clip(composite, -1.0, 1.0))
        idx = int(np.searchsorted(self.bin_edges, x, side="right") - 1)
        idx = int(np.clip(idx, 0, len(self.bin_means) - 1))
        return float(self.bin_means[idx])


# ---------------------------------------------------------------------------
# Selection + artifact
# ---------------------------------------------------------------------------


def _make_candidates() -> List[BaseCalibrator]:
    return [
        IdentityCalibrator(),
        PlattCalibrator(),
        IsotonicCalibrator(),
        BetaCalibrator(),
        TemperatureCalibrator(),
    ]


@dataclass
class CalibrationArtifact:
    """Versioned calibrator selection result — evidence only, no order authority."""

    version: str
    selected: str
    fitted_at: float
    n_fit: int
    n_select: int
    metrics_raw: Dict[str, float]
    metrics_selected: Dict[str, float]
    candidate_metrics: Dict[str, Dict[str, float]]
    params: Dict[str, Any]
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def is_calibrated(self) -> bool:
        return self.selected != "identity" and self.n_fit > 0


@dataclass
class CalibratorSelector:
    """
    Fit candidates on calibration set; score on selection set; pick best.

    Primary ranking: lower Brier, then lower log_loss, then lower ECE.
    Requires meaningful improvement over identity (min_brier_improve).
    """

    min_brier_improve: float = 0.002
    min_n: int = 40

    def select(
        self,
        scores_fit: Sequence[float],
        y_fit: Sequence[float],
        scores_sel: Sequence[float],
        y_sel: Sequence[float],
    ) -> Tuple[BaseCalibrator, CalibrationArtifact]:
        yf = np.asarray(y_fit, dtype=np.float64)
        ys = np.asarray(y_sel, dtype=np.float64)
        sf = np.asarray(scores_fit, dtype=np.float64)
        ss = np.asarray(scores_sel, dtype=np.float64)

        raw_m = full_metrics(ys, IdentityCalibrator().transform(ss))
        candidate_metrics: Dict[str, Dict[str, float]] = {"identity": raw_m}

        best: BaseCalibrator = IdentityCalibrator()
        best_m = raw_m
        best_name = "identity"

        if len(sf) < self.min_n or len(ss) < max(20, self.min_n // 2):
            art = self._artifact(best, raw_m, raw_m, candidate_metrics, len(sf), len(ss),
                                 notes=["INSUFFICIENT_N_FOR_SELECTION"])
            return best, art

        for cal in _make_candidates():
            if cal.name == "identity":
                continue
            try:
                cal.fit(sf, yf)
            except Exception:
                continue
            if not getattr(cal, "fitted", False) and cal.name != "identity":
                continue
            p = cal.transform(ss)
            m = full_metrics(ys, p)
            candidate_metrics[cal.name] = m
            if self._better(m, best_m):
                best, best_m, best_name = cal, m, cal.name

        # require meaningful improvement vs identity
        if best_name != "identity":
            if raw_m["brier"] - best_m["brier"] < self.min_brier_improve:
                best = IdentityCalibrator()
                best_m = raw_m
                best_name = "identity"
                notes = ["NO_MEANINGFUL_IMPROVE_KEEP_IDENTITY"]
            else:
                notes = [f"SELECTED_{best_name}"]
        else:
            notes = ["KEEP_IDENTITY"]

        art = self._artifact(best, raw_m, best_m, candidate_metrics, len(sf), len(ss), notes=notes)
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

    def _artifact(
        self,
        cal: BaseCalibrator,
        raw_m: Dict[str, float],
        sel_m: Dict[str, float],
        cand: Dict[str, Dict[str, float]],
        n_fit: int,
        n_sel: int,
        notes: Optional[List[str]] = None,
    ) -> CalibrationArtifact:
        params = cal.params_dict() if hasattr(cal, "params_dict") else {"name": cal.name}
        blob = json.dumps({"params": params, "metrics": sel_m, "cand": list(cand.keys())}, sort_keys=True)
        ver = hashlib.sha256(blob.encode()).hexdigest()[:16]
        return CalibrationArtifact(
            version=ver,
            selected=cal.name,
            fitted_at=time.time(),
            n_fit=n_fit,
            n_select=n_sel,
            metrics_raw=raw_m,
            metrics_selected=sel_m,
            candidate_metrics=cand,
            params=params,
            notes=notes or [],
        )
