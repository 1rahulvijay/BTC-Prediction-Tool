# Does the app work, and is the decision chain in sync? — `2026-08-08`

The app was **booted**, not only read. Boot completes in 47s, every restore path reports,
and the chain from model heads to paper execution is wired correctly end to end.

**The headline is not a bug. It is that model-driven trade execution is currently CLOSED,
by a deliberate refusal, and one of its two gates was being held shut by a reason that had
already been fixed.**

---

## What was verified by running it

```text
[BOOT 1-7/7]  Ready in 47.3s
Restored 0 pending predictions
Restored 195 current-model verified predictions
Restored per-model verifier: 6812 committed outcomes, 0 pending votes
Restored 0 A/B variant outcomes
Restored 0 served confidences into the percentile window (release bundle_1786176441)
Restored 129 price-to-beat outcomes
[BOOT 5/7]    Rejecting legacy bundle without identity manifest -> startup training
[TRAIN]       86,325 sequences, 8 regime buckets, 72 components, training dispatched
```

Both new boot-time restores run clean. The percentile window restores **zero** rows and
that is correct: it is scoped to the serving release, and this release has no history yet.

Two refusals fired at boot and both are working as designed — a saved bundle with no
identity manifest is rejected rather than loaded, and `persistence_model (P(hold))` is
refused with `MODEL_UNAVAILABLE_UNKNOWN_IDENTITY`.

---

## The decision chain, end to end

```text
model heads         direction / confidence / expectedMove / expectedMoveRange / stopLoss
     |              targetContract stamped at emission
server policy       meta filter -> expectancy neutraliser -> decision_gate
     |              finalDirection / finalSignal / finalAction / no_trade_reasons
boundary            _binance_paper_context, a 16-field WHITELIST
     |
model_consensus     entry: verdict==TRADE and actionable and not blocking and UP/DOWN
                    exit:  MODEL_DIRECTION_FLIP / MODEL_CONFIDENCE_COLLAPSE /
                           MODEL_EDGE_DECAY_PROFIT_LOCK / MODEL_CONTEXT_STALE
```

**The whitelist is in sync.** `model_consensus` reads 15 prediction fields and every one is
carried across; the other four strategies (`trend_following`, `breakout`, `mean_reversion`,
`random_control`) read none — by design, they are the baselines and the control the
model-driven strategy has to beat.

**Entry and exit are both model-driven**, and symmetric on direction: a flip to the
opposite side closes the position. Buy, sell, hold and exit all come from the same
`finalDirection` / `finalAction` pair, so no two of them can disagree about the same
moment.

---

## Why nothing trades: two gates, measured

### Gate 1 — calibration provenance. **Was stale. Fixed here.**

`model_consensus` lists `live_probability_calibration` as a required input and returns
NO_DATA without it. `calibratedConfidence` is only set when `PrecisionEngine.calibrated()`
returns a value, and it refused while `contract_provenance == "UNRECORDED"`.

The reason it refused was written in `calibration.py`:

> *It cannot simply be filtered by contract: `predictions_{h}m` has NO target_contract
> column.*

**That column exists**, along with `release_id`, and `log_prediction` requires both. The
comment had outlived the fix, and a gate was being held shut by a blocker that was gone.

The fit now filters on the stored contract and — the part that matters more — grades
correctness with the **contract's own outcome** (`raw_direction == actual_direction`)
instead of endpoint sign (`actual_move > 0`). Those are different rules. Measured on a
fixture of 120 first-touch rows that are 75% correct by contract and 0% correct by endpoint
sign, the old rule would have taught a base rate of **0.000** from evidence whose true rate
is **0.750**.

Provenance is now **earned by a fit that happened**, not declared by the code path that
intended one, and it is cleared on release swap alongside the maps it describes — a line
that only became necessary once "RECORDED" could be earned.

**Honest consequence: this does not open the gate today.** Every stored row is
`UNKNOWN_LEGACY` (5m: 2,514 rows / 1,282 leans; 15m: 861 / 620) and `resolution_basis` is
empty on all of them, so none can be attributed to a contract and all are excluded. Legacy
rows are not pooled in: a row that cannot say which question it answered cannot calibrate an
answer to a specific one. Calibration is now blocked on **new data**, not on code, and it
unblocks itself as fresh stamped rows accumulate.

### Gate 2 — the contract mismatch. **The real one, and it does not clear with time.**

```text
>>> assert_admissible(BINANCE_DIRECTIONAL_EV, first_touch_triple_barrier_v1)
REFUSED: binance_directional_ev needs one of ['endpoint_settlement_v1']
>>> assert_admissible(BINANCE_DIRECTIONAL_EV, endpoint_settlement_v1)
ADMITTED
```

`model_consensus` computes `EV = (2p - 1) * move - costs`, which treats `p` as the
probability the **endpoint** lands on the predicted side. The served model answers
`first_touch_triple_barrier_v1` — which barrier is touched first — and the two disagree on
roughly a quarter of paths (24.9%, measured in the contract selftest).

So with the shipped configuration the strategy refuses **every** entry at
`target_contract_inadmissible`. That is the correct behaviour and it is not a defect: the
alternative is trading an EV computed from a probability that answers a different question.

**It clears in exactly one of two ways**, and both are real work rather than a flag:

1. serve an endpoint-contract probability alongside the first-touch one, or
2. write an EV formula for first touch — where the payoff is the barrier, not the endpoint
   move, so `(2p - 1) * move` is the wrong shape to begin with.

---

## The other execution paths

| path | model-driven? | state |
|---|---|---|
| `model_consensus` (Binance paper) | yes | wired, refusing at gate 2 |
| `trend_following` / `breakout` / `mean_reversion` | no | technical baselines, running |
| `random_control` | no | the zero-information control, and `_promotion_gate` now requires beating it |
| price-to-beat HOLD/EXIT advice | yes | live, and now labelled with the contract it grades under (5.31) |
| `polymarket_policy` | yes | prices actions from the recorded ladder; no real-money authority exists anywhere |

---

## Verdict

The app **works**: it boots, restores, trains, predicts, grades and persists, and the
decision chain is internally consistent — one direction, one verdict, one moment, carried
from the heads through the policy to entry and exit.

What it does **not** do is trade on the model, because the probability it produces and the
expected value the strategy computes answer different questions. That gate is doing its job.
The thing worth fixing was the *other* gate: a solved blocker that was still closing a door,
which is the same failure mode as a stale defect register — and both were found in the same
sweep.
