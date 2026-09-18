# Risk Engine — Absolute Authority (Production)

## Hierarchy

```
Strategy / ML / Challenger / Ensemble / Governor  → evidence / policy only
                ↓
         RiskEngine.admit()  → ALLOW + reservation | DENY
                ↓
         ExecutionManager (LIVE only for exchange)
                ↓
         RestClient.new_order → Tokocrypto
```

## Production execution

- **LIVE** = production trading path (only path that calls RestClient).
- **PAPER / SHADOW** = non-production test harness; still require Risk ALLOW; never production fallback.

## State health

| Health | Trading |
|--------|---------|
| UNRECONCILED / RECONCILING / INVALID / UNKNOWN | **NO TRADE** |
| VALID | subject to all hard gates |

Restart: default `UNRECONCILED` → must `apply_authoritative_snapshot()` after REST reconcile.

## Reduce-only (authoritative)

Caller `reduce_only_intent` is **ignored** for allow decisions.

Engine computes from position + side + notional:

- Long + SELL ≤ position → reducing
- Long + SELL > position → not fully reducing → DENY in REDUCE_ONLY
- BUY on long/flat → increasing
- Flat + SELL → `SPOT_SHORT_NOT_ALLOWED` (spot default)

## Admission / reservation

`admit()` under `RLock`: check + reserve rate slot + reservation_id.  
`commit_reservation` after exchange ACK.  
`release_reservation(safe=True)` if aborted before send.  
UNKNOWN after send → `mark_unknown_order` + release `safe=False` (capacity not restored).

## Hard blocks (non-exhaustive)

STATE_*, KILL_SWITCH, CIRCUIT_BREAKER, MARKET_STALE, WS_DISCONNECTED,
ORDERBOOK_DESYNC, CLOCK_DRIFT, INVALID_*, MAX_*, REDUCE_ONLY_MODE,
SPOT_SHORT_NOT_ALLOWED, LOSS_COOLDOWN, VOLATILITY_HALT

## Kill / breaker reset

Requires `operator_approved=True` and `state_health==VALID`.  
Does not clear active max-drawdown / daily-loss fundamental breaches.

## Session day

`session_tz_offset_hours` default **7** (WIB). Daily PnL / order counters roll on that day key.

## Advisory vs authoritative

| Item | Role |
|------|------|
| size_multiplier | advisory only |
| admit/check ALLOW | authoritative |
| is_risk_reducing | authoritative |
