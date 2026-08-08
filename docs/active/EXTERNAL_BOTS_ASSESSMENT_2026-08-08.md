# External Polymarket bots — what is worth taking — `2026-08-08`

Nine links reviewed. Four fetched successfully; Reddit blocks automated access and
`openclaws.io` returned HTTP 403.

**Short answer: almost nothing to implement.** Not because the ideas are bad, but because none
of them reports the thing that decides whether an idea works. Recorded so this is a decision
with reasons rather than a shrug.

---

## What each one actually documents

### `aulekator/Polymarket-BTC-15-Minute-Trading-Bot` — our exact market

The closest match to this repository's problem, so it got the most attention.

```text
data          Coinbase REST + Binance WS + Fear&Greed/social + optional Solana RPC
signals       spike detection, sentiment, price divergence -> weighted "fusion engine"
position      $1 max per trade
exit          30% stop loss, 20% take profit
thresholds    spike 0.15, divergence 0.05
claim         "~75% win rate in early runs" (simulation)
```

**Not addressed:** fees, slippage, fill rates or partial fills, settlement-source verification,
sample size, drawdown, any live result.

We already have every one of its inputs, and more. Its "fusion engine with weighted voting" is
the architecture our own complementarity study measured as the weak point — six of seven seats
are near-duplicates with positively correlated errors. Adding a fourth correlated voter is the
thing that analysis says not to do.

**One genuinely checkable claim, below.**

### `kenfri13/polymarket-arbitrage-trading-bot`

No arbitrage formula. No default thresholds. Fees mentioned only as "you pay network gas fees".
No depth requirement, no liquidity floor, **no second-leg mechanics at all** — which is the
single thing the measured post-mortems say destroys the strategy (−$3,185 of unhedged residual
against +$8,293 of arb profit). No results.

### `Drakkar-Software/OctoBot-prediction-market`

An established vendor, and the README is aspirational: copy trading is "🚧 work in progress",
the arbitrage bot is "under development", Kalshi is planned. No endpoints, order types, fee
handling, settlement or risk controls documented. **No performance claim of its own** — it cites
someone else's arbitrage study.

### The remainder

`MrFadiAi`, `skharchikov`, the Reddit 5-minute bot and the openclaws write-up could not be
retrieved or add nothing beyond the above pattern.

---

## The pattern, and why it matters here

Every one of these reports a strategy and omits **fees, slippage, fill probability and
settlement source**. That is the precise gap the two measured post-mortems quantify
(`POLYMARKET_ARB_EVIDENCE_2026-08-07.md`):

```text
arb legs            +$8,293      every leg carried >=7% theoretical edge
unhedged residual   -$3,185      38% of the profit, destroyed by legging risk
fill rate           37.4% -> 1.0% over four months
```

**A post-mortem with 3,858 bets and $95,830 of volume is worth more than all nine of these
combined**, because it reports what happened rather than what was intended. That document is
already the basis for the freshness and cost-model work landed this week.

There is one indirect signal worth noting: the volume of published BTC Up/Down bots is itself
evidence the lane is crowded, which is consistent with the fill-rate collapse that
post-mortem measured. Crowding is a cost, not an opportunity.

---

## The one thing worth taking: a testable external claim

The aulekator bot asserts **~75% win rate with a 20% take-profit and 30% stop-loss**. Unlike
everything else here, that is falsifiable with data we already hold.

The arithmetic first, because it constrains the claim:

```text
break-even win rate for +20 / -30   =  30 / (20 + 30)  =  60.0%
at the claimed 75%                  =  0.75*20 - 0.25*30  =  +7.5% per trade, pre-cost
at 60%                              =  0.00
at 55%                              =  -2.5%
```

So the claim is not absurd — it is *load-bearing on the win rate*, and 15 percentage points of
margin over break-even is a large claim for a market whose own price is a well-calibrated
probability. Our §4.5 measurement found the Polymarket ask beats both model vintages on Brier,
log loss, ECE **and** AUC.

**The test we can run, on data already on disk:** `pm_round_snapshots` holds 1.7M rows over
7,787 markets (§4.2), and `backend/polymarket_policy/` already prices actions from the recorded
ladder — buy at the ask, sell at the bid, cross the spread each way, canonical fees. Evaluating
a fixed +20%/−30% bracket against those recorded quote paths is a **new bracket in an existing
engine**, not a new engine.

Precedent for the likely answer: §10.5 test 106 ran a frozen 4×4 target/stop grid over 8,639
disjoint 60m Binance windows and **no cell cleared costs** — the best was target 20 / stop 50 at
−9.70 bps against a 12 bps round trip, with barriers near-symmetric (48.4% vs 48.7%), exactly
what a martingale predicts.

I would run it anyway. It is cheap, it is decisive, and "an external bot's headline number does
not survive our own executable-price engine" is a more useful artefact than an opinion.

---

## Recommendation

| | |
|---|---|
| adopt wholesale | **nothing** |
| adopt a technique | **nothing** — every input is already present, and the fusion-voting architecture is what our complementarity study warns against |
| test one claim | **yes** — the +20/−30 bracket through `polymarket_policy`, against recorded ladders |
| licence/legal | none reviewed; not needed unless code is actually reused, which is not recommended |

The gap between these projects and this repository is not features. It is that this one refuses
to report a number it cannot defend — and every one of them leads with a win rate that has no
sample size attached to it.
