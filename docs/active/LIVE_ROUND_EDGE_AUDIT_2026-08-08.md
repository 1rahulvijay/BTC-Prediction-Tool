# Live round edge audit — the venue's own question, on recorded rounds — `2026-08-08`

Every accuracy number elsewhere in this repository is graded under the *training* contract,
so reading it requires knowing which question it answers. `price_to_beat` is different: it
is the **venue's** question, recorded live. Anchor at the window open, settle at the window
close, UP if the end is at or above the anchor. Nothing to grade, nothing to interpret —
either the lean was on the winning side or it was not.

**Result: no edge, at either horizon, on 3,180 live rounds over 22 days.**

```text
                       n      win     95% CI              p(shuffle >= obs)
5m   all leans      2,404   0.4933   [0.4734, 0.5133]          0.818
15m  all leans        776   0.4858   [0.4508, 0.5210]          0.749

break-even after a 2c round trip on a ~50c binary:  0.5200
clears costs:  NO at both horizons — the lower bound is far below it
```

Both intervals straddle 0.50 and both point estimates sit *below* it. A permutation null
that shuffles the leans against the outcomes reaches the observed rate **82%** of the time at
5m. This is a coin flip, and after costs it is a coin flip that loses.

`research/live_round_edge_audit.py` — read-only, standalone, exits non-zero only on a data
problem. A study that fails when the answer is unfavourable is not a study.

---

## The `lean_source` split is refuted

`price_to_beat.py` carries this as measured evidence, and the field exists because of it:

> *EVIDENCE (DuckDB 2026-06-10, 9.6h): the model's committed 3-class leans win ~64% at 5m,
> but the two-way probability FALLBACK leans are ~coin-flip — mixing them dragged the mirror
> from 58.7% to 51.5%. Track separately so the betting guidance can say "bet model leans,
> skip fallback leans".*

On 22 days instead of 9.6 hours:

```text
5m   model     n=1,228   win 0.4935   95% CI [0.4656, 0.5214]
5m   fallback  n=1,176   win 0.4932   95% CI [0.4647, 0.5217]
15m  model     n=  561   win 0.4848   95% CI [0.4437, 0.5262]
15m  fallback  n=  215   win 0.4884   95% CI [0.4223, 0.5548]
```

**Indistinguishable — three thousandths apart at 5m.** The 64% was a 9.6-hour sample. The
guidance it produced ("bet model leans, skip fallback leans") has no support and must not be
acted on.

The field is **kept**. It costs nothing, it is real provenance, and separating the two is
exactly what made this refutation measurable. The claim attached to it is annotated in place
rather than deleted, so the next reader sees both the original measurement and what a larger
sample did to it.

---

## The leans are biased where the market is not

```text
5m    leans UP 68.8% of the time;  the market settles UP 50.3% of the time
15m   leans UP 56.8% of the time;  the market settles UP 47.4% of the time
```

A bettor with that bias and **zero information** scores 0.5011 at 5m and 0.4965 at 15m by
arithmetic alone. Observed:

```text
5m    0.4933   =  bias-only 0.5011  -0.0078
15m   0.4858   =  bias-only 0.4965  -0.0107
```

So the part attributable to information is **negative at both horizons**. The leans do
slightly *worse* than their own directional bias would achieve knowing nothing. Not
significantly worse — the intervals are wide — but there is no positive information
component to find.

The 68.8% UP bias against a 50.3% UP market is the more actionable observation. It is a
property of the served lean, not of the market, and it is large.

---

## The bias does not respond to the trend — which is the diagnostic

Split the 5m rounds by the regime label the app itself assigned:

```text
                    n     UP-lean   market UP    win
UNKNOWN            880     54.7%      51.5%     0.4977
TRENDING_UP        454     78.0%      46.7%     0.4670
TRENDING_DOWN      429     77.4%      51.7%     0.4685
RANGE              385     78.4%      48.8%     0.5325
HIGH_VOLATILITY    152     63.8%      55.9%     0.5132
LOW_VOLATILITY     104     85.6%      47.1%     0.5000
```

**The lean is UP 78.0% of the time in an uptrend and 77.4% of the time in a downtrend — a
gap of 0.6 points.** A lean that tracked the trend would differ sharply between those two
cells. It does not differ at all.

That localises the finding. The UP bias is not trend-following behaviour and it is not a
response to the market; it is a property of the head, present at essentially the same
strength no matter which way the market is labelled as moving. Candidate causes, in order of
how cheaply they can be checked: the three-class prior at 5m (`DOWN 0.269 / NEUTRAL 0.460 /
UP 0.270` on the train slice — near symmetric, so probably *not* this), the two-way
renormalisation that produces `fallback` leans, or a feature-pipeline asymmetry.

The two trend regimes are also the two **worst** cells (0.467, 0.469), and RANGE is the best
(0.533, n=385). That is directionally consistent with the older regime finding that
RANGE/LOW_VOLATILITY are the only cells above a coin flip, though at 53.3% rather than the
59% recorded there.

`UNKNOWN` is 37% of rows and is much less biased (54.7%) — the bias concentrates in exactly
the rows where a regime *was* labelled.

---

## Stability

```text
5m    first half 0.4975   second half 0.4892
15m   first half 0.4897   second half 0.4820
```

Flat and slightly declining. There is no era in this window where it worked.

---

## What this does and does not say

It says: **the served directional lean has no measurable edge on the venue's own question.**
That is the question a Polymarket bet actually settles, so this is the relevant test for
tradeability, and it is consistent with everything else measured here — the model bakeoff
ceiling (13 families, all 0.50–0.535 AUC), the direction-dead result across 17 microstructure
features, and §4.5's finding that the Polymarket ask beats both model vintages on Brier, log
loss, ECE and AUC.

It does **not** say the app is broken. It says the directional head is what the ceiling work
already concluded it was, now confirmed on live recorded rounds under the venue's own rule
rather than a training contract.

It also does not test the paths that were never directional: `big_move` timing (robust at
0.56–0.62), the P(hold) keeper family, or the late-leader rule frozen in
`KAGGLE_SOURCES_AND_FROZEN_RULES_2026-07-02.md`. Those answer different questions and are
untouched by this.

---

## Next, in order of decisiveness

1. **Φ(z) vs the recorded Polymarket ask.** Still the only comparison that decides
   tradeability, and still unrun. This audit shows the lean cannot win on direction alone;
   the remaining hope is a *mispricing* against the book, not a better direction.
2. **The UP bias, now localised.** It is 78% in both trend directions, so it is not a market
   response. The train-slice priors are near symmetric, which argues against a class-prior
   cause and points at the two-way renormalisation or the feature pipeline. Cheap to check
   next: compare the UP share of `rawDirection` against the UP share of the `probUp >= probDown`
   fallback on the same rounds.
3. **Re-run this audit per model bundle.** These 22 days span more than one release; the
   pooled number could hide a release that differed. The rows carry timestamps, and
   `fetch_price_to_beat_history` already has the era-filter machinery.
