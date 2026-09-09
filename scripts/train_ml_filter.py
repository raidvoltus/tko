#!/usr/bin/env python3
"""Offline ML filter training for TKO Stage 5.

Additive only: trains a classifier that may later gate rule-based BUY signals.
Never enables live filter. Promotion requires beating the rule baseline OOS.

Usage (from repo root, after pip install -e '.[ml]'):

  PYTHONPATH=src python scripts/train_ml_filter.py \\
    --ohlcv-dir state/ohlcv \\
    --symbol BTC_IDR \\
    --timeframe 15m \\
    --kind logreg \\
    --out-dir state/ml

  # or synthetic demo when no store yet:
  PYTHONPATH=src python scripts/train_ml_filter.py --synthetic --out-dir /tmp/tko_ml_demo
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running without install
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from tko.core.config import Settings
from tko.core.types import OHLCV
from tko.ml.backtest import run_rule_baseline
from tko.ml.features import build_feature_matrix, rows_to_xy
from tko.ml.labels import LabelConfig, label_forward_direction
from tko.ml.models import make_classifier, save_model, sklearn_available
from tko.ml.ohlcv_store import OhlcvStore
from tko.ml.walk_forward import walk_forward_classify

logger = logging.getLogger("train_ml_filter")


def _synth_candles(n: int = 600, start_px: float = 1_000_000.0) -> list[OHLCV]:
    """Deterministic synthetic series for smoke / CI without live data."""
    out: list[OHLCV] = []
    px = start_px
    ts = 1_700_000_000_000
    for i in range(n):
        px = px * (1.0 + 0.0015 * math.sin(i / 11.0) + 0.0004 * ((i % 7) - 3))
        out.append(
            OHLCV(
                timestamp_ms=ts + i * 900_000,
                open=px,
                high=px * 1.003,
                low=px * 0.997,
                close=px,
                volume=800.0 + (i % 13) * 40,
            )
        )
    return out


def _load_candles(ohlcv_dir: Path, symbol: str, timeframe: str) -> list[OHLCV]:
    store = OhlcvStore(ohlcv_dir)
    candidates = [symbol, symbol.replace("_", "/"), symbol.replace("/", "_")]
    for sym in candidates:
        candles = store.load(sym if "/" in sym else sym.replace("_", "/"), timeframe)
        if not candles:
            candles = store.load(sym, timeframe)
        if candles:
            logger.info("loaded %s bars for %s %s", len(candles), sym, timeframe)
            return candles
    return []


def train(
    *,
    candles: list[OHLCV],
    kind: str,
    out_dir: Path,
    symbol: str,
    timeframe: str,
    train_bars: int,
    test_bars: int,
    step_bars: int,
    horizon: int,
    up_th: float,
    down_th: float,
) -> dict:
    if not sklearn_available():
        raise SystemExit(
            "scikit-learn required. Install: pip install 'tko[ml]' "
            "or pip install scikit-learn pandas numpy joblib"
        )

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    settings = Settings(live_mode=True, min_quote_balance=1)
    baseline = run_rule_baseline(candles, settings, horizon_bars=horizon, fee_bps=10.0)
    base_m = baseline.metrics.to_dict()

    rows = build_feature_matrix(candles)
    labels = label_forward_direction(
        candles,
        rows,
        LabelConfig(horizon_bars=horizon, up_threshold=up_th, down_threshold=down_th),
    )
    x, y = rows_to_xy(rows, labels)
    n_dir = sum(1 for yi in y if yi in (-1, 1))
    logger.info(
        "features=%s labels_dir=%s total_xy=%s baseline_net=%.4f sharpe=%.3f",
        len(rows),
        n_dir,
        len(y),
        base_m["net_return"],
        base_m["sharpe"],
    )

    if n_dir < train_bars + test_bars:
        raise SystemExit(
            f"not enough directional labels ({n_dir}); need >= {train_bars + test_bars}. "
            "Collect more OHLCV or lower thresholds / train_bars."
        )

    wf = walk_forward_classify(
        x,
        y,
        kind=kind,
        train_bars=train_bars,
        test_bars=test_bars,
        step_bars=step_bars,
    )

    pairs = [(xi, yi) for xi, yi in zip(x, y) if yi in (-1, 1)]
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    clf = make_classifier(kind)
    clf.fit(xs, ys)

    model_name = f"{symbol.replace('/', '_')}_{timeframe}_{kind}.joblib"
    model_path = out_dir / model_name
    save_model(clf, model_path)

    report = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "timeframe": timeframe,
        "kind": kind,
        "n_candles": len(candles),
        "n_features_rows": len(rows),
        "n_directional_labels": n_dir,
        "label_config": {
            "horizon_bars": horizon,
            "up_threshold": up_th,
            "down_threshold": down_th,
        },
        "walk_forward": wf.to_dict(),
        "baseline_rule": base_m,
        "model_path": str(model_path),
        "promotion_gate": {
            "ml_filter_enabled_default": False,
            "require_oos_better_than_baseline": True,
            "notes": (
                "Do NOT set TKO_ML_FILTER_ENABLED=true until walk-forward mean accuracy "
                "and a separate held-out backtest beat baseline Sharpe / max DD / profit factor "
                "with a clear margin you define (e.g. Sharpe +10% and lower drawdown)."
            ),
        },
    }
    report_path = out_dir / f"{symbol.replace('/', '_')}_{timeframe}_{kind}_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    logger.info("wrote model=%s report=%s wf_mean_acc=%.3f", model_path, report_path, wf.mean_accuracy)
    return report


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    p = argparse.ArgumentParser(description="Train optional TKO ML signal filter (offline)")
    p.add_argument("--ohlcv-dir", type=Path, default=Path("state/ohlcv"))
    p.add_argument("--symbol", default="BTC/IDR", help="e.g. BTC/IDR or BTC_IDR")
    p.add_argument("--timeframe", default="15m")
    p.add_argument("--kind", default="logreg", choices=("logreg", "rf", "gb", "gbm"))
    p.add_argument("--out-dir", type=Path, default=Path("state/ml"))
    p.add_argument("--train-bars", type=int, default=200)
    p.add_argument("--test-bars", type=int, default=40)
    p.add_argument("--step-bars", type=int, default=40)
    p.add_argument("--horizon", type=int, default=4)
    p.add_argument("--up-threshold", type=float, default=0.002)
    p.add_argument("--down-threshold", type=float, default=-0.002)
    p.add_argument(
        "--synthetic",
        action="store_true",
        help="Use synthetic candles (demo/CI) instead of OhlcvStore",
    )
    args = p.parse_args()

    if args.synthetic:
        candles = _synth_candles(600)
        symbol = args.symbol
    else:
        candles = _load_candles(args.ohlcv_dir, args.symbol, args.timeframe)
        if not candles:
            raise SystemExit(
                f"no OHLCV at {args.ohlcv_dir} for {args.symbol} {args.timeframe}. "
                "Run the bot with ohlcv_store_enabled=true first, or pass --synthetic."
            )
        symbol = args.symbol

    report = train(
        candles=candles,
        kind=args.kind,
        out_dir=args.out_dir,
        symbol=symbol,
        timeframe=args.timeframe,
        train_bars=args.train_bars,
        test_bars=args.test_bars,
        step_bars=args.step_bars,
        horizon=args.horizon,
        up_th=args.up_threshold,
        down_th=args.down_threshold,
    )
    print(json.dumps({"ok": True, "model_path": report["model_path"], "wf_mean_accuracy": report["walk_forward"]["mean_accuracy"], "baseline_sharpe": report["baseline_rule"]["sharpe"]}, indent=2))


if __name__ == "__main__":
    main()
