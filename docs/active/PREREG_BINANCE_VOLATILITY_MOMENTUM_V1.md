# PREREGISTRATION — `BINANCE_VOLATILITY_MOMENTUM_V1`

**Status: FROZEN 2026-07-26, before any Binance analysis was run and before any model was fit.**
No element may be changed after the first M0 score is computed. If something proves unworkable the
experiment is **abandoned and archived, not amended**. A near miss is a miss.

Discipline template: `CONDITIONAL_STOPPING_V1`, which closed cleanly at its M0 gate without fitting
a model. Reuse it exactly.

---

## 0. DATA-ADMISSIBILITY CONTRACT (binding, and the reason this section is first)

> **A feature becomes available at `recv_ts`, never at `exch_ts`.**

`exch_ts` is when the market event happened. `recv_ts` is when this system could have known.
Replaying a REST-polled event at its `exch_ts` manufactures lead-lag alpha the live system cannot
access. This is not hypothetical: on 2026-07-26 the collector measured Binance perp REST trades
arriving with a **median ~54-second** delivery lag, which briefly appeared as "clock drift" until
poll-based streams were excluded from that measurement.

### Feature classes

| class | sources | permitted use |
|---|---|---|
| **A — live event-time** | Binance spot WS (bookTicker, aggTrade), Binance perp WS (bookTicker), Bybit WS (orderbook.1, publicTrade), Coinbase WS (ticker) | immediate flow, price discovery, **lead-lag** |
| **B — delayed observable** | Binance perp REST (aggTrades, premiumIndex, openInterest, fundingRate) | slow aggregate state **only**, aggregated over >= 60s, with age recorded |
| **C — PROHIBITED** | any REST-polled event ordered by `exch_ts` as though received live | **never** |

**Measured constraint (2026-07-26):** the Binance futures WebSocket serves `bookTicker` from this
host but delivers **zero** messages for `aggTrade` and `markPrice@1s` (verified in single, combined
and `/ws/` forms). Futures REST returns all of it. Therefore **perp trade flow, basis, OI and
funding are Class B for this experiment** — usable as slow state, never as timing or lead-lag
evidence. Any later claim that "Binance perp flow moved first" is inadmissible under this contract
unless it is sourced from perp bookTicker (Class A).

### Required provenance on every derived feature

```
feature_name  source_stream  source_mode(WS|REST_POLL)
max_exch_ts   max_recv_ts    age_ms_at_decision
event_count   health_status
```

A feature whose provenance is absent is treated as unavailable, not as fresh. This makes it
mechanically impossible for a Class B feature to masquerade as Class A.

---

## 1. Research question

> At fixed one-minute decision times, can a causal volatility-expansion and cross-venue-flow state
> select BTCUSDT perpetual long or short positions whose five-minute return is positive after
> executable spread, fees, latency and slippage?

One instrument, one cadence, one horizon. No TP/SL grid, no exit policy, no multi-horizon search —
those would be separate preregistrations.

## 2. Venue and execution

```
instrument        Binance USD-M BTCUSDT perpetual
decision cadence  once per completed minute
primary horizon   5 minutes
position mode     one position at a time (no overlap)
entry / exit      taker / taker
entry latency     1s primary; 2s sensitivity
size              fixed tiny notional within displayed top-level size
leverage          1x economic exposure
```

```
LONG   entry = executable ask after latency; exit = executable bid at +5m
SHORT  entry = executable bid after latency; exit = executable ask at +5m
```

Fees: the account-specific taker fee recorded at freeze. If unavailable, the frozen conservative
assumption is **4.0 bps round trip (2.0 bps per side)**, which must not be revised after results.
No capacity claim beyond displayed top-level size.

## 3. Primary outcome

```
net_return = signed executable price return - entry fee - exit fee - spread - measured slippage
```

**Primary comparison: paired net return versus volatility-matched random entries.**
Secondary (diagnostic): profit factor, win rate, day-block CI, max drawdown, CVaR, positive-week
ratio, MFE/MAE. **Direction accuracy and AUC are diagnostic only and cannot support promotion.**

## 4. Frozen information set

**Class A (real-time):** Binance spot signed trade imbalance, CVD change, trade-arrival
acceleration, best bid/ask pressure, short-term returns; Binance perp best bid/ask, spread,
top-level quantity, microprice deviation; Bybit trade imbalance, L1 imbalance, short-term return,
price impulse; Coinbase return, Binance–Coinbase premium, price impulse.

**Class B (delayed, >= 60s aggregation, age recorded):** Binance perp REST trade imbalance,
perpetual basis, open-interest change, funding state, premium-index change. **No sub-minute feature
may be derived from Class B.**

**Market state:** realized volatility, volatility acceleration, range expansion, trend efficiency,
compression ratio, cross-venue directional agreement, spread regime, data-quality state.

## 5. Prohibited

```
future-restamped REST events          poll-based "who moved first"
Polymarket outcomes or prices         future high/low information
unfinished-minute aggregates          post-decision liquidations
manually selected hour/day filters    any feature added after seeing M0 results
```

## 6. M0 gate — NO MODEL FITTING

One predeclared composite mechanism, not a search over variables.

**Composite state** (thresholds from the training period only):
`volatility_expansion_score`, `cross_venue_flow_alignment`, `execution_quality`.

