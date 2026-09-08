# TKO Architecture — State Authority & Lifecycle

## Single source of truth (SSOT)

LIVE trading must not treat independent local stores as equal authorities.
Authority hierarchy:

| Domain | Authority | Local role |
|--------|-----------|------------|
| Balances & open orders | **Tokocrypto exchange** | Snapshot only; never invent |
| Order lifecycle (submit → fill) | **IntentStore** (`state/order_intents.json`) | Client order id, status machine |
| Position qty / entry | **PositionStore** (`state/positions.json`) | Cache of fills; must reconcile to exchange free balances |
| Realized PnL / daily notional | **PnL ledger** (`state/pnl_ledger.jsonl`) | Append-only accounting of *confirmed* fills |
| Kill / halt | **Kill file** (`state/KILL`) | Operational control; checked every loop |
| Instance exclusivity | **Lock** (`state/tko.lock`) | One live process |

### Rules

1. **Exchange wins on mismatch.** Reconciliation compares PositionStore to
   `fetch_balance()`. Missing/extra local positions are adjusted or flagged;
   they are never treated as more true than the exchange.
2. **Intents never invent fills.** UNKNOWN / RECONCILIATION states only become
   CONFIRMED after exchange lookup by `clientOrderId`. After repeated misses →
   `MANUAL_REVIEW` (no blind retry).
3. **PnL only from confirmed fills.** Risk gates and daily loss use the ledger;
   do not recompute PnL from ticker alone.
4. **Risk gates before submit.** Hard caps and kill switch run in RiskEngine /
   ExecutionEngine *before* `create_order`.
5. **Audit is observational.** `state/audit_log.jsonl` records decisions; it is
   not a recovery source of truth.

```
submit path:
  Risk.approve → Intent.persist(clientOrderId) → Exchange.create_order
       │                    │                         │
       │                    ▼                         ▼
       │              IntentStore              fill / timeout
       │                    │                         │
       └─────────────────────┴──── reconciliation ──────┘
                                    │
                    PositionStore ←──│
                    PnL ledger    ←──┘
```

## Startup paths (canonical)

Both resolve to the same function — no divergent lifecycle:

- `python -m tko` → `tko.__main__` → `entrypoint.main`
- console script `tko` → `entrypoint.main`

## Module boundaries

```
CLI / entrypoint
  → Config + Credentials (keyring, fail-closed)
  → Runtime (lock, heartbeat, metrics)
  → Risk → Execution → Exchange (CCXT Tokocrypto)
  → Reconciliation → PositionStore + PnL
  → Notify (Telegram) + Audit
```

Strategy does not call the exchange directly. Execution does not write PnL
except through RiskEngine.record_fill after confirmed fills.
