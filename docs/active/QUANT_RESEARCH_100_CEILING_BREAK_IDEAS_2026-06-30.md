# Quant Research: 100 Ceiling-Break Experiments

Date: 2026-06-30  
Status: research backlog, not production claims  
Scope: BTC 5m/15m prediction, path forecasting, Polymarket pricing, execution, and risk

## Purpose

This document turns ideas from foundational market-microstructure, volatility, derivatives,
crypto-market, validation, and execution research into 100 concrete experiments for this app.
It is not a promise that 100 new features will create profit. It is a controlled search for
new information that the current system has not already exhausted.

The current measured boundary matters:

- the app has 136 raw features and a selected 69-feature model input;
- generic 5m/15m close direction remains near coin-flip after many model families and feature sets;
- path magnitude, barrier touch, range, volume/activity, and P(Hold) are materially more learnable;
- the Polymarket quote-to-settlement sample is still too small to prove executable edge;
- profitability remains unproven.

Therefore, these experiments are not a license to add another giant ensemble. Each candidate must
either add genuinely new information, improve a learnable target, improve executable pricing, or
prevent false deployment.

## Plain-English Thesis

The app has become good at squeezing information from ordinary candles. The remaining ceiling is
unlikely to break because of a new RSI formula or a larger neural network. It may break through:

1. seeing the order book as a sequence of events rather than a static snapshot;
2. measuring which exchange discovers price first;
3. forecasting path, volatility, touch, and survival instead of forcing endpoint direction;
4. comparing option-implied and prediction-market probabilities with the app's probabilities;
5. modeling whether an apparent edge can actually be filled before it disappears;
6. rejecting research discoveries that are statistical luck.

## Evidence And Availability Codes

| Code | Meaning |
|---|---|
| `NOW-BF` | Backfillable now from the existing matrix or public Binance trade/candle archives. |
| `FREE-BF` | Historical data is available from a free/public external source, subject to its terms. |
| `HYBRID` | Some history is public; full fidelity requires recording forward. |
| `FWD` | Free live stream or API, but reliable event history must be recorded from now onward. |
| `P0` | Highest-value next experiment; directly supports the current product. |
| `P1` | Strong candidate after P0 infrastructure exists. |
| `P2` | Useful research, usually slower-frequency or dependent on more data. |
| `P3` | Advanced policy research; do not deploy before the evidence and simulator are trustworthy. |

`Free` does not automatically mean unrestricted commercial use. API and dataset terms must be
checked before production deployment.

## Non-Negotiable Research Protocol

Every experiment below must use the same discipline.

1. Write the hypothesis, target, feature formula, horizon, and success threshold before testing.
2. Fit only on past data. Embargo overlapping labels around every split.
3. Keep one final chronological month untouched until the implementation is frozen.
4. Report AUC/Brier/calibration for probabilities and MAE/coverage for numeric forecasts.
5. For trading claims, report bid/ask fills, fees, latency, slippage, profit factor, expectancy,
   drawdown, and the number of independent rounds.
6. Compare against the current champion, persistence, market probability, and a simple baseline.
7. Log every attempted variant, including failures. Apply false-discovery control across trials.
8. Require stability by month, horizon, regime, and venue. A single good aggregate score is not enough.
9. Promote in shadow mode first. Never let a research artifact silently replace a live champion.
10. Prefer the smallest feature set that retains the result.

Minimum promotion gates:

| Output | Minimum research gate |
|---|---|
| Probability head | Positive lift in every recent walk-forward block; calibrated or explicitly rank-only. |
| Path regression | Positive skill versus persistence plus stable interval coverage. |
| Polymarket strategy | At least 500 independent settled rounds and positive net lower confidence bound. |
| Execution policy | Positive after conservative fees, slippage, latency, and missed-fill assumptions. |
| New live feature | Train/serve parity test, freshness monitor, null-rate monitor, and restart persistence. |

## Recommended First Wave

Do not launch 100 tests simultaneously. The best first wave is:

1. event-time normalized order-flow imbalance;
2. microprice residual and queue-depletion hazard;
3. sweep refill half-life and impact resilience;
4. rolling cross-venue price-discovery share;
5. noise-robust realized volatility and roughness;
6. competing-risk time-to-touch models;
7. option-implied barrier/touch probability;
8. Polymarket edge duration and executable depth;
9. maker/taker fill probability;
10. feature half-life and multiple-testing controls.

These attack missing information and execution rather than repeating the direction bakeoff.

---

## A. Event-Time Order Book And Trade Flow (1-10)

The app already has OBI, CVD, spread, walls, VPIN, spoofing, absorption, and queue-pressure fields.
These ideas are different: they model event sequencing, depletion, refill, impact, and decay.

