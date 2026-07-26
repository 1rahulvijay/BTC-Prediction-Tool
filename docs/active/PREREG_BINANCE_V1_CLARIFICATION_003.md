# CLARIFICATION 003 - `BINANCE_VOLATILITY_MOMENTUM_V1`

Required-stream enumeration and stale-stream interpretation. This record completes the collector
health contract without editing the frozen preregistration.

```text
Declared:                        2026-07-26
Production rows at declaration:  0
M0 results available:            no
Any Binance analysis run:        no

Original preregistration:
    unchanged
    sha256 0973744b73651e8287b44309c976530f72a3964ceb082703c6b49400564c72f7
```

## Why clarification is required

Sections 10 and 12 say `8/8 stream health`, while the frozen information set requires nine
distinct inputs. Bybit `publicTrade` supplies the Class-A Bybit trade imbalance used by M0; treating
it as optional would allow an episode to qualify without one of the composite's required signals.

The phrase `stream stale` also needs to cover silence, not just transport delay. A stream that sent
one low-latency event and then stopped for the rest of an episode is stale even though that event's
`recv_ts - exch_ts` was small.

## Binding required-stream set

```text
1  binance_spot / bookTicker
2  binance_spot / aggTrade
3  binance_perp / bookTicker
4  binance_perp / aggTrade_rest
5  binance_perp / premiumIndex
6  binance_perp / openInterest
7  bybit_perp / orderbook.1
8  bybit_perp / publicTrade
9  coinbase / ticker
```

Therefore every `8/8` health reference in the frozen document is interpreted as **9/9 over the
enumerated set above**. This corrects a denominator typo; it does not add a new research feature.

## Binding stale-stream interpretation

```text
Required WebSocket maximum age:  5,000 ms
Required REST maximum age:      60,000 ms
```

For each required stream, age is the maximum of:

1. observed receive delay relative to a trustworthy exchange timestamp, where available;
2. maximum inter-arrival silence inside the episode;
3. silence from the last observed row to the episode boundary.

Missing or invalid exchange timestamps do not become zero. They remain NULL and use receive-time
silence for health. A stream with no persisted row in the episode is missing. Parsed-but-uncommitted
rows do not satisfy health.

## Continuity

The required four continuous weeks means an unbroken run of qualifying five-minute episodes. A
missing or excluded episode breaks the run. Count plus wall-clock span is insufficient. Any future
tolerance for isolated excluded episodes requires another hashed pre-data clarification.

## Audit chain

```text
PREREG_BINANCE_VOLATILITY_MOMENTUM_V1.md   0973744b73651e82...  frozen, unchanged
PREREG_BINANCE_V1_CLARIFICATION_001.md     hashed in PREREG_HASH.txt
PREREG_BINANCE_V1_CLARIFICATION_002.md     hashed in PREREG_HASH.txt
PREREG_BINANCE_V1_CLARIFICATION_003.md     this file; hash appended before collection
```
