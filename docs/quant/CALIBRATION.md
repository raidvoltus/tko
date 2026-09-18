# TKO Calibration Framework

## Principle

Calibration improves **honesty of probabilities**, not automatic profitability.
It has **no order authority**. Pipeline:

```
Raw score → OOF/PIT candidates (Platt | Isotonic | Beta | Temperature | Identity)
         → select by Brier → log_loss → ECE (must beat identity by min_brier_improve)
         → CalibrationArtifact (versioned)
         → expected edge (optional EdgeCalibrator)
         → Ensemble / Governor (policy)
         → RiskEngine ALLOW/DENY
         → LIVE Execution
```

## References

- Fonseca & Lopes (2017) PD calibration, time-series recalibration
- Guo et al. (2017) temperature scaling, ECE
- Kull et al. (2017) beta calibration
- AFML / trading: leakage-free OOF, economic impact of miscalibration

## Selection rule

**Not** `n < 200 → Platt`.  
**Yes** candidate fit on cal fold → score on selection fold → proper scoring rules.

## Walk-forward

TRAIN → PURGE → CAL fit → CAL select → TEST OOS (per `WalkForwardRunner`).

## Files

- `src/evaluation/calibration.py` — metrics, calibrators, `CalibratorSelector`, `CalibrationArtifact`
- `src/evaluation/runner.py` — hooks selector on validation halves