| # | Pri | Research idea | Exact implementation | Data | Best target | First kill test |
|---:|:---:|---|---|---|---|---|
| 1 | P0 | Depth-normalized event OFI | Reconstruct every bid/ask add, cancel, and execution; compute Cont-style OFI divided by contemporaneous depth at levels 1/5/10/20. Aggregate by event count and clock time. | FWD | next $25/$50 touch, P(Hold) stress | No walk-forward lift over current OBI+CVD in three consecutive months. |
| 2 | P0 | Microprice residual | Compute size-weighted microprice, then `microprice-mid`, z-score, velocity, and residual after removing spread and imbalance effects. | HYBRID | very-short touch hazard, late-round P(Hold) | Residual adds no conditional lift after OBI and spread. |
| 3 | P0 | Queue-imbalance term structure | Fit imbalance as a function of depth level and distance from mid; use slope, curvature, sign changes, and concentration instead of three independent OBI snapshots. | FWD | touch side as confirmation only; path risk | Shape statistics are unstable across book-depth settings or exchange reconnects. |
| 4 | P1 | Add/cancel hazard asymmetry | Estimate rolling hazard rates for bid additions, bid cancellations, ask additions, and ask cancellations, normalized by resting depth. | FWD | liquidity vacuum, barrier-touch probability | Hazards do not lead spread/range after excluding same-timestamp leakage. |
| 5 | P0 | Queue-depletion survival | Model time until best bid or ask queue depletes using queue size, trade intensity, cancellation intensity, and refill rate. | FWD | time-to-next-price-change, fill risk | Survival model fails to beat a queue-size-only baseline. |
| 6 | P0 | Sweep refill half-life | Detect multi-level market-order sweeps; measure how quickly consumed depth returns on each side and whether it returns at the same price. | FWD | continuation versus temporary impact | Refill speed has no stable relation to subsequent range or reversal. |
| 7 | P1 | Concave impact exponent | Fit causal size-to-impact curves `impact = lambda * size^delta` by buy/sell side and liquidity state; track the exponent and tail-cost asymmetry instead of one linear Kyle-lambda value. | NOW-BF | adverse-move risk, trade sizing | Nonlinear curve does not improve cost/path forecasts over the existing linear impact feature. |
| 8 | P0 | Impact-resilience curve | After a trade or sweep, fit the fraction of price impact remaining after 1s/5s/15s/30s; classify transient versus permanent impact. | FWD | exit timing, P(Hold) decay | Curve parameters are too noisy after minimum event/sample filters. |
| 9 | P1 | Hawkes reflexivity ratio | Fit rolling self- and cross-exciting intensities for buy trades, sell trades, bid cancels, and ask cancels; use branching ratio and stability margin. | FWD | activity burst, jump/touch hazard | Estimated kernels are unstable or do not outperform simple event-rate features. |
| 10 | P1 | Marked-Hawkes pressure | Weight Hawkes events by size, distance from mid, and whether they cross multiple levels; derive expected near-term buy/sell event imbalance. | FWD | touch ordering as a weak confirmer | Marked model does not beat unmarked intensity after out-of-sample calibration. |

## B. Cross-Venue Price Discovery And Liquidity (11-20)

The current Coinbase premium and Bybit/Binance fields are snapshots. These experiments estimate who
leads, whether the lead is executable, and how liquidity migrates.

| # | Pri | Research idea | Exact implementation | Data | Best target | First kill test |
|---:|:---:|---|---|---|---|---|
| 11 | P0 | Rolling Hasbrouck information share | Synchronize Binance, Coinbase, Kraken, OKX, and Bybit midprices; estimate each venue's contribution to the common efficient price in rolling windows. | HYBRID | venue lead, stale-price detection | Information shares are not stable enough to improve a fixed consensus price. |
| 12 | P1 | Gonzalo-Granger component share | Fit cointegrated venue prices and estimate which venue drives permanent versus transitory price moves. | HYBRID | consensus fair price, arbitrage filter | Component shares collapse when estimated only on causal rolling windows. |
| 13 | P1 | Lead-lag network centrality | Build directed lagged-response edges among venues using only past ticks; derive leader centrality and leader-switch events. | FWD | short touch hazard, data-quality routing | Network leadership is explained entirely by timestamp/latency differences. |
| 14 | P1 | Asynchronous covariance | Use Hayashi-Yoshida-style covariance rather than forced one-second forward fills to measure venue coupling without synchronization bias. | FWD | divergence confidence | It produces no more stable lead estimate than causal resampling. |
| 15 | P0 | Venue-specific flow residual | Predict each venue's signed flow from global flow, then retain the unexplained Coinbase/Binance/Bybit residual. | HYBRID | institutional-flow confirmation, path pressure | Residual has no lift after global CVD and premium velocity. |
| 16 | P0 | Executable cross-venue spread | Compute best simultaneous buy/sell route after fees, depth, transfer constraints, and stale-quote rejection; store duration and available notional. | FWD | Polymarket fair-value confidence, market-quality state | Apparent spreads vanish under realistic depth and latency. |
| 17 | P1 | Spot-perpetual shock propagation | Detect innovations in spot, perpetual basis, funding, and OI; estimate causal response order and decay. | HYBRID | liquidation/path regime | Futures innovations do not improve path/touch after spot flow and volatility. |
| 18 | P1 | Stablecoin quote triangle | Compare BTC/USD, BTC/USDC, and BTC/USDT fair prices; model depeg-adjusted basis, velocity, and venue dispersion. | HYBRID | fair-value quality, risk veto | Quote-currency effects are smaller than costs or are exchange-specific artifacts. |
| 19 | P0 | Venue staleness/outage contagion | Track quote age, sequence gaps, reconnects, spread explosions, and divergence before/after venue outages. | FWD | feed-quality veto, confidence haircut | Outage states do not measurably worsen calibration or fills. |
| 20 | P1 | Liquidity migration index | Measure the share of top-of-book depth and aggressive volume moving between venues over 10s/1m/5m. | FWD | leader-switch, path magnitude | Migration is contemporaneous only and does not lead touch/range. |

