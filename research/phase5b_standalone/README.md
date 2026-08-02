# Phase 5B Standalone Research Suite

This directory contains experiments 43-88 from the information-quality, state-transition,
liquidity-response and application-evidence blueprint. Every experiment is isolated from model
serving and trading authority.

## Invariants

- Every experiment owns a frozen hash-checked protocol, runner, self-test and README.
- Modeled tests use TRAIN, CALIBRATION, POLICY_SELECTION and UNTOUCHED_TEST with purge gaps.
- Economic tests include declared costs and 1.5x/2x stress.
- Diagnostic prediction quality cannot become `PASS_CANDIDATE` without executable economics.
- Missing history, timestamp proof, action arms or fills produce a blocked/insufficient result.
- Reports are immutable and always set `capital_authority=false`.

## Commands

```powershell
python research\phase5b_standalone\run_all.py --selftest
python research\phase5b_standalone\run_all.py --smoke --maximum-rows 5000
python research\phase5b_standalone\run_all.py --run
```

The full run can be expensive. Run it only when the application and model training are stopped.
Smoke mode verifies real schemas and runtime paths; it is not sufficient evidence for promotion.
