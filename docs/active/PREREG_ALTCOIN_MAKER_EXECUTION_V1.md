# PREREG — ALTCOIN_MAKER_EXECUTION_V1

**Frozen `2026-08-03`, before any altcoin maker result was computed.** Hash checked in CI.

## Admission

DIAGNOSTIC under the Phase 6 freeze, by explicit operator instruction on `2026-08-03`.

## Question

`BINANCE_MAKER_EXECUTION_V1` found passive execution on BTCUSDT perp fails twice over: the
median spread is **0.02 bps** so there is almost nothing to capture, and real fills lose
**1.53 bps** to adverse selection. Its stated limit was that BTCUSDT is the *tightest*-spread
instrument on the venue.

> On a wider-spread altcoin perpetual, is there enough spread to make passive execution pay —
> or does adverse selection scale with it?

The two outcomes are genuinely different. A wider spread offers more to capture. It also
usually means thinner books, more informed flow per unit volume, and worse markouts. Which
effect dominates is the question.

## Data, and the era problem it creates

Binance's public `bookTicker` archive **ends 2024-03-30**; the dataset was discontinued. Real
historical best-bid/ask for altcoin perpetuals therefore exists only up to that date, roughly
two years before this test is run.

```
source    data.binance.vision/data/futures/um/daily
date      2024-03-28   (a single UTC day, the same day for every symbol)
files     {SYMBOL}-bookTicker-2024-03-28.zip   best bid/ask and sizes
          {SYMBOL}-aggTrades-2024-03-28.zip    price, size, side
```

**BTCUSDT is included as a control on the same date.** Comparing a 2024 altcoin result against
the 2026 BTCUSDT result would confound instrument with era. Running BTC on the same day makes
the altcoin comparison within-era, and the BTC-2024 versus BTC-2026 comparison measures the era
effect separately.

## Symbols — all reported, none selected

```
BTCUSDT    control, tightest spread, same day
AVAXUSDT   wider-spread candidate
LINKUSDT   wider-spread candidate
XRPUSDT    wider-spread candidate
```

All four are declared now and **all four are reported** whatever they show. Choosing the best
afterwards would be a search across instruments.

## Method — unchanged from BINANCE_MAKER_EXECUTION_V1

The simulation, fill bounds, latency, order life, markouts, fee treatment and hour-block
bootstrap are **reused unmodified** from the sealed BTCUSDT protocol, so that any difference in
result is attributable to the instrument rather than to the method.

```
order         0.01 of the visible best level, one per 60s, alternating side
life          60 seconds, 250 ms submission latency
bounds        NO_FILL / IMMEDIATE (hindsight ceiling) / TOUCH / VOLUME_AHEAD / OPERATIONAL
markouts      1s / 5s / 15s / 30s / 60s
maker fee     1.0 bps, CHARGED
```

Order size is expressed as a fraction of the visible best-level size rather than a fixed coin
amount, because 0.01 BTC and 0.01 LINK are not comparable quantities. Orders whose size would
exceed the visible level are excluded rather than assumed to fill.

## Primary endpoint

**Net value per order SUBMITTED under `OPERATIONAL`**, per symbol, with an hour-block 95% CI.

## Secondary endpoints

```
median spread per symbol, in bps
fill rate per bound per symbol
adverse selection = gross(IMMEDIATE) - gross(OPERATIONAL), per symbol
correlation between median spread and adverse selection across symbols
BTCUSDT 2024 versus BTCUSDT 2026, as the era check
```

The spread-versus-adverse-selection relationship is the actual scientific content: it says
whether a wider spread is an opportunity or a warning.

## Verdicts — per symbol, declared before results

```
MAKER_VIABLE_ON_THIS_INSTRUMENT
    OPERATIONAL net per submitted order is positive with an hour-block 95% CI
    excluding zero.

MAKER_LOST_TO_ADVERSE_SELECTION
    Adverse selection >= half the median spread plus the taker-fee saving,
    or the IMMEDIATE ceiling is itself negative.

MAKER_FILL_RATE_INSUFFICIENT
    OPERATIONAL fill rate below 5%.

MAKER_SAVES_BUT_NOT_ENOUGH
    Otherwise.
```

## Kill rule

If adverse selection scales with spread across all four symbols — that is, wider spreads buy
no net improvement — then passive execution is closed as a route on perpetual venues, and the
remaining hypothesis is a venue with structurally wider spreads and less informed flow, which
is not Binance perpetuals.

## What this may not do

No threshold tuning, no alternative order sizes, no signal-conditioned entry, no symbol
selection after results, and no presentation of a single 2024 day as a forward claim. The era
gap must be stated in any summary of this result.

## Stopping rule

Scored **once**.