## C. Volatility, Jumps, And Path Geometry (21-30)

The app has proved that magnitude is more learnable than direction. These ideas refine magnitude,
timing, jump risk, and barrier order. They are not additional close-direction models.

| # | Pri | Research idea | Exact implementation | Data | Best target | First kill test |
|---:|:---:|---|---|---|---|---|
| 21 | P0 | Rough-volatility exponent | Estimate rolling log-variogram slope/Hurst exponent of realized volatility at multiple scales; track roughness level and change. | NOW-BF | range, touch, interval width | Roughness adds no lift over realized-vol level and acceleration. |
| 22 | P0 | Realized-kernel volatility | Build noise-robust realized variance from trades or one-second prices using realized kernels instead of raw squared returns. | HYBRID | calibrated path bands, P(Hold) | Kernel RV does not improve coverage/width versus current RV at equal latency. |
| 23 | P1 | Intraminute jump segmentation | Separate continuous and jump variation using high-frequency bipower/truncated variation; unlike the prior one-minute proxy, locate jump times inside the minute. | HYBRID | barrier-touch hazard, risk veto | Event-time jumps add no lift beyond one-minute range and volume spikes. |
| 24 | P1 | Signed jump-intensity state | Estimate separate positive/negative jump arrival rates and jump-size distributions without using future endpoint direction. | HYBRID | downside-tail risk, big-drop head | Signed intensities are not stable by month or merely relabel recent returns. |
| 25 | P0 | Volatility-of-volatility | Forecast changes in realized volatility itself: rolling variance of RV, vol-of-vol acceleration, and percentile transitions. | NOW-BF | interval width, touch probability | No improvement in conditional coverage or Brier score. |
| 26 | P0 | HAR residual surprise | Fit causal HAR-RV; use the residual between observed and forecast volatility as a shock state rather than another raw vol feature. | NOW-BF | path-plan confidence, regime alarm | Residual is redundant with vol acceleration and volume surprise. |
| 27 | P1 | Quantile HAR | Forecast 10th/50th/90th percentiles of future range and realized variance, not just the mean. | NOW-BF | asymmetric high/low bands | Coverage is not stable or intervals are wider than current quantile head without better coverage. |
| 28 | P0 | Intrinsic-time path clock | Measure how quickly the round consumes expected variation, volume, and event-count budgets; forecast remaining path opportunity in intrinsic time rather than wall-clock minutes. | NOW-BF/HYBRID | touch timing, late-round P(Hold) | Intrinsic clock does not improve touch timing over seconds remaining plus realized volatility. |
| 29 | P0 | Joint excursion distribution | Model future max-up and max-down jointly with a copula or multivariate quantiles; derive probability of touching both sides, round-trip size, and excursion asymmetry. | NOW-BF | round-trip/path-plan head | Joint model does not improve both-touch or range calibration over separate high/low models. |
| 30 | P1 | L2-conditioned post-touch state | Re-test continuation/reversal only after a barrier touch, using true refill, OFI, and impact-resilience features unavailable in the previous coarse probe. | FWD | early exit versus hold | No lift over touch timing and current lead after 500+ events per bucket. |

## D. Options And Derivatives-Implied Information (31-40)

The app has basic options snapshots. It has not yet built a causally aligned historical volatility
surface, option-flow history, or option-implied barrier probabilities.

| # | Pri | Research idea | Exact implementation | Data | Best target | First kill test |
|---:|:---:|---|---|---|---|---|
| 31 | P0 | Variance-risk premium | Compare Deribit implied variance/DVOL with forecast realized variance at matching maturity; track level, z-score, and shock. | FREE-BF | magnitude regime, confidence | It adds no path/touch lift beyond realized volatility alone. |
| 32 | P1 | IV term-structure factors | Fit level, slope, and curvature across option expiries; record inversions and rapid factor changes. | HYBRID | jump/touch risk | Factors are too slow for 5m/15m and provide no useful veto. |
| 33 | P1 | Skew-surface dynamics | Fit delta-normalized put/call skew and curvature by expiry; use changes, not just current 25-delta skew. | HYBRID | downside-tail risk, big-drop | Skew changes lag spot moves and add no out-of-sample lift. |
| 34 | P0 | Risk-neutral barrier probability | Infer probability mass above/below exact round anchors and dollar barriers from the option surface, with maturity interpolation. | HYBRID | independent P(Hold)/touch estimate | Surface-derived probabilities are poorly calibrated at short horizons or dominated by spread. |
| 35 | P0 | Option-implied touch probability | Convert terminal risk-neutral distribution and implied volatility into one-touch approximations; compare with the path forecaster. | HYBRID | champion disagreement, interval pricing | No incremental value over current path model and DVOL. |
| 36 | P1 | Option net-buying pressure | Classify option trades as buyer/seller initiated; aggregate delta-, vega-, and gamma-weighted net pressure by expiry and strike. | FWD | tail/jump regime | Trade-side inference is unreliable or pressure is contemporaneous only. |
| 37 | P1 | IV-surface residual shock | Predict each contract's IV from smooth surface factors; aggregate abnormal residual changes and cross-sectional dispersion. | FWD | informed-flow anomaly | Residual shocks do not lead path risk after spot moves are removed. |
| 38 | P1 | Gamma concentration map | Estimate gamma exposure concentration around strikes and distance from spot; track pinning zones and gamma cliffs without claiming dealer sign as observed fact. | HYBRID | support/resistance confidence, range state | No repeatable relation to range compression/expansion around expiry. |
| 39 | P2 | Vanna/charm flow proxy | Estimate how option delta changes with volatility and time; construct scenario-based hedging-flow pressure under clearly stated sign assumptions. | HYBRID | event/risk scenario only | Results change sign under reasonable dealer-position assumptions. |
| 40 | P0 | Options-perp disagreement | Compare option-implied tail/touch probabilities with perpetual funding, OI, liquidations, and basis; flag disagreement rather than averaging them. | HYBRID | regime veto, opportunity rank | Disagreement buckets do not improve calibration or expected edge. |

