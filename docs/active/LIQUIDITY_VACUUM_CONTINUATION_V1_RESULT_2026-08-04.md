# RESULT — LIQUIDITY_VACUUM_CONTINUATION_V1

**Scored `2026-08-04`. Verdict: FAIL.** Scored once, per the frozen protocol
(`PREREG_LIQUIDITY_VACUUM_CONTINUATION_V1.md`, sha256 `8a81a2ee…`).

## The number that mattered did not replicate

| | exploratory (2026-08-02) | 5 preregistered test days |
|---|---:|---:|
| P(continue \| vacuum, replenished) | **80.89%** | 50.68% – 58.26% |
| lift over same-day baseline | **+30.85 pp** | **+2.91 pp** point |
| day-block LB95 on lift | — | **+1.46 pp** |
| gate | — | LB95 > +15 pp → **FAIL** |

```text
2026-07-28  vacuum-episodes 15,223  P(cont|vac) 51.84%  base 49.49%  lift +2.35%
2026-07-29  vacuum-episodes 17,886  P(cont|vac) 52.26%  base 50.90%  lift +1.36%
2026-07-30  vacuum-episodes 13,680  P(cont|vac) 50.68%  base 49.42%  lift +1.26%
2026-07-31  vacuum-episodes 13,370  P(cont|vac) 51.71%  base 50.06%  lift +1.64%
2026-08-01  vacuum-episodes  4,581  P(cont|vac) 58.26%  base 50.35%  lift +7.91%
```

The effect is **real and roughly one tenth** the discovered size. Both structural gates passed —
5 days, largest day 27.6% of 64,740 episodes — so this is a failure of the effect, not of coverage.

## What was wrong on the discovery day

The exploratory pass counted anchors where `move_during == 0`. The sign of
`move_during × move_after` is undefined there, and including them inflated the conditional arm.
The frozen protocol excluded them explicitly — *"sign undefined, not a coin flip"* — and that
exclusion appears to account for most of the collapse.

**A correction to what was claimed on 2026-08-03.** Two checks were run before publishing 80.89%:
the day's efficiency ratio (0.0176, near-pure chop, so no trend to borrow) and the unconditional
baseline (50.04%, a clean coin flip). Both were correct, and **both were insufficient.** A
validated baseline does not validate the conditional arm — the defect was in how vacuum episodes
were selected, and only a preregistered replication on unseen days could surface it. The 80.89%
was a measurement error, not a regime difference.

## Materiality, as declared in advance

The protocol predicted this result would be *"statistically positive and economically
insufficient."* Measured:

```text
median |move| after vacuum      $11.10   (1.76 bps)
P(|move| >= $10 in 30s)         51.47%   (1.6 bps)
P(|move| >= $25 in 30s)         19.24%   (4.0 bps)
P(|move| >= $50 in 30s)          4.30%   (7.9 bps)

Bybit maker round trip          ~2.0 bps
measured passive adverse sel.   -0.56 bps  (BYBIT_L2_MAKER_V2_TRADE_DRIVEN)
```

Even the 2.91 pp of genuine lift sits inside the spread. Writing the expectation down first is what
stops *"51% chance of a $10 move"* reading like an opportunity.

## Status

`LIQUIDITY_VACUUM_CONTINUATION_V1` is **answered, negative, and spent.** Per the protocol: no
threshold search, no horizon search, no dropping the +7.91% day. Any variation requires a new
protocol name and a new hash.

Machine result: `data/research/LIQUIDITY_VACUUM_CONTINUATION_V1.json`
Scorer: `research/score_liquidity_vacuum_continuation_v1.py`

## Unaffected

The crossing heads (AUC 0.6715 vs 0.5196 clock) come from a different pipeline and are untouched
by this. But the standing audit finding that `reverted_within_30s` actually measures
`state_original_side_at_30s` is now more pressing, not less — it is the same shape of defect
(a label that does not mean what its name says) in the one family that has worked.
