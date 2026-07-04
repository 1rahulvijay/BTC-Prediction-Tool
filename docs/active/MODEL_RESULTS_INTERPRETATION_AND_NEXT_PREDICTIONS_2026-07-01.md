# Model Results Interpretation And Next Predictions

Date: 2026-07-01  
Status: canonical operating guide and research backlog  
Scope: 5m/15m BTC path forecasting and Polymarket decision support  
Safety state: PAPER ONLY; profitable execution is not yet proven

## Purpose

The project has tested many models, but a model name is not a trading conclusion. This guide answers four practical questions:

1. What can the app use now?
2. How should results from many models and specialist heads be interpreted?
3. What should the app predict next?
4. Which genuinely new features can be collected freely?

The central conclusion is stable:

> BTC path, movement size, speed, volatility and late-round hold risk are forecastable. Exact 5m/15m closing direction is weak. Profit requires an executable Polymarket price below conservative fair value after every cost.

## 1. Three Schemas That Must Not Be Confused

| Schema | Count | Purpose |
|---|---:|---|
| Production raw feature schema | 136 | Full serving schema, including live-only and external-feed columns |
| Production SAFE model mask | 69 | 57 candle-derived plus 12 aggregate-trade features with train/serve parity |
| Recent standalone research contract | 70 | Causal features computed only from completed pre-anchor 30-second data |

The 69-feature production model and 70-feature research models are not interchangeable. The research contract was designed to make historical tests causal. Production retains a 136-column outer schema so saved artifacts and live overlays remain structurally stable, but SAFE pruning excludes 63 non-backfillable external columns and four forward-only Polymarket columns from the main direction learner.

## 2. Models Versus Questions

XGBoost, LightGBM, CatBoost, Random Forest, Extra Trees, HistGradientBoosting, Logistic Regression and TCN are **estimators**. They do not represent eight independent market opinions when trained on the same rows and labels.

The independent unit is the **prediction head**:

| Head | Question | Correct use |
|---|---|---|
| Direction | Where will the window close relative to its start? | Weak confirmation only |
| Big move/activity | Will enough movement occur to create an opportunity? | Participation gate |
| Time-to-touch | Will a chosen barrier be reached before a chosen deadline? | Target feasibility and speed |
| Quantile path | What low/median/high excursion range is plausible? | Risk zone and target room |
| Regime transition | Is the market about to become high-volatility or trend-like? | Risk and strategy-mode gate |
| First-touch side | Which barrier is more likely to be touched first? | 5m shadow lean only |
| Future-side flip | Will the currently leading anchor side reverse before settlement? | In-round hold-risk warning |
| P(Hold) | Will the side currently ahead remain ahead at settlement? | Conservative fair-value input |
| Late shock | Is a $20/$50/$100 move still likely before settlement? | Tail-risk and exit urgency |
| Opportunity drought | How soon is another active/path-opportunity round likely? | Scheduling and abstention |
| Net EV | Is the executable share cheap after fees, slippage and risk buffer? | Final trade gate; data-blocked |
| Fill/exit | Can the intended order actually fill and later exit? | Execution gate; data-blocked |

Do not average probabilities from different rows in this table. `P(touch)=0.85`, `P(Hold)=0.92`, and `P(UP)=0.54` answer different questions.

## 3. What Can Be Used Now

### Strong enough for display and forward shadowing