## E. On-Chain, Mempool, Miner, And Stablecoin State (41-50)

These are mostly regime or stress features. Slow on-chain data should not be sold as a direct 5m
direction oracle. The useful question is whether it changes touch, tail, liquidity, or confidence.

| # | Pri | Research idea | Exact implementation | Data | Best target | First kill test |
|---:|:---:|---|---|---|---|---|
| 41 | P1 | Mempool fee-bucket pressure | From a Bitcoin node or public mempool API, record pending vbytes and value by fee-rate bucket; use concentration and acceleration. | FREE-BF/HYBRID | activity/jump regime | No lift beyond exchange volume and realized volatility. |
| 42 | P1 | Fee-pressure acceleration | Model first/second differences of median and high-priority fees plus the gap between fee tiers. | FREE-BF | stress/activity regime | Signal is too delayed or only reflects already observed price volatility. |
| 43 | P2 | Block-fullness surprise | Compare block weight, transaction count, fees, and interval with causal expectations; retain standardized surprises. | FREE-BF | medium-horizon activity | No stable relation to 5m/15m path outcomes. |
| 44 | P1 | Confirmation-time quantiles | Forecast mempool transaction confirmation-time distribution from fee buckets and block arrival state. | FREE-BF | network-congestion regime | Does not add market information; keep as network dashboard only. |
| 45 | P1 | Large-value transfer tail | Count and sum transfers above rolling value quantiles, excluding known change/self-churn heuristics where possible. | FREE-BF | tail-risk/activity | Results disappear after removing batching/change artifacts. |
| 46 | P2 | Transaction-value distribution shape | Compute median, upper quantiles, Gini/concentration, and tail share of on-chain transfer value. | FREE-BF | slower risk regime | No incremental monthly stability versus simple transaction volume. |
| 47 | P2 | Coin-age spending pressure | Track spent-output age bands, dormancy, and coin-days destroyed using open chain data or verified community metrics. | FREE-BF | slow distribution-risk veto | Too slow for product horizons or unavailable at sufficient frequency. |
| 48 | P2 | Miner stress composite | Combine hash rate, difficulty, fee share, block subsidy value, and estimated hashprice; detect rapid miner-revenue compression. | FREE-BF | multi-day regime only | No value for app decisions after a properly lagged alignment. |
| 49 | P1 | Stablecoin supply impulses | Track USDT/USDC issuance/redemption and chain-level supply changes; distinguish minting from exchange arrival. | FREE-BF | liquidity regime | Supply changes do not lead exchange liquidity after release timing is respected. |
| 50 | P1 | Stablecoin venue-flow timing | Track stablecoin movement into known exchange clusters or public aggregate exchange-flow metrics; model lag to BTC volume/volatility. | HYBRID | activity and tail risk | Address labels are unreliable or results fail across metric providers. |

## F. Macro, Institutional, And Scheduled-Event State (51-60)

These features are usually context or risk gates. Use ALFRED vintages where revisions matter; never
train with today's revised history as if it were known in the past.

