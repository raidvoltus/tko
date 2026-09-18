# PIT Dataset + Walk-Forward Scorecard

## Purpose

Empirical evaluation layer **after** Risk Engine hardening:

```
PIT bars (no lookahead features)
  → walk-forward splits (train | purge | valid | test)
  → StrategyEngine signals on test folds only
  → cost-adjusted PnL series
  → Scorecard (+ block-bootstrap CI)
  → PromotionGate (still requires operator approval)
```

## PIT rules

| At time t | Allowed |
|-----------|---------|
| Features | only data ≤ t |
| Labels `fwd_ret_*` | stored for training/analysis; **not** fed as features at t |
| Last H rows | `fwd_ret_H` must be NaN |

Schema hash: `src.evaluation.pit_dataset.schema_hash()`.

## Runner

`WalkForwardRunner.run(ds, train_size, valid_size, test_size, purge_size)`

- Does **not** call RiskEngine or ExecutionManager
- Cost model explicit; `calibrated=False` until real calibration exists
- Aggregate scorecard = concat of test-fold PnLs

## Champion vs Challenger

```python
from src.evaluation import WalkForwardRunner, compare_champion_challenger
from src.champion.promotion import PromotionGate

rep_c = WalkForwardRunner(...).run(ds)
rep_x = WalkForwardRunner(entry_threshold=...).run(ds)  # challenger config
cards = compare_champion_challenger(rep_c, rep_x)
PromotionGate().evaluate(cards["champion"], cards["challenger"], cost_adjusted=True)
```

Default gate outcome without operator: **KEEP_CHAMPION** / **INSUFFICIENT_EVIDENCE**.

## Not claimed

- Strategy profitability
- Calibrated expected returns
- Automatic promotion
