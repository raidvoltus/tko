# TKO Scientific Calibration Framework

## Absolute rules

- Calibration ≠ profitability
- Ranking ≠ calibrated probability
- **No order authority** (no `new_order`, no RestClient)
- RiskEngine remains absolute ALLOW/DENY
- Identity baseline ≠ calibrated

`TOKOCRYPTO_REAL_OOS_EVIDENCE = PENDING` until real exchange OOS is run.

## Fold layout (leakage-safe)

```
TRAIN | PURGE | CAL_FIT | CAL_SELECT | TEST(OOS)
```

OOS never enters fit or selection.

## Candidates & selection

Identity | Platt | Isotonic | Beta | Temperature

Order: **Brier → log_loss → ECE** on selection segment only.  
Require `min_brier_improve` vs identity; else `UNCALIBRATED`.

## Diagnostics

- Brier, log_loss, ECE, MCE (equal-width + quantile ECE)
- **Logit** slope/intercept (primary): logit P = α + β logit p
- Linear slope/intercept (auxiliary)
- Paired **block bootstrap** ΔBrier CI (not claimed as significance test alone)

## Artifact

`CalibrationArtifact` with `identity_hash` = SHA256(canonical payload **excluding** `fitted_at`).  
`verify_artifact()` fails closed on hash/schema/model/dataset mismatch.

## EdgeCalibrator

Status: `UNCALIBRATED_HEURISTIC` | `CALIBRATED_OOS` — never silent.

## Live path

Raw score → artifact (if valid) → evidence → Governor → **RiskEngine** → Execution LIVE
