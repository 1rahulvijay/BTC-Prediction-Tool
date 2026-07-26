# CLOSURE RECORD — `CONDITIONAL_STOPPING_V1`

**Closed 2026-07-26 by operator decision, at the preregistered M0 stop rule. No model was ever
fitted.** This is the archival record; it supersedes nothing and reopens nothing.

```
CONDITIONAL_STOPPING_V1

M0 strict gate:        FAIL
Multiple-test-aware:   FAIL
Models fitted:         NONE
Protocol integrity:    PRESERVED
Lane status:           CLOSED
Reopening condition:   fundamentally new information
```

## The distinction this experiment establishes

> **A temporary profitable exit frequently exists in hindsight.**
>
> **No causally observable stopping state has shown that taking it improves PnL versus holding.**

Those are different claims, and conflating them is what kept this lane open. The first is a
property of the price *path* (~90% of leader entries contain some profitable exit, typically within
9–39s; hindsight ceiling +19.0c / +10.7c). The second is a property of *decidability*, and it is
the only one that pays.

## Why it closed

**1. The formal gate failed.** M0 required observable state to stratify before any model was fit.
One strict monotonic pass appeared in **1 of 28** variable×cell searches. With 5 buckets a random
ordering is monotone with probability `2/5! = 1.7%`, so across 28 tests:

```
expected chance passes        0.47
P(at least one by chance)     37.5%
observed                      1        -> not credible evidence
```

The analysis script's own criterion ("any variable monotone with >=5pp spread") returned PASS.
**That automated PASS was overturned before any decision was taken**, by applying the
preregistration's multiple-testing discipline to the gate rather than only to final scoring.
Recording this explicitly because the failure mode — a loose automated criterion waving through a
chance ordering — is the exact thing that turns into months of model fitting and a misleading AUC.

**2. The one real effect endorses the incumbent.** `net_pnl` is not strictly monotone, but spreads
**+12 to +21pp with the same sign in 4 of 4 cells** — genuine conditional structure. Its direction:
*the higher the current unrealised profit, the more likely the bid keeps improving.* Momentum, not
mean reversion. A model trained on it learns **"when winning, keep holding"** — which is HOLD, the
policy that already beat all seven causal stopping baselines. The strongest feature in the
preregistered set argues for the incumbent rather than against it.

**3. The economics were already against it.**

```
round-trip cost      ~3.5c   (spread + two taker-fee legs)
latency cost         ~0.6-0.8c per second (ledger/replay reconciliation)
structural edge      ~1c
7 causal baselines   all worse than HOLD, negative LBs, 0/4 positive weeks
```

M1–M3 would have needed a large conditional effect that simpler policies missed, surviving
selection bias, latency, bid-side execution and a paired comparison against HOLD.

## What was deliberately NOT done

**M1 was not run on `net_pnl` inside this experiment.** Doing so would change the experiment after
seeing which variable looked strongest — the precise protocol violation the preregistration exists
to prevent. A narrow single-feature test could be scientifically valid only as a *new* frozen
preregistration (`PNL_MOMENTUM_POLICY_V1`: one feature, one monotone policy, paired incremental
post-cost PnL vs HOLD as the only metric — no LightGBM, no CatBoost, no model search). **It is not
prioritised**: static exits already failed, and the observed momentum relationship recommends
exactly what HOLD already does.

**The preregistration file was not edited on closure** — not even to add a "CLOSED" banner —
because its SHA-256 is the integrity record for the entire experiment. Verified at closure:

```
recorded at freeze  5fcae7b6aa069141d0c44f6c54bfac1d87a4e81e2fdba35b7f76f362dfa1a35c
verified at closure 5fcae7b6aa069141d0c44f6c54bfac1d87a4e81e2fdba35b7f76f362dfa1a35c
INTEGRITY           INTACT - the protocol was never edited after freezing
```

## Archived artifacts

| artifact | path |
|---|---|
| Frozen protocol | `docs/active/PREREG_CONDITIONAL_STOPPING_V1.md` |
| Freeze hash record | `docs/active/PREREG_HASH.txt` |
| M0 result + overturned auto-verdict | `docs/active/COND_STOPPING_M0_2026-07-26.md` |
| M0 code | `backend/research/cond_stopping_m0.py` |
| Baseline study that motivated it | `docs/active/STOPPING_BASELINES_2026-07-25.md` |
| Baseline code | `backend/research/test_stopping_baselines.py` |

## Reopening condition

A **fundamentally new dataset or economic mechanism** — not a new model, not a new feature set, not
a re-run with different buckets. Candidates that would qualify: sub-second book data showing
decidable structure invisible at ~1.8s cadence, or a materially different cost structure (maker
rebates, lower fees) that changes the ~3.5c round-trip arithmetic.

## Assessment

The preregistration did its job. It converted a plausible, appealing hypothesis into a bounded,
cheap, falsifiable test that closed in one stage without fitting a single model — and it caught its
own tooling's false positive on the way. That is the intended outcome of the method, not a
disappointment.