| Head | Evidence | Interpretation | Allowed use |
|---|---|---|---|
| Big-window/activity | Big-window AUC about 0.84/0.83; activity AUC about 0.73-0.74 | The coming window may offer enough range | Skip quiet rounds; never choose side from this |
| Time-to-touch | Barrier/checkpoint AUC about 0.81-0.89 | A barrier is feasible within 30/60/120 seconds | Target feasibility and urgency |
| High/low/range quantiles | Approximately 77-81% conformal coverage in recent tests | A likely zone, not an exact price | Show low/median/high range with historical coverage |
| P(Hold) | ECE about 0.011; high-probability buckets historically hold often | The current leader is likely to remain ahead | Fair-value input after time/distance threshold; require real ask |
| 5m future HIGH_VOL | AUC 0.943, selective precision 94.12%, n=204 | Calm assumptions may soon fail | Widen bands, reduce size, avoid passive exit assumptions |
| Future-side flip | AUC 0.816/0.891 | The currently leading side remains at risk of crossing | Warning or confidence haircut |
| Late $50 shock | AUC 0.851/0.845 | Material movement is still plausible before expiry | Tail-risk warning and exit urgency |
| Opportunity within three rounds | AUC 0.837/0.812 | Another active path window is likely soon | Wait for a cleaner round rather than force a trade |
| Rounds to next opportunity | R2 0.404/0.340 | Approximate waiting time beats a constant baseline | Scheduling estimate, never an exact countdown |
| Flow-burst probability | AUC about 0.70-0.73 | Aggressive market activity may increase | Market-heat and execution-risk context |

### Promising but restricted

| Head/policy | Evidence | Restriction |
|---|---|---|
| Touch-side settles | 98.78%/97.56% selected precision, but only n=82/123 | Extremely selective; needs more independent forward calls and executable asks |
| 5m first-touch side | 58.37% exclusive precision, Wilson LB 54.71%, n=711 | Predicts first path direction, not settlement; shadow only |
| 5m DOWN close subset | 65.28%, n=144, but decayed materially in later test | Frozen shadow threshold only; do not promote |
| TP50/SL10 stopping | BTC proxy PF 2.75/1.72 after $2 cost; positive day-block intervals | Not Polymarket PnL; requires quote-by-quote replay |
| Volatility-onset class | Macro AUC 0.729/0.697 | Use the probability vector; exact class is imbalanced and weak |
| Maximum favorable/adverse excursion | Quantile bands have useful approximate coverage | Risk zones only; no guarantee and no direct side decision |

### Do not use as trade triggers

- Unconditional 5m/15m close direction: approximately coin-flip after extensive testing.
- Exact future close price: current-price/zero-return baselines win.
- Automatic post-touch fade: causal selected win rate about 42%, below symmetric breakeven before costs.
- Exact continuation or retracement size: worse than median baselines.
- Exact path-archetype, high-time, low-time or volume-peak class.
- Generic historical model-failure/meta-skip head: failure AUC about 0.50.
- Spot-versus-perpetual lead claim from existing aggregate flow: follower persistence explains it better.
- Another deep direction model on the same candles: TCN, PatchTST, iTransformer, LSTM and GRU did not break the direction ceiling.

## 4. How To Read Every Metric

| Metric | What it answers | Minimum interpretation rule |
|---|---|---|
| Accuracy | Fraction correct at one threshold | Compare with majority/base-rate accuracy |
| AUC | Ranking quality across thresholds | AUC is not win rate and does not prove profit |
| Precision | Correct fraction among emitted calls | Always display call count and base rate |
| Recall | Fraction of true events found | High precision with tiny recall may rarely fire |
| Wilson lower bound | Conservative precision bound | Promotion should use the lower bound, not the point estimate |
| Brier score | Probability calibration and discrimination | Compare with market probability and base-rate forecast |
| ECE/calibration curve | Whether stated probabilities match outcomes | Inspect separately by horizon, time-left and liquidity |
| MAE | Average size error | Must beat naive current-price, zero-return or median baseline |
| R2 | Variance explained | Small positive R2 may still be operationally useless |
| Quantile coverage | Frequency outcomes fall inside the band | Show coverage together with band width |
| Mean net PnL/expectancy | Average result after costs | Must use executable prices, not midpoint or BTC movement proxy |
| Profit factor | Gross wins divided by gross losses | Require sufficient independent trades and stable periods |
| Maximum drawdown/CVaR | Capital and tail risk | A profitable mean can still be unusable due to drawdown |
| Block-bootstrap interval | Robust range under dependent days | The lower bound should remain positive in a truly untouched era |

### Example

Suppose the app shows:

- `P(big move) = 82%`
- `P(touch $50 within 120s) = 76%`
- `P(UP close) = 54%`
- `P(current UP side holds) = 93%`
- UP ask = `0.94`

