# Restore `src/tko/runtime/bot.py` (Stage 5)

## Canonical source (verified)

| Field | Value |
|-------|-------|
| Artifact | `bot_stage5_ml_wired.py` (project artifacts) |
| SHA256 | `ffd9eca4ded411ab920c24011160e6f9ebef2cbb95343437d08b99dcdc3ff8c0` |
| Size | 20364 bytes |
| Markers | `MlSignalFilter`, `OhlcvStore`, `analyze(ohlcv)`, `ohlcv_timeframe` |

## One-shot restore (your machine with git auth)

```bash
cd /path/to/tko

# 1) Verify hash of the artifact you downloaded/copied
sha256sum bot_stage5_ml_wired.py
# expect: ffd9eca4ded411ab920c24011160e6f9ebef2cbb95343437d08b99dcdc3ff8c0

# 2) Install
cp bot_stage5_ml_wired.py src/tko/runtime/bot.py

# 3) Sanity
grep -n "PLACEHOLDER\\|SEE_LOCAL_FILE" src/tko/runtime/bot.py && exit 1 || echo "no placeholder OK"
grep -n MlSignalFilter src/tko/runtime/bot.py
grep -n "analyze(ohlcv)" src/tko/runtime/bot.py

# 4) Commit + push
git add src/tko/runtime/bot.py
git commit -m "Stage 5: restore full bot.py with ML filter + OHLCV store wiring"
git push origin main

# 5) Tests
PYTHONPATH=src python -m pytest tests/ -q
```

## After restore — training (optional, offline)

```bash
pip install "tko[ml]"
PYTHONPATH=src python scripts/train_ml_filter.py --synthetic --out-dir /tmp/tko_ml_demo
# or real OHLCV after bot has collected candles:
PYTHONPATH=src python scripts/train_ml_filter.py --ohlcv-dir state/ohlcv --symbol BTC/IDR --timeframe 15m --out-dir state/ml
```

Keep `TKO_ML_FILTER_ENABLED=false` until OOS metrics beat baseline (see docs/BASELINE_METRICS.md).
