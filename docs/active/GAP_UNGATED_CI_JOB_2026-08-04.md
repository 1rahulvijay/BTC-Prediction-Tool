# Gap — 27 selftests were gated by nothing

Found 2026-08-04 while registering `crossing_calibration_v1.py` into CI.

## The defect

`.github/workflows/invariants.yml` declares **two** jobs:

```
invariants   ubuntu-latest    118 python commands
startbat     windows-latest    99 python commands
```

- **GitHub Actions has never executed a step** — the account is billing-locked, 51 runs, 0
  successes. This is the standing condition `run_ci_locally.py` exists to work around.
- **`run_ci_locally.py` called `workflow_steps()` with its default `job="invariants"`.**

So the commands unique to `startbat` were run by *neither*. There were **27**:

```
backend/check_feature_contract.py               <- and it FAILS, see below
backend/tests/test_round_state_causal_contract.py     <- pins the P0-01 and P0-02 fixes
backend/datastore_identity.py                   backend/recorder_health.py
research/research_status.py                     research/regime_labeler_v1.py
research/regime_volatility_control_v1.py        research/tradability_head_v1.py
research/conditional_direction_v1.py            research/exit_timing_v1.py
research/direction_ensemble_v1.py               research/maker_execution_v1.py
research/altcoin_maker_execution_v1.py          research/crossing_heads_v1.py
research/crossing_calibration_v1.py             research/bybit_l2_maker_v1.py
research/algodesk_17_agents_v1.py               research/algodesk_ml_rl_dl.py
research.algodesk.{data,agents,backtest}        research.multihorizon.{fetch,features,run}
backend/polymarket_crossing_recorder.py         backend/crossing_recorder_hf.py
backend/train_crossing_heads.py
```

That is **every research study built since the crossing work began**, plus the contract test that
pins the same-minute feature leak (P0-02) and the artifact version contract (P0-01).

This is the same defect the runner was written to prevent, reproduced one level up: a gate that
appears to cover the workflow while silently skipping a job of it. A step added to `startbat`
looked wired and was not.

## The fix

`every_step()` in `backend/tests/run_ci_locally.py` now walks **every job** in the workflow and dedupes
per **command** — `startbat` packs 99 commands into a single step while `invariants` lists them
individually, so a step-level key would never match and the whole Windows block would re-run.
`command_identity()` treats `python backend/x.py` and `python -m backend.x` as the same gate.

Steps referencing `start.bat` are excluded **whole**, not line-by-line: dropping just the `call`
line left its `set BTC_VALIDATE_STARTUP=1` and `if errorlevel 1` fragments behind to fail under
sh. Launching the app is the operator's action, per standing instruction.

```
commands gated before   123
commands gated now      152   (+29)
```

## What the widened gate immediately found

`python backend/check_feature_contract.py` **fails**, and has presumably been failing for as long
as it has existed, unobserved:

```
0 STALE, 12 UNKNOWN of 12 present artifacts.

The VWAP formula changed in v2 (cumulative -> trailing time-anchored). Any model
trained under v1 learned from a near-constant VWAP column and is now being fed a
materially different one. That is train/serve skew: it will not raise, it will
just be quietly wrong.
```

This is consistent with the standing `0/25 serviceable` artifact state and needs a challenger
retrain. The script deliberately does not retrain — promotion stays a gated act.

26 of the 27 newly-gated commands pass.

## Local CI status after the fix

```
2 FAILING STEP(S) of 118

[invariants] Oracle release freeze intact    5 artifacts DRIFTED from ORACLE_2026_07_04
                                             (parallel session's in-flight retrain)
[startbat]   check_feature_contract          12 UNKNOWN artifacts, VWAP v1->v2 skew
```

**Both require a governance decision that is not mine to make.** The freeze failure needs a *new
release id* — overwriting the freeze in place would erase the only record of what produced the
live sample. The feature-contract failure needs a challenger retrain bundle.

Neither is suppressed, and neither was caused by this work. The second was already true; it is
newly *visible*.
