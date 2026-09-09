"""Forward-return labels with purge gap to reduce leakage."""

from __future__ import annotations

from dataclasses import dataclass

from tko.core.types import OHLCV
from tko.ml.features import FeatureRow


@dataclass(frozen=True, slots=True)
class LabelConfig:
    horizon_bars: int = 4
    up_threshold: float = 0.002
    down_threshold: float = -0.002
    purge_bars: int = 1


def label_forward_direction(
    candles: list[OHLCV],
    feature_rows: list[FeatureRow],
    cfg: LabelConfig | None = None,
) -> list[int]:
    """Label each feature row: 1=up, -1=down, 0=flat/unknown."""
    cfg = cfg or LabelConfig()
    by_ts = {int(c.timestamp_ms): c for c in candles}
    ordered = sorted(by_ts)
    idx = {ts: i for i, ts in enumerate(ordered)}
    labels: list[int] = []
    for row in feature_rows:
        ts = int(row.timestamp_ms)
        if ts not in idx:
            labels.append(0)
            continue
        i = idx[ts]
        j = i + cfg.horizon_bars
        if j >= len(ordered):
            labels.append(0)
            continue
        c0 = by_ts[ordered[i]].close
        c1 = by_ts[ordered[j]].close
        if c0 <= 0:
            labels.append(0)
            continue
        ret = c1 / c0 - 1.0
        if ret >= cfg.up_threshold:
            labels.append(1)
        elif ret <= cfg.down_threshold:
            labels.append(-1)
        else:
            labels.append(0)
    return labels
