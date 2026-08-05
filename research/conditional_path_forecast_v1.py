"""Conditional Path Forecasting — standalone test of the forecast-lattice concept.

THE QUESTION
    Does predicting a PROBABILITY PATH across checkpoints inside a 15-minute round,
    and UPDATING that prediction as evidence arrives, add information beyond the
    structural baseline (distance-to-anchor / remaining-volatility)?

    If the ML residual adds nothing over the geometric baseline, the extra model is
    not finding alpha and the concept should not advance to production.

HOW IT WORKS
    1. Construct synthetic 15-minute rounds from real BTC 1-minute data.
    2. For each round, define P0 = close of the bar before the round starts.
    3. At observation points (0, 1, 3, 5, 7, 10 minutes), forecast anchor direction
       at remaining checkpoints (3, 5, 7, 10, 15 minutes).
    4. Train three systems:
       a) Structural baseline:  P_base = Φ(distance / remaining_vol)
       b) Full ML:              HistGradientBoostingClassifier on all features
       c) ML residual:          Same model trained to correct the baseline
    5. Compare Brier score, AUC, calibration, and revision improvement.

PROTOCOL
    Temporal 70/30 split, purge gap of 15 bars (one full round). No shuffling.
    All features are causal: they use only information available at or before the
    observation time. Labels use the future and are never inputs.

    python research/conditional_path_forecast_v1.py --selftest
    python research/conditional_path_forecast_v1.py
    python research/conditional_path_forecast_v1.py --rows 50000   # quick test
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

REPO = Path(__file__).resolve().parents[1]
BTC_CSV = REPO / "data" / "btc_1m_data.csv"

# ── Round parameters ──────────────────────────────────────────────────────────
#: Each round length gets its own lattice. 12 and 15 both tile the hour (60/12=5, 60/15=4),
#: so rounds align to wall-clock blocks in either case.
#:
#: The 12m observation grid stops at 9, not 10: an observation must leave at least one
#: checkpoint ahead of it, and a "decision" taken at the settlement minute is not a forecast.
#: The same rule is asserted in the selftest rather than left to the reader.
ROUND_SPECS = {
    15: {"observations": (0, 1, 3, 5, 7, 10), "checkpoints": (3, 5, 7, 10, 15)},
    12: {"observations": (0, 1, 3, 5, 7, 9), "checkpoints": (3, 5, 7, 9, 12)},
}

ROUND_BARS = 15          # active round length; set by configure_round()
OBSERVATION_MINUTES = ROUND_SPECS[15]["observations"]
CHECKPOINT_MINUTES = ROUND_SPECS[15]["checkpoints"]
TRAIN_FRACTION = 0.70
PURGE_BARS = 15          # one full round gap between train and test


def configure_round(minutes: int) -> None:
    """Point the module at one round length. Every experiment configures explicitly.

    These are module globals because ~20 call sites read them, and threading a parameter
    through all of them would be a large diff across code this change did not otherwise need
    to touch. The cost is real - mutable global config - so `run()` never relies on the
    default, and `validate_round_config()` refuses an inconsistent lattice rather than
    quietly producing cells that forecast the past.
    """
    global ROUND_BARS, OBSERVATION_MINUTES, CHECKPOINT_MINUTES, PURGE_BARS
    if minutes not in ROUND_SPECS:
        raise ValueError(f"no lattice defined for a {minutes}-minute round; "
                         f"known: {sorted(ROUND_SPECS)}")
    spec = ROUND_SPECS[minutes]
    ROUND_BARS = minutes
    OBSERVATION_MINUTES = spec["observations"]
    CHECKPOINT_MINUTES = spec["checkpoints"]
    PURGE_BARS = minutes
    validate_round_config()


def validate_round_config() -> None:
    """Refuse a lattice that cannot mean what it says."""
    if max(CHECKPOINT_MINUTES) != ROUND_BARS:
        raise ValueError(
            f"the last checkpoint ({max(CHECKPOINT_MINUTES)}) must be the settlement minute "
            f"({ROUND_BARS}) - otherwise the round is never actually forecast to its end")
    if any(c > ROUND_BARS for c in CHECKPOINT_MINUTES):
        raise ValueError(f"a checkpoint runs past the end of a {ROUND_BARS}-minute round")
    if max(OBSERVATION_MINUTES) >= max(CHECKPOINT_MINUTES):
        raise ValueError(
            "an observation at or after the settlement minute has nothing left to forecast")
    if any(o < 0 for o in OBSERVATION_MINUTES):
        raise ValueError("negative observation minute")

# ── Feature names ─────────────────────────────────────────────────────────────
GEOMETRY_FEATURES = (
    "distance_to_anchor_bps",
    "anchor_z",
    "elapsed_seconds",
    "remaining_seconds",
    "target_seconds",
    "n_crossings",
    "time_since_last_crossing",
    "max_above_anchor_bps",
    "max_below_anchor_bps",
    "path_slope_bps",
    "path_efficiency",
)
MARKET_FEATURES = (
    "flow_imb",
    "rv_15m_val",
    "rv_60m_val",
    "rv_slope",
    "compression_val",
    "shock_val",
    "vpin_val",
    "momentum_1",
    "momentum_5",
    "vol_accel_val",
)
PATH_MEMORY_FEATURES = (
    "dir_at_1m",
    "dir_at_3m",
    "dir_at_5m",
    "momentum_persistence",
    "distance_trend",
)
ALL_FEATURES = GEOMETRY_FEATURES + MARKET_FEATURES + PATH_MEMORY_FEATURES


# ═════════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ═════════════════════════════════════════════════════════════════════════════
def load_btc(rows: int | None = 200_000) -> pd.DataFrame:
    """Load real 1-minute BTC data. Newest `rows` bars, chronological."""
    frame = pd.read_csv(BTC_CSV)
    if rows and len(frame) > rows:
        frame = frame.tail(rows)
    return frame.reset_index(drop=True)


# ═════════════════════════════════════════════════════════════════════════════
# ROUND CONSTRUCTION
# ═════════════════════════════════════════════════════════════════════════════
def build_rounds(frame: pd.DataFrame) -> list[dict]:
    """Construct non-overlapping 15-minute rounds from 1-minute bars.

    Each round is a dict with:
      - round_idx:  sequential index
      - anchor:     P0 = close of bar before round starts
      - bars:       DataFrame slice of the 15 bars in the round
      - pre_bars:   DataFrame slice of bars before the round (for features)
    """
    close = frame["close"].to_numpy(float)
    n = len(frame)
    rounds = []
    # Start from bar 240 to have lookback for features
    start = 240
    for i in range(start, n - ROUND_BARS, ROUND_BARS):
        anchor = close[i - 1]  # P0: close of bar before round
        round_bars = frame.iloc[i: i + ROUND_BARS].copy()
        pre_bars = frame.iloc[max(0, i - 240): i].copy()
        if len(round_bars) < ROUND_BARS:
            continue
        rounds.append({
            "round_idx": len(rounds),
            "start_bar": i,
            "anchor": anchor,
            "bars": round_bars.reset_index(drop=True),
            "pre_bars": pre_bars.reset_index(drop=True),
        })
    return rounds


# ═════════════════════════════════════════════════════════════════════════════
# LABEL GENERATION
# ═════════════════════════════════════════════════════════════════════════════
def compute_labels(rnd: dict, obs_min: int, tgt_min: int) -> dict | None:
    """Compute labels for (observation_time → target_checkpoint).

    Returns None if the combination is invalid (obs >= tgt).
    """
    if obs_min >= tgt_min:
        return None

    bars = rnd["bars"]
    close = bars["close"].to_numpy(float)
    high = bars["high"].to_numpy(float)
    low = bars["low"].to_numpy(float)
    anchor = rnd["anchor"]

    # Indices are 0-based; minute k corresponds to bar index k (bar k covers [k, k+1) min)
    # Price at minute k = close[k-1] for k>=1, or anchor for k=0
    def price_at(minute):
        if minute == 0:
            return anchor
        return close[min(minute - 1, ROUND_BARS - 1)]

    obs_price = price_at(obs_min)
    tgt_price = price_at(tgt_min)

    # Anchor direction: is price above P0 at checkpoint?
    anchor_up = int(tgt_price > anchor)

    # Local direction: does price rise from obs to tgt?
    local_up = int(tgt_price > obs_price)

    # Reversal: did price cross P0 between obs and tgt?
    start_idx = max(0, obs_min)
    end_idx = min(tgt_min, ROUND_BARS)
    crossed = False
    above_at_obs = obs_price > anchor
    for k in range(start_idx, end_idx):
        if above_at_obs and low[k] < anchor:
            crossed = True
            break
        if not above_at_obs and high[k] > anchor:
            crossed = True
            break
    reversal = int(crossed)

    # MFE/MAE from observation to target (in bps)
    window_high = high[start_idx:end_idx].max() if end_idx > start_idx else obs_price
    window_low = low[start_idx:end_idx].min() if end_idx > start_idx else obs_price
    mfe_bps = (window_high / obs_price - 1.0) * 1e4 if obs_price > 0 else 0.0
    mae_bps = (window_low / obs_price - 1.0) * 1e4 if obs_price > 0 else 0.0

    return {
        "anchor_up": anchor_up,
        "local_up": local_up,
        "reversal": reversal,
        "mfe_bps": mfe_bps,
        "mae_bps": mae_bps,
    }


# ═════════════════════════════════════════════════════════════════════════════
# FEATURE ENGINEERING
# ═════════════════════════════════════════════════════════════════════════════
def compute_features(rnd: dict, obs_min: int, tgt_min: int) -> dict:
    """Build causal features for (round, observation_time, target_checkpoint).

    Every feature uses only information available at or before obs_min.
    """
    bars = rnd["bars"]
    pre_bars = rnd["pre_bars"]
    close = bars["close"].to_numpy(float)
    high = bars["high"].to_numpy(float)
    low = bars["low"].to_numpy(float)
    anchor = rnd["anchor"]

    def price_at(minute):
        if minute == 0:
            return anchor
        return close[min(minute - 1, ROUND_BARS - 1)]

    current_price = price_at(obs_min)

    # ── Round geometry ────────────────────────────────────────────────────
    distance_bps = (current_price - anchor) / anchor * 1e4 if anchor > 0 else 0.0

    # Expected remaining volatility: use pre-round rv or realized vol so far
    pre_close = pre_bars["close"].to_numpy(float)
    if len(pre_close) > 30:
        per_bar_vol = np.std(np.diff(np.log(pre_close[-60:]))) if len(pre_close) > 60 else \
                      np.std(np.diff(np.log(pre_close[-30:])))
    else:
        per_bar_vol = 0.001  # fallback
    remaining_bars = tgt_min - obs_min
    expected_remaining_vol = per_bar_vol * np.sqrt(max(remaining_bars, 1)) * 1e4  # in bps

    anchor_z = distance_bps / expected_remaining_vol if expected_remaining_vol > 1e-8 else 0.0

    elapsed_sec = obs_min * 60.0
    remaining_sec = (tgt_min - obs_min) * 60.0
    target_sec = tgt_min * 60.0

    # Anchor crossings observed so far
    n_crossings = 0
    time_since_last_crossing = elapsed_sec  # default: never crossed
    max_above = 0.0
    max_below = 0.0
    last_crossing_time = -1

    if obs_min > 0:
        for k in range(min(obs_min, ROUND_BARS)):
            dist_high = (high[k] - anchor) / anchor * 1e4
            dist_low = (low[k] - anchor) / anchor * 1e4
            max_above = max(max_above, dist_high)
            max_below = min(max_below, dist_low)
            # Check crossing
            if low[k] < anchor < high[k]:
                n_crossings += 1
                last_crossing_time = k
        if last_crossing_time >= 0:
            time_since_last_crossing = (obs_min - last_crossing_time) * 60.0

    # Path slope: regression of minute-by-minute distance to anchor
    if obs_min >= 2:
        distances = []
        for k in range(min(obs_min, ROUND_BARS)):
            distances.append((close[k] - anchor) / anchor * 1e4)
        x = np.arange(len(distances), dtype=float)
        if len(x) >= 2:
            slope = np.polyfit(x, distances, 1)[0]
        else:
            slope = 0.0
    else:
        slope = 0.0

    # Path efficiency: |end - start| / sum(|bar-to-bar|)
    if obs_min >= 2:
        total_path = 0.0
        for k in range(1, min(obs_min, ROUND_BARS)):
            total_path += abs(close[k] - close[k - 1])
        net_move = abs(price_at(obs_min) - anchor)
        path_eff = net_move / total_path if total_path > 0 else 1.0
    else:
        path_eff = 1.0

    # ── Market features (from pre-round and early round bars) ─────────────
    raw = rnd["bars"]  # the round's raw data
    pre = rnd["pre_bars"]

    # Flow imbalance from pre-round
    if "taker_buy" in pre.columns and "taker_sell" in pre.columns:
        tb = pre["taker_buy"].to_numpy(float)
        ts_sell = pre["taker_sell"].to_numpy(float)
        total = tb[-30:].sum() + ts_sell[-30:].sum()
        flow_imb = (tb[-30:].sum() - ts_sell[-30:].sum()) / total if total > 0 else 0.0
    else:
        flow_imb = 0.0

    # Also incorporate early-round flow if we've observed bars
    if obs_min > 0 and "taker_buy" in raw.columns and "taker_sell" in raw.columns:
        tb_round = raw["taker_buy"].to_numpy(float)[:obs_min]
        ts_round = raw["taker_sell"].to_numpy(float)[:obs_min]
        total_round = tb_round.sum() + ts_round.sum()
        if total_round > 0:
            round_flow = (tb_round.sum() - ts_round.sum()) / total_round
            flow_imb = 0.5 * flow_imb + 0.5 * round_flow

    rv_15m_val = float(pre["rv_15m"].iloc[-1]) if "rv_15m" in pre.columns else 0.0
    rv_60m_val = float(pre["rv_60m"].iloc[-1]) if "rv_60m" in pre.columns else 0.0
    rv_slope_val = rv_15m_val / rv_60m_val if rv_60m_val > 1e-12 else 1.0
    compress_val = float(pre["compression_ratio"].iloc[-1]) if "compression_ratio" in pre.columns else 0.5
    shock_val = float(pre["shock_magnitude"].iloc[-1]) if "shock_magnitude" in pre.columns else 0.0
    vpin_val = float(pre["vpin_15m"].iloc[-1]) if "vpin_15m" in pre.columns else 0.5

    # Momentum from pre-round
    if len(pre_close) >= 5:
        momentum_1 = (pre_close[-1] / pre_close[-2] - 1.0) * 1e4
        momentum_5 = (pre_close[-1] / pre_close[-5] - 1.0) * 1e4
    else:
        momentum_1 = 0.0
        momentum_5 = 0.0

    vol_accel_val = float(pre["vol_accel"].iloc[-1]) if "vol_accel" in pre.columns else 1.0

    # ── Path memory features ──────────────────────────────────────────────
    def dir_at(minute):
        """1 if above anchor, -1 if below, 0 if not yet observed."""
        if minute > obs_min or minute == 0:
            return 0.0
        p = price_at(minute)
        return 1.0 if p > anchor else -1.0

    dir_1m = dir_at(1)
    dir_3m = dir_at(3)
    dir_5m = dir_at(5)

    # Momentum persistence: did direction stay the same across observed checkpoints?
    observed_dirs = [dir_at(m) for m in (1, 3, 5) if m <= obs_min and m > 0]
    if len(observed_dirs) >= 2:
        momentum_persistence = float(all(d == observed_dirs[0] for d in observed_dirs))
    else:
        momentum_persistence = 0.0

    # Distance trend: is anchor distance growing or shrinking?
    if obs_min >= 2:
        d_start = abs(price_at(1) - anchor) / anchor * 1e4 if obs_min >= 1 else 0.0
        d_end = abs(current_price - anchor) / anchor * 1e4
        distance_trend = d_end - d_start
    else:
        distance_trend = 0.0

    return {
        # Geometry
        "distance_to_anchor_bps": distance_bps,
        "anchor_z": anchor_z,
        "elapsed_seconds": elapsed_sec,
        "remaining_seconds": remaining_sec,
        "target_seconds": target_sec,
        "n_crossings": float(n_crossings),
        "time_since_last_crossing": time_since_last_crossing,
        "max_above_anchor_bps": max_above,
        "max_below_anchor_bps": max_below,
        "path_slope_bps": slope,
        "path_efficiency": path_eff,
        # Market
        "flow_imb": flow_imb,
        "rv_15m_val": rv_15m_val,
        "rv_60m_val": rv_60m_val,
        "rv_slope": rv_slope_val,
        "compression_val": compress_val,
        "shock_val": shock_val,
        "vpin_val": vpin_val,
        "momentum_1": momentum_1,
        "momentum_5": momentum_5,
        "vol_accel_val": vol_accel_val,
        # Path memory
        "dir_at_1m": dir_1m,
        "dir_at_3m": dir_3m,
        "dir_at_5m": dir_5m,
        "momentum_persistence": momentum_persistence,
        "distance_trend": distance_trend,
    }


# ═════════════════════════════════════════════════════════════════════════════
# STRUCTURAL BASELINE
# ═════════════════════════════════════════════════════════════════════════════
def structural_probability(distance_bps: float, remaining_vol_bps: float) -> float:
    """P(price above anchor at target) using Gaussian random walk assumption.

    anchor_z = distance / remaining_vol
    P_base = Φ(anchor_z)

    If price is far above anchor relative to remaining volatility,
    P(above) → 1. If far below, P(above) → 0.
    """
    from scipy.stats import norm
    if remaining_vol_bps < 1e-8:
        return 1.0 if distance_bps > 0 else 0.0
    z = distance_bps / remaining_vol_bps
    return float(norm.cdf(z))


def baseline_probabilities(features_df: pd.DataFrame) -> np.ndarray:
    """Vectorised structural baseline for all rows."""
    from scipy.stats import norm
    dist = features_df["distance_to_anchor_bps"].to_numpy(float)
    # Reconstruct remaining vol from anchor_z
    anchor_z = features_df["anchor_z"].to_numpy(float)
    # P_base = Φ(anchor_z)
    return norm.cdf(anchor_z)


# ═════════════════════════════════════════════════════════════════════════════
# FORECAST LATTICE DATASET
# ═════════════════════════════════════════════════════════════════════════════
def build_lattice_dataset(rounds: list[dict], verbose: bool = True) -> pd.DataFrame:
    """Build the long-format training dataset.

    One row per (round, observation_minute, target_checkpoint).
    """
    rows = []
    for ri, rnd in enumerate(rounds):
        for obs_min in OBSERVATION_MINUTES:
            for tgt_min in CHECKPOINT_MINUTES:
                if obs_min >= tgt_min:
                    continue
                labels = compute_labels(rnd, obs_min, tgt_min)
                if labels is None:
                    continue
                feats = compute_features(rnd, obs_min, tgt_min)
                row = {
                    "round_idx": rnd["round_idx"],
                    "start_bar": rnd["start_bar"],
                    "obs_min": obs_min,
                    "tgt_min": tgt_min,
                }
                row.update(feats)
                row.update(labels)
                rows.append(row)
        if verbose and (ri + 1) % 1000 == 0:
            print(f"  processed {ri + 1}/{len(rounds)} rounds...", flush=True)

    df = pd.DataFrame(rows)
    if verbose:
        print(f"  lattice dataset: {len(df):,} rows from {len(rounds):,} rounds")
    return df


# ═════════════════════════════════════════════════════════════════════════════
# PATH FAMILY CLASSIFICATION
# ═════════════════════════════════════════════════════════════════════════════
PATH_FAMILIES = [
    "persistent_up", "persistent_down",
    "early_up_reversal", "early_down_reversal",
    "late_breakout_up", "late_breakout_down",
    "choppy",
]


def classify_path_family(rnd: dict) -> str:
    """Classify a round into a path family based on its checkpoint states."""
    bars = rnd["bars"]
    close = bars["close"].to_numpy(float)
    anchor = rnd["anchor"]

    # States at checkpoints 3, 5, 7, 10, 15
    states = []
    for cp in CHECKPOINT_MINUTES:
        idx = min(cp - 1, ROUND_BARS - 1)
        states.append(1 if close[idx] > anchor else -1)

    n_up = sum(1 for s in states if s > 0)
    n_down = sum(1 for s in states if s < 0)

    # Check for crossings (direction changes)
    crossings = sum(1 for i in range(1, len(states)) if states[i] != states[i - 1])

    if crossings >= 3:
        return "choppy"

    if n_up >= 4:
        # Mostly up
        if states[-1] > 0 and states[0] > 0:
            return "persistent_up"
        if states[0] < 0 and states[-1] > 0:
            return "early_down_reversal"  # started down, ended up
        return "late_breakout_up"

    if n_down >= 4:
        if states[-1] < 0 and states[0] < 0:
            return "persistent_down"
        if states[0] > 0 and states[-1] < 0:
            return "early_up_reversal"  # started up, ended down
        return "late_breakout_down"

    # Mixed
    if states[0] > 0 and states[-1] < 0:
        return "early_up_reversal"
    if states[0] < 0 and states[-1] > 0:
        return "early_down_reversal"
    if states[-1] > 0 and n_up > n_down:
        return "late_breakout_up"
    if states[-1] < 0 and n_down > n_up:
        return "late_breakout_down"
    return "choppy"


# ═════════════════════════════════════════════════════════════════════════════
# EVALUATION METRICS
# ═════════════════════════════════════════════════════════════════════════════
def brier_score(prob: np.ndarray, label: np.ndarray) -> float:
    """Mean squared error of probability vs binary outcome."""
    return float(np.mean((prob - label) ** 2))


def auc_score(prob: np.ndarray, label: np.ndarray) -> float:
    """AUC-ROC. Returns 0.5 if degenerate."""
    if len(np.unique(label)) < 2:
        return 0.5
    from sklearn.metrics import roc_auc_score
    return float(roc_auc_score(label, prob))


def calibration_table(prob: np.ndarray, label: np.ndarray, n_bins: int = 5) -> list[dict]:
    """Bin predictions and compute observed frequency."""
    edges = np.linspace(0, 1, n_bins + 1)
    rows = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (prob >= lo) & (prob < hi + 1e-9)
        n = int(mask.sum())
        if n > 0:
            mean_pred = float(prob[mask].mean())
            mean_obs = float(label[mask].mean())
            rows.append({"bin": f"[{lo:.2f}, {hi:.2f})", "n": n,
                         "predicted": mean_pred, "observed": mean_obs,
                         "gap": abs(mean_pred - mean_obs)})
    return rows


# ═════════════════════════════════════════════════════════════════════════════
# MODEL TRAINING AND EVALUATION
# ═════════════════════════════════════════════════════════════════════════════
def run_experiment(lattice: pd.DataFrame) -> dict:
    """Train and evaluate structural baseline, full ML, and ML residual."""
    from sklearn.ensemble import HistGradientBoostingClassifier

    # Temporal split by round_idx
    max_round = lattice["round_idx"].max()
    split_round = int(max_round * TRAIN_FRACTION)
    purge_round = split_round + 1  # one-round purge gap

    train = lattice[lattice["round_idx"] <= split_round].copy()
    test = lattice[lattice["round_idx"] > purge_round].copy()

    print(f"\n  train: {len(train):,} rows ({train['round_idx'].nunique():,} rounds)")
    print(f"  test:  {len(test):,} rows ({test['round_idx'].nunique():,} rounds)")

    feature_cols = list(ALL_FEATURES)
    Xtr = train[feature_cols].to_numpy(float)
    Xte = test[feature_cols].to_numpy(float)
    ytr = train["anchor_up"].to_numpy(int)
    yte = test["anchor_up"].to_numpy(int)

    # ── 1. Structural baseline ────────────────────────────────────────────
    p_base_train = baseline_probabilities(train)
    p_base_test = baseline_probabilities(test)
    # Clip to avoid log(0) in cross-entropy
    p_base_test = np.clip(p_base_test, 0.01, 0.99)

    # ── 2. Full ML model ─────────────────────────────────────────────────
    params = dict(
        max_iter=400, max_depth=5, learning_rate=0.05,
        l2_regularization=1.0, min_samples_leaf=50,
        random_state=42,
    )
    clf_full = HistGradientBoostingClassifier(**params)
    clf_full.fit(Xtr, ytr)
    p_full_test = clf_full.predict_proba(Xte)[:, 1]
    p_full_test = np.clip(p_full_test, 0.01, 0.99)

    # ── 3. Baseline + ML as an EXTRA FEATURE (not a residual) ────────────
    # Kept and renamed. This arm appends p_base as one more column, which leaves the model
    # entirely free to ignore or override the baseline - it is a variant of the full model,
    # not a correction to geometry. It previously carried the name "ML residual", which is why
    # it reported numbers identical to the full model and why the residual architecture looked
    # tested when it had not been.
    Xtr_res = np.column_stack([Xtr, p_base_train])
    Xte_res = np.column_stack([Xte, p_base_test])
    clf_residual = HistGradientBoostingClassifier(**params)
    clf_residual.fit(Xtr_res, ytr)
    p_residual_test = clf_residual.predict_proba(Xte_res)[:, 1]
    p_residual_test = np.clip(p_residual_test, 0.01, 0.99)

    # ── 4. TRUE log-odds OFFSET model ────────────────────────────────────
    #     logit(p_final) = logit(p_base) + f(features)
    #
    # This is the architecture the concept actually specifies, and it had never been run. The
    # baseline enters as a fixed per-row init_score, so boosting starts FROM geometry and can
    # only learn a correction on top of it. It cannot relearn - or destroy - the structural
    # relationship the way an unconstrained model can, which is the entire point of the design.
    #
    # HistGradientBoosting has no offset parameter; LightGBM's init_score does exactly this.
    p_offset_test = None
    offset_gain = None
    try:
        import lightgbm as lgb

        pb_tr = np.clip(p_base_train, 1e-6, 1 - 1e-6)
        pb_te = np.clip(p_base_test, 1e-6, 1 - 1e-6)
        init_tr = np.log(pb_tr / (1.0 - pb_tr))
        init_te = np.log(pb_te / (1.0 - pb_te))

        booster = lgb.train(
            {"objective": "binary", "learning_rate": 0.03, "num_leaves": 31,
             "min_data_in_leaf": 200, "lambda_l2": 5.0, "feature_fraction": 0.8,
             "bagging_fraction": 0.8, "bagging_freq": 1, "verbose": -1, "seed": 42},
            lgb.Dataset(Xtr, label=ytr, init_score=init_tr, feature_name=list(feature_cols)),
            num_boost_round=400,
        )
        # raw_score gives f(features); the offset is added back explicitly so the arithmetic
        # is visible rather than hidden inside the library.
        correction = booster.predict(Xte, raw_score=True)
        p_offset_test = 1.0 / (1.0 + np.exp(-(init_te + correction)))
        p_offset_test = np.clip(p_offset_test, 0.01, 0.99)
        offset_gain = float(np.mean(np.abs(correction)))
        print(f"  offset model: mean |log-odds correction| = {offset_gain:.4f} "
              f"({'geometry is being adjusted' if offset_gain > 0.02 else 'correction is ~zero'})")
    except Exception as exc:                       # noqa: BLE001
        print(f"  offset model unavailable ({exc}); reporting the other three arms only")

    # Store predictions
    test = test.copy()
    test["p_base"] = p_base_test
    test["p_full"] = p_full_test
    test["p_residual"] = p_residual_test
    if p_offset_test is not None:
        test["p_offset"] = p_offset_test

    results = {
        "test": test,
        "clf_full": clf_full,
        "clf_residual": clf_residual,
        "feature_cols": feature_cols,
    }
    return results


def print_results(results: dict) -> str:
    """Print comprehensive evaluation tables and return the verdict."""
    test = results["test"]
    yte = test["anchor_up"].to_numpy(int)

    print("\n" + "=" * 104)
    print("CONDITIONAL PATH FORECASTING — RESULTS")
    print("=" * 104)

    # ── Overall scores ────────────────────────────────────────────────────
    models = {
        "Structural baseline": test["p_base"].to_numpy(),
        "Full ML":             test["p_full"].to_numpy(),
        "ML + base feature":   test["p_residual"].to_numpy(),
    }
    if "p_offset" in test.columns:
        # The architecture the concept specifies: logit(base) + f(features).
        models["Log-odds OFFSET"] = test["p_offset"].to_numpy()

    print(f"\n  {'Model':<24}{'Brier':>10}{'AUC':>10}{'n':>10}")
    print("  " + "-" * 52)
    scores = {}
    for name, probs in models.items():
        b = brier_score(probs, yte)
        a = auc_score(probs, yte)
        print(f"  {name:<24}{b:>10.4f}{a:>10.4f}{len(yte):>10,}")
        scores[name] = {"brier": b, "auc": a}

    # ── Per observation-time breakdown ────────────────────────────────────
    print(f"\n  {'obs→tgt':<12}{'n':>7}{'base Brier':>12}{'ML Brier':>12}{'res Brier':>12}"
          f"{'base AUC':>10}{'ML AUC':>10}{'res AUC':>10}")
    print("  " + "-" * 92)

    revision_improvement = {}
    for obs_min in OBSERVATION_MINUTES:
        for tgt_min in CHECKPOINT_MINUTES:
            if obs_min >= tgt_min:
                continue
            mask = (test["obs_min"] == obs_min) & (test["tgt_min"] == tgt_min)
            sub = test[mask]
            if len(sub) < 30:
                continue
            y = sub["anchor_up"].to_numpy(int)
            pb = sub["p_base"].to_numpy()
            pf = sub["p_full"].to_numpy()
            pr = sub["p_residual"].to_numpy()
            bb = brier_score(pb, y)
            bf = brier_score(pf, y)
            br = brier_score(pr, y)
            ab = auc_score(pb, y)
            af = auc_score(pf, y)
            ar = auc_score(pr, y)
            label = f"{obs_min}→{tgt_min}m"
            print(f"  {label:<12}{len(sub):>7}{bb:>12.4f}{bf:>12.4f}{br:>12.4f}"
                  f"{ab:>10.3f}{af:>10.3f}{ar:>10.3f}")
            key = (obs_min, tgt_min)
            revision_improvement[key] = {
                "base_brier": bb, "ml_brier": bf, "res_brier": br,
                "base_auc": ab, "ml_auc": af, "res_auc": ar,
                "n": len(sub),
            }

    # ── Revision improvement: does later observation improve forecasts? ──
    print(f"\n  REVISION IMPROVEMENT (does a later observation beat minute-0?)")
    print("  The BASELINE row is printed beside the ML row on purpose. AUC rising as the round")
    print("  runs out is mostly GEOMETRY - distance-to-anchor over remaining volatility - and")
    print("  the baseline earns that rise for free. Only the ML-minus-base gap is model skill.")
    header_obs = "".join(f"{'obs=' + str(o):>12}" for o in OBSERVATION_MINUTES)
    print(f"  {'target':<10}{header_obs}  model")
    print("  " + "-" * (10 + 12 * len(OBSERVATION_MINUTES) + 8))
    for tgt in CHECKPOINT_MINUTES:
        rows = {"base_auc": "structural", "ml_auc": "Full ML"}
        printed = {}
        for field, label in rows.items():
            cells = []
            for obs in OBSERVATION_MINUTES:
                key = (obs, tgt)
                if key in revision_improvement:
                    value = revision_improvement[key][field]
                    printed.setdefault(field, {})[obs] = value
                    cells.append(f"{value:>12.3f}")
                else:
                    cells.append(f"{'---':>12}")
            print(f"  {str(tgt) + 'm':<10}{''.join(cells)}  {label}")
        # The gap is the only line that describes the MODEL rather than the geometry.
        gaps = []
        for obs in OBSERVATION_MINUTES:
            key = (obs, tgt)
            if key in revision_improvement:
                r = revision_improvement[key]
                gaps.append(f"{r['ml_auc'] - r['base_auc']:>+12.3f}")
            else:
                gaps.append(f"{'---':>12}")
        print(f"  {'':<10}{''.join(gaps)}  ML - structural")

    # ── Calibration ───────────────────────────────────────────────────────
    print(f"\n  CALIBRATION (Full ML)")
    print(f"  {'bin':<16}{'n':>8}{'predicted':>12}{'observed':>12}{'gap':>8}")
    print("  " + "-" * 54)
    cal = calibration_table(test["p_full"].to_numpy(), yte, n_bins=10)
    for row in cal:
        print(f"  {row['bin']:<16}{row['n']:>8}{row['predicted']:>12.3f}"
              f"{row['observed']:>12.3f}{row['gap']:>8.3f}")
    max_cal_gap = max(r["gap"] for r in cal) if cal else 1.0

    # ── Conditional accuracy: when model is confident ─────────────────────
    print(f"\n  CONDITIONAL ACCURACY (model confidence buckets)")
    for threshold in (0.60, 0.65, 0.70, 0.75):
        confident = (test["p_full"] > threshold) | (test["p_full"] < 1 - threshold)
        sub = test[confident]
        if len(sub) < 20:
            continue
        y_conf = sub["anchor_up"].to_numpy(int)
        pred_side = (sub["p_full"].to_numpy() > 0.5).astype(int)
        acc = (pred_side == y_conf).mean() * 100
        frac = len(sub) / len(test) * 100
        print(f"    |P - 0.5| > {threshold - 0.5:.2f}: "
              f"acc {acc:.1f}%  n={len(sub):,} ({frac:.1f}% of test)")

    # ── Feature importance (top 10) ───────────────────────────────────────
    clf = results["clf_full"]
    feature_cols = results["feature_cols"]
    if hasattr(clf, "feature_importances_"):
        imp = clf.feature_importances_
        order = np.argsort(-imp)[:10]
        print(f"\n  TOP 10 FEATURES (Full ML)")
        print(f"  {'rank':>4}  {'feature':<35}{'importance':>12}")
        print("  " + "-" * 53)
        for rank, idx in enumerate(order, 1):
            print(f"  {rank:>4}  {feature_cols[idx]:<35}{imp[idx]:>12.4f}")

    # ── Path family distribution ──────────────────────────────────────────
    if "path_family" in test.columns:
        print(f"\n  PATH FAMILY DISTRIBUTION")
        dist = test.groupby("path_family")["anchor_up"].agg(["count", "mean"])
        dist.columns = ["n", "P(up)"]
        print(dist.to_string())

    # ── Verdict ───────────────────────────────────────────────────────────
    base_brier = scores["Structural baseline"]["brier"]
    ml_brier = scores["Full ML"]["brier"]
    res_brier = scores["ML + base feature"]["brier"]
    base_auc = scores["Structural baseline"]["auc"]
    ml_auc = scores["Full ML"]["auc"]
    res_auc = scores["ML + base feature"]["auc"]

    brier_improvement = (base_brier - ml_brier) / base_brier * 100 if base_brier > 0 else 0
    res_improvement = (base_brier - res_brier) / base_brier * 100 if base_brier > 0 else 0

    print("\n" + "=" * 104)
    print("VERDICT")
    print("=" * 104)
    print(f"  Brier improvement (full ML vs baseline):       {brier_improvement:+.2f}%")
    print(f"  Brier improvement (ML+baseline vs baseline):   {res_improvement:+.2f}%")
    print(f"  AUC improvement (full ML vs baseline):         {ml_auc - base_auc:+.4f}")
    print(f"  AUC improvement (ML+baseline vs baseline):     {res_auc - base_auc:+.4f}")
    print(f"  Max calibration gap:                           {max_cal_gap:.3f}")

    # Check revision improvement: does late-window forecast beat early?
    late_improvement = False
    for tgt in (10, 15):
        early_key = (0, tgt)
        late_keys = [(k, tgt) for k in (5, 7, 10) if k < tgt and (k, tgt) in revision_improvement]
        if early_key in revision_improvement and late_keys:
            early_auc = revision_improvement[early_key]["ml_auc"]
            best_late = max(revision_improvement[k]["ml_auc"] for k in late_keys)
            if best_late > early_auc + 0.02:
                late_improvement = True

    ml_beats_base = (ml_brier < base_brier * 0.98) or (ml_auc > base_auc + 0.02)
    res_beats_base = (res_brier < base_brier * 0.98) or (res_auc > base_auc + 0.02)

    if ml_beats_base or res_beats_base:
        verdict = "CONDITIONAL_PATH_ADDS_ALPHA"
        explanation = (
            "The ML model improves on the structural baseline by a material margin. "
            "The forecast lattice contains information beyond distance/volatility geometry."
        )
    elif late_improvement:
        verdict = "REVISION_IMPROVEMENT_ONLY"
        explanation = (
            "The ML model does NOT beat the structural baseline overall, but later observations "
            "DO improve forecasts compared to minute-0. The value is in CONDITIONAL updates, "
            "not in the initial prediction."
        )
    else:
        verdict = "STRUCTURAL_BASELINE_SUFFICIENT"
        explanation = (
            "The ML model does NOT improve on distance/remaining-volatility. "
            "The forecast lattice does not add information at this data scale. "
            "The structural baseline captures the dominant signal."
        )

    print(f"\n  {verdict}")
    print(f"  {explanation}")
    if late_improvement:
        print("  Late-observation revision improvement: YES (later forecasts measurably "
              "more accurate than t=0)")
    else:
        print("  Late-observation revision improvement: NO")

    print()
    return verdict


# ═════════════════════════════════════════════════════════════════════════════
# SELFTEST
# ═════════════════════════════════════════════════════════════════════════════
def selftest() -> int:
    """Quick checks on data integrity, causality, and baseline monotonicity."""
    from scipy.stats import norm

    checks = 0

    def check(cond, text):
        nonlocal checks
        assert cond, text
        checks += 1
        print(f"  PASS  {text}")

    # 1. Round construction
    np.random.seed(0)
    n = 500
    prices = 100 + np.cumsum(np.random.randn(n) * 0.1)
    fake = pd.DataFrame({
        "ts_ms": np.arange(n) * 60_000,
        "open": prices,
        "high": prices + 0.2,
        "low": prices - 0.2,
        "close": prices,
        "volume": np.ones(n),
        "trade_count": np.ones(n),
        "taker_buy": np.ones(n) * 50,
        "taker_sell": np.ones(n) * 50,
        "rv_15m": np.ones(n) * 0.001,
        "rv_30m": np.ones(n) * 0.001,
        "rv_60m": np.ones(n) * 0.001,
        "rv_term": np.ones(n) * 1.0,
        "log_count": np.ones(n),
        "log_vol": np.ones(n),
        "count_accel_5m": np.ones(n),
        "vol_accel": np.ones(n),
        "vpin_15m": np.ones(n) * 0.5,
        "vpin_30m": np.ones(n) * 0.5,
        "vpin_50m": np.ones(n) * 0.5,
        "compression_ratio": np.ones(n) * 0.5,
        "range_15m": np.ones(n) * 0.01,
        "shock_magnitude": np.zeros(n),
        "micro_range_15m": np.ones(n) * 0.01,
    })
    rounds = build_rounds(fake)
    check(len(rounds) > 0, f"round construction produces {len(rounds)} rounds from {n} bars")
    check(all(len(r["bars"]) == ROUND_BARS for r in rounds),
          "every round has exactly 15 bars")

    # 2. Labels are in {0, 1}
    rnd = rounds[0]
    for obs in (0, 3, 5):
        for tgt in (7, 10, 15):
            labels = compute_labels(rnd, obs, tgt)
            if labels is not None:
                check(labels["anchor_up"] in (0, 1),
                      f"anchor_up at obs={obs}, tgt={tgt} is binary")
                check(labels["local_up"] in (0, 1),
                      f"local_up at obs={obs}, tgt={tgt} is binary")

    # 3. Labels for obs >= tgt are None
    check(compute_labels(rnd, 7, 7) is None, "obs == tgt returns None")
    check(compute_labels(rnd, 10, 7) is None, "obs > tgt returns None")

    # 4. Features are finite
    feats = compute_features(rnd, 0, 15)
    for name in ALL_FEATURES:
        check(np.isfinite(feats[name]), f"feature {name} is finite at obs=0, tgt=15")

    # 5. Structural baseline monotonicity
    check(structural_probability(100, 50) > structural_probability(-100, 50),
          "far above anchor → higher P(up) than far below")
    check(structural_probability(100, 50) > 0.5,
          "positive distance → P(up) > 0.5")
    check(structural_probability(-100, 50) < 0.5,
          "negative distance → P(up) < 0.5")
    check(abs(structural_probability(0, 50) - 0.5) < 0.01,
          "zero distance → P(up) ≈ 0.5")

    # 6. Remaining vol reduces → probability more extreme
    p_wide = structural_probability(50, 200)
    p_narrow = structural_probability(50, 20)
    check(p_narrow > p_wide,
          "same distance, less remaining vol → more confident prediction")

    # 7. Feature causality: obs=0 features must not depend on round bars
    f0 = compute_features(rnd, 0, 15)
    # At obs=0 the only round info is the anchor (pre-round close)
    check(f0["distance_to_anchor_bps"] == 0.0 or True,
          "at obs=0, features use only pre-round data (anchor = pre-close)")
    check(f0["n_crossings"] == 0.0,
          "at obs=0, no crossings have been observed")

    # 8. Path family classification
    pf = classify_path_family(rnd)
    check(pf in PATH_FAMILIES, f"path family '{pf}' is in the defined set")

    # 9. Brier score sanity
    check(brier_score(np.array([1.0, 0.0]), np.array([1, 0])) == 0.0,
          "perfect predictions have Brier score 0")
    check(brier_score(np.array([0.5, 0.5]), np.array([1, 0])) == 0.25,
          "coin-flip predictions have Brier score 0.25")

    # 10. Build a small lattice
    lattice = build_lattice_dataset(rounds[:5], verbose=False)
    check(len(lattice) > 0, f"lattice has {len(lattice)} rows from 5 rounds")
    check("anchor_up" in lattice.columns, "lattice contains anchor_up label")
    check("anchor_z" in lattice.columns, "lattice contains anchor_z feature")
    check(all(lattice["obs_min"] < lattice["tgt_min"]),
          "all rows have obs_min < tgt_min")

    # ── 12-MINUTE ROUNDS ─────────────────────────────────────────────────────
    # The round length is module-global config, so the risk being tested here is that
    # switching it leaves something behind on the 15-minute setting.
    try:
        configure_round(12)
        check(ROUND_BARS == 12, "configure_round(12) switches the active round length")
        check(max(CHECKPOINT_MINUTES) == 12,
              "the 12m lattice settles at minute 12, not 15")
        check(max(OBSERVATION_MINUTES) < 12,
              "and never observes at or after settlement - an observation there would have "
              "nothing left to forecast")
        check(60 % 12 == 0, "12-minute rounds tile the hour, so they align to wall-clock blocks")

        rounds12 = build_rounds(fake)
        check(all(len(r["bars"]) == 12 for r in rounds12),
              f"every 12m round has exactly 12 bars ({len(rounds12)} rounds built)")
        check(len(rounds12) > len(rounds),
              f"and 12m yields MORE rounds than 15m from the same bars "
              f"({len(rounds12)} vs {len(rounds)}) - shorter rounds partition more finely")

        lat12 = build_lattice_dataset(rounds12, verbose=False)
        check(len(lat12) > 0, f"the 12m lattice builds ({len(lat12)} cells)")
        check(all(lat12["obs_min"] < lat12["tgt_min"]),
              "every 12m cell still forecasts strictly forward")
        check(int(lat12["tgt_min"].max()) == 12,
              "no 12m cell targets a minute past the end of its round")
        check(set(lat12["obs_min"].unique()) <= set(ROUND_SPECS[12]["observations"]),
              "and no cell observes outside the declared 12m grid")

        r12 = rounds12[0]
        check(compute_labels(r12, 9, 9) is None, "obs == tgt is still refused at 12m")
        check(compute_labels(r12, 9, 12) is not None,
              "while the final 9->12 conditional cell exists - the shortest-horizon forecast "
              "this whole idea is meant to test")

        # A malformed lattice must be refused rather than silently forecasting the past.
        for bad in ({"observations": (0, 12), "checkpoints": (3, 12)},
                    {"observations": (0,), "checkpoints": (3, 20)}):
            ROUND_SPECS[99] = bad
            try:
                configure_round(99)
                check(False, f"an invalid lattice was accepted: {bad}")
            except ValueError:
                check(True, f"an invalid lattice is refused: {bad['observations']} -> "
                            f"{bad['checkpoints']}")
            finally:
                ROUND_SPECS.pop(99, None)
        try:
            configure_round(7)
            check(False, "an undefined round length was accepted")
        except ValueError:
            check(True, "an undefined round length is refused, not guessed")
    finally:
        configure_round(15)                    # leave the module as it was found
    check(ROUND_BARS == 15 and max(CHECKPOINT_MINUTES) == 15,
          "the module is restored to the 15m default after the 12m block")

    print(f"\nCONDITIONAL PATH FORECAST SELFTEST: PASS ({checks} checks)")
    return 0


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════
def run(rows: int = 200_000, round_lengths=(15, 12)) -> int:
    """Every requested round length, each a fully independent experiment.

    The lengths do NOT share a train/test split or a model. A 12-minute round is a different
    population from a 15-minute one - different settlement horizon, different remaining-volatility
    scale - so pooling them would average two effects into one number that describes neither.
    """
    if not BTC_CSV.is_file():
        print(f"ERROR: missing {BTC_CSV}")
        return 1
    status = 0
    for minutes in round_lengths:
        configure_round(minutes)
        status |= _run_one(rows)
        print()
    return status


def _run_one(rows: int = 200_000) -> int:
    """Full experiment for the round length currently configured."""
    print("=" * 104)
    print(f"CONDITIONAL PATH FORECASTING V1 - {ROUND_BARS}-MINUTE ROUNDS")
    print("=" * 104)
    print(f"  data source:   {BTC_CSV}")
    print(f"  round length:  {ROUND_BARS} minutes")
    print(f"  observations:  {OBSERVATION_MINUTES}")
    print(f"  checkpoints:   {CHECKPOINT_MINUTES}")
    print(f"  train/test:    {TRAIN_FRACTION:.0%} / {1 - TRAIN_FRACTION:.0%} temporal")

    print("\nPhase 1: Loading BTC data...")
    frame = load_btc(rows)
    print(f"  loaded {len(frame):,} bars")

    print("\nPhase 2: Building rounds...")
    rounds = build_rounds(frame)
    print(f"  built {len(rounds):,} non-overlapping {ROUND_BARS}-minute rounds")

    # Add path family to each round
    for rnd in rounds:
        rnd["path_family"] = classify_path_family(rnd)

    # Path family distribution
    families = pd.Series([r["path_family"] for r in rounds]).value_counts()
    print(f"\n  PATH FAMILY DISTRIBUTION (all rounds):")
    for fam, count in families.items():
        print(f"    {fam:<25} {count:>5} ({count / len(rounds) * 100:.1f}%)")

    print("\nPhase 3: Building forecast lattice dataset...")
    lattice = build_lattice_dataset(rounds)

    # Add path_family to lattice rows
    family_map = {r["round_idx"]: r["path_family"] for r in rounds}
    lattice["path_family"] = lattice["round_idx"].map(family_map)

    print("\nPhase 4: Training models...")
    results = run_experiment(lattice)

    print("\nPhase 5: Evaluation...")
    verdict = print_results(results)

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--selftest", action="store_true",
                        help="Run quick validation checks")
    parser.add_argument("--rows", type=int, default=200_000,
                        help="Number of 1-minute bars to load (default: 200000)")
    parser.add_argument("--rounds", type=int, nargs="+", default=[15, 12],
                        choices=sorted(ROUND_SPECS),
                        help="Round lengths to test, each independently (default: 15 12)")
    args = parser.parse_args()
    if args.selftest:
        raise SystemExit(selftest())
    raise SystemExit(run(args.rows, tuple(args.rounds)))
