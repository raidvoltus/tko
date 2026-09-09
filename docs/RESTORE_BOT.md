# Restore `src/tko/runtime/bot.py` (Stage 5 ML wiring)

Remote `bot.py` was corrupted during a connector push (placeholder / truncated). Foundation modules, training script, and baseline docs are already on `main`.

## Canonical file

Use the project artifact:

- `bot_stage5_ml_wired.py`
- SHA256: `ffd9eca4ded411ab920c24011160e6f9ebef2cbb95343437d08b99dcdc3ff8c0`

Copy its full contents over `src/tko/runtime/bot.py`, then:

```bash
git add src/tko/runtime/bot.py
git commit -m "Stage 5: restore full bot.py with ML filter + OHLCV store wiring"
git push origin main
PYTHONPATH=src python -m pytest tests/ -q
```

## Expected markers after restore

- `from tko.ml.filter import MlSignalFilter`
- `from tko.ml.ohlcv_store import OhlcvStore`
- `decision_td = self.strategy.analyze(ohlcv)` (no second `last` arg)
- `self.s.ohlcv_timeframe` / `self.s.ohlcv_limit`
- No `PLACEHOLDER` / `SEE_LOCAL_FILE`

## Already on remote

- `src/tko/ml/*`
- `scripts/train_ml_filter.py`
- `docs/BASELINE_METRICS.md`
- `docs/ARCHITECTURE.md` Stage 5 section
- Settings: `ml_filter_enabled=False` (default OFF)