```
LONG  : volatility expanding + spot flow positive + Bybit flow positive
        + Coinbase impulse >= 0 + perp book pressure positive
SHORT : symmetric negative conditions
```

Class B perp flow may enter **only** as slow confirmation, never as a timing term.

**M0 table:** signed opportunity score in 5 buckets (Q1 strongest opposing → Q5 strongest aligned),
reported separately for long and short: n, mean/median net return, win rate, profit factor,
day-block LB, positive weeks, matched-random difference.

### Frozen thresholds (declared here, before running)

```
minimum Q5 - Q3 net-return spread   >= 2.0 bps   (must exceed noise AND be economically
                                                  meaningful against a 4.0 bps round trip)
declared M0 family                  2 sides x 2 latencies = 4 primary cells
multiplicity procedure              Benjamini-Hochberg across all 4, q <= 0.10
chance-monotonicity control         with 5 buckets a random ordering is monotone with
                                    p = 2/5! = 1.7%; any monotonicity claim must be reported
                                    WITH the family size and the expected chance count, and a
                                    lone monotone cell inside a large search is NOT a pass
```

That last line is inherited directly from `CONDITIONAL_STOPPING_V1`, where a loose automated
criterion produced 1 monotone result in 28 searches — exactly the chance rate — and would have
licensed months of model fitting had the correction not been applied to the gate itself.

**M0 passes only if ALL hold:** (1) economic ordering monotone or directionally consistent;
(2) Q5−Q3 spread >= the frozen minimum; (3) extreme aligned bucket has positive day-block LB;
(4) beats volatility-matched random entries; (5) sign stable across most calendar weeks;
(6) still positive at 2s latency; (7) not driven by one venue, day or hour; (8) survives BH.

**If M0 fails:** `BINANCE_VOLATILITY_MOMENTUM_V1 CLOSED. MODELS FITTED = NONE.`

## 7. Models — only after M0 passes

```
M1 regularized logistic regression    M2 LightGBM    M3 CatBoost
```

No TCN, transformer or RL in V1. Targets: positive net 5-minute return; long-positive-after-costs;
short-positive-after-costs. A competing-risk TP-before-SL head is a **later, separate**
preregistration and may not be added to this one.

## 8. Temporal split

```
oldest 60%   fit thresholds and models
next   20%   choose the frozen decision threshold
newest 20%   untouched test, scored once
```

Purge around every split for the 5-minute outcome horizon. After the offline test, freeze the whole
strategy and run it forward. **Overlapping minute decisions are not independent:** either prevent
overlapping positions, or define the independent unit as one 5-minute exposure episode. The latter
is the declared default.

## 9. Controls (mandatory)

```
always long                always short             random side
random time matched by volatility                   score shuffled within day x volatility bucket
spot-flow-only             Bybit-flow-only          price-momentum-only
NO-DELAYED-REST            (does Class B add anything over Class A alone?)
```

The `NO-DELAYED-REST` control is the direct test of whether the poll-lagged perp data earns its
place at all.

## 10. Data-quality gates — no decision when

```
any required WS stream stale        sequence integrity fails
REST source age > frozen limit      spread > frozen maximum
top-level size inadequate           cross-venue timestamp alignment unhealthy
clock drift beyond tolerance        collector stream health incomplete (< 8/8)
```

Stream health is part of the immutable decision record, not a runtime convenience.

## 11. Promotion contract

```
>= 1,000 independent exposure episodes    >= 8 calendar weeks
positive absolute post-cost EV            day-block lower bound > 0
profit factor > 1.20                      positive in most weeks
beats matched-random control              beats always-long AND always-short
survives multiplicity correction          positive at 1s AND 2s latency
no single week/hour > 50% of profit       forward paper consistent with replay
```

Path: `RESEARCH -> SHADOW -> PAPER`. **No real-money routing at any stage of V1.**

## 12. Data availability — stated in advance

**M0 cannot run today.** The multi-venue collector was built and verified on 2026-07-26 but has
collected no production data; the smoke runs were in-memory and discarded. M0 as specified requires
genuine `recv_ts` provenance, which historical archives cannot supply — backfills carry `exch_ts`
only, and inventing a `recv_ts` for them violates section 0.

```
required before M0     >= 4 continuous weeks of collector uptime (8/8 stream health),
                       covering >= 1,000 non-overlapping 5-minute episodes
```

**Optional backfill pre-screen (kill-only authority).** A Class-A-degraded screen on public
archives (Binance spot + perp aggTrades) may be run to kill the hypothesis cheaply. It uses
`exch_ts` with a uniform conservative delay and therefore **cannot satisfy section 0**. It may
return a NEGATIVE that closes the lane; it may **never** be cited as a pass, and its result carries
no weight toward promotion. If run, it must be labeled `M0-PRESCREEN (non-promoting)`.

## 13. Freeze record

```
frozen_at      2026-07-26
scope          one instrument, one cadence, one horizon; entry selection only
family_size    4 primary M0 cells
dev_data       collector output, >= 4 weeks (not yet collected)
forward_test   scored once after the offline test
prereg_sha256  recorded in PREREG_HASH.txt on commit
```

Any edit to this file after the first M0 score invalidates the experiment.
