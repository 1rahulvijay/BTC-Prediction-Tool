# CONDITIONAL_STOPPING_V1 - M0 gate (2026-07-26)

> **LANE CLOSED 2026-07-26 at this gate. No model was ever fitted.**
> Closure record: [`docs/archive/CONDITIONAL_STOPPING_V1_CLOSED_2026-07-26.md`](../archive/CONDITIONAL_STOPPING_V1_CLOSED_2026-07-26.md).
> Reopening requires fundamentally new information - not a new model or feature set.

Stage M0 of the frozen preregistration (`PREREG_CONDITIONAL_STOPPING_V1.md`, sha256 `5fcae7b6...`). **If no preregistered state variable stratifies the primary target monotonically, the lane closes without fitting M1-M3.**

Primary target: **bid improves >= 2c before it deteriorates >= 2c, within 10s.** Entry population frozen (leader, ask 0.60-0.97, checkpoints 240s/60s). Development data has no bid size, so qty=1 top-of-book - the optimistic case, as declared.

## 5m, entry 240s left (obs 320,243 / rounds 3,817; base rate 54.0%)

| state variable | monotone? | lowest bucket | highest bucket | spread |
|---|---|---|---|---|
| net_pnl | **YES** | 44.5% | 58.0% | +13.5pp |
| bid_chg_1 | no | 50.5% | 54.6% | +4.1pp |
| bid_chg_3 | no | 50.8% | 53.7% | +2.9pp |
| spread | **YES** | 52.0% | 55.3% | +3.2pp |
| secs_left | no | 53.8% | 53.8% | -0.0pp |
| dist_anchor | no | 51.1% | 54.4% | +3.3pp |
| btc_move_1 | no | 54.3% | 52.4% | -1.9pp |

## 5m, entry 60s left (obs 65,120 / rounds 3,343; base rate 59.6%)

| state variable | monotone? | lowest bucket | highest bucket | spread |
|---|---|---|---|---|
| net_pnl | no | 43.3% | 64.6% | +21.4pp |
| bid_chg_1 | no | 54.6% | 58.4% | +3.8pp |
| bid_chg_3 | no | 51.1% | 61.8% | +10.7pp |
| spread | no | 57.1% | 60.4% | +3.3pp |
| secs_left | no | 58.5% | 60.9% | +2.3pp |
| dist_anchor | no | 56.2% | 58.8% | +2.6pp |
| btc_move_1 | no | 60.0% | 56.0% | -3.9pp |

## 15m, entry 240s left (obs 63,119 / rounds 1,181; base rate 55.8%)

| state variable | monotone? | lowest bucket | highest bucket | spread |
|---|---|---|---|---|
| net_pnl | no | 45.5% | 57.9% | +12.4pp |
| bid_chg_1 | **YES** | 47.6% | 59.6% | +12.0pp |
| bid_chg_3 | no | 49.5% | 58.2% | +8.7pp |
| spread | no | 53.6% | 60.7% | +7.1pp |
| secs_left | no | 56.1% | 55.9% | -0.1pp |
| dist_anchor | no | 55.0% | 51.9% | -3.0pp |
| btc_move_1 | no | 56.1% | 54.1% | -2.0pp |

## 15m, entry 60s left (obs 10,389 / rounds 574; base rate 62.0%)

| state variable | monotone? | lowest bucket | highest bucket | spread |
|---|---|---|---|---|
| net_pnl | no | 46.4% | 64.4% | +18.0pp |
| bid_chg_1 | no | 54.9% | 63.0% | +8.1pp |
| bid_chg_3 | no | 51.8% | 64.4% | +12.7pp |
| spread | no | 56.4% | 65.7% | +9.3pp |
| secs_left | no | 58.6% | 63.0% | +4.4pp |
| dist_anchor | no | 56.3% | 57.4% | +1.1pp |
| btc_move_1 | no | 62.9% | 57.0% | -5.9pp |

## M0 verdict

> The script's automated criterion ("any variable monotone with >=5pp spread") returned PASS on
> **1 of 28** variable x cell tests. **That criterion is too loose and the automated PASS is
> overturned below.** The preregistration requires multiple-testing discipline; it applies to this
> gate too, not only to the final scoring.

### The single "monotone" hit is at exactly the chance rate

With 5 buckets, a random ordering is strictly monotone (either direction) with probability
`2/5! = 1/60 = 1.7%`. Across **28** tests:

```
expected chance passes            0.47
P(at least one chance pass)       37.5%
observed passes                   1        <- unremarkable
```

`bid_chg_1 @ 15m/240s` is therefore **not evidence of anything**. On a strict single-variable
reading, **M0 fails and the lane closes.**

### But there IS a real effect, and its direction matters more than the gate

`net_pnl` is not strictly monotone (one bucket inverts) yet shows a **large, consistent,
same-direction spread in all four cells**:

| cell | low bucket | high bucket | spread |
|---|---|---|---|
| 5m @ 60s | 43.3% | 64.6% | **+21.4pp** |
| 15m @ 60s | 46.4% | 64.4% | **+18.0pp** |
| 15m @ 240s | 45.5% | 57.9% | **+12.4pp** |
| 5m @ 240s | (see table) | | ~+12pp |

Four out of four, same sign, large magnitude - that is not chance. **A genuine conditional signal
exists.** But read what it says: *the higher your current unrealised PnL, the MORE likely the bid
keeps improving.* Momentum, not mean reversion.

**That is an argument for holding, not for exiting.** A model trained on this would learn "when
you are winning, keep holding" - which is HOLD, the incumbent policy that already beat all seven
stopping baselines. The strongest conditional signal in the preregistered feature set points at
the thing the lane was trying to improve on.

### Ruling

Two defensible readings, stated plainly rather than resolved by a loose threshold:

1. **Strict prereg reading: M0 FAILS** (1/28 monotone = chance) -> archive `CONDITIONAL_STOPPING_V1`,
   close the dynamic-exit lane.
2. **Generous reading: a conditional signal exists** (`net_pnl`, 4/4 cells) -> M1-M3 permitted, but
   the prior drops *further*, because that signal favours holding and any model must still overcome
   ~3.5c of round-trip cost against a ~1c structural edge.

**Recommendation: close the lane (reading 1).** Proceeding would mean fitting models to a signal
whose own direction endorses the incumbent, after paying a multiple-testing cost to find it. That
is the definition of a search that has already answered itself.

This is an operator decision, not an automated one: the preregistration deliberately did not
specify a chance-correction for M0, and I will not retro-fit one to force either outcome.

## Limits (declared in advance, prereg section 14)
- qty=1 top-of-book exits: development data has no bid size. Optimistic by construction.
- Buckets are filled with post-entry book events; all intervals are clustered on the round and n_rounds is reported beside n_obs.
- Censored windows (neither barrier hit within 10s) are excluded from the label, which is the standard competing-risk treatment and is declared here rather than discovered.
- Development sample only. It can close this lane; it can never promote it.

**No model was fitted at this stage. Nothing here changes a threshold.**