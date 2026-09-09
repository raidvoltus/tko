# Baseline metrics — rule-based `BtcAnalyzer` (Stage 5 reference)

ML filter may be enabled **only** after an offline model clearly beats this baseline on held-out / walk-forward metrics. Default remains `TKO_ML_FILTER_ENABLED=false`.

## What is the baseline?

- Strategy: `tko.strategy.btc.BtcAnalyzer` (RSI + EMA cross / oversold rules).
- Harness: `tko.ml.backtest.run_rule_baseline` — long-only, enter on BUY, exit on SELL or horizon, fee 10 bps round-trip default.
- Features/labels for ML are separate and causal; they must not leak future bars into training.

## How to measure

```bash
# Synthetic smoke (no live data required)
PYTHONPATH=src python scripts/train_ml_filter.py --synthetic --out-dir /tmp/tko_ml_demo

# After bot has appended candles under state/ohlcv/
PYTHONPATH=src python scripts/train_ml_filter.py \
  --ohlcv-dir state/ohlcv \
  --symbol BTC/IDR \
  --timeframe 15m \
  --kind logreg \
  --out-dir state/ml
```

The script writes:

| Artifact | Purpose |
|----------|---------|
| `*.joblib` | Trained classifier (joblib) |
| `*_report.json` | Walk-forward folds + **baseline_rule** metrics |

`baseline_rule` fields (from `BacktestMetrics`):

| Field | Meaning |
|-------|---------|
| `n_bars` / `n_trades` | Sample size |
| `net_return` | Equity − 1 after fees |
| `max_drawdown` | Peak-to-trough |
| `sharpe` | Mean trade return / std (trade-level, not annualized) |
| `win_rate` | Wins / trades |
| `profit_factor` | Gross profit / gross loss |
| `exposure` | Fraction of bars in position |

## Promotion gate (manual)

Do **not** set `TKO_ML_FILTER_ENABLED=true` until:

1. Walk-forward mean accuracy is stable across folds (not a single lucky fold).
2. A separate evaluation shows the **filtered** rule strategy improves vs unfiltered baseline on at least:
   - higher Sharpe (suggested margin: ≥ +10% relative), **and**
   - lower or equal max drawdown,
   - profit factor not worse.
3. Fail-closed behavior verified: missing model / sklearn → BUY blocked, SELL still allowed.
4. RiskEngine + LifecycleGovernor still gate every live order.

Suggested env when promoting a proven model:

```text
TKO_ML_FILTER_ENABLED=true
TKO_ML_MIN_CONFIDENCE=0.55
TKO_ML_MODEL_PATH=state/ml/BTC_IDR_15m_logreg.joblib
```

Record the baseline numbers from `*_report.json` → `baseline_rule` in this doc or your ops notes for the symbol/timeframe you trade. Re-run baseline after any change to `BtcAnalyzer` parameters.

## Invariants (do not relax)

1. Rule-based signal remains primary; ML only filters BUY.
2. ML never calls `create_order` and never overrides risk/lifecycle.
3. Features use past + current bars only.
4. No random train/test split for time series — walk-forward only.
5. Live default OFF.