| # | Pri | Research idea | Exact implementation | Data | Best target | First kill test |
|---:|:---:|---|---|---|---|---|
| 51 | P1 | Dollar-liquidity surprise | Build a causal composite from the broad dollar, real yields, credit spreads, and liquidity series; use release-time changes and rolling residuals. | FREE-BF | regime/confidence | Adds no lift after BTC's own volatility and US-session state. |
| 52 | P1 | Real-yield shock | Use changes in inflation-indexed Treasury yields, aligned to their actual availability, and interactions with BTC volatility. | FREE-BF | tail-risk veto | Slow frequency makes the signal unusable for 5m/15m. |
| 53 | P2 | Yield-curve PCA shock | Fit causal level/slope/curvature factors to Treasury yields and retain factor innovations rather than many correlated rates. | FREE-BF | macro regime | No stable conditional difference in path/touch outcomes. |
| 54 | P2 | Funding-stress state | Combine SOFR, secured funding spreads, commercial-paper/credit stress, and financial-condition changes. | FREE-BF | liquidity-risk regime | The state changes too slowly to affect current product decisions. |
| 55 | P1 | Fed balance-sheet liquidity impulse | Build a weekly net-liquidity proxy from Fed assets, Treasury General Account, and reverse repo; use only as a slow prior. | FREE-BF | weekly risk budget | No incremental value in month-by-month holdout. |
| 56 | P1 | CFTC BTC positioning change | Parse official COT data for CME Bitcoin contracts; derive leveraged-fund and asset-manager net-position changes normalized by OI. | FREE-BF | medium-horizon crowding | Weekly data does not improve any supported target. |
| 57 | P1 | CME open/gap dislocation | Measure BTC price movement while CME is closed, then convergence, continuation, and volatility around reopen. | HYBRID | Sunday/Monday path risk | Effect is not stable after transaction costs and daylight-saving handling. |
| 58 | P1 | Spot-ETF share/holding impulse | Collect official issuer holdings/shares outstanding and calculate net BTC/share change using publication timestamps. | FREE-BF | US-session liquidity regime | Publication timing is ambiguous or signal is already in price/volume. |
| 59 | P1 | First-release macro shock | Use ALFRED vintages and official release timestamps to compute standardized first-release changes for CPI, payrolls, and Fed decisions without hindsight revisions. | FREE-BF | event-time volatility/touch | Too few events or no stable improvement over an event-calendar flag. |
| 60 | P1 | Cross-asset co-jump state | Detect simultaneous BTC, equity-index, rates, dollar, gold, and volatility-index jumps around US hours using causally available prices. | HYBRID | systemic-risk veto | Cross-asset data latency or licensing makes the feature non-reproducible. |

## G. Polymarket Price Formation And Executable Edge (61-70)

This is the closest group to a profit test. All results must be based on one causal entry per independent
round, the actual executable side of the book, and official settlement.

| # | Pri | Research idea | Exact implementation | Data | Best target | First kill test |
|---:|:---:|---|---|---|---|---|
| 61 | P0 | Quote-age and stale-price score | Store exchange timestamp, receive timestamp, last book update, last trade, and BTC movement since each update. | FWD | edge validity | Stale score does not explain apparent edge or realized outcomes. |
| 62 | P0 | Edge duration and half-life | For `fair probability - executable ask - costs`, measure how long each edge tier survives and its decay after detection. | FWD | entry latency budget | Median edge disappears faster than a realistic order can arrive/fill. |
| 63 | P0 | BTC-shock underreaction | Event-study Polymarket repricing after BTC moves toward/away from the anchor; estimate causal response delay by round age and liquidity. | FWD | taker-entry opportunity | Underreaction vanishes after excluding stale/missing quotes. |
| 64 | P0 | Complement-book arbitrage | Monitor `UP ask + DOWN ask`, `UP bid + DOWN bid`, fees, and depth; identify executable sum-probability violations. | FWD | market-quality/arbitrage | Violations vanish after fees, minimum size, and simultaneous-depth checks. |
| 65 | P0 | Depth-adjusted executable edge | Integrate the book to proposed stake size; compute average fill price and remaining edge at every depth tier. | FWD | stake sizing | Top-of-book edge turns negative at practical size. |
| 66 | P0 | Fill-probability model | Predict fill before expiry/reprice from queue position proxy, spread, depth, trade intensity, order age, and distance to touch. | FWD | maker execution | Model is not calibrated or cannot beat empirical fill rate by price distance. |
| 67 | P0 | Maker-versus-taker selector | Estimate net EV of immediate taker fill versus maker order including fill probability, adverse selection, missed edge, and fee schedule. | FWD | execution choice | Selector underperforms always-taker and always-maker baselines out of sample. |
| 68 | P1 | Market-maker response function | Estimate how quotes, spread, and depth react to BTC distance, volatility, and time remaining; use residual mispricing from that expected response. | FWD | relative-value signal | Residual is not stable across market makers or liquidity regimes. |
| 69 | P1 | Cross-horizon probability consistency | Compare overlapping 5m and 15m contracts through conditional probability constraints and anchor relationships. | FWD | bad-quote detector | Apparent inconsistencies are not simultaneously tradable. |
| 70 | P0 | Option-versus-Polymarket probability | Compare short-horizon option-implied terminal/touch probabilities, app P(Hold), and executable Polymarket prices; trade only robust disagreement. | HYBRID | independent fair-value ensemble | Disagreement is not calibrated or costs consume the spread. |

## H. Better Labels, Targets, And Selective Decisions (71-80)

These experiments change the question the model answers. They are especially important because the
existing endpoint-direction question is weak.

