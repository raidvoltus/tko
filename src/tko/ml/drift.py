"""Feature / prediction / regime drift monitors — fail-closed for candidates."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass
class DriftReport:
    feature_drift: bool
    prediction_drift: bool
    regime_drift: bool
    schema_mismatch: bool
    stale: bool
    ok: bool
    reasons: list[str]


def psi(expected: Sequence[float], actual: Sequence[float], *, n_bins: int = 10) -> float:
    """Population Stability Index (simple equal-width)."""
    if len(expected) < 5 or len(actual) < 5:
        return 0.0
    lo = min(min(expected), min(actual))
    hi = max(max(expected), max(actual))
    if hi <= lo:
        return 0.0
    width = (hi - lo) / n_bins
    def hist(xs: Sequence[float]) -> list[float]:
        counts = [0.0] * n_bins
        for x in xs:
            b = min(n_bins - 1, int((float(x) - lo) / width))
            counts[b] += 1.0
        total = sum(counts) or 1.0
        return [(c + 1e-6) / (total + n_bins * 1e-6) for c in counts]
    e = hist(expected)
    a = hist(actual)
    import math
    return sum((ai - ei) * math.log(ai / ei) for ei, ai in zip(e, a))


def evaluate_drift(
    *,
    ref_features: Sequence[Sequence[float]] | None = None,
    cur_features: Sequence[Sequence[float]] | None = None,
    ref_preds: Sequence[float] | None = None,
    cur_preds: Sequence[float] | None = None,
    ref_regime_dist: dict[str, float] | None = None,
    cur_regime_dist: dict[str, float] | None = None,
    feature_schema_ok: bool = True,
    market_fresh: bool = True,
    psi_threshold: float = 0.25,
    pred_shift_threshold: float = 0.15,
) -> DriftReport:
    reasons: list[str] = []
    feat_drift = pred_drift = reg_drift = False
    if not feature_schema_ok:
        reasons.append("schema_mismatch")
    if not market_fresh:
        reasons.append("stale_market")
    if ref_features and cur_features and ref_features[0] and cur_features[0]:
        # mean PSI across dims
        dims = min(len(ref_features[0]), len(cur_features[0]))
        scores = []
        for d in range(dims):
            scores.append(psi([r[d] for r in ref_features], [c[d] for c in cur_features]))
        if scores and (sum(scores) / len(scores)) > psi_threshold:
            feat_drift = True
            reasons.append("feature_psi")
    if ref_preds is not None and cur_preds is not None and ref_preds and cur_preds:
        rm = sum(ref_preds) / len(ref_preds)
        cm = sum(cur_preds) / len(cur_preds)
        if abs(cm - rm) > pred_shift_threshold:
            pred_drift = True
            reasons.append("prediction_shift")
    if ref_regime_dist and cur_regime_dist:
        keys = set(ref_regime_dist) | set(cur_regime_dist)
        shift = sum(abs(ref_regime_dist.get(k, 0) - cur_regime_dist.get(k, 0)) for k in keys)
        if shift > 0.5:
            reg_drift = True
            reasons.append("regime_dist_shift")
    ok = (
        feature_schema_ok
        and market_fresh
        and not feat_drift
        and not pred_drift
        and not reg_drift
    )
    return DriftReport(
        feature_drift=feat_drift,
        prediction_drift=pred_drift,
        regime_drift=reg_drift,
        schema_mismatch=not feature_schema_ok,
        stale=not market_fresh,
        ok=ok,
        reasons=reasons,
    )
