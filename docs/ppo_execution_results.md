# RL Execution Sandbox - SYNTHETIC, NOT EVIDENCE

> **Not promotable. Must not be cited as an edge.** Every environment number
> - fees, fill probabilities, queue advancement, penalties - was CHOSEN, not
> measured. The agent learns the environment's author, not the market. The
> multi-venue archive has 0 rows, so no fill model has been validated against
> reality.

## What the first version got wrong

It paid a maker **rebate** of +1.5 bps. Binance USD-M charges a maker **fee** of
2.0 bps at the tier `event_conditional_v1/frozen_protocol.json` assumes.
Flipping only that one sign, everything else identical:

```text
maker rebate +1.5 (as written)   agent mean  +0.57 bps
maker fee    -2.0 (real venue)   agent mean  -2.88 bps
```

The reported 88% win was an artifact of an invented rebate. The benchmark was
also unfair - the naive comparator ran ONE step against the agent's full
episode. Both are corrected below.

## Corrected run: frozen-protocol fees, fair benchmark

| policy | mean episode cost |
|---|---:|
| naive taker (full episode) | `-6.98 bps` |
| trained agent | `-3.76 bps` |
| difference | `+3.22 bps` |

**Both policies are net NEGATIVE.** Patience reduces cost relative to always
crossing, but it does not produce profit - there is no rebate to harvest. Any
apparent edge here is a property of this hand-written simulator.

## What would make this real

Fill probabilities and queue dynamics measured from the recorded L2 tape; the
venue's actual fee schedule; adverse selection after fill; missed-fill
opportunity cost; and the TRADE_THROUGH / QUEUE_ESTIMATED fill standards already
defined in `event_conditional_v1`. None are available at 0 archive rows.

## Policy converged on IN THIS SIMULATOR ONLY

```text
Time    Spread    Queue      -> Action
--------------------------------------
Low     Narrow    None       -> TAKER
Low     Narrow    Back       -> TAKER
Low     Narrow    Top        -> WAIT
Low     Wide      None       -> TAKER
Low     Wide      Back       -> TAKER
Low     Wide      Top        -> TAKER
Medium  Narrow    None       -> MAKER
Medium  Narrow    Back       -> WAIT
Medium  Narrow    Top        -> WAIT
Medium  Wide      None       -> MAKER
Medium  Wide      Back       -> WAIT
Medium  Wide      Top        -> WAIT
High    Narrow    None       -> WAIT
High    Narrow    Back       -> WAIT
High    Narrow    Top        -> WAIT
High    Wide      None       -> MAKER
High    Wide      Back       -> WAIT
High    Wide      Top        -> WAIT
```

Read as a description of the toy environment's incentives, not as a trading
rule. It says: cross when out of time, wait when already at the front of the
queue. That is what the reward function was written to reward.