This does **not** mean BUY UP. The path is likely active and the current UP side may hold, but paying 94 cents for a conservative probability no greater than 91-93 cents is negative after fees. Correct output: `NO EDGE / WAIT`.

## 5. Decision Flow For A Non-Trader

The application should explain one sequential decision, not present many competing signals:

```text
1. Is every feed fresh and synchronized?
   No -> DATA STALE, DO NOTHING

2. Is meaningful movement likely?
   No -> QUIET, WAIT

3. What path risk is expected?
   Show touch time, range, regime transition, flip risk and late-shock risk

4. Is there a side lean?
   Show it as weak/medium confirmation, never as proof

5. What is conservative settlement fair value?
   Use calibrated P(Hold)/settlement models and uncertainty haircut

6. Is the real executable ask cheap enough?
   fair - ask - fee - slippage - safety buffer <= 0 -> NO EDGE

7. Can the order fill and exit safely?
   No -> AVOID

8. Only then emit PAPER BUY UP / PAPER BUY DOWN
```

Recommended action vocabulary:

| Action | Plain meaning |
|---|---|
| DATA STALE | Inputs cannot be trusted |
| WAIT | No useful movement or insufficient evidence |
| WATCH | Opportunity may form, but no priced edge exists yet |
| AVOID LONG/SHORT | Tail or flip risk is unusually high for that side |
| NO EDGE | Prediction may be right, but the share is too expensive |
| PAPER BUY UP/DOWN | Positive conservative EV and execution checks pass; still simulated |
| EXIT/REDUCE | Remaining hold EV is worse than an executable exit |

## 6. Highest-Value Predictions To Build Next

### Priority 0: directly connected to profit

| Prediction | Required data | Output |
|---|---|---|
| Conservative share fair value | Polymarket quotes, official settlement, P(Hold), time/distance/path state | `p_fair_low`, `p_fair_mid`, `p_fair_high` |
| Net executable EV | Fair value, ask depth, exact fee, latency/slippage model | Expected cents per contract after costs |
| Edge half-life | Full quote sequence and BTC event timestamps | Probability edge survives 100/250/500/1000ms |
| BTC-shock quote underreaction | Synchronized BTC and Polymarket books | Residual repricing after a BTC shock |
| First-achievable profitable exit | Future causal bids and bid size | Probability and time to executable positive exit |
| Fill probability | Order price/size, queue proxy, trades and user order lifecycle | Fill probability before edge disappears |
| Adverse selection after fill | Fill timestamp and subsequent fair/market prices | Expected loss immediately after maker/taker fill |
| Maker-versus-taker value | Fill odds, edge decay, fees and missed opportunity | Best execution mode or `SKIP` |
| Cross-horizon consistency | Simultaneous 5m/15m contracts and anchors | Mispricing/inconsistency flag |
| Complement-book arbitrage | Simultaneous UP/DOWN depth and fees | Executable `UP ask + DOWN ask` violation |

### Priority 1: new independent market information

| Prediction | Required data | Output |
|---|---|---|
| Liquidation cascade probability | Binance/Bybit liquidation events, OI, depth and returns | P(cascade $50/$100 within 30/60s), side and notional band |
| L2 micro-direction | Event-time depth/trades from several venues | P(up/down move over 1/5/15/30s), not 5m close |
| Liquidity vacuum persistence | Diff books and trades | Probability thin book persists long enough to move price |
| Book recovery/resilience | Shock, cancellation, replenishment and spread | Time until depth/spread normalizes |
| Venue price-discovery order | Synchronized event and receive timestamps | Which venue moves first after controlling own persistence |
| Options-implied short-horizon tail | Deribit surface/trades plus realized volatility | Touch/tail probabilities independent of candle model |
| Volatility-regime duration | Existing realized volatility plus event-time features | Remaining duration of HIGH_VOL/RANGE state |
| Conditional stopping value | Causal signal state and BTC path, later quote replay | EV of TP/SL/time policies with uncertainty |

### Priority 2: governance that prevents false discoveries

