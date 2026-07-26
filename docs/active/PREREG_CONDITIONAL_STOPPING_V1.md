# PREREGISTRATION — `CONDITIONAL_STOPPING_V1`

**Status: FROZEN on 2026-07-25, before any model was fit or any result was seen.**
Nothing in this document may be changed after the first score is computed. If any element proves
unworkable, the experiment is **abandoned and archived** — not amended. A near miss is a miss.

This is the final clean test of the dynamic-exit idea. It exists because the completed evidence
killed *unconditional* stopping, which is not the same claim as *conditional* stopping:

| already tested | verdict |
|---|---|
| 2,880 static 15m TP/SL cells | 0 positive, 0 BH survivors, every week negative |
| 7 frozen causal stopping policies (5m, 2 checkpoints) | all lost to HOLD, negative LBs, 0/4 weeks positive |
| complexity wall on entry mispricing | shuffled-label null scored ≈ the real model |

Every one of those applies **one rule to every round**. None tested whether *observable state can
identify the subset of rounds where exiting early beats holding*. That gap — and only that gap — is
what this experiment tests.

---

## 1. Hypothesis

> Using only causally observable quote-path and round-state information, a **state-conditioned**
> exit policy outperforms holding to settlement, on the **same frozen entry population**, after
> bid-side execution, fees, latency and depth.

**Prior: LOW.** A round trip costs ~3.5c (spread + two fee legs), latency costs a further
~0.6–0.8c/second (measured in the ledger/replay reconciliation), and the entire unconditional
structural signal is ~1c. The policy must find **>3.5c of conditional edge** in a market where six
independent tests found that conditional selection adds nothing. This is one bounded attempt, not
a research programme.

## 2. What this explicitly does NOT test

- It does **not** re-open entry selection. The exit model may not choose *which* rounds to enter —
  otherwise entry and exit selection are confounded and any result is uninterpretable.
- It does **not** re-open direction, fair-value residual ML, maker entry, or static barriers.
- It does **not** license threshold tuning on any strategy, frozen or otherwise.

## 3. Entry population (frozen)

Exactly the entries used by `test_stopping_baselines.py`, unchanged:

```
market            BTC 5m and 15m, tested SEPARATELY (no pooling)
entry checkpoints 240s and 60s remaining (5m); the 15m analogues
side              the market leader (higher bid) at the entry quote
ask gate          0.60 <= ask < 0.97
entry price       ask VWAP at intended quantity + taker fee 0.07*p*(1-p)
one decision      per round per policy
```

## 4. Allowed features — CLOSED LIST

No feature may be added after inspecting any result. Anything not on this list is out of scope for
`V1`, permanently.

```
current net executable PnL
bid change over 1s / 3s / 5s
ask change over 1s / 3s / 5s
spread, spread change
bid persistence (quotes since last bid change)
best-bid size
bid depth within 1c / 2c / 5c
depth added / removed since previous book
quote-update intensity
time since last Polymarket update (book age)
BTC move since last Polymarket update
distance from anchor
seconds remaining
current leader side
flip-risk probability
late-shock probability
```

## 5. Targets

**Primary (competing risk):**

```
bid improves by >= 2c  BEFORE  bid deteriorates by >= 2c, within the next 10s
```

**Secondary:** `P(+1c in 5s)`, `P(+2c in 10s)`, `P(+3c in 30s)`, `P(-2c before +2c)`,
`P(profitable bid disappears within 3s)`, remaining executable MFE and MAE over 5s/10s/30s.

Every target is computed on **future bid VWAP at intended quantity, minus exit fee, after latency**
— never on best bid alone.

## 6. Models — CLOSED LIST (4 families)

```
M0  empirical monotonic state table   (the baseline that must be beaten by any model)
M1  regularized logistic survival
M2  LightGBM
M3  CatBoost
```

Forbidden: RL, neural Q-learning, transformers, unbounded hyperparameter searches, per-regime model
proliferation. If M0 does not stratify monotonically, **stop before fitting M1–M3.**

