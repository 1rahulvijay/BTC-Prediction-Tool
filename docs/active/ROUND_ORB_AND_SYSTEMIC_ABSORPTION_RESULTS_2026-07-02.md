# Round ORB And Systemic Absorption Research Results

Date: 2026-07-02  
Status: completed; no live model or Champion behavior changed

## Executive Verdict

| Idea | Result | Decision |
|---|---|---|
| Polymarket round ORB | small lift for several path targets and a notable 15m line-cross lift; poor P(Hold) veto economics | retain as a narrow research candidate for a future 15m line-cross head; do not add to Champion/P(Hold) |
| PCA systemic absorption | reduced AUC and worsened Brier score for every tested big-move/big-drop target; veto did not improve hold precision | reject from live models and Champion |

The proposal's core discipline was correct: neither feature should create a trade. The measured results
also show that neither currently deserves a live fragility veto.

## Implemented Research Files

| File | Purpose |
|---|---|
| `backend/research/test_round_orb_features.py` | causal 5m/15m round-ORB path lift and P(Hold) veto test |
| `backend/research/test_systemic_absorption_fragility.py` | synchronized BTC/ETH/SOL PCA absorption and fragility test |
| `run_round_orb_research.bat` | reproduce ORB experiment |
| `run_systemic_absorption_research.bat` | reproduce systemic absorption experiment |

Outputs:

```text
data/research/round_orb/
data/research/systemic_absorption/
```

## Important Naming Correction

The existing feature named `absorption_ratio` is an order-book execution/absorption measure. The PCA
concept in this experiment is different and is named only with the `systemic_absorption_*` prefix.
They must never share a slot or be interpreted as the same signal.

---

## Part A: Polymarket Round ORB

### Causal Design

Classic daily ORB is not appropriate for 24/7 BTC. This experiment uses clock-aligned Polymarket rounds:

- 5m: first completed minute defines opening range; second completed minute observes breakout/failure;
  prediction checkpoint is 120 seconds after round open.
- 15m: first three completed minutes define opening range; fourth completed minute observes behavior;
  prediction checkpoint is 240 seconds after round open.

No unfinished candle enters a feature. Targets begin only after the decision checkpoint.

Data: 518,400 one-minute rows, producing 138,240 complete round checkpoints. Models use the oldest 70%
for training and newest 30% for testing.

### ORB Features

```text
orb_width_bps
orb_width_vs_rv
orb_close_position
orb_anchor_move_bps
breakout_side
breakout_distance_bps
failed_up
failed_down
both_sides_break
close_back_inside
orb_expansion
orb_impulse_quality
```

Baseline features:

```text
rv_15m, rv_30m, rv_60m, compression_ratio, shock_magnitude
```

Targets are future-from-checkpoint $50 touch, $50 two-sided round trip, early $50 touch, anchor line
cross by settlement and future $50 downside touch.

### Feature-Lift Results

| Horizon | Target | Baseline AUC | + ORB AUC | Lift | Brier change | Verdict |
|---:|---|---:|---:|---:|---:|---|
| 5m | touch $50 | 0.7898 | 0.7950 | +0.0052 | -0.0018 | small positive |
| 5m | round trip $50 | 0.8704 | 0.8774 | +0.0070 | -0.0020 | small positive |
| 5m | early touch $50 | 0.8087 | 0.8144 | +0.0057 | -0.0009 | small positive |
| 5m | line cross | 0.5847 | 0.5835 | -0.0012 | 0.0000 | reject |
| 5m | big drop $50 | 0.7148 | 0.7167 | +0.0019 | +0.0001 | negligible |
| 15m | touch $50 | 0.8347 | 0.8350 | +0.0003 | -0.0016 | negligible |
| 15m | round trip $50 | 0.7811 | 0.7776 | -0.0035 | +0.0020 | reject |
| 15m | early touch $50 | 0.8022 | 0.8044 | +0.0022 | -0.0018 | small |
| 15m | line cross | 0.5505 | 0.5887 | **+0.0382** | -0.0051 | research candidate |
| 15m | big drop $50 | 0.6778 | 0.6734 | -0.0044 | -0.0001 | reject |

The only material incremental result is 15m line-cross risk. That is consistent with ORB being a path
structure feature, not a direction feature. It needs purged multi-era and forward validation before even
shadow promotion because this table tested multiple targets.

### P(Hold) Veto Results

The veto test uses the saved P(Hold) model at the causal ORB checkpoint and one shared threshold:
`P(Hold) >= 0.93`.