| Prediction/monitor | Purpose |
|---|---|
| Calibration-drift probability | Haircut a head before its accuracy visibly collapses |
| Out-of-distribution score | Detect states unlike training data without claiming direction |
| Feature half-life | Retire features whose lift or sign disappears by month |
| Head failure by context | Measure failure by time-left, liquidity, spread and regime using forward records |
| Conditional conformal width | Expand uncertainty bands when current context is poorly represented |
| Probability of backtest overfitting | Penalize winners selected from many models/policies |

## 7. Free Features Worth Adding

The word `free` refers to public access, not guaranteed historical coverage, latency, reliability, or unrestricted commercial licensing.

### A. Polymarket public market data: highest ROI

Polymarket exposes public read endpoints plus a no-auth market WebSocket with book, price-change, last-trade and best-bid/ask events. Record:

- exchange event time and local receive time;
- UP and DOWN best bid/ask price **and size**;
- depth at 1/5/10/25 cents from best price;
- spread, depth slope, convexity and imbalance;
- signed trade flow and trade-arrival intensity;
- quote age and time since last book/trade update;
- UP+DOWN complement bid/ask sums after fees;
- quote response to BTC distance, velocity and volatility;
- edge duration, maximum edge and first achievable exit;
- user-channel order state for paper/live fill calibration.

