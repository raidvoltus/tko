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


## Hardening notes (post-4a72d3c)

### Champion identity
`identity_hash()` excludes `created_at` / `effective_from` so identity is stable and
reproducible from schema/config/model/commit hashes.

### Promotion is never automatic
`PromotionGate.evaluate(..., operator_approved=False)` may set `promotion_eligible=True`
but decision remains **KEEP_CHAMPION** until operator approval.

`ChampionRegistry.promote(..., operator_approved=True, approval_ref=...)` is required.

### Expected edge terminology
- **Cost-adjusted edge**: PASS (fee/spread/slip/impact/exec/vol deducted)
- **Calibrated expected return**: NOT YET (needs PIT historical calibration)

### Production vs non-production modes
- **LIVE**: production trading path
- **PAPER/SHADOW**: non-production (unit tests / offline); not production authority
- Challenger **shadow evaluation** = intelligence only, never execution authority

### Runtime authority boundary
`src/security/authority.py` — `assert_no_execution_capability` for Strategy/Governor/Ensemble/Registry.
