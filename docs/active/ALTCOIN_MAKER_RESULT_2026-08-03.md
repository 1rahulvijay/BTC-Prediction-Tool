# Altcoin maker execution — does a wider spread pay?

**Protocol** `PREREG_ALTCOIN_MAKER_EXECUTION_V1.md` sha256 `a6ce76dc…`, frozen before any result ·
**Script** `research/altcoin_maker_execution_v1.py` · Scored **once**

```
day 2024-03-28, four symbols, same day    order size = 10% of the median visible best level
method reused UNMODIFIED from the sealed BINANCE_MAKER_EXECUTION_V1
BTCUSDT included as the WITHIN-ERA control
```

## Result

| symbol | median spread | fill% | gross IMMEDIATE | gross OPERATIONAL | adverse selection | net / submitted | hour-block 95% CI |
|---|---:|---:|---:|---:|---:|---:|---|
| BTCUSDT | 0.014 | 72.2% | +0.335 | −0.796 | 1.131 | −1.296 | [−1.549, −1.004] |
| AVAXUSDT | 0.184 | 72.6% | +0.251 | −1.300 | 1.551 | −1.670 | [−2.131, −1.198] |
| LINKUSDT | 0.518 | 70.2% | +0.596 | −1.391 | 1.986 | −1.678 | [−2.024, −1.280] |
| XRPUSDT | 1.606 | 66.9% | +0.896 | −1.484 | 2.380 | −1.663 | [−2.007, −1.299] |

**All four: not viable.** Every interval lies entirely **below** zero.

## The finding: adverse selection scales with spread, r = +0.916

```
correlation(median spread, adverse selection) = +0.916 across four symbols
```

Wider spread buys more adverse selection, and the two very nearly cancel:

```
symbol    spread    adverse   adverse / half-spread
BTC        0.014     1.131         160x
AVAX       0.184     1.551          17x
LINK       0.518     1.986         7.7x
XRP        1.606     2.380         3.0x
```

Two things are true at once, and both matter:

**Relatively, wider is much better.** BTC loses 160× its half-spread to adverse selection; XRP
loses only 3×. Spread grows **115-fold** from BTC to XRP while adverse selection grows only
**2.1-fold** — strongly sub-linear. The wider-spread hypothesis was directionally right.

**Absolutely, it changes nothing.** Net value per submitted order is flat across all four
(−1.30 to −1.68 bps) and mildly *worse* on the altcoins. The extra spread captured is handed
straight back, plus a little more.

The ratio never falls below 1. Even on the widest-spread instrument tested, adverse selection is
**three times** the spread available to capture. Passive execution on this venue does not become
viable by moving down the liquidity curve — it becomes *less bad in relative terms* while
staying negative in absolute ones.

## The era control worked

`BTCUSDT` on the same 2024 day versus the 2026 measurement:

```
                    2024      2026
median spread       0.014     0.020 bps
adverse selection   1.131     1.526 bps
```

Same order of magnitude on both. The two-year gap did not distort the mechanism, so the altcoin
comparison is not an era artifact. This is the specific thing the control was included to check,
and it is the reason the result can be read as an instrument effect.

## A wording imprecision in my own output, corrected

The script prints *"CI does not exclude zero"* for the `MAKER_SAVES_BUT_NOT_ENOUGH` verdict.
That is wrong here, and it understates the result: every interval **does** exclude zero — from
**below**. All four instruments are definitively unprofitable, not merely inconclusive.

The verdict label is correct (not viable); the explanatory sentence is imprecise, because the
branch was written for the "spans zero" case and these results are stronger than that. Recorded
here rather than silently corrected, since the numbers are already published.

## What this closes

`BINANCE_MAKER_EXECUTION_V1` closed BTCUSDT and named its own limit: BTC is the tightest-spread
instrument on the venue, so wider-spread altcoins deserved a test. They have now had one.

```
gross edge available        +0.97 to +1.97 bps      (MULTIHORIZON, measured)
taker round trip                   14.0  bps
maker round trip                    2.0  bps        (fees only, best case)
adverse selection, BTC              1.13 - 1.53 bps
adverse selection, altcoins         1.55 - 2.38 bps
```

Passive execution is closed as a route on Binance perpetuals, per the protocol's kill rule.
Adverse selection scaled with spread across all four symbols, and wider spreads bought no net
improvement.

The remaining hypothesis is a venue with structurally wider spreads **and** less informed flow.
Binance perpetuals are not that venue, and neither, on the evidence in this repository, is
Polymarket — where the taker cost floor is ~149 bps and only 0.1% of 15-minute windows move far
enough to clear it.

## Limits

- **One day, and a day two years old.** Binance discontinued the public `bookTicker` archive on
  2024-03-30, so this is the most recent real altcoin book available. It sizes a mechanism; it
  is not a forward claim.
- Fill rates here (67–73%) are not comparable to the 48% in the BTC-2026 run: order size is 10%
  of the visible level here versus a fixed 0.01 BTC there. Adverse selection *is* comparable,
  which is why the era check uses that and not fill rate.
- Four symbols is enough to see a strong monotone relationship and not enough to fit anything
  to it. The +0.916 correlation is descriptive.

## Governance

- Protocol frozen and hashed before any result; 20/20 hashes verify in CI.
- `simulate()` is **imported from the sealed BTC protocol**, not reimplemented — the selftest
  asserts `simulate.__module__ == "maker_execution_v1"`, so a divergence in method cannot be
  mistaken for a difference in instrument.
- All four symbols were declared in advance and all four are reported.
- Order size is a **fraction of the visible level**, because 0.01 BTC and 0.01 XRP are not
  comparable quantities.
- `IMMEDIATE` is a hindsight ceiling throughout and is never reported as achievable.
- Selftest 10 checks, including that `MAKER_VIABLE_ON_THIS_INSTRUMENT` is reachable — so the
  negative result is not an artifact of an unreachable branch.
