# What the two arbitrage post-mortems actually say — `2026-08-07`

Retrieved from `kacho.io` (`/polymarket-arbitrage-real-numbers` and
`/why-my-polymarket-arbitrage-bot-lost-money`). Recorded because a proposal was drafted against
an *assumption* about their contents, and the assumption was wrong in a way that changes what is
worth building.

---

## Correction first: this is not YES/NO package arbitrage

The proposal framed the work around

```text
edge = 1 - (Ask_YES + Ask_NO)
```

— buying both sides of one Polymarket market for under $1.

**That is not the strategy in these articles.** The author arbitraged **sportsbook odds against
Polymarket prices**: devig the book's implied probability, and if Polymarket disagrees by ≥7%,
take the Polymarket side. Cross-venue, one leg on each venue.

Two consequences:

- The `1 − (Ask_YES + Ask_NO)` scanner would answer a question these articles never asked.
- The markets are **esports and sports** (CS2, LoL, Valorant, Dota 2), with **20–30¢ spreads**.
  BTC Up/Down markets are orders of magnitude tighter. Nothing about the *magnitudes* transfers.

The *mechanisms*, however, transfer very well — and one of them lands directly on work already
in flight here.

---

## The number that matters most

```text
arbitrage legs (1,075 matched pairs)   +$8,293.46
directional / unhedged residual        -$3,184.78
cancelled markets (236)                  -$134.43
                                       -----------
net                                     +$4,973
```

**38% of the arbitrage profit was destroyed by legging risk** — positions left unhedged when the
second leg did not fill, *despite each leg carrying ≥7% theoretical edge at detection*.

This is the single strongest available piece of evidence that a displayed edge and an executable
edge are different objects. It is measured, not modelled.

## The fill-rate collapse

```text
          filled / bids          rate     locked arb profit
Jan         760 /  2,034        37.4%          $2,865
Feb       7,971 / 53,283        15.0%          $4,158
Mar       2,309 / 45,795         5.0%          $1,254
Apr         222 / 22,467         1.0%             $17
```

Passive fill probability fell 37x in four months as faster market makers arrived and outbid by
1¢. The strategy did not stop being *identifiable*; it stopped being *reachable*.

## The author's own #1 cause: adverse selection from stale quotes

Odds were refreshed every 7–30 minutes while lines moved:

```text
Call of Duty   60% of games moved >=5pp   median jump 10.9pp
League         49%                        median  4.9pp
Dota 2         37%                        median  3.1pp
CS2            31%                        median  1.9pp
```

> *"my 60¢ bid is now a gift sitting on the book"*

**This is the same defect class this repository has been repairing all week.** Scan-4 items 4.1,
4.2 and 4.3 — exchange-event age computed but never used as a rejection condition, Pyth freshness
taken from receipt time rather than publish time, a stale feed still permitted to settle a round
— are the identical failure: *acting on a quote whose source age nobody checked.* The
`kline_schema` work (`source_event_ts_ms` vs `received_ts_ms`) is the precondition for measuring
it here.

## Other quantified causes

| cause | quantification |
|---|---|
| expanding into unfamiliar sports | −$509 combined; ~75% of March's directional losses. Basketball, football, hockey, rugby **all net negative** |
| devig methodology error (Shin's) | with it: favourites +1.5% ROI, underdogs +19.7%. Without: favourites +11.7%, underdogs −0.4%. A method error that *inverted* where the edge sat |
| implementation bugs | swapped probabilities, reused odds from a previous match, odds matched from the **wrong sport**. "Several hundred bucks" |
| cancellation mechanics | Polymarket refunds cancelled markets at **50¢ regardless of entry price** — a systematic loss on every expensive entry |
| no order recording | *"Flying blind until the losses forced me to create the dashboard"* — the dataset covers mostly the losing period, so his own conclusions are selection-biased |

That last one is worth dwelling on: **the author cannot fully diagnose his own strategy because
he did not record decisions from day one.** It is the same argument as the evidence-durability
work here (2.17, terminal states; 1.7, contract columns) — and it is why those are not
bureaucracy.

---

## What this justifies building, and what it does not

**Justified by the evidence:**

| module | why |
|---|---|
| `LEG_FILL_RISK_V1` | −$3,185 on 1,075 pairs. The largest measured loss source, and it is a fill-probability problem, not a forecasting one |
| `ARB_DECAY_V1` / three-price accounting | THEORETICAL / ARRIVAL / REALIZED, never mixed. This is what turns "we lost" into "we lost *here*" |
| `ADVERSE_SELECTION_V1` | the author's own #1 cause, and it maps onto 4.1/4.2/4.3 already open here |
| `REALIZED_COST_V1` | one shared cost model — already flagged as scan-4 item 4.19 |

**Not justified, or not yet:**

- **`POLY_ARB_SCANNER_V1` as specified** — it scans for a strategy the articles do not describe.
  A cross-venue scanner would need a second venue's odds feed, which this repo does not have.
- **`QUEUE_POSITION_V1`** — the Kaggle archive is **YES-side books only**. Queue position needs
  order-level data, not L2 snapshots.
- **Anything claiming NO-side execution realism.** `NO_ask = 1 − YES_bid` holds economically and
  *not* for executable depth. Any such work must be labelled `YES_BOOK_ONLY` and refuse to claim
  NO-side fill evidence — otherwise it reproduces the exact defect class five scans just removed.

**The transferable lesson, stated plainly:** the failure was never forecasting. Each leg carried
≥7% theoretical edge and the arb legs *did* make money. What lost was execution — fills,
staleness, and mechanics. That is an argument for finishing the execution-integrity work already
open here **before** starting a new alpha lane.

---

## Licensing note

The Kaggle L2 archive is **CC BY-NC 4.0** — non-commercial research only. Any commercial
deployment path needs that reviewed first. The archive is also the 42.5 GB
`polymarket_data/archive.zip`, now gitignored; it has not been unpacked or validated.
