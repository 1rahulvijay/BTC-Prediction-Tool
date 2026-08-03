# Conditional direction on gated windows — result

**Protocol** `PREREG_CONDITIONAL_DIRECTION_V1.md` sha256 `dd5c7a75…`, frozen before any result ·
**Script** `research/conditional_direction_v1.py` · Scored **once**

```
test 155,450 bars -> 2026-07-30   cost 14 bps round trip   horizon 15 bars
gate = top 10% of predicted movement   trades do NOT overlap
```

## Result

| arm | trades | hit% | net bps | day-block 95% CI |
|---|---:|---:|---:|---|
| GATED_DIRECTION | 1,507 | 51.0 | **−13.82** | [−15.55, −11.82] |
| UNCONDITIONAL | 9,716 | 50.3 | −13.97 | [−14.39, −13.58] |
| GATED_RANDOM | 1,507 | 50.7 | −13.09 | [−14.59, −11.38] |
| ALWAYS_FLAT | 0 | – | 0.00 | zero by construction |

```
gated - unconditional   +0.15 bps   CI [-1.55, +2.16]   spans zero
gated - random side     -0.73 bps   CI [-2.93, +1.74]   spans zero
direction AUC           0.498 gated / 0.511 unconditional
```

**VERDICT: `DIRECTION_NOT_PREDICTABLE`.** Both AUCs are within 0.02 of a coin flip.

## The three numbers that matter

**1. Every arm loses almost exactly the cost.** −13.82, −13.97, −13.09 against a 14 bps round
trip. Gross return before costs is approximately **zero** in all three. At a 15-bar horizon this
market is a martingale to this feature set, and each arm simply pays the spread.

**2. The direction model does not beat a coin.** `GATED_DIRECTION` is **−0.73 bps worse** than a
random side in the identical windows, and the interval spans zero. The kill rule in the protocol
fires on exactly this: whatever the sign of the raw return, a direction model that cannot beat a
random side contributes nothing.

**3. Gating did not change direction.** +0.15 bps versus unconditional, interval spanning zero,
and AUC actually *lower* inside the gated windows (0.498 vs 0.511) — the opposite of the
hypothesis. Selecting for large expected movement did not select for predictable sign. If
anything, the windows with the largest expected moves are marginally the least directionally
predictable, which is intuitive: they are where surprise arrives.

## This is the conjunction the whole plan rested on, and it fails

`TRADABILITY_HEAD_V1` established a real movement gate: +2.67 points over the volatility
incumbent, interval clear of zero. That result stands — it was measured against the right
baseline and it survives.

This test asked the only question that made the gate economically interesting: **is there
direction to trade inside those windows?** There is not. A window predicted to move 40 bps with
a sign no better than 50/50 is not an opportunity; it is a coin flip with a 14 bps entry fee.

That was stated as the necessary caveat when the gate was published. It is now measured rather
than assumed.

## On the apparent contradiction with "direction AUC 0.87"

Phase 5 reported direction AUC 0.87. That figure came from a different question on a different
venue — Polymarket round settlement given the current leader, where the leader's identity is
itself most of the answer. It is not a 15-minute forward return sign on Binance, which is what
is measured here and which is 0.498.

The two are not in conflict, and conflating them would be the error. Predicting *which side is
currently ahead will still be ahead* is not predicting *which way price moves next*.

## Governance

- Protocol frozen and hashed before any result; 15/15 hashes verify in CI.
- Both the gate and the direction model are trained on **train only**; the test window was
  scored once.
- **Trades do not overlap.** With a 15-bar horizon, consecutive selected bars describe the same
  move; counting them separately would have reported ~15× the trades and made one lucky move
  look like fifteen. Declared in the protocol because it changes the result.
- `GATED_RANDOM` was pre-declared as the control that decides. Without it, `GATED_DIRECTION` at
  −13.82 could have been blamed on costs rather than on the model, when in fact a random side in
  the same windows did slightly better.
- Costs were not negotiated downward to rescue the result. At 14 bps the lane loses; that is the
  answer, not a parameter.
- Selftest 15 checks, including that a market which never moves loses exactly the cost, and that
  an arm compared against itself yields a zero-width interval.

## Where this leaves the plan

```
Regime labeler         separable, 84% of it volatility        -> not a router basis
Strategy Router        NOT BUILT
Tradability head       ADDS on Binance (+2.67 pts)            -> real, and not monetisable alone
                       IS_VOLATILITY on Polymarket
Conditional direction  NOT PREDICTABLE (AUC 0.498)            -> the gate has nothing to gate
```

The Binance 15-minute lane is closed on this evidence. The movement gate is a genuine
measurement and it has no profitable use without a direction signal that does not exist here.

What is **not** ruled out, and would need its own preregistration:

- **A different horizon.** 15 bars was chosen to match the Polymarket round; direction may exist
  at 60–240 minutes, where the earlier opportunity work measured +19/+30 bps exit-timing ceilings.
- **Different information.** This used the frozen 23-feature set. Book depth and sequenced L2 are
  absent from the archive and are the obvious untested inputs — the same gap that blocks SCALP.
- **Path management rather than entry.** Every result in this repository that showed a large
  measured ceiling was about *exit timing* on positions already open, not about entry direction.