| Policy | N | Coverage | Hold rate | Wilson lower bound | Misses avoided | Correct calls lost |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 1,299 | 100.0% | 97.31% | 96.28% | 0 | 0 |
| exclude failed breakout against current side | 1,126 | 86.68% | 97.51% | 96.43% | 7 | 166 |
| exclude both-side break | 1,299 | 100.0% | 97.31% | 96.28% | 0 | 0 |
| combined fragile veto | 1,126 | 86.68% | 97.51% | 96.43% | 7 | 166 |

The 0.20 percentage-point precision gain costs 12.8% of correct calls. It removes only one bad call for
approximately 24 good calls removed. This is not a useful veto.

### ORB Decision

- Do not add round ORB to the main direction model.
- Do not raise P(Hold) thresholds based on ORB failure.
- Do not use breakout side as BUY/SELL.
- Keep the 15m line-cross lift as a standalone research hypothesis.
- Next valid test: multi-era purged walk-forward plus a later forward sample, only for line-cross risk.

---

## Part B: Systemic Absorption Ratio

### Data And Calculation

Requested history: 180 days. Final synchronized frame after 30-day rolling warm-up: 51,241 five-minute
rows. Free data inputs:

```text
BTC spot close
BTC perpetual close reconstructed from spot and perp basis
ETH spot close
SOL spot close
BTC spot/perp basis change
BTC spot/perp CVD changes
BTC spot/perp volume changes
```

ETH/SOL use cached Binance Vision monthly 5m archives, with daily-archive fallback for the incomplete
latest month.

Each return/factor is normalized by trailing 30-day volatility using only past data. Rolling covariance
eigenvalues are calculated over 30m, 2h and 6h windows.

```text
systemic_absorption = sum(top 20% eigenvalues) / sum(all eigenvalues)
top_eigen_share = largest eigenvalue / sum(all eigenvalues)
effective_rank = exp(-sum(eigen_weight * log(eigen_weight)))
```

Two versions are tested:

- price: BTC spot, BTC perp, ETH and SOL returns;
- extended: price returns plus basis, CVD and volume changes.

The important spike fields are the 2h absorption value minus its trailing 30-day mean and its causal
30-day z-score.

### Incremental Model Results

Baseline is the same five path keepers used by the path model. The augmented model adds all systemic
absorption, top-eigen, effective-rank, spike and z-score features.

| Horizon | Target | Baseline AUC | + absorption AUC | AUC change | Brier change |
|---:|---|---:|---:|---:|---:|
| 5m | big move | 0.7453 | 0.7412 | -0.0041 | +0.0008 |
| 5m | big drop | 0.7513 | 0.7464 | -0.0049 | +0.0020 |
| 15m | big move | 0.7381 | 0.7322 | -0.0059 | +0.0023 |
| 15m | big drop | 0.7345 | 0.7279 | -0.0066 | +0.0017 |

Every AUC falls and every Brier score worsens. The feature family is redundant/noisy relative to realized
volatility, compression and shock magnitude.

### P(Hold) Fragility Veto

One first qualifying `P(Hold) >= 0.93` observation is retained per round/horizon.

| Policy | N | Coverage | Hold rate | Wilson lower | Misses avoided | Correct calls lost |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 54,114 | 100.0% | 94.07% | 93.87% | 0 | 0 |
| exclude price AR z >= 2 | 54,114 | 100.0% | 94.07% | 93.87% | 0 | 0 |
| exclude extended AR z >= 2 | 52,429 | 96.89% | 94.06% | 93.86% | 95 | 1,590 |
| exclude price AR top 10% | 48,702 | 90.0% | 93.95% | 93.74% | 262 | 5,150 |
| exclude extended AR top 10% | 48,702 | 90.0% | 94.03% | 93.82% | 300 | 5,112 |

The veto either never fires or makes retained hold quality slightly worse. It removes roughly 17 correct
calls for each miss avoided in the extended top-decile policy.

### Systemic Absorption Decision

- Do not add `systemic_absorption_*` to the 69-feature main model.
- Do not add a Champion confidence haircut or edge-buffer increase.
- Do not change P(Hold) thresholds based on this signal.
- Preserve the standalone script and negative result to prevent this idea being repeatedly reintroduced.
- Revisit only if genuinely new cross-market information becomes available, such as a broad crypto index,
  stablecoin stress, credit/liquidity measures or a substantially different event-time design.

## Reproduce

```powershell
.\run_round_orb_research.bat
.\run_systemic_absorption_research.bat --days 180
```

Network-free tests:

```powershell
.\run_round_orb_research.bat --selftest
.\run_systemic_absorption_research.bat --selftest
```

## Final Interpretation

ORB contains a narrow amount of line-cross/path information, but it is not a profitable strategy by
itself. Systemic absorption is theoretically reasonable but empirically redundant here. The correct
engineering outcome is to keep production unchanged and spend the next research cycle on executable
Polymarket mispricing, exact depth and actual fill evidence.
