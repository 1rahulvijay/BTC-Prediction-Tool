# Research Frontier V4 / V5 / V6 — Options, Microstructure, Topology, Sizing

**Status: PROPOSAL BACKLOG. Nothing here is built, and most of it cannot be built today.**

This merges the V4 (options / microstructure), V5 (adversarial flow / stationarity) and V6
(topology / chaos / distillation / sizing) proposals
into one backlog, because they share a single gating question: *does the archive contain the data
each method consumes?* For five of the eight proposals the answer is no, and no amount of
implementation effort changes that — it is a schema gap, not a coverage gap.

Feasibility below is checked by `backend/research/frontier_feasibility.py`, which reads the
collector and the clients rather than this prose, so the table cannot quietly go stale.

---

## Feasibility summary

| # | Proposal | Verdict | Blocking fact |
|---|----------|---------|---------------|
| 1 | Breeden–Litzenberger RND from Deribit | **REFRAME** | No per-strike chain stored; no expiry matches a 5m contract |
| 2 | Probabilistic L3 / MBO queue reconstruction | **BLOCKED** | No depth stream exists at all |
| 3 | Micro-latency HMM (toxic flow fading) | **BLOCKED** | Spot has no `exch_ts`; home-link jitter >> engine latency |
| 4 | Causal rolling normalization | **BUILD NOW** | Uses existing features; a live bug of this class was just fixed |
| 5 | Spoofing detection via cancel rates | **BLOCKED** | Cancellations are only visible in depth diffs |
| 6 | Liquidation cascade forecasting | **BLOCKED** | `forceOrder` is not recorded; leverage is unobservable |
| 7 | Fractional differencing | **BUILD NOW** | Pure transform on existing price series |
| 8 | Cross-asset VIX / DXY transmission | **BLOCKED** | No free real-time VIX/DXY feed exists |
| 9 | TDA / persistent homology for icebergs | **BLOCKED** | No depth data; and the topology is degenerate |
| 10 | Lyapunov exponents for regime collapse | **NULL-TEST FIRST** | Computable, but LLE on noise reads positive |
| 11 | L2-Transformer + knowledge distillation | **BLOCKED** | The named table contains no L2 sequences |
| 12 | Endogenous Kelly (self-impact) | **BUILD NOW** | Real flaw; the book data needed already exists |

Three are buildable today. One is worth building after a reframe, one after a null test. Seven
need new collection first, and two of those need data not publicly available at any tier we use.

---

## 1. Breeden–Litzenberger — legitimate mathematics, wrong target

The formula is real and standard:

    f(K) = e^(rT) · ∂²C/∂K²

The risk-neutral density is the second derivative of the call price curve in strike. Nothing
about that is disputed. Three things about the proposed application are.

**There is no matching expiry.** The lane trades Polymarket BTC **5-minute and 15-minute**
up/down contracts. Deribit's shortest BTC expiry is *daily*, at 08:00 UTC. Breeden–Litzenberger
yields the density **at the option's expiry**, not at an arbitrary horizon. Extracting a 5-minute
distribution from a daily surface requires assuming a diffusion to interpolate down — and that
assumption, not the surface, then supplies the answer. The claim of "the exact expiration date
matching the Polymarket contract" describes an instrument that does not exist.

**Risk-neutral is not physical.** `f(K)` is a pricing density. Converting it to the real-world
probability that settles a Polymarket contract requires the pricing kernel, and the wedge — the
variance risk premium — is one of the most robustly documented effects in options research, and
is large and time-varying in crypto. So "perfectly price Polymarket contracts and instantly
arbitrage any deviations" is unsound: a persistent gap between the Deribit-implied and Polymarket
prices is the expected state of the world, not an arbitrage.

**The data is not collected.** `DeribitOptionsClient` polls REST every 30 s and keeps five
aggregate scalars (`put_call_ratio`, `skew_25d`, `max_pain`, `atm_iv`, `gex`) in memory. It stores
no per-strike chain and persists nothing. B–L needs the full strike ladder of call prices, and a
30-second poll cannot support anything described as instantaneous.