| # | Pri | Research idea | Exact implementation | Data | Best target | First kill test |
|---:|:---:|---|---|---|---|---|
| 71 | P0 | Upper/lower/no-touch competing risk | Jointly estimate cause-specific probability for upper first, lower first, or no touch. | NOW-BF | path-plan head | No Brier/log-loss lift over separate binary classifiers. |
| 72 | P0 | Time-to-touch survival | Predict survival curve for each dollar barrier and update it causally as the round progresses. | NOW-BF | P(Hold), exit timing | Poor time calibration or no lift over distance/volatility formula. |
| 73 | P0 | No-touch probability | Train a dedicated probability that neither selected barrier is reached before expiry, with exact-dollar labels. | NOW-BF | quiet/avoid head | Duplicates one minus touch probability without calibration benefit. |
| 74 | P0 | Maximum adverse excursion | Predict conditional MAE distribution for each proposed side and entry time, even when endpoint call is correct. | NOW-BF | stop/risk sizing | Quantiles have unstable coverage or no advantage over current low/high band. |
| 75 | P0 | Maximum favorable excursion | Predict MFE distribution and probability of reaching feasible take-profit levels before expiry. | NOW-BF | exit target, opportunity rank | Does not improve realized capture versus fixed target tiers. |
| 76 | P1 | Path entropy/choppiness target | Label windows by directional alternation, permutation entropy, and path efficiency; predict smooth versus whipsaw paths. | NOW-BF | execution mode | Labels are highly sensitive to sampling frequency or do not affect costs. |
| 77 | P0 | Realized spread/cost target | Predict future executable spread, depth slippage, and adverse-selection cost rather than assuming a constant fee/spread. | FWD | net EV gate | Cost model is no better than a rolling median and does not change decisions. |
| 78 | P0 | Fill-before-edge-decay label | A signal is positive only if the intended order could fill while net edge remained above the required buffer. | FWD | execution classifier | Too few trustworthy order/fill events or no better than taker-only logic. |
| 79 | P0 | Net-profitable-after-cost label | Label the entire causal decision, including entry side, actual fill, optional exit, settlement, fees, and slippage. | FWD | final champion meta-head | Fewer than the required independent outcomes or no stable positive lower bound. |
| 80 | P0 | Selective-risk/abstention target | Optimize error at fixed coverage or net EV at fixed trade count; learn when to abstain, not just direction. | NOW-BF/FWD | BUY/SELL/AVOID gate | Retained-call precision does not improve on untouched recent data. |

## I. Validation, Decay, Shift, And False-Discovery Control (81-90)

These ideas do not generate alpha directly. They stop the project from mistaking noise for alpha,
which is equally important after many experiments.

| # | Pri | Research idea | Exact implementation | Data | Best target | First kill test |
|---:|:---:|---|---|---|---|---|
| 81 | P0 | Combinatorial purged CV | Evaluate candidate policies across multiple purged train/test path combinations with embargoed overlapping labels. | NOW-BF | every research claim | Candidate fails most paths despite one strong chronological split. |
| 82 | P0 | Deflated Sharpe ratio | Record the number/correlation of trials and deflate observed Sharpe for selection, non-normality, and sample length. | NOW-BF/FWD | strategy promotion | DSR does not support skill after counting all tried variants. |
| 83 | P0 | Probability of backtest overfitting | Use combinatorially symmetric CV to estimate how often the selected winner ranks below median out of sample. | NOW-BF | model/policy selection | PBO remains high; candidate cannot be promoted. |
| 84 | P0 | Reality-check/SPA test | Test the best candidate against a family of alternatives while controlling data snooping with bootstrap dependence. | NOW-BF | research-family approval | Candidate fails White Reality Check or Hansen SPA. |
| 85 | P0 | False-discovery-rate ledger | Give every experiment a permanent ID and p-value/effect estimate; control Benjamini-Hochberg FDR by research family. | NOW-BF | governance | Discovery does not survive the declared FDR level. |
| 86 | P0 | Feature half-life and decay | Estimate rolling permutation/SHAP importance, marginal lift, sign, and rank by month; fit decay/half-life and retirement rules. | NOW-BF/FWD | feature governance | Feature has short/unstable half-life and no causal live analogue. |
| 87 | P1 | Invariant predictor search | Seek small feature sets whose conditional relationship survives venues, months, volatility regimes, and session changes. | NOW-BF/HYBRID | robust specialist heads | Effect depends on one environment or reverses sign. |
| 88 | P0 | Adaptive conformal intervals | Adjust path/price interval widths online to recover target coverage under drift; log width versus miss rate. | NOW-BF/FWD | high/low/range bands | Coverage fails under shift or intervals become operationally useless. |
| 89 | P1 | Conformal martingale drift alarm | Convert sequential nonconformity scores into an anytime-valid change alarm for calibration and residual shift. | FWD | automatic confidence haircut | Alarm is too noisy or detects changes only after existing monitors. |
| 90 | P0 | Online change-point ensemble | Combine CUSUM/Bayesian online change detection on calibration, residuals, spread, and flow; reset only affected heads. | NOW-BF/FWD | regime/health state | Resets worsen untouched-period performance or create excessive churn. |

## J. Execution, Sizing, And Adaptive Policy (91-100)

These should be built only after fair probabilities, fill data, and cost models are credible. A policy
optimizer cannot manufacture edge; it can only allocate and execute measured edge.

