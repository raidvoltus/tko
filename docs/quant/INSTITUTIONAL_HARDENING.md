# Institutional higher-order hardening (assurance layer)

## Classification

| Layer | Status |
|-------|--------|
| Architecture / safety / execution authority | Production-grade foundation |
| Scientific evaluation / calibration | Hardened |
| Empirical Tokocrypto OOS edge | **PENDING** |
| Profitability / optimality | **Not claimed** |

## Modules

| Module | Role |
|--------|------|
| `src/audit/ledger.py` | Append-only hash-chained audit events |
| `src/audit/clock.py` | Monotonic elapsed + UTC + drift gate |
| `src/audit/lineage.py` | Dataset→feature→model→calibration lineage |
| `src/ops/recovery.py` | Crash→reconcile→verify→READY/NO_TRADE |
| `src/ops/drift.py` | PSI / Brier drift detection (operator review only) |
| `src/validation/independent.py` | Scorecard eval without StrategyEngine |

## Invariants preserved

- RiskEngine absolute authority
- LIVE-only production execution
- Calibration/strategy/governor: evidence only
- Drift detection **never** auto-promotes models

## Not yet full institutional desk

- Exchange-side independent recon service process isolation
- Full chaos suite in CI matrix for every failure mode
- Cryptographic signatures beyond hash-chain (PKI)