**Reframed as something true and useful:**

    DERIBIT_RND_FEATURE_V1
      - persist the full per-strike chain (strike, bid, ask, mark IV, expiry) to the recorder
      - fit the smile (SVI or arbitrage-free spline), enforcing no butterfly/calendar arbitrage
      - extract the DAILY risk-neutral density and its moments
      - use RND shape - skew, kurtosis, left-tail mass - as RANKING FEATURES for the daily
        horizon, alongside a measured risk-neutral-to-physical adjustment
      - never as a direct arbitrage trigger against a 5-minute contract

That is a real, defensible campaign. It is not free money, and the horizon mismatch has to be
respected rather than interpolated away.

---

## 2, 5. L3 queue reconstruction and spoofing detection — same blocking fact

Both proposals open by describing what to do with the `depthUpdate` stream.

**There is no depth stream.** Verified in `backend/venues/rl_data_readiness.py`: every recorded
book stream is top-of-book only — `binance_spot/bookTicker`, `binance_perp/bookTicker`,
`bybit_perp/orderbook.1` (the name states the level count). Searching the collector for `@depth`,
`lastUpdateId`, `diffDepth` and `(U, u)` returns zero matches.

Consequences that follow directly:

* **Queue position is uncomputable.** `bookTicker` reports the *aggregate* size resting at the
  touch and never the identity or ordering of the orders composing it. "Record the total volume
  at Price X, then advance our position as volume is removed" cannot start, because no per-price
  volume outside the touch is ever observed.
* **Cancellations are unobservable.** A `cancelled_volume / executed_volume` ratio at price bands
  away from mid requires seeing individual removals at those bands. A spoof wall "50 ticks below
  the mid" is exactly the region the archive never sees.

Even with a full L2 diff feed, note that L2 gives *queue proxies*, not L3 identity. Binance does
not publish per-order IDs on public feeds. The honest ceiling is an interval — optimistic, base
and pessimistic queue-ahead — never an exact position.

**Prerequisite for both:** snapshot + sequenced `@depth@100ms` diffs with `(U, u)` continuity and
recorded resyncs. Until then these are not slow, they are impossible.

---

## 3. Micro-latency HMM — blocked, and the physics is unfavourable

The mechanism (matching-engine queuing under load) is real. Detecting it from here is not.

* **Spot carries no exchange timestamp.** Measured 2026-07-26: spot `bookTicker` has no `E` and
  no `T`, only `u,s,b,B,a,A`, so `exch_ts` is NULL and `recv_ts - exch_ts` does not exist for
  that stream. Futures `bookTicker` does carry one, so the measurement is *only* possible on perp.
* **The signal is below the noise floor.** Matching-engine delays are microseconds to low
  milliseconds. This system runs Python on a consumer connection; scheduling jitter, GC pauses,
  TLS, Wi-Fi retransmits and the 2-second main loop each contribute far more variance. A latency
  HMM here would be fitting the local machine's noise, and would fire its circuit breaker on
  garbage collection.
* **There are no passive orders to pull.** Real orders are disabled and there is no maker order
  path. The described action — "instantly cancel all passive limit orders" — has nothing to act on.

If pursued later, it belongs to a colocated execution stack, not to this one.

---

## 4. Causal rolling normalization — BUILD NOW, with one hard rule

This is the strongest proposal in either document, and its premise is confirmed: unbounded
features do decay across macro regimes.

**One rule makes or breaks it: the normalizer must be causal.** A rolling mean and standard
deviation computed over the whole array leaks the future into every past row. This is not
hypothetical — a bug of exactly this class was found and fixed in this repo days ago:
`regime.py` computed `np.median(volumes)` over whatever array it was handed, so appending a
high-volume future silently rescaled every past observation, *and* serving normalized against a
5-bar window while training used ~1500.

    ROLLING_NORMALIZATION_V1
      - strictly trailing windows: row t uses data through t-1 only
      - normalizer statistics FROZEN at fit time and shipped in the artifact bundle,
        so serving cannot re-derive a different scale (V2 bundles already carry
        feature_schema_hash and feature_semantics_version for exactly this)
      - multiple EW half-lives (1h / 24h / 7d) as separate features, not one blended scale
      - explicit policy for the warm-up period rather than a silently different scale
      - a test that appending future rows cannot change ANY earlier normalized value