| # | Pri | Research idea | Exact implementation | Data | Best target | First kill test |
|---:|:---:|---|---|---|---|---|
| 91 | P1 | Uncertainty-haircut fractional Kelly | Size from net edge and payoff odds, then shrink by calibration error, model disagreement, and confidence-interval width. | FWD | stake sizing | Worse drawdown-adjusted return than fixed tiny stakes. |
| 92 | P1 | Drawdown-constrained Kelly | Optimize growth subject to hard stake, daily loss, expected shortfall, and maximum-drawdown budgets. | FWD | capital preservation | Constraint estimates are unstable at current sample size. |
| 93 | P0 | Reservation-edge controller | Require `fair - executable price - fees - slippage - model-risk buffer` above a dynamic minimum tied to liquidity and uncertainty. | FWD | final trade gate | Dynamic gate does not improve net expectancy over fixed buffer. |
| 94 | P2 | Impact-aware execution schedule | Adapt Almgren-Chriss concepts to split larger crypto orders across time based on temporary impact, volatility, and deadline. | FWD | larger spot hedge/execution | Order sizes are too small for measurable impact or schedule loses to immediate execution. |
| 95 | P2 | Inventory-aware maker quotes | Adapt Avellaneda-Stoikov reservation price/spread to prediction-market maker orders, inventory, time to expiry, and binary payoff risk. | FWD | maker mode | Simulated gains vanish with realistic fill priority and adverse selection. |
| 96 | P0 | Queue-position fill simulator | Replay order-book updates with conservative queue-ahead assumptions, partial fills, cancels, latency, and disconnects. | FWD | all execution research | Simulator cannot reproduce observed paper/live fills within tolerance. |
| 97 | P1 | Cancel/requote policy | Learn when to leave, improve, or cancel a maker order from edge decay, queue loss, adverse flow, and time remaining. | FWD | maker execution | Policy overtrades or underperforms simple timeout rules. |
| 98 | P1 | Specialist-head risk budgeting | Allocate risk among P(Hold), touch, big-move, and mispricing opportunities using covariance of realized PnL/errors, not vote count. | FWD | portfolio/champion layer | Diversification benefit disappears out of sample. |
| 99 | P2 | Logged contextual bandit | Choose among pre-approved actions/policies using context and inverse-propensity/doubly robust evaluation; preserve exploration bounds. | FWD | policy selection | Off-policy estimates are unsupported because historical propensities/actions lack coverage. |
| 100 | P3 | Constrained FSR-PPO controller | Revisit financial-signal representation plus PPO only as an offline policy over validated heads; reward net PnL, penalize turnover/drawdown, and restrict actions by risk gates. | FWD | final execution policy | Fails simple rules on multiple untouched periods, is seed-sensitive, or exploits simulator defects. |

---

## Data Acquisition Map

