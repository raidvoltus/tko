#!/usr/bin/env python3
"""Offline ML training runner — never places live orders."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

def main() -> int:
    p = argparse.ArgumentParser(description="TKO ML autonomous training (offline)")
    p.add_argument("--data-dir", type=Path, required=True)
    p.add_argument("--symbol", default="BTC/IDR")
    p.add_argument("--timeframe", default="15m")
    p.add_argument("--out", type=Path, default=Path("artifacts/ml"))
    p.add_argument("--registry", type=Path, default=Path("state/ml_registry"))
    args = p.parse_args()
    from tko.ml.ohlcv_store import OhlcvStore
    from tko.ml.train_pipeline import TrainConfig, run_training
    store = OhlcvStore(args.data_dir)
    candles = store.load(args.symbol, args.timeframe)
    if len(candles) < 200:
        print(json.dumps({"error": "insufficient_candles", "n": len(candles)}))
        return 2
    report = run_training(
        candles, registry_dir=args.registry, artifact_root=args.out,
        config=TrainConfig(timeframe=args.timeframe),
    )
    print(json.dumps(report.to_dict(), indent=2))
    return 0

if __name__ == "__main__":
    sys.exit(main())
