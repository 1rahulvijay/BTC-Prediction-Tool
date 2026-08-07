# Kaggle Polymarket L2 archive — inventory before modelling — `2026-08-07`

Table inventory, timestamp semantics and coverage matrix, read from the archive's **own**
validation reports. No model, no backtest — deliberately, because the coverage numbers change
what is worth building.

Read without extracting: 39.7 GiB, 73 entries, streamed from
`polymarket_data/archive.zip` (gitignored).

---

## Structure

```text
orderbook/     21 files   34.87 GiB   raw YES-side price-change ticks
snapshots/     21 files    3.84 GiB   raw full L2 book snapshots (JSON)
features_v3/   21 files    1.54 GiB   corrected daily 1-minute features
labels/         2 files    0.05 GiB   trades.parquet, market_targets.parquet
features/       1 file     0.03 GiB   ml_features_1m_v2.parquet  (DEPRECATED)
code/           3 files                builder + auditor + requirements
```

`validation_report_v3.json` self-audits: **21/21 partitions, 0 missing dates, 0 unexpected
dates, 0 market-outcome conflicts, 270,998,156 rows**, per-file sha256. The archive validates
itself — that is a good sign and it is not the same as being fit for our purpose.

---

## The coverage matrix — the reason this document exists

| | share of the 271M rows |
|---|---:|
| rows with **depth** (L2) | **2.89%** |
| rows with **trades** | **0.242%** |
| rows with a market outcome | 13.21% |
| rows with an exact 15m target | 73.7% |

Per-day depth coverage ranges 2.10% – 4.90%; trades 0.032% – 0.596%.

**The L2 depth — the entire reason this archive was interesting — is present on roughly 3 of
every 100 market-minutes.** The headline "5 billion ticks" is real, and the *depth panel* is
thin. Ninety-seven percent of rows have a mid price and no book behind it.

Trades are rarer still: 0.242%. Any VPIN, aggressor-CVD or trade-intensity feature would be
computed on a quarter of one percent of rows and forward-filled across the rest — which is how
a feature that looks alive gets built on almost nothing.

## Scale is not sample

```text
24,465 - 29,449 markets per day   -- ALL Polymarket markets, every category
```

These are not BTC rounds. BTC Up/Down 5m/15m is a small subset that still has to be located,
and the effective independent sample is **rounds and days**, not the 271M rows. 21 days is 21
regime observations.

## Two timestamp semantics worth stating before anyone builds on them

From the archive's own report:

```text
target_15m := close_mid at exact t+15 OBSERVED minute > close_mid at t
return_1m  := close_mid at t / close_mid at exact t-1 OBSERVED minute - 1
```

Both are defined on **observed** minutes, and the data is event-driven — the README says
plainly that *"individual markets therefore do not form a complete minute-by-minute panel"*.
The v3 builder requires the **exact** minute (73.7% of rows qualify), so it does not silently
stretch a horizon. That is correct, and it is exactly the failure the deprecated v2 file has:

> *"Its `return_1m` uses the previous available observation, which can span more than one clock
> minute when bars are missing."*

`ml_features_1m_v2.parquet` is **deprecated by the author**: 4,710 markets, only 2026-03-06 to
03-11, and its `target` column is the **final market outcome**, not a 15-minute forward label.
It is the same defect class this repository has spent five audits removing — a column whose name
does not match the question it answers. **Do not train on it.**

## Other facts that matter

- **Window is 2026-03-06 → 2026-03-26.** Five months stale relative to today. Fine for
  microstructure research, useless as forward evidence.
- **YES-side only**, confirmed by the README's own first line. `NO_ask = 1 − YES_bid` holds
  economically and not for executable depth, so any execution work must be labelled
  `YES_BOOK_ONLY` and must refuse to claim NO-side fill evidence.
- **93 crossed-book rows** across all partitions — small, and they exist. A book where bid ≥ ask
  is not tradeable and must be dropped, not winsorised.
- **Licence CC BY-NC 4.0** — non-commercial research only.

---

## What this inventory changes

**Before the numbers**, the proposed lane was ten heads including queue position, fill
probability and adverse selection.

**After the numbers**, most of that is not supportable by this archive:

| proposed | verdict on this data |
|---|---|
| depth imbalance / slope / walls / depletion | **2.89% coverage.** Buildable as an *event-conditioned* feature; not as a panel. Any minute-bar model would be ~97% imputed |
| VPIN, aggressor CVD, trade intensity | **0.242% coverage.** Not supportable |
| queue position, fill probability | needs order-level data; L2 snapshots cannot express queue |
| NO-side execution realism | YES book only |
| settlement residual alpha vs market price | **supportable** — mid price is dense, outcomes exist for 13.2% of rows, and the market-implied baseline is the right hurdle |
| Binance→Polymarket repricing lag | **supportable in principle**, but needs a synchronised Binance join and the 21-day window bounds it to discovery |

