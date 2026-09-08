# TKO — Autonomous Tokocrypto LIVE Trading Bot

Bot trading **LIVE** khusus Tokocrypto untuk Windows 10+.

**Mode: LIVE only.** Tidak ada paper, demo, atau dry-run.

## Risk controls (P0)

- **Durable daily PnL** — `state/pnl_ledger.jsonl` (append-only). Loss harian > `max_daily_loss_pct` → kill switch + Telegram.
- **Position store** — `state/positions.json` (entry price, qty). Survive restart; recon vs saldo exchange.
- **Hard caps** — `max_order_notional`, `max_daily_notional` (env `TKO_*`).
- **Single-instance lock** — `state/tko.lock` (PID). Instance kedua ditolak.

Contoh `.env`:

```text
TKO_MAX_ORDER_NOTIONAL=7500000
TKO_MAX_DAILY_NOTIONAL=30000000
TKO_MAX_DAILY_LOSS_PCT=5
TKO_RISK_TIMEZONE=Asia/Jakarta
```

## CLI

```text
python -m tko setup
python -m tko run
python -m tko status
python -m tko stop
tko.exe install-service
tko.exe install-service --use-system --force
tko.exe uninstall-service
```

`install-service` default: akun user saat ini. `--use-system` opt-in (+ peringatan ACL).

## Execution safety

- recvWindow = 5000
- Client order ID persistent sebelum submit
- Timeout / 5XX → UNKNOWN → reconciliation
- Quantity SELL floor Decimal
- LOT_SIZE / MARKET_LOT_SIZE / minNotional
- 429 / 418 circuit breaker

## State authority (SSOT)

Exchange balances/orders are authoritative. Local stores are caches that must
reconcile:

| Store | Path | Role |
|-------|------|------|
| Intent | `state/order_intents.json` | Order lifecycle + clientOrderId |
| Positions | `state/positions.json` | Entry/qty cache → recon vs balance |
| PnL | `state/pnl_ledger.jsonl` | Confirmed fills only (append-only) |
| Lock | `state/tko.lock` | Single live instance |
| Kill | `state/KILL` | Halt trading |

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Reproducible install

```text
pip install -r requirements.lock
pip install -e ".[dev]"
```

CI installs from `requirements.lock` (exact pins). Prefer the lockfile over open ranges.