| Source | Free/public use in this program | Backfill expectation | Main ideas |
|---|---|---|---|
| Existing 360-day research matrix | Candles, selected flow proxies, path labels, current heads | Immediate | 21, 25-29, 51-60, 71-86 |
| [Binance public archives](https://data.binance.vision/) | Trades, aggregate trades, candles, futures snapshots where available | Strong for trades/candles; true L2 history is limited | 7, 21-29 |
| [Binance](https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams)/Coinbase/Kraken/OKX/Bybit WebSockets | Trades, top book, diff depth, venue timestamps | Record forward | 1-20, 61-69, 77-79, 96-97 |
| Polymarket CLOB plus official settlements | Quotes, depth, trades, market definitions, outcomes | Existing history is partial; record forward | 61-70, 78-80, 91-100 |
| Deribit public API | DVOL, futures/funding, option books, IV, Greeks, trades | DVOL backfill; full surface/flow is hybrid or forward | 31-40, 70 |
| Coin Metrics Community API | Selected market/on-chain metrics without an API key for non-commercial use | Metric-dependent | 41-50 |
| Bitcoin Core / open block data | Blocks, transactions, UTXOs, mempool if recorded | Chain history is reproducible; mempool is forward unless archived | 41-48 |
| FRED/ALFRED | Macro series, release dates, vintages | Strong; API key required | 51-55, 59-60 |
| CFTC COT | Weekly trader positioning and historical files | Strong | 56 |
| Official ETF issuer pages/filings | Holdings, shares, disclosures | Daily and source-dependent | 58 |

## What Has Already Been Tested Or Should Not Be Repeated

Do not count these as new experiments unless the input information or target genuinely changes:

- more RSI/MACD/CCI/MFI/Williams variants;
- another XGB/LightGBM/CatBoost/RF/Transformer vote on the same candle matrix;
- generic LSTM/GRU/TCN/PatchTST/iTransformer/Mamba direction bakeoffs;
- fractional differentiation as a direction cure;
- one-minute candle versions of standard volatility estimators already tested;
- spot/futures OHLCV and basic funding/OI/basis as another close-direction classifier;
- meta-labeling a direction signal that has no underlying edge;
- hour/regime/streak pockets selected after looking at the test set;
- reinforcement learning before cost, fill, and market-response simulators are validated.

## Experiment Record Template

Each experiment should create a machine-readable row and a short Markdown report.

```text
experiment_id:
idea_id:
hypothesis_frozen_at:
owner:
target_and_horizon:
feature_formula_version:
data_start/data_end:
availability_delay:
train/validation/final_test:
embargo:
trials_in_family:
baseline:
primary_metric:
promotion_threshold:
cost_and_fill_assumptions:
result_by_month/regime/venue:
calibration_or_coverage:
PBO/DSR/FDR_result:
decision: reject | record-forward | shadow | promote
artifact_hash:
```

## Proposed Delivery Order

### Phase 1: Measurement Before Modeling

- Implement experiment registry, FDR ledger, PBO/DSR reports, and feature half-life.
- Validate the microstructure recorder's sequence continuity and timestamp latency.
- Extend Polymarket recording with depth, quote age, and hypothetical maker fills.
- Record synchronized venue books and trades without changing live decisions.

### Phase 2: Highest-Value New Information

- Test ideas 1, 2, 5, 6, 8, 11, 15, 19, 61, 62, 65, and 66.
- Train only specialist targets: touch, time-to-touch, path stress, stale edge, and fill.
- Require walk-forward incremental lift over the current feature/head baseline.

### Phase 3: Independent Probability Sources

- Build a causally aligned Deribit DVOL/option-surface store.
- Test ideas 31, 34, 35, 40, and 70.
- Keep option probabilities independent until calibration and disagreement behavior are measured.

### Phase 4: Policy And Capital

- Build labels 77-79 and simulator 96 first.
- Add reservation edge, tiny fixed-stake shadow, then uncertainty-haircut Kelly.
- Consider contextual bandits/PPO only after a large logged action/outcome sample exists.

## Foundational Research And Primary Sources

### Market Microstructure And Price Discovery

1. Cont, Kukanov, and Stoikov, [The Price Impact of Order Book Events](https://doi.org/10.1093/jjfinec/nbt003).
2. Gould and Bonart, [Queue Imbalance as a One-Tick-Ahead Price Predictor](https://arxiv.org/abs/1512.03492).
3. Bacry, Mastromatteo, and Muzy, [Hawkes Processes in Finance](https://arxiv.org/abs/1502.04592).
4. Hasbrouck, [Intraday Price Formation in U.S. Equity Index Markets](https://users.nber.org/~confer/2003/micros03/hasbrouck.pdf).
5. Makarov and Schoar, [Trading and Arbitrage in Cryptocurrency Markets](https://doi.org/10.1016/j.jfineco.2019.07.001).
6. Alexander and Heck, [Price Discovery in Bitcoin: The Impact of Unregulated Markets](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3583843).

### Volatility, Jumps, And Options

7. Corsi, [A Simple Approximate Long-Memory Model of Realized Volatility](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1365738).
8. Barndorff-Nielsen, Kinnebrock, and Shephard, [Measuring Downside Risk: Realised Semivariance](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1262194).
9. Gatheral, Jaisson, and Rosenbaum, [Volatility Is Rough](https://arxiv.org/abs/1410.3394).
10. Barndorff-Nielsen et al., [Realized Kernels in Practice](https://shephard.scholars.harvard.edu/sites/g/files/omnuum7741/files/KernelMult_25_7_08.pdf).
11. Bollerslev et al., [Stock Return Predictability and Variance Risk Premia](https://www.cambridge.org/core/services/aop-cambridge-core/content/view/0BE5DE1D942A0342DDBA24D7BFBEA5C8/S0022109014000453a.pdf/stock_return_predictability_and_variance_risk_premia_statistical_inference_and_international_evidence.pdf).
12. Pan and Poteshman, [The Information in Option Volume for Future Stock Prices](https://www.nber.org/system/files/working_papers/w10925/w10925.pdf).
13. Hoang and Baur, [How Effective Are Bitcoin Futures and Options in Forecasting Bitcoin Volatility?](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3554542).
14. [Net Buying Pressure and the Information in Bitcoin Option Trades](https://www.sciencedirect.com/science/article/pii/S1386418122000544).

### Crypto, On-Chain, And Public Data

15. Liu, Tsyvinski, and Wu, [Common Risk Factors in Cryptocurrency](https://www.nber.org/papers/w25882).
16. Liu and Tsyvinski, [Risks and Returns of Cryptocurrency](https://www.nber.org/system/files/working_papers/w24877/w24877.pdf).
17. Makarov and Schoar, [Blockchain Analysis of the Bitcoin Market](https://www.nber.org/papers/w29396).
18. Griffin and Shams, [Is Bitcoin Really Un-Tethered?](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3195066).
19. [Coin Metrics Community API documentation](https://docs.coinmetrics.io/api).
20. [FRED/ALFRED API documentation](https://fred.stlouisfed.org/docs/api/fred/overview.html).
21. [CFTC Commitments of Traders documentation](https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm).
22. [Deribit public market-data API](https://docs.deribit.com/api-reference/market-data/public-get_order_book).

### Prediction Markets, Validation, And Execution

23. Ottaviani and Sorensen, [Noise, Information, and the Favorite-Longshot Bias](https://doi.org/10.1257/mic.2.1.58).
24. Bailey et al., [The Probability of Backtest Overfitting](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253).
25. Bailey and Lopez de Prado, [The Deflated Sharpe Ratio](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551).
26. Almgren and Chriss, [Optimal Execution / adaptive execution research lineage](https://web.stanford.edu/~bvr/pubs/adaptive-execution.pdf).
27. Avellaneda and Stoikov, [High-Frequency Trading in a Limit Order Book](https://doi.org/10.1080/14697680701381228).
28. Gibbs and Candes, [Adaptive Conformal Inference Under Distribution Shift](https://arxiv.org/abs/2106.00170).
29. Wang and Wang, [An Adaptive Financial Trading Strategy Based on PPO and Financial Signal Representation](https://doi.org/10.1016/j.engappai.2024.109365).

## Final Decision Rule

The objective is not to make the architecture more impressive. It is to find a small number of effects
that remain calibrated, executable, and positive after costs on untouched data. If none of the P0
experiments passes, the correct result is not to lower the gate. It is to conclude that this data and
product do not currently contain a proven edge.
