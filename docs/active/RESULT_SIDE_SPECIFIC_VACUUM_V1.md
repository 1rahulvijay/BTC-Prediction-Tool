# RESULT — SIDE_SPECIFIC_VACUUM_V1

Protocol `PREREG_SIDE_SPECIFIC_VACUUM_V1.md`, sha256 `2df7042f95bcb38d50083d695907e8e8e71743d086a7f2b5eff0f0ee6d50e4b9`,
frozen 2026-08-04 **before any event was counted**. Scored **once**, 2026-08-04.

Data: 6 days of Bybit `ob200` (2026-07-28 .. 2026-08-02), **5,157,096** grid rows at 100 ms,
median mid **$63,523**. 22 + 24 selftest checks, **9/9 mutations caught**.

## VERDICT: `SIDE_SPECIFIC_BUT_SUB_COST`

Side-specific depth withdrawal **does** predict direction. The effect is real, survives a null
floor, is robust across bands and windows — and lives **entirely below the cost of trading it**.

---

## 1. Primary cell — as frozen

Band 5 bps, window 5 s, horizon 30 s, `VACUUM_QUIET`, threshold **$70** (= 11 bps round-trip
taker, the declared cost-clearing move).

```
P(down >= $70 | BID vacuum)  0.0292     | ASK vacuum  0.0196     gap +0.0096
P(up   >= $70 | ASK vacuum)  0.0269     | BID vacuum  0.0266     gap +0.0003

ASYM  +0.0049     day-block 95% CI [-0.0082, +0.0155]     6 blocks
null floor        mean -0.0001          95% band [-0.0075, +0.0068]
```

**The interval includes zero.** At the size that pays for a trade, the directional effect is not
distinguishable from noise on this archive.

The null floor is centred at −0.0001 against a declared tolerance of ±0.02, so the estimator is
unbiased and the study is **not** void. Episodes: 789 BID / 818 ASK, both above the 200 floor.

---

## 2. The effect is real — and it decays faster than cost rises

ASYM by threshold × horizon (secondary; no verdict attaches):

| threshold | 5 s | 15 s | 30 s | | |
|---|---|---|---|---|---|
| $10 | +0.1139 | +0.1111 | **+0.1171** | 1.6 bps | below cost |
| $25 | +0.0169 | +0.0311 | +0.0567 | 3.9 bps | below cost |
| $50 | +0.0019 | +0.0074 | +0.0100 | 7.9 bps | below cost |
| **$70** | −0.0012 | +0.0061 | **+0.0049** | 11.0 bps | **clears cost** |
| $100 | −0.0000 | +0.0012 | +0.0018 | 15.7 bps | clears cost |

At $10 the asymmetry is **+0.117** — large, and its day-block CI excludes zero. By $70 it is
+0.005 and indistinguishable from noise. The signal falls ~24x between $10 and $70 while the cost
of acting stays fixed.

**The effect does not grow with horizon at $10** (0.114 / 0.111 / 0.117). It is fully expressed
within 5 seconds and then stops. That is the decisive detail: a vacuum marks an *immediate small
repricing*, not the onset of a move that could be held into profitability. There is no horizon at
which waiting converts this into a tradeable move.

---

## 3. The reverse-causality guard mattered — in the opposite direction to my prediction

I built `VACUUM_QUIET` (|dmid| <= $5 across the lookback) expecting the naive `VACUUM_ANY`
definition to be *inflated* by depth that collapsed because price had already moved through it.

Measured, `VACUUM_ANY` is **weaker**, not stronger:

| threshold | QUIET | ANY | difference |
|---|---|---|---|
| $10 | **+0.1171** | +0.0524 | −0.0648 |
| $25 | +0.0567 | +0.0245 | −0.0322 |
| $70 | +0.0049 | +0.0039 | −0.0011 |

```
episodes    QUIET  789 bid / 818 ask        ANY  6,289 bid / 6,339 ask
```

The naive definition **understates the effect by 55%** at $10. Episodes where price already moved
during the window dilute the signal — that information is priced by the time the vacuum is
observed. The prior study used the unsigned, unguarded definition; both choices cost it signal.

The guard was still necessary: without it the estimate is wrong, just wrong in the safer
direction. And the clipping diagnostic confirms the measurement is not manufacturing events —
admitted BID events had mean drift **+$0.33** (price slightly *rising*), only 42.0% falling. A
clipping artifact would have skewed them toward falling price. Max |dmid| among admitted events
was $5.00, at most a 16% band reduction against a 50% collapse rule.

---

## 4. What the vacuum *does* predict: volatility, strongly, at tradeable size

Unsigned hazard, baseline scored with the **identical** max-excursion statistic on 30,000 random
timestamps:

| threshold | after a vacuum | unconditional | lift |
|---|---|---|---|
| $10 | 0.7897 | 0.5566 | 1.4x |
| $25 | 0.4070 | 0.2003 | 2.0x |
| $50 | 0.1276 | 0.0426 | 3.0x |
| **$70** | **0.0510** | **0.0152** | **3.3x** |
| $100 | 0.0205 | 0.0044 | 4.7x |

**The lift grows with threshold** — the opposite shape to the directional effect. A vacuum triples
the chance of a cost-clearing move and nearly quintuples the chance of a $100 move; it just does
not say which way.

> A first version of this table compared max excursion after a vacuum against a *close-to-close*
> move unconditionally, and reported a 6.1x lift at $70. Max excursion is >= |close−close| by
> construction, so most of that was the comparison, not the market. Corrected to 3.3x.

This is the usable result, and it is a **risk** signal, not an entry signal: a 3.3x rise in the
probability of an adverse cost-clearing move is directly a cancel/refuse condition for passive
quoting — which is where the maker studies already showed value was being lost.

---

## 5. The liquidity was withdrawn, not consumed

**1,455 of 1,607 episodes (90.5%)** had aggressive traded volume inside the band worth less than
20% of the depth lost. These are genuine cancellations, not the book being bought or sold through.
That is what makes the events informative rather than an echo of flow that has already printed.

---

## 6. Robustness

| band | ASYM @ $70 | ASYM @ $10 | episodes (bid/ask) |
|---|---|---|---|
| 1 bps | +0.0012 | +0.0862 | 12,437 / 12,541 |
| 2 bps | +0.0025 | +0.1497 | 9,580 / 9,704 |
| **5 bps** | **+0.0049** | **+0.1171** | 789 / 818 |
| 10 bps | +0.0015 | +0.0823 | 688 / 672 |
| 20 bps | +0.0015 | +0.0817 | 687 / 672 |

| window | ASYM @ $70 | ASYM @ $10 | episodes |
|---|---|---|---|
| 1 s | +0.0131 | +0.1012 | 750 / 854 |
| **5 s** | **+0.0049** | **+0.1171** | 789 / 818 |
| 15 s | +0.0013 | +0.1035 | 617 / 682 |

The $10 effect is present in **every** cell (+0.08 to +0.15). The $70 effect is near zero in
**every** cell. The conclusion does not depend on the primary cell's particular choices — which
is the point of declaring them in advance and sweeping afterwards.

---

## 7. Declared limitations, restated

- **6 days is not "several weeks".** The operator's own testing standard asks for several weeks;
  this archive holds six days. A day-block bootstrap on 6 blocks is weak, and this was stated in
  the protocol before any result was seen. Where the primary CI excludes zero it would be
  *suggestive*, not established — and it does not exclude zero, so the point is moot for the
  verdict but not for the $10 secondary.
- **Bybit only.** Tick size, fee schedule, participant mix and matching rules differ on Binance
  and Polymarket. Nothing here transfers without its own forward test.
- **Max excursion inflates absolute hazards.** Tolerated only because ASYM differences two
  probabilities computed identically, so the inflation cancels. No absolute hazard carries a
  verdict.
- **Band-outside-cache refusals: 965 of 10,314,192 depth queries (0.009%)** — fail-closed, never
  truncated to a short count that would read as a vacuum.

---

## 8. What this changes for the build sequence

Idea #1 (`SIDE_SPECIFIC_VACUUM_AND_SIGNED_BURST_V1`) is **answered**: signed vacuum prediction
exists and is not tradeable directionally on this venue at these costs.

The result redirects rather than closes. The strong, threshold-*increasing* unsigned lift is the
input the **toxicity/cancel** ideas (#9, #12) actually need, and those are about *avoiding* a
loss rather than capturing a move — a bar this measurement clears where the directional bar it was
built for is missed by roughly 24x.

The reusable infrastructure now exists: `research/bybit_l2/grid.py` caches a 100 ms
book-reconstruction (410 MB for 6 days, ~65 s/day to build) with fixed-price band queries, so
ideas #2, #3, #6, #12, #13 no longer need to re-parse the archive.

---

## 9. Registration

```
docs/active/PREREG_SIDE_SPECIFIC_VACUUM_V1.md    sealed, 25/25 hashes intact
research/side_specific_vacuum_v1.py              run_all_sequence FRONTIER (frontier=32)
research/side_specific_vacuum_v1_run.py          NON_STUDY (scoring half, invoked by the study)
research/bybit_l2/grid.py                        shared cache builder
.github/workflows/invariants.yml                 both selftests wired
FIRST_SCORING                                    pins ASYM +0.0049; a rerun that moves it exits
                                                 non-zero
model_registry                                   NOT registered - no artifact, no authority
```
