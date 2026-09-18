# Champion–Challenger Architecture (Production)

## Hierarchy (authority never inverted)

```
DATA VALIDATION
  → FEATURES (v2 schema hash)
  → REGIME (hysteresis; UNKNOWN ⇒ NO_TRADE)
  → STRATEGIES (confluence scores — evidence only)
  → ML (model score — not calibrated “probability” unless proven)
  → ENSEMBLE (weighted fusion)
  → GOVERNOR (BUY/SELL/HOLD/WAIT/NO_TRADE policy)
  → RISK ENGINE  ← absolute order authority
  → EXECUTION ENGINE
  → EXCHANGE (Tokocrypto signed REST only)
```

## Champion

- Identity: `ChampionManifest` (immutable fields + `identity_hash`)
- Current: `tko-champion-v1` / feature `v2` / strategy `v1-confluence` / regime `v1-hysteresis`
- Reconstructible from manifest + `code_commit`
- **No “latest model” without hash**

## Challenger lifecycle

```
CANDIDATE → BACKTESTED → VALIDATED → SHADOW_EVALUATED → ELIGIBLE → PROMOTED
failure: REJECTED | DEGRADED | STALE | INVALID | ROLLED_BACK
```

Illegal: `CANDIDATE → PROMOTED`.

## Promotion gate (default conservative)

Requires: sample/trade/window floors, cost-adjusted edge, drawdown floor,
regime coverage, reproducibility, **unchanged risk limits**.

Default when uncertain: **KEEP_CHAMPION** / **INSUFFICIENT_EVIDENCE**.

## Expected edge

```
net = gross − fee − spread − slippage − impact − exec_risk − vol_risk
```

Strategy composite is **not** a calibrated expected return.

## Terminology

| Term | Meaning |
|------|---------|
| Strategy Score / Confluence Score | Bounded evidence [-1,1] |
| Model Score | Model output (not “99% probability” unless calibrated) |
| Expected Edge | Cost-adjusted net edge |

## Production execution policy

- Real exchange orders only via `ExecutionManager` → `RestClient.new_order` after **RiskEngine** approval.
- Strategy / ML / Challenger / Governor **cannot** place orders.
- CI never sends LIVE exchange orders.
- Fail-closed: UNKNOWN / STALE / INVALID / RISK BLOCK → **NO_TRADE**.

## Walk-forward

`generate_walk_forward_splits`: train → purge → validation → test (no random split).