These features enable the missing fair-value, net-EV, fill, exit and underreaction heads. Official references: [market WebSocket](https://docs.polymarket.com/market-data/websocket/market-channel), [public market-data APIs](https://docs.polymarket.com/market-data/overview), and [user order/trade channel](https://docs.polymarket.com/market-data/websocket/user-channel).

### B. Multi-venue event-time books: high ROI, forward record

Use Binance diff-depth/trade, Coinbase `level2_batch`, and Bybit orderbook/trade streams. Record event and receive timestamps rather than one latest snapshot.

New features:

- microprice and microprice-minus-mid;
- order-flow imbalance over event-count and clock-time windows;
- queue imbalance and depletion speed at top 1/5/10 levels;
- cancellation-to-add ratio and cancellation bursts;
- depth slope, convexity and gap to next occupied level;
- spread/depth recovery time after a market-order shock;
- aggressive-volume impact per BTC and impact decay;
- cross-venue mid/microprice residuals;
- venue leader sequence after controlling each venue's own persistence;
- message intensity, inter-arrival quantiles and burst clustering;
- stale-venue and crossed-venue flags.

Official references: [Coinbase Exchange WebSocket channels](https://docs.cdp.coinbase.com/exchange/websocket-feed/channels) and [Bybit V5 orderbook stream](https://bybit-exchange.github.io/docs/v5/websocket/public/orderbook). Binance public streams provide real-time trades, book ticker and diff-depth; public files remain useful for candles/trades, but full historical L2 generally must be recorded forward.

### C. Actual liquidation and derivatives events: high ROI, forward record

Replace generic price/volume cascade proxies with:

- long/short liquidation notional per 0.5/1/5/15/30 seconds;
- liquidation count, mean/maximum size and concentration;
- liquidation acceleration and same-side clustering;
- price distance from liquidation bankruptcy prices;
- liquidation-to-depth and liquidation-to-volume ratios;
- OI drop during the event;
- cross-exchange liquidation ordering;
- post-liquidation continuation, exhaustion and book-recovery labels.

Bybit publishes an all-liquidation WebSocket with side, size, bankruptcy price and 500ms updates: [Bybit all-liquidation stream](https://bybit-exchange.github.io/docs/v5/websocket/public/all-liquidation). OI history is available through [Bybit V5 open interest](https://bybit-exchange.github.io/docs/v5/market/open-interest).

### D. Options surface: medium/high potential, mostly public live data

The current schema has only put/call ratio, 25-delta skew, max-pain distance and ATM IV. Add:

- ATM IV changes by expiry rather than level only;
- IV term-structure slope and curvature;
- 10d/25d risk-reversal velocity;
- butterfly/convexity and smile asymmetry;
- implied-versus-realized volatility spread;
- option volume/OI concentration by strike and expiry;
- signed option trade IV and delta flow where derivable;
- short-expiry gamma concentration near spot;
- option-implied terminal-above-anchor probability;
- option-implied touch probability compared with app and Polymarket prices.

Use these primarily for tail/range and independent fair-value context. Do not use max pain as a directional rule.

### E. Free on-chain/network state: slower context

Coin Metrics provides a community HTTP API without an API key. Bitcoin Core can reproduce historical block statistics, while mempool state must generally be recorded forward.

Candidate features:

- fee-rate percentile level, acceleration and dispersion;
- mempool virtual bytes and high-fee backlog;
- block interval surprise and fullness;
- transaction-count/value tail concentration;
- active-address, transfer-volume and supply-state changes where community metrics allow;
- miner-revenue/hash-price stress on daily horizons;
- stablecoin supply impulse and exchange-arrival timing where labels are reliable.

Official references: [Coin Metrics API v4 community endpoint](https://docs.coinmetrics.io/api/v4/), [Bitcoin Core `getblockstats`](https://developer.bitcoin.org/reference/rpc/getblockstats.html), and [`getrawmempool`](https://developer.bitcoin.org/reference/rpc/getrawmempool.html).

These are context/risk features. Their publication frequency is usually too slow to directly predict a 5m side.

### F. Free macro and institutional context: low-frequency vetoes

Candidate features:

- release-time CPI/payroll/Fed event flag and first-release surprise;
- dollar and Treasury-yield event shock;
- weekly CFTC CME Bitcoin positioning change;
- CME open/reopen gap state;
- official spot-ETF holdings/share changes with publication timestamps.

Use ALFRED vintages rather than today's revised history. FRED/ALFRED require a free API key and support real-time vintages; CFTC publishes weekly and historical COT files. Official references: [FRED/ALFRED API](https://fred.stlouisfed.org/docs/api/fred/overview.html), [ALFRED vintages](https://fred.stlouisfed.org/docs/api/fred/alfred.html), and [CFTC Commitments of Traders](https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm).

These should change confidence, risk budget or event mode. They should not be presented as 5m BUY/SELL signals without strong incremental evidence.

## 8. Features Not Worth Adding Again

- More RSI/MACD/CCI/MFI/Williams variants.
- Another standard candle pattern library.
- More interactions among weak direction indicators without a new data source.
- Another deep architecture trained on the same OHLCV direction label.
- Generic sentiment scraped without timestamp, revision and source controls.
- Slowly updated on-chain or macro values repeated across thousands of minute rows as if each row were independent.
- Historical external columns filled with zero while live serving uses real values.
- Any feature without exact event time, receive time, missingness flag and train/serve parity.

## 9. Recommended Small-Ensemble Architecture

Do not build one giant ensemble for every target. Use small specialist ensembles and one rules-first decision controller:

```text
Data health
  -> Opportunity ensemble: big move + touch hazard + drought
  -> Path-risk ensemble: range quantiles + regime transition + late shock + side flip
  -> Side lean: weak direction + 5m first-touch side
  -> Settlement ensemble: P(Hold) + touch-side-settles + market-implied prior
  -> Fair-value residual: conservative model probability - market probability
  -> Execution ensemble: fill + slippage + edge half-life + exit opportunity
  -> Decision controller: DATA STALE / WAIT / WATCH / NO EDGE / PAPER BUY / EXIT
```

Each specialist should use two to four diverse models selected only by validation evidence. Adding a model is justified only when it improves untouched Brier/log-loss, calibration, or net policy value after accounting for correlated errors.

## 10. Accuracy And Profit Improvement Plan

1. **Finish the recorder, not another direction model.** Store public Polymarket L2, sizes, trades, timestamps and official settlements continuously.
2. **Train residual fair value.** Predict settlement residual versus the market's own probability, rather than rebuilding probability from candles alone.
3. **Use cost-aware labels.** Positive label means executable net profit after fee, slippage and first achievable exit, not merely correct direction.
4. **Calibrate conditionally.** Measure P(Hold)/fair-value calibration by horizon, time-left, distance, spread and liquidity.
5. **Add actual liquidation and event-time L2 data.** These are genuinely new information sources for 1-30 second path risk.
6. **Freeze every candidate before the final window.** Use purged chronological tests, whole-day block bootstrap and at least one later independent month.
7. **Control experiment count.** Record every attempted feature/head/policy and apply false-discovery/backtest-overfitting checks.
8. **Optimize selective net EV.** Prefer fewer positive-lower-bound trades over higher all-round accuracy.
9. **Paper trade with realistic execution.** Use observed bids/asks, depth, latency and rejected fills.
10. **Promote in stages.** SHADOW -> PAPER -> tiny capped live only after independent positive net-EV evidence and hard drawdown controls.

## 11. Next Build Order

| Order | Build | Requires retraining? | Why now |
|---:|---|---|---|
| 1 | Recorder completeness and health audit | No | Every profit prediction depends on trustworthy quotes/outcomes |
| 2 | Polymarket depth/size/trade and timestamp fields | No | Enables executable edge and fills |
| 3 | Edge half-life and quote-underreaction analysis | New research head | Closest untested source of profit |
| 4 | Fair-value residual and net-EV head | Yes, after 200+ joined rounds | Converts forecasts into price decisions |
| 5 | First-achievable-exit and taker-fill model | Yes, after enough quote sequences | Removes hindsight-best-exit bias |
| 6 | Actual Bybit/Binance liquidation recorder | No initially | Replaces weak cascade proxy |
| 7 | Synchronized Binance/Coinbase/Bybit event-time L2 recorder | No initially | Tests genuine microstructure information |
| 8 | Liquidation/L2 specialist heads | Yes, after weeks of coverage | Independent 1-30 second risk information |
| 9 | Conditional champion/meta-policy | Yes, after 500-1,000 independent decisions | Learns selection only after base evidence exists |

## 12. Promotion Contract

A prediction head may move from research to shadow only when:

- its target has a precise economic meaning;
- every feature is causal and available identically in train and live serving;
- it beats an appropriate naive and market baseline;
- probability calibration is acceptable on untouched data;
- its effect survives horizon, month and regime slices without sign reversal;
- selection thresholds are frozen before the final test;
- the sample count and Wilson/bootstrap lower bound are displayed.

A betting action may move beyond PAPER only when:

- fair value exceeds executable ask plus exact fees, slippage and safety buffer;
- quote/outcome joins are official and one-entry-per-independent-round;
- fill and exit assumptions are observed or conservative;
- net expectancy, profit factor and lower confidence bound are positive in a later untouched period;
- maximum drawdown, expected shortfall, daily loss and position limits are enforced;
- feed failure, stale quote and model-version rollback controls are tested.

## Final Answer

The app should use its many models as specialist measurement instruments, not as a crowd voting on UP or DOWN. Its strongest existing capability is deciding **whether, how far, how fast and how dangerously BTC may move**. Its weakest capability is predicting the exact 5m/15m closing side.

The next ceiling break is unlikely to come from another model family. It is most likely to come from modeling **mispricing and execution** with public Polymarket books, synchronized event-time exchange data and actual liquidations. Until those models are trained on sufficient forward records, the correct output remains decision support and PAPER actions, not an assertion of guaranteed profit.

## Related Evidence

- `TP50_SL10_WALKFORWARD_AUDIT_2026-07-01.md`
- `POLYMARKET_SHOCK_SHARE_REPLAY_RESULTS_2026-07-01.md`
- `POLYMARKET_MARKET_RESPONSE_TEST_2026-07-01.md`
- `ROUND_STATE_AND_STOPPING_RESULTS_2026-07-01.md`
- `DECISION_HEAD_RESEARCH_RESULTS_2026-07-01.md`
- `EXISTING_DATA_PATH_DYNAMICS_RESULTS_2026-07-01.md`
- `ANCHOR_ROUNDTRIP_180D_RESULTS_2026-07-01.md`
- `MODEL_RESEARCH_CATALOG_AND_APP_PROPOSAL_2026-06-17.md`
- `QUANT_RESEARCH_100_CEILING_BREAK_IDEAS_2026-06-30.md`
- `PROFITABILITY_AND_BETTING_VALIDATION_2026-07-01.md`