Do not promise inputs "between roughly -3 and +3": z-scores of fat-tailed crypto returns exceed
that routinely, and clipping to it destroys the tail information the big-move heads exist to
detect. Standardize, then handle tails deliberately.

---

## 6. Liquidation cascade forecasting — blocked on both inputs

* **Liquidations are not recorded.** `forceOrder` appears nowhere in the collector. The recorded
  perp streams are `bookTicker`, `aggTrade_rest`, `premiumIndex` and `openInterest`.
* **Average retail leverage is not observable.** "Divide total OI by the estimated average
  leverage of retail participants" requires a quantity no exchange publishes. Binance discloses
  neither per-account leverage nor the margin mode distribution. Any "pain point" so derived
  inherits an unfalsifiable assumption, and liquidation *heatmaps* sold commercially are built on
  the same guess.

`forceOrder` is cheap to add and genuinely informative — but as an observed event stream for
studying cascades after they begin, not as a way to compute where they will start.

---

## 7. Fractional differencing — BUILD NOW

Legitimate and well-founded: `(1-L)^d` via binomial expansion, choose the minimum `d` passing an
ADF stationarity test, keeping more memory than first differencing. It is standard practice from
López de Prado's *Advances in Financial Machine Learning* (2018) — widely published and taught,
not "a closely guarded secret of top prop firms."

    FRACTIONAL_DIFFERENCING_V1
      - fixed-width window expansion with a weight-loss threshold
      - d selected on the TRAINING FOLD ONLY, then frozen; selecting d on the full series
        is the same leak class as fitting the HMM on the full series
      - the chosen d and threshold recorded in bundle provenance
      - baselines: raw levels, log returns, first differences
      - retained only if it improves UNTOUCHED economic performance, not ADF p-values

Worth noting the horizon: the lane trades 5-minute contracts. Long-memory retention matters most
for long-horizon forecasting; on a 5-minute barrier its benefit is an empirical question, not a
given.

---

## 8. Cross-asset VIX / DXY transmission — blocked on data availability

The economics are real; the feed is not. There is **no free real-time WebSocket for VIX or DXY**.
VIX is a CBOE-licensed product — free sources are delayed, typically 15 minutes, which is useless
against a "multi-second transmission delay". DXY is ICE-licensed with the same problem. Real-time
entitlements are a paid market-data agreement.

Tradeable proxies do exist on free feeds and are the honest substitute: VIX futures during RTH,
UVXY/VIXY ETFs, EUR/USD and USD/JPY spot for dollar strength. Any campaign should name the proxy
and its basis to the index, not claim the index itself. Also note the delay cuts both ways: if a
retail Python stack could reliably arbitrage a multi-second macro transmission into crypto, that
gap would already be closed by colocated participants.

---

## 9. Topological data analysis for iceberg detection — blocked twice over

**Blocked on data, like every other depth proposal.** Mapping "the L2 order book at any
millisecond into a point cloud" requires an L2 order book. We record the touch.

**And the topology is degenerate even with the data.** An order book at one instant is a set of
(price, size) pairs — a monotone price axis with a size at each level. Persistent homology finds
connected components (H0) and loops (H1); a 1-D ordered sequence of levels has no non-trivial
loops, so H1 is empty by construction and Betti-1 "holes in liquidity" do not exist in that
object. To get interesting topology you must first embed the book into some higher-dimensional
space, and then the *embedding choice* — not the topology — is what produces the signal. The
proposal does not specify one, which is where all the content would be.

An iceberg's actual footprint is a time-series signature: repeated replenishment at one price
after each execution. That is directly detectable with per-level depth diffs and trade prints,
by counting replenishment events. It needs the depth feed, not homology.

## 10. Lyapunov exponents — computable, but null-test before believing anything

Unlike the others this needs no new data: `aggTrade` is recorded and LLE estimators
(Rosenstein, Kantz) run on a return series.

The problem is that it will produce a number regardless. LLE estimation assumes a low-dimensional
deterministic system observed with little noise. Financial returns are high-dimensional and
noise-dominated, and standard estimators return **positive** exponents on pure stochastic
processes — the estimate does not distinguish chaos from randomness. Decades of work on
deterministic chaos in financial series has been largely negative.

"LLE spikes prove a regime collapse is occurring before volatility expands" also inverts the
computation: LLE is estimated over a *window* of past returns, so a window containing the start
of a volatility expansion is a lagging observation of it, not a leading one.

    LYAPUNOV_NULL_TEST_V1
      - estimate LLE on the real return series
      - estimate LLE on matched surrogates: phase-randomised, IID-shuffled, and a
        GARCH fit with the same unconditional variance and clustering
      - if the real series' LLE distribution is indistinguishable from the surrogates,
        the lane closes and nothing is built on it
      - only if it separates: test whether LLE at time t predicts realised volatility
        over (t, t+h] BETTER than trailing realised volatility does

Run the null test before any downstream design. It costs little and will most likely close the
lane, which is the useful outcome.

## 11. L2-Transformer with knowledge distillation — the named source is empty

The proposal says to train "strictly on the historical sequence of L2 `depthUpdate` snapshots
from `multi_venue.duckdb`". That table contains `bookTicker`, `aggTrade`, `aggTrade_rest`,
`premiumIndex`, `openInterest`, `orderbook.1` and `publicTrade`. There are no `depthUpdate`
sequences in it. The teacher has nothing to learn from.

Two further corrections for when the data exists:

* Distillation does **not** give "Transformer-level intelligence at linear speed". The student
  has less capacity and loses accuracy; the technique buys a favourable trade-off, not a free
  one. Its real benefit — softened targets carrying inter-class structure — is a regularizer.
* Latency is not this system's binding constraint. The main loop runs every 2 seconds and the
  measured parquet writer ceiling is ~43–65 writes/s. Nothing here operates at sub-millisecond
  scale, so the problem distillation solves is not a problem this system has.

Sequence models on top-of-book and trade prints are legitimate and buildable *now*; that is a
different, smaller, honest project.

## 12. Endogenous Kelly — correct, and being built

**This is the strongest proposal across V4, V5 and V6, and the diagnosis is right.** Standard
Kelly assumes the odds are exogenous. Polymarket books for 5m/15m contracts are thin, so buying
size walks the ask up and the realised entry is worse than the quoted one. Sizing on the top-of-
book price systematically overstates the edge, and the error grows with size — exactly where it
matters.

One correction to the stated formula. Written as

    f* = argmax[ p·log(1 + f·b(f)) + (1-p)·log(1 - f) ]

`b(f)` must be the odds implied by the **average fill price** across the consumed depth, not the
marginal price at the last level. Using the marginal price understates the cost of the whole
order. The repo already computes VWAP-style entry consumption and a conservative capacity
quantile, so the impact function is measurable from recorded book state rather than assumed.

Implemented in this commit as `endogenous_kelly()` — see `backend/trading_simulator.py` and
`backend/test_endogenous_kelly.py`. It remains research-only: live Kelly sizing stays disabled.

---

## What this backlog actually authorizes

**Now, on existing data:** `ROLLING_NORMALIZATION_V1`, `FRACTIONAL_DIFFERENCING_V1`,
endogenous Kelly sizing (built here), `LYAPUNOV_NULL_TEST_V1` (as a null test that will
probably close its own lane).

**After collector work:** persist the Deribit per-strike chain (enables `DERIBIT_RND_FEATURE_V1`
for daily horizons); add `forceOrder`; add snapshot + `@depth@100ms` with `(U, u)` continuity
(unblocks queue proxies, cancel-rate analysis, and the execution RL currently refused in code).

**Not pursued here:** micro-latency HMM, VIX/DXY index arbitrage, persistent homology,
knowledge distillation.

Every campaign above inherits the standing gates: preregistered protocol, fold-local fitting,
untouched chronological evaluation, day-block lower confidence bounds, incremental improvement
over simple baselines, complete cost model, forward shadow evidence, no automatic promotion, and
no authenticated order path. Real orders remain disabled.