## 7. Declared test family and multiple-testing correction

Declared in advance: **4 models × 2 horizons × 2 checkpoints = 16 primary cells.** Secondary
targets are diagnostic only and carry no promotion weight. Benjamini–Hochberg is applied across all
16; the reported q-value must survive at 0.10. Reporting the best cell without the correction is
a protocol violation.

## 8. Data separation

```
DEVELOPMENT      the existing 21-day Oracle sample - feature verification, model selection,
                 threshold selection. All choices frozen at the end of this stage.
UNTOUCHED TEST   the next 30 calendar days, scored ONCE.
PROMOTION        >= 8 calendar weeks total.
```

The 21-day sample can kill this experiment; it can never promote it.

## 9. Policy

At each valid book event:

```
Q_exit_now, Q_wait_5s, Q_wait_10s, Q_wait_30s, Q_hold
```

Exit only when

```
lower_bound(Q_exit_now) > conservative(Q_best_alternative) + action_change_buffer
```

The buffer is declared in development and frozen; it exists so tiny noisy differences cannot
generate churn.

## 10. Mandatory controls

```
HOLD_TO_SETTLEMENT            (the benchmark - the model must beat THIS)
FIRST_NET_PLUS_1C
FIRST_NET_PLUS_2C
FIXED_TIMEOUT
RANDOM_EXIT matched on ask x time-left
SHUFFLED_SCORE within ask x time-left x day
ASK-MATCHED model control
```

## 11. Primary metric

**Paired incremental net PnL versus HOLD on identical rounds**, via day-block paired bootstrap:

```
mean(model_pnl - hold_pnl), resampling whole DAYS
```

Paired-and-blocked because the two arms share rounds (paired removes round-level variance) and
because trades within a day share a regime (blocking prevents the over-confident naive interval
that the 2026-07-25 gate-math fix already corrected once).

## 12. Promotion gate — ALL required

```
>= 500 independent rounds
>= 8 calendar weeks
paired incremental EV lower bound > 0
absolute post-cost EV > 0
profit factor > 1.20
positive in most weeks
positive at 1s AND 2s latency
positive at 1, 5 and 10 shares
beats shuffled-score control
beats matched-random exit
no single week contributes > 50% of profit
BH q-value <= 0.10 across the 16 declared cells
no post-result threshold adjustment
```

## 13. Kill rule

If the untouched forward test fails any gate condition:

```
archive CONDITIONAL_STOPPING_V1
do not retune thresholds
do not try new model variants
CLOSE the dynamic-exit lane
```

Reopening requires a **fundamentally new dataset or economic mechanism** — not a new model.

## 14. Known limitations, declared IN ADVANCE

1. **The development data cannot support the primary target as written.** The 21-day Oracle window
   recorded `top_ask_size` but **no bid size** — so "future bid VWAP at intended quantity" is not
   computable on it. Development therefore runs at **qty = 1 (top-of-book bid)**, and this is
   declared as a known bias: 1-share exits are the most optimistic case, so development will
   *overstate* every exit policy. The recorder upgrade of 2026-07-25 (full bid+ask ladders,
   `top_bid_size`, `b1/b2/b5`) means the **forward test can and must run size-aware**, at 1, 5 and
   10 shares. A policy that only survives at qty=1 fails the gate.
2. **Decision frequency is per book event, not per second.** Median quote cadence on this feed is
   ~1.8s; "once per second" is not achievable and is not claimed.
3. Latency is applied as: a condition observed at book event *i* executes at event *i+1*.
4. The forward window will contain whatever regime it contains. No regime filtering is permitted
   after the fact.

## 15. Freeze record

```
frozen_at      2026-07-25
scope          exit policy only, on frozen entries
family_size    16 primary cells
dev_data       21-day Oracle deployment sample
forward_test   next 30 calendar days, scored once
prereg_sha256  (recorded on commit - see PREREG_HASH.txt)
```

Any edit to this file after the first score invalidates the experiment.
