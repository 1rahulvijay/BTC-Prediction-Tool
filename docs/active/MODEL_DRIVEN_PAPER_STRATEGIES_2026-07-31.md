# Model-Driven Paper Strategies

Date: 2026-07-31

Status: implemented, validated, **paper/shadow only**. This work adds measurable decision and
exit policies; it does not claim profitability and it creates no real-order authority.

## Purpose

The application already produced many model/head outputs, but the Binance paper engine did not
consume the final ensemble decision and the Polymarket Champion only measured mostly fixed exit
rules. This change adds one narrow strategy per venue:

1. `model_consensus` for Binance futures paper execution.
2. `CHAMPION_DYNAMIC_PAPER_V1` for Polymarket paper execution.

Both strategies use the final post-filter decision, executable prices, explicit costs and causal
dynamic exits. Neither strategy bypasses an existing safety, calibration or economic gate.

## Binance: `model_consensus`

### Entry authority

The strategy consumes the same finalized 5-minute prediction shown by the app after:

- base-model ensemble and OOF stacker;
- meta-model trust filtering;
- live probability calibration;
- feed/regime/confusion checks;
- expected-cost filtering; and
- the structured do-not-trade reason engine.

An entry is refused unless all of these are true:

- Binance futures book is healthy;
- model context is no more than 90 seconds old and not future-dated;
- model is trained and the prediction carries a bundle identity;
- `finalAction == TRADE`, `actionable == true`, and no blocking reason exists;
- final direction is `UP` or `DOWN`;
- calibrated probability is at least 0.58;
- ensemble agreement is at least 0.67;
- meta trust is at least 0.55;
- the move-size head supplies a positive expected range; and
- conservative expected value remains positive after 12 bps assumed round-trip cost.

The lower-bound probability is `calibrated_probability - 0.05`. Because this probability predicts
the endpoint direction, expected value is calculated against the conservative horizon move:

```text
lower_EV_bps = (2 * lower_probability - 1) * conservative_move_bps - 12
```

It is deliberately not interpreted as a take-profit-before-stop probability. That would change the
target without training a barrier-order model.

### Position geometry

- maximum paper notional: $500 before the shared risk engine clamps it;
- stop: model stop distance, bounded to 8-60 bps;
- target: 80% of the conservative move, bounded to 18-100 bps;
- maximum hold: 300 seconds;
- shared fill engine: executable book depth, 500 ms default latency, 1 bp slippage and configurable
  taker fee (5 bps default);
- shared portfolio controls: isolated strategy account, leverage/notional caps, cooldown, loss,
  drawdown and kill-switch gates.

The 18 bps target floor is above the default 12 bps round trip. The structural strategy-base guard
raises if any strategy tries to open with a target inside its assumed round-trip cost.

### Dynamic exits

The position is sent through the normal paper close lifecycle when any causal condition fires:

- `MODEL_CONTEXT_STALE`;
- `MODEL_DIRECTION_FLIP` when a new final `TRADE` reverses direction;
- `MODEL_CONFIDENCE_COLLAPSE` below 0.45;
- `MODEL_EDGE_DECAY_PROFIT_LOCK` when the trade is already beyond cost plus 2 bps and the final
  model gate no longer authorizes it;
- fixed stop, take-profit or maximum-hold fallback from the shared portfolio.

Dynamic exit prices remain executable bid for longs and executable ask for shorts. Fees and
slippage are charged by the normal fill/accounting engine.

## Polymarket: `CHAMPION_DYNAMIC_PAPER_V1`

### Entry authority

This strategy cannot manufacture its own bet. It only enters when the existing rules-first Champion
already returns `PAPER_BET` with `bet_candidate=true` and positive post-cost edge. The current
P(Hold) calibration lockdown remains default-off through `BTC_ENABLE_PAPER_BET=0`, so this strategy
is dormant unless that existing explicit paper override and every Champion permission pass.

