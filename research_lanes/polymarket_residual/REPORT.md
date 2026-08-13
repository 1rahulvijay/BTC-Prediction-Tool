# POLYMARKET_RESIDUAL_V1

**Verdict: BLOCKED — cannot be run at all.** Not a weak result. Zero rows.

Run 2026-08-13 · `research_lanes/common/pm_data.py`

---

## Question

Do not train `X → P(UP)`. Train the residual against the market's own price:

```
logit(p_true) = logit(p_market) + f(X)
```

The market becomes the baseline, and the model must beat *it*, on Brier, log loss, and net PnL
at executable prices.

## Why it cannot run

The two datasets required do not overlap. At all.

**Polymarket snapshots** — `data/pm_export_snapshots.parquet`, 152,788 rows, 916 rounds. Every
field a residual model needs is present: `up_bid`, `up_ask`, `up_mid`, ladder depth,
`p_hold_cur`, `distance_bps`, `seconds_left`, `vol_60s_pct`, `book_age_s`. The data is good.
But it exists on **10 distinct days only**:

```
2026-06-16    4 rounds
2026-06-29   31
2026-06-30   25
2026-07-02  150
2026-07-03  177
2026-07-04  168
   <-- 35-day gap -->
2026-08-09   88
2026-08-10  136
2026-08-12   54
2026-08-13   83
```

**Official settlement** — `price_to_beat` rows with `settlement_source LIKE 'official:%'`:
6,725 rounds (5,726 `polymarket_gamma`, 999 `polymarket_clob`), covering **2026-07-05 →
2026-07-25**.

That window falls entirely inside the snapshot gap.

```
anchor_ms intersection:                 0
PM rounds inside the official window:   0
```

## What I refused to do

Derive the outcome from a Binance close instead. That is
`ROLLING_EXCHANGE_RETURN_SIGN_V1`, which `model_registry` holds at `may_price=False`,
`may_rank=False` because it uses the wrong price series and the wrong reference point for this
venue. A residual measured against a proxy answers a different question than the one that pays,
and it would have produced a confident-looking number from data that cannot support it.

The lane returns zero rows rather than a number.

## Unblock path — and it is cheap

Both halves exist; they were just never recorded at the same time. Backfill official settlement
for the **10 days where snapshots already exist**. The machinery is already in the repo —
`official:polymarket_gamma` is an established source, so this is running an existing fetcher
over a different date range, not building anything.

That would yield ~916 rounds of joined data. Small, but real, and enough for a first
round-clustered read on whether the model beats the market's own price.

Second: keep the PM recorder and the settlement fetcher running **together**. The gap exists
because they ran at different times. Ten days of paired data in two months is the actual
constraint on this entire venue, and it is an operational problem, not a modelling one.

## Why this matters more than the other lanes

`BINANCE_COST_CLEARANCE_V1` closed sub-30m Binance direction. Polymarket is the venue that
result does **not** bound — binary payoff, edge measured in cents on a $1 contract. It is the
most promising remaining direction, and it is dark.

Every other lane in this directory is a Binance lane. Until this join exists, the app's primary
venue has no measured edge, in either direction.

## Status

| requirement | state |
|---|---|
| PM quote/L2 snapshots | present, 916 rounds, 10 days |
| official settlement | present, 6,725 rounds, 20 days |
| **overlap** | **zero** |
| exchange-proxy substitute | refused — wrong contract |
| next action | backfill Gamma settlement for the 10 snapshot days |
