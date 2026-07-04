# VWAP, Bollinger, And Mechanical Path Research

Date: 2026-07-02  
Status: completed historical research; no live-model or trade-policy promotion

## Question

Can causal VWAP deviation, Bollinger state, and mechanical support/resistance improve the app's existing 5m/15m path predictions or safely veto high-confidence P(Hold) calls?

The experiment deliberately did **not** test the retail rule "above VWAP means sell." It translated the ideas into path questions:

- Will price touch $50 from the checkpoint?
- Will both +$50 and -$50 be touched?
- Will the first touch occur early?
- Will price cross the round anchor before settlement?
- Will price drop at least $50?
- Does a stretched/reverting state identify FADE context?
- Does an expanding breakout identify RIDE context?
- Can any context remove bad P(Hold) calls without discarding too many good calls?

## Reproducible Command

```powershell
.\run_vwap_bollinger_path_research.bat
```

Implementation:

- `backend/research/test_vwap_bollinger_path_features.py`
- `run_vwap_bollinger_path_research.bat`

Outputs:

- `data/research/vwap_bollinger_path/feature_lift.csv`
- `data/research/vwap_bollinger_path/fade_ride_rules.csv`
- `data/research/vwap_bollinger_path/phold_veto.csv`
- `data/research/vwap_bollinger_path/round_base_rows.parquet`
- `data/research/vwap_bollinger_path/round_path_rows.parquet`
- `data/research/vwap_bollinger_path/summary.json`

The round-label cache is reused only while it is at least as new as the source matrix. A matrix rebuild automatically invalidates and regenerates the cache, preventing current features from being evaluated against stale labels.

## Causality And Split

- Source: 518,400 contiguous 1-minute candles (360 days).
- Usable fixed round checkpoints: 138,203.
- 5m decision: after two complete 1-minute candles.
- 15m decision: after four complete 1-minute candles.
- Every target begins after the completed decision candle.
- Evaluation: chronological first 70% train, final 30% test.
- Algorithms: balanced logistic regression for feature isolation and HistGradientBoosting for nonlinear confirmation.
- Causality self-test: mutating all future candles leaves past features unchanged.
- No live models, saved artifacts, champion policy, or startup files are modified.

## Features Tested

### Rolling VWAP

- 30-minute rolling volume-weighted typical price
- distance from VWAP in basis points
- trailing distance z-score
- five-minute VWAP slope
- one-minute VWAP cross
- failed upward/downward reclaim
- one-minute movement back toward VWAP

This is a continuous-market rolling VWAP proxy, not a US-session or exchange-day VWAP.

### Bollinger State

- close z-score around the trailing 20-minute mean
- band width and trailing width percentile
- upper/lower band touch
- five-minute band expansion
- width versus trailing median (squeeze ratio)
- close back inside the upper/lower band

### Mechanical Levels

- distance to prior-20-minute support/resistance
- five-minute level slopes
- support/resistance touch
- confirmed break
- failed breakdown/breakout

Levels use prior candles only. There are no hand-drawn trendlines.

## Feature-Lift Results

The table reports test AUC. `All` means baseline + round ORB + all new path features.

| Horizon | Target | Logistic baseline | Logistic all | Delta | HistGB baseline | HistGB all | Delta |
|---:|---|---:|---:|---:|---:|---:|---:|
| 5m | touch $50 | 0.7879 | 0.7955 | +0.0076 | 0.7892 | 0.7959 | +0.0067 |
| 5m | round-trip $50 | 0.8733 | 0.8798 | +0.0064 | 0.8760 | 0.8845 | +0.0084 |
| 5m | early touch $50 | 0.8086 | 0.8166 | +0.0080 | 0.8103 | 0.8186 | +0.0083 |
| 5m | anchor line-cross | 0.5848 | 0.5897 | +0.0049 | 0.6016 | 0.6485 | +0.0469 |
| 5m | big drop $50 | 0.7140 | 0.7145 | +0.0005 | 0.7142 | 0.7190 | +0.0049 |
| 15m | touch $50 | 0.8285 | 0.8335 | +0.0050 | 0.8282 | 0.8337 | +0.0055 |
| 15m | round-trip $50 | 0.7795 | 0.7779 | -0.0016 | 0.7795 | 0.7816 | +0.0021 |
| 15m | early touch $50 | 0.7993 | 0.8035 | +0.0042 | 0.7992 | 0.8028 | +0.0036 |
| 15m | anchor line-cross | 0.5477 | 0.5869 | +0.0393 | 0.5334 | 0.6288 | +0.0954 |
| 15m | big drop $50 | 0.6766 | 0.6690 | -0.0076 | 0.6746 | 0.6715 | -0.0031 |

