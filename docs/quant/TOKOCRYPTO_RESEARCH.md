# Tokocrypto research evaluation layer

Implements missing pieces from the WFA + DSR research design for Tokocrypto
(evaluation only — not live edge proof).

## Friction (IDR spot, conservative)

| Leg | Approx |
|-----|--------|
| Buy | taker 0.20% + CFX 0.0222% + slip |
| Sell | taker 0.20% + PPh 0.21% + CFX 0.0222% + slip |
| Round-trip | `TokocryptoFriction.round_trip_pct()` ≈ 0.65%+ with slip |

Source: research table (PMK / PFAK cost structure). Verify against current exchange schedule before production sizing.

## Metrics

- Sortino, Calmar
- Walk-Forward Efficiency (WFE); paper flags WFE < 0.5 as fragile
- Deflated Sharpe Ratio (approximate Bailey–López de Prado form with n_trials)

## Strategy harness (offline)

DCA · Trend EMA · Mean reversion RSI+BB · Hybrid — signals only.

## Explicit non-claims

`TOKOCRYPTO_REAL_OOS_EVIDENCE = PENDING`

This layer does not place orders; RiskEngine remains absolute authority.