The latest live audit reinforces that lockdown: P(Hold) ranks outcomes (AUC 0.7762) but is
overconfident (ECE 0.0678), and its ranking is already largely contained in the share ask. No
P(Hold ask bucket survived the corrected multiple-testing/economic robustness checks. See
`docs/PHOLD_AUC_AND_EXPECTANCY.md`.

Additional execution checks require:

- a valid same-round, same-side quote no more than five seconds old;
- ask and bid ordered inside `[0,1]`;
- spread no wider than 3 cents;
- at least one share at the top ask;
- more than 10 seconds remaining; and
- a valid, non-late Pyth anchor.

Entry is at ask. The crypto taker fee is charged per share using the venue formula:

```text
fee = 0.07 * share_price * (1 - share_price)
```

The bridge publishes fee enablement/rate metadata; the official crypto default is retained as the
fallback. See [Polymarket fee documentation](https://docs.polymarket.com/trading/fees).

### Dynamic exits

The strategy evaluates the held side at the live executable bid and subtracts a second taker fee.
It exits on:

- `DYNAMIC_TARGET`: net profit reaches half the initial Champion edge, bounded to 1-5 cents;
- `DYNAMIC_STOP`: net loss reaches a P(Hold)-scaled 2-5 cent stop;
- `MODEL_INVALIDATED`: probability of the held side falls to 0.35 or below;
- `EDGE_DECAY_PROFIT_LOCK`: the position is profitable, Champion withdraws authority, and held-side
  probability falls by at least 0.10 from entry; or
- `LAST_CHANCE_MODEL_EXIT`: 10 seconds or less remain and held-side probability is below 0.50.

If the quote is stale, missing, malformed or for the wrong side, the exit logic fails closed and
waits for a valid quote. An open position that reaches settlement is closed by the existing generic
one-side settlement ledger. Every no-entry round receives `NO_SIGNAL`, preserving the denominator.

## Integration Corrections Found During Audit

1. `_side_quote` originally omitted its side. The dynamic exit correctly rejected that malformed
   quote, which meant it could never exit live. The helper now includes side and has a direct
   regression test.
2. Entry originally reused `_leader_quote`, which follows the Polymarket bid leader. Champion
   evaluates the BTC/Pyth current side. These can disagree, so entry now uses the exact BTC-side
   quote Champion evaluated; a disagreement no longer silently changes the intended side.
3. Binance strategy status initially exposed only `ensemble_prediction`. It now reports live
   calibration and model-bundle identity separately, matching the actual decision requirements.
4. The Polymarket strategy is now included in all three registry surfaces: tracker logging, server
   summaries and human-readable UI names. The AST registry audit reports 17/17 consistent.
5. Numeric model evidence, quote fields and restored position state are finite/range checked before
   hashing or arithmetic. Corrupt inputs now fail to `NO_DATA`, `NO_TRADE` or `HOLD` instead of
   interrupting the evaluation loop.

## Validation Performed

```text
Focused model-driven strategy tests       7 passed
Complete pytest suite                     98 passed
Canonical local CI                        74/74 passed
Binance paper self-test                   passed
Binance strategy-economics tests          5/5 passed
Polymarket strategy registry              17/17 consistent
Python compile and maintained Pyflakes    passed
Frontend production build                 passed
npm high-severity audit                   0 vulnerabilities
```

The focused tests cover calibrated entry, refusal paths, future/stale context, positive cost-adjusted
EV, long/short dynamic exit routing, actual helper quote shape, Champion authority, executable bid,
two-sided fee accounting and missing-quote refusal.

## Required Evidence Before Promotion

Do not promote either strategy from paper on a backtest or a handful of trades. At minimum require:

- independent forward decisions with immutable model/artifact identity;
- positive net expectancy after observed fees, spread, slippage and funding;
- positive day-clustered lower confidence bound;
- profit factor above the predeclared threshold;
- stability by regime, horizon and fee stress;
- no single day/week dominating profit;
- a matched zero-information control for Binance; and
- exact Polymarket ask/bid/depth/settlement reconciliation.

Until those gates pass, the correct label is **implemented paper experiment**, not profitable bot.