### What Actually Caused The Lift

VWAP alone was nearly flat: its logistic AUC change ranged from -0.0008 to +0.0013. It is not an independent edge in this test.

Bollinger state added small, repeatable lift to touch and early-touch targets:

- 5m touch: +0.0040
- 5m early touch: +0.0035
- 15m touch: +0.0053
- 15m early touch: +0.0040

The large nonlinear line-cross improvement mostly came from the already-tested round ORB state:

- 5m HistGB ORB only: 0.6498; ORB + all path: 0.6485
- 15m HistGB ORB only: 0.6293; ORB + all path: 0.6288

Therefore the new indicators do **not** deserve credit for the line-cross jump. ORB remains the useful structure feature.

## FADE And RIDE Context

These are deterministic context tags tested only on the final 30% holdout. They are not trade PnL.

| Horizon | Context | N | Line-cross | Round-trip $50 | Held same side | Touch $50 |
|---:|---|---:|---:|---:|---:|---:|
| 5m | all non-neutral | 30,370 | 33.29% | 3.76% | 66.54% | 50.41% |
| 5m | FADE | 1,148 | 40.77% | 7.84% | 59.23% | 61.32% |
| 5m | RIDE | 4,474 | 29.01% | 5.72% | 70.76% | 54.49% |
| 15m | all non-neutral | 10,330 | 34.03% | 19.08% | 65.93% | 85.57% |
| 15m | FADE | 877 | 29.99% | 22.81% | 70.01% | 87.69% |
| 15m | RIDE | 1,158 | 23.66% | 22.54% | 76.25% | 86.96% |

Interpretation:

- The 5m FADE context raises line-cross incidence by 7.48 percentage points, but covers only 3.69% of test rounds.
- The same FADE definition fails at 15m. It lowers line-cross incidence and raises held-side frequency.
- RIDE context is directionally consistent at both horizons, especially 15m, where held-side frequency rises by 10.32 points.
- These are path frequencies, not executable returns. A share can be correctly held yet still be overpriced at entry.

## P(Hold) Veto Result

Baseline P(Hold) >= 0.93 contained 2,801 calls:

- held rate: 95.43%
- Wilson 95% lower bound: 94.59%

| Filter | Retained N | Held rate | Wilson lower | Bad avoided | Good lost |
|---|---:|---:|---:|---:|---:|
| Exclude FADE against held side | 2,518 | 95.43% | 94.55% | 13 | 270 |
| Exclude Bollinger reentry | 2,702 | 95.56% | 94.72% | 8 | 91 |
| Exclude failed VWAP reclaim | 2,770 | 95.49% | 94.65% | 3 | 28 |
| Exclude any path failure | 2,429 | 95.55% | 94.66% | 20 | 352 |

Verdict: reject every tested veto. None improves the confidence bound, and each discards far more correct calls than errors.

## Promotion Decision

### Keep Research-Only

- VWAP features: no independent measurable lift.
- Mechanical levels: small/inconsistent lift.
- Generic FADE rule: horizon-dependent and unsafe.
- All P(Hold) vetoes: rejected.

### Candidate For A Further Shadow Test

- Bollinger width/state for touch and early-touch heads.
- RIDE context as an explanatory tag, especially at 15m.
- 5m FADE context as a target for a separate calibrated classifier, not a hard rule.

Before any promotion, require:

1. Expanding or rolling month-by-month walk-forward validation.
2. A frozen feature definition and adjusted multiple-comparison gate.
3. Forward recorder confirmation.
4. Real Polymarket ask entry, bid exit, fee, latency, depth, and settlement replay.
5. Net EV and Wilson-lower-bound profitability, not BTC path accuracy alone.

## Explicitly Rejected Ideas

- Martingale: never implement; it changes loss shape, not edge.
- Reverse martingale: sizing research only after an independently proven positive edge.
- VIX mean reversion: too indirect for 5m/15m BTC rounds.
- Post-earnings drift: not applicable to BTC.
- Option premium harvesting: a separate derivatives/risk system.
- Discretionary ICT patterns: unacceptable unless converted to causal, machine-verifiable labels.
- Hand-drawn trendline trading: replaced here by prior-candle mechanical levels and still showed weak evidence.

## Plain-English Conclusion

VWAP did not become a secret buy/sell signal. Bollinger state helps slightly with the question "is a sizeable move likely soon?" ORB helps more with "will price cross the round's starting line?" The clearest combined context is RIDE, but even that does not tell us whether a Polymarket share is cheap enough to buy. Price, fees, liquidity, and exit value remain mandatory before betting.