**The one thing I would build first** is the least glamorous: locate the BTC 5m/15m markets in
`labels/market_targets.parquet`, count the actual rounds, and measure depth coverage *on those
rounds specifically*. Every number above is across all 24k+ daily markets; BTC rounds could be
far better covered or far worse, and nothing should be designed until that is known.

Doing it the other way round — building ten heads and then discovering the depth panel is 3%
— is precisely how this repository accumulated the backlog it has spent five audits clearing.

---

# BTC rounds located, and depth measured on them - `2026-08-07`

The follow-up the inventory called for. Every number below is measured, not estimated.

## The rounds exist, and there are enough of them

`labels/market_targets.parquet` holds 123,895 markets, 8,495 mentioning bitcoin/BTC. The Up/Down
series is identifiable by question text (`"Bitcoin Up or Down - March 16, 8:05PM-8:10PM ET"`),
and the window is parseable from the title:

```text
Bitcoin Up or Down markets   6,418
   5-minute rounds           4,461
  15-minute rounds           1,483
 240-minute rounds              93
  unparsed titles             381
```

5,944 usable 5m/15m rounds over 21 days. As a *count*, adequate for LightGBM-scale work.

## Depth on BTC rounds is ~3x the archive average - and still thin

| | 2026-03-12 | 2026-03-19 |
|---|---:|---:|
| BTC 5m/15m rows | 106,762 | 12,624 |
| share of that day's rows | 0.765% | 0.130% |
| **rows with depth (BTC rounds)** | **7.64%** | **7.72%** |
| rows with depth (all markets) | 2.60% | 4.48% |

Split by round length on 2026-03-12:

```text
 5m rounds   576 markets    6.73% of rows have depth   99.8% of markets have >=1 depth row
15m rounds   193 markets   10.34% of rows have depth   96.9% of markets have >=1 depth row
```

Better than feared, still sparse: ~92% of BTC market-minutes carry a mid price with no book
behind it. Nearly every round has *some* depth observation, which makes event-conditioned
features viable and a minute-by-minute depth panel not.

## Two findings that matter more than the depth number

### 1. There are no settlement outcomes for BTC rounds. None.

```text
target over ALL markets        None 100,749   |   0: 17,177   |   1: 5,969
target over BITCOIN UP/DOWN    None   6,418   |   0:      0   |   1:     0
market_outcome in features_v3  0.00% of BTC rows, on both days sampled
```

23,146 other markets carry a resolved `target`. **Every one of the 6,418 BTC Up/Down markets is
NULL** - despite `uma_status = "resolved"` on 6,247 of them.

The supervision signal for the exact markets this repository cares about is **absent from the
archive**. A settlement-residual model cannot be trained on it as published. The label would
have to be reconstructed per round from the Polymarket API - which is the `round_truth.py` work
already in this repo, and which makes the archive's value order-book features, not labels.

### 2. There are no trades on BTC rounds

```text
trade_count non-null   100.00%
trade_count > 0          0.00%     on both 5m and 15m rounds
```

Present on every row and zero on every row. A coverage check that counts non-nulls reports 100%
and means nothing - the same "a check that passes while the property it guarantees is false"
shape this repository keeps finding, here in someone else's dataset.

Corroborated by the metadata: **5,703 of 6,418** BTC markets have zero recorded volume
(p50 = 0, p75 = 0), and **6,247** have zero liquidity.

### A detail that would have bitten later

Rows per market are ~139-140, not 5 or 15. A market's rows span far more than its own round
window, so a per-round dataset must **clip to the bounds parsed from the title**. Grouping by
`market_id` alone would silently include ~134 minutes of out-of-round observations per
5-minute round.

## Verdict

| proposed head | verdict on this data |
|---|---|
| settlement residual alpha | **BLOCKED** - no outcome labels for BTC rounds |
| any trade-flow feature (VPIN, CVD, intensity) | **DEAD** - trade_count is zero on every BTC row |
| depth imbalance / book shape, event-conditioned | **viable** - 7.6% of rows, ~99% of rounds have >=1 observation |
| minute-by-minute depth panel | **not viable** - ~92% would be imputed |
| Binance -> Polymarket repricing lag | **viable, and the strongest remaining use** - it needs the mid price (100% present) and a synchronised Binance join, not outcomes or trades |

**This is a YES-side quote-and-book dataset, not a settlement dataset.** Its BTC value is
repricing dynamics against Binance; the labels must come from elsewhere.

Measuring this cost one afternoon. Building the ten-head lane first and discovering
`target = NULL` afterwards would have cost considerably more.
