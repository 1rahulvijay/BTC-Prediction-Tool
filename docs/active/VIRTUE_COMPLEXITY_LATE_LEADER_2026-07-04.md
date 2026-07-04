# Virtue of Complexity — Late-Leader Fair Value (2026-07-04)

Kelly/Malamud/Zhou recipe (many weak features + ridge) on the ONE ask-priced learnable target: leader mispricing `y = win − ask` at the 30s checkpoint. Last 30 days of kachoio executable quotes, n=3,568 rounds, purged day-wise walk-forward (5 tested folds). Trade rule for every model: buy leader iff pred > taker_fee + 1c. Metric: fee-adjusted EV/share at the ACTUAL ask.

| model | trades | win @ ask | EV/share | EV (LB) |
|---|---|---|---|---|
| M0 market (all leaders) | 2,873 | 84.2% @ 80.2c | **+3.05c** | +1.55c |
| M1 simple(3f) (+folds 4/5) | 2,140 | 83.7% @ 80.0c | **+2.78c** | +1.03c |
| M2 base(~23f) (+folds 4/5) | 2,151 | 83.7% @ 80.1c | **+2.64c** | +0.89c |
| M3 poly(~300f) (+folds 5/5) | 1,985 | 83.7% @ 79.3c | **+3.43c** | +1.63c |
| M4 rff(1500f) (+folds 5/5) | 2,272 | 84.4% @ 80.8c | **+2.66c** | +0.99c |

## Nulls
| model | shuffled-label trades | EV |
|---|---|---|
| M1 simple(3f) | 2102 | +2.54c |
| M2 base(~23f) | 2137 | +2.55c |
| M3 poly(~300f) | 2015 | +3.41c |
| M4 rff(1500f) | 1715 | +2.79c |

## Verdict (pre-declared bar: complex ≥ simple +0.5c EV, positive LB, ≥4/6 folds)
The automated bar read "complexity wins" (M3 poly +3.43c vs M1 +2.78c) — **but the mandatory null
OVERRIDES it: models trained on SHUFFLED labels trade at the SAME EV (M3 null +3.41c vs real +3.43c;
every model's null ≈ its real result), and the no-model baseline M0 (+3.05c) beats most models.**

**FINAL: NO MODEL — simple or complex — adds information beyond buying every leader.** The mechanism
is exact and instructive: with a mispricing target `y = win − ask` whose MEAN is ~+3c, ridge under
shuffled labels predicts ≈ the constant mean → the gate (`pred > fee + 1c`) fires on most rounds →
collects the same baseline edge. The entire late-window "edge" is the **intercept** — a constant
market-wide underpricing of the leader in the final seconds — not a conditionally predictable,
round-by-round mispricing. (Ironic echo of the paper's own critique literature: the result lives in
the intercept handling.)

This is the THIRD independent confirmation that **the ask is the sufficient statistic**: BTC-state
gates failed the shuffled-gate nulls (gated-lift test), setup-quality filters selected into pricier
asks, and now a 1,500-feature complexity wall finds nothing conditional either. The alive edge
remains exactly what `LATE_LEADER_30S_V1` already trades: buy the leader late, at a sane ask, every
qualifying round. Secondary value: M0 on these last-30-days = **+3.05c EV (LB +1.55c)** — a fresh
out-of-window re-confirmation of the frozen rule's basis. **No head is built from this test.**

## Operator ruling (2026-07-04, accepted)

**The late-leader edge is a STRUCTURAL INTERCEPT EDGE, not conditional alpha.** The market slightly
underprices the current leader on average in the final ~30s; no feature set reliably ranks which
specific rounds are better. Four independent confirmations now agree:
1. Champion/BTC-state gates did not improve late-leader EV (shuffled-gate nulls, p 0.74–0.97).
2. P(Hold)/setup filters proved no independent edge (they select into pricier asks).
3. Early-window strategies all failed (the book is efficient until the final minute).
4. The complexity wall (ridge/poly/RFF, 1,500 features) found no lift over baseline/null.

**Consequences — frozen:**
- No complexity head is built. No fair-value model gates the rule. `LATE_LEADER_30S_V1` stays
  small, dumb, frozen, and hard to overfit — exactly the right shape before live validation.
- The app's job is OPERATIONAL DISCIPLINE, not cleverness: is it 5m · final ~30s · current leader ·
  ask ≥ 0.60 · executable · enough depth · latency OK · settlement join working · one entry only.
- Complexity is reserved for the recorder-gated EXECUTION targets (edge-duration, fillability,
  depth-disappearance, ask-runaway, VWAP slippage, book toxicity, exitability, maker-fill) — those
  are mechanics, not priced into the ask the same way.
- Next and only proof that matters: live recorder replication toward the n≥500 gate.

## Honest limits
- Trade prices are real executable asks, but top-of-book, 1s cadence, no latency model.
- 30 days ≈ one regime slice; the paper's virtue claims are themselves contested (zero-intercept / aggregation critiques) — this is a bounded test, not a doctrine.
- The shuffled-label null must be ≈0/absent; if it trades profitably, the gate itself (pred > fee+buffer on a mispricing target) is selecting on ask level → distrust everything above.
- Not wired anywhere. PAPER research only.