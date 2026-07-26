# PREREGISTRATION — `COMPLETE_TRADE_M0_V2`

**Status: FROZEN 2026-07-26, before any V2 score was computed and before any V2 model was fit.**
No element may be changed after the first V2 score. If something proves unworkable the experiment
is **abandoned and archived, not amended**. A near miss is a miss.

---

## 0. Why this is V2 and not a re-run of V1

The V1 gate was never executed, but it is **not unspent**. Between freezing V1 and today, every
load-bearing element changed:

```
selection unit      per side x quantity candidate   ->  one trade per ROUND
ranking target      P(ever profitable)              ->  P(plan net PnL > 0)
crossing labels     post-expiry values present      ->  terminal-settlement 0/1
future price targets  post-expiry values present    ->  NULL past expiry
settlement filter   none                            ->  frozen provenance allowlist
capacity gate       median (q50)                    ->  conservative (q10)
quote survival      "some entry existed"            ->  size-aware VWAP comparison
logging contract    INSERT OR REPLACE, errors eaten ->  append-only, monitored
```

A different selection unit, a different target and different labels is a different experiment.

**More decisively: the 395-round July pilot has already been inspected, analysed, and used to
design these fixes.** Even though the corrected scoring function has not been executed against it,
that dataset is now **development data**. Freezing a protocol around data whose defects shaped the
protocol is circular.

## 1. Status of the existing dataset

```
data/research/complete_trade_forecast/complete_trade_dataset.parquet
    role         DEVELOPMENT ONLY
    permitted    mechanical validation, model fitting, debugging, rough effect-size estimation
    prohibited   the final promotion test; any claim of an edge; any promotion decision
```

Its M0 result, whenever computed, is labelled `M0-DEV (non-promoting)`. It may **kill** the lane.
It may never pass it.

## 2. The definitive test

```
untouched forward data collected AFTER this file is frozen and hashed
```

Nothing collected before the freeze timestamp is admissible as the promotion test, regardless of
quality.

## 3. Research question

> At a chosen entry checkpoint within a Polymarket BTC up/down round, can a causal book-and-flow
> state select one side and one quantity whose realized `TAKE_3C_OR_STOP_3C` net PnL is positive
> after executable entry, exit, fees, latency and capacity limits?

One decision per round. One exit plan. No plan grid, no multi-horizon search — those are separate
preregistrations.

## 4. The independent unit (binding)

```
one trade per round_id
```

The policy chooses, per round: **checkpoint + side + quantity + exit plan**, or `NO_TRADE`.

Four to ten checkpoints inside one round share the same settlement outcome and overlapping price
path. Scoring each as its own trade inflates n and understates variance even after side/quantity
duplication is removed. This is the simpler of the two defensible designs and the harder one to
fool.

**The portfolio alternative is explicitly NOT what is being scored here.** Allowing multiple
entries per round would require modelling existing position, incremental quantity, a total
exposure cap, same-round correlation, and realized combined PnL, with all statistics clustered by
`round_id` and by day. That is a separate, larger experiment.

Clustering for every statistic: **by `round_id`, and by day for the block bootstrap.**

## 5. Frozen primary metric

```
score      plan_take_3c_or_stop_3c_profitable   = P(plan net PnL > 0)
realized   plan_take_3c_or_stop_3c_net          = that plan's net PnL
```

Probability and dollars, the same question about the same plan. The barrier event
`label_take_3c_before_stop_3c` and `label_ever_profitable` are **diagnostics only** — the barrier
event ignores rounds where neither barrier is struck and settlement decides, target/stop overshoot,
the entry price actually paid, fees, and quantity-dependent impact.

**Primary comparison:** mean realized net PnL of the selected trade per round, top score bucket
versus a matched-random control drawn from the same round's candidate set.

Secondary (diagnostic only): win rate, profit factor, day-block CI, CVaR, positive-week ratio,
MFE/MAE, direction accuracy, AUC. **None of these can support promotion.**

## 6. Label semantics (binding)

```
1     the event occurred before terminal settlement
0     the event did not occur before terminal settlement  (TERMINAL, not unknown)
NULL  evidence was genuinely missing or corrupt BEFORE the terminal boundary
```

Settlement is a **terminal competing event**, not right-censoring. A position cannot exist past
settlement, so "did +3c occur within 120s?" is fully answered by the contract's own lifetime.

Marking non-crossing late-checkpoint cases NULL would drop **definite failures** while retaining
early successes — upward selection bias. That error was present in an intermediate build on
2026-07-26 and is corrected here; the regression test `test_a2b_terminal_settlement_labels`
pins it.

Exact future **price** targets past expiry remain `NULL`: no executable price exists 120s out when
the contract settled at 30s. An **event over the position's life** is not undefined.

## 7. Frozen thresholds (declared before any V2 result is seen)

```
minimum independent rounds (forward)   1,000
minimum calendar weeks (forward)       8
top-bucket day-block lower bound       > 0
profit factor                          > 1.20
beats matched-random control           required
positive in most calendar weeks        required
survives 1000ms latency stress         required
no single week or hour > 50% of profit required
multiplicity                           Benjamini-Hochberg, q <= 0.10, family declared at scoring
chance-monotonicity control            with 5 buckets a random ordering is monotone with
                                       p = 2/5! = 1.7%; report family size and expected chance
                                       count with any monotonicity claim
```

Capacity is gated at **q10**; cost at **q80**. `CLASS_B`-style stale inputs are refused, not imputed.

## 8. Prohibited

```
post-expiry price targets                NULL-ing definite non-crossings
scenario-engine economics as evidence    per-checkpoint independence claims
threshold changes after seeing results    promotion on the development dataset
any feature added after the first V2 score
```

## 9. Scenario engine

`backend/trade_forecast/scenario_engine.py` output is `diagnostic_only: True`, `promotable: False`,
and `trade_plan_optimizer` **refuses to authorize any action** on a plan not tagged promotable.
Five artificial paths built from marginal quantiles do not identify a joint path distribution.
Direct per-plan PnL heads (expected plan net PnL, plan PnL q10, P(plan net PnL > 0)) are what will
lift this restriction; until they exist, no scenario number may be cited as evidence.

## 10. If M0 V2 fails

```
COMPLETE_TRADE_M0_V2 CLOSED. MODELS FITTED = NONE.
```

Reopening requires a fundamentally new dataset or economic mechanism — not a new model, not a new
feature set, not a re-run with different buckets.

## 11. Freeze record

```
frozen_at        2026-07-26
independent unit round_id (one trade per round)
primary metric   plan_take_3c_or_stop_3c_profitable / plan_take_3c_or_stop_3c_net
dev_data         395-round July pilot -- DEVELOPMENT ONLY, kill authority only
promotion_data   forward evidence collected AFTER this freeze
prereg_sha256    recorded in PREREG_HASH.txt on commit
```

Any edit to this file after the first V2 score invalidates the experiment.
