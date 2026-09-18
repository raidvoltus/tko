# Risk Engine (Absolute Authority)

References informing design (not copied code):
- riskkit (drawdown ladder, concentration, session limits)
- robson (monthly/daily budget, circuit breaker, audit events)
- enterprise-crypto (kill switch, reduce-only, fail-closed hierarchy)
- industry practice: HWM drawdown, order-rate limits, NaN rejection

## Hierarchy

```
Strategy / ML / Governor  →  propose only
        ↓
   RiskEngine.check()     →  ALLOW / DENY (absolute)
        ↓
   ExecutionManager       →  RestClient only if ALLOW
```

## Modes

| Mode | Meaning |
|------|---------|
| NORMAL | Full limits apply |
| WARNING | size_multiplier advisory 0.5 |
| REDUCE_ONLY | only reduce_only_intent orders |
| HALT | no new orders (kill / breaker / max DD) |

## Hard blocks (examples)

KILL_SWITCH, CIRCUIT_BREAKER, MARKET_STALE, WS_DISCONNECTED,
ORDERBOOK_DESYNC, CLOCK_DRIFT, INVALID/ZERO/NEGATIVE_NOTIONAL,
MAX_ORDER_VALUE, MAX_POSITION, MAX_EXPOSURE, MAX_SYMBOL_CONCENTRATION,
MAX_DAILY_LOSS, MAX_CONSECUTIVE_LOSSES, MAX_DRAWDOWN, REDUCE_ONLY_MODE,
MAX_ORDERS_PER_DAY, MAX_ORDERS_PER_MINUTE, LOSS_COOLDOWN, VOLATILITY_HALT

## Fail-closed

Any invalid data state → deny. No silent pass on NaN/Inf.
