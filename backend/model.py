"""
Multi-model ensemble prediction engine.
Current base models: XGBoost, LightGBM, optional CatBoost,
HistGradientBoosting, optional PyTorch TCN/sequence model, Logistic Regression,
and a TCN sequence model (full stacker seat since v6; SGD retired as anti-signal).
Includes direction locking, hysteresis, prediction cooldown, and persistence.
"""

import time
import os
import copy
import logging
import numpy as np
import warnings
import xgboost as xgb
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.linear_model import LogisticRegression
from sklearn.calibration import CalibratedClassifierCV
from sklearn.model_selection import TimeSeriesSplit
from sklearn.base import clone
from typing import Optional, Dict
from features import LOOKBACK, rsi, atr, ema, NUM_FEATURES
from model_contract import (
    DL_ARCH,
    MODEL_ARCH_VERSION,
    MODEL_FEATURE_INDICES,
    MODEL_FEATURE_NAMES,
    MODEL_FEATURE_PRUNING,
    MODEL_FEATURE_SCHEMA_HASH,
    MODEL_NUM_FEATURES,
)
from artifact_identity import (
    artifact_compatibility,
    artifact_manifest_path,
    configured_model_training_days,
    current_training_identity,
    training_identity_issues,
    write_artifact_manifest,
)

warnings.filterwarnings("ignore", message=".*Falling back to prediction using DMatrix due to mismatched devices.*")

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


MOVE_SIZE_REGIME_SCOPE = os.getenv("BTC_MOVE_SIZE_REGIME_SCOPE", "GLOBAL").upper()
QUANTILE_REGIME_SCOPE = os.getenv("BTC_QUANTILE_REGIME_SCOPE", "GLOBAL").upper()
MOVE_SIZE_MAX_SAMPLES = _env_int("BTC_MOVE_SIZE_MAX_SAMPLES", 12000)
QUANTILE_MAX_SAMPLES = _env_int("BTC_QUANTILE_MAX_SAMPLES", 12000)
MOVE_SIZE_MAX_ITER = _env_int("BTC_MOVE_SIZE_MAX_ITER", 60)
QUANTILE_MAX_ITER = _env_int("BTC_QUANTILE_MAX_ITER", 45)
LINEAR_MAX_SAMPLES = _env_int("BTC_LINEAR_MAX_SAMPLES", 12000)
# (SGD_MAX_ITER removed in v6 — SGD retired from the roster)
STACKER_MAX_SAMPLES = _env_int("BTC_STACKER_MAX_SAMPLES", 6000)
TCN_MAX_SAMPLES = _env_int("BTC_TCN_MAX_SAMPLES", 25000)
SAMPLE_WEIGHT_MODE = os.environ.get(
    "BTC_SAMPLE_WEIGHT_MODE", "recency_similarity"
).strip().lower()
# Memory-safe cap for each direction-model regime bucket. A value of 0 uses every row.
# Long-window laptop runs set this explicitly and sample across the full history plus a
# recent tail; this avoids materializing a multi-gigabyte GLOBAL advanced-index copy.
DIRECTION_MAX_SAMPLES = _env_int("BTC_DIRECTION_MAX_SAMPLES", 0)
# Cap booster training threads so a background retrain does NOT saturate every core —
# leave headroom for the asyncio event loop (live price + charts + WebSocket feeds) so the
# UI doesn't freeze mid-train. Default: all cores minus 4 (min 2). Env: BTC_TRAIN_THREADS.
TRAIN_THREADS = max(2, _env_int("BTC_TRAIN_THREADS", max(2, (os.cpu_count() or 4) - 4)))


def _representative_training_indices(indices: np.ndarray, max_samples: int) -> np.ndarray:
    """Cover the full window while reserving half the budget for the recent tail."""
    indices = np.asarray(indices, dtype=np.int64)
    if not max_samples or len(indices) <= max_samples:
        return indices
    recent_n = max(1, max_samples // 2)
    history_n = max_samples - recent_n
    recent = indices[-recent_n:]
    older = indices[:-recent_n]
    if history_n <= 0 or len(older) == 0:
        return recent
    positions = np.linspace(0, len(older) - 1, num=history_n, dtype=np.int64)
    return np.unique(np.concatenate([older[positions], recent]))


#: PREREGISTERED. The largest share of a seat's OOF folds that may fit FEWER than the three
#: production classes before that seat is dropped from the stacker for the bucket.
#:
#: Declared here, before any run, because a tolerance chosen after seeing the counts is not a
#: gate - it is a description. With the five-fold split in use, 0.20 states the rule plainly:
#: at most ONE fold in five.
#:
#: WHY A RATE AND NOT A COUNT
#:     The mismatch was already being counted, and the count was written to
#:     `_oof_class_set_mismatch` and never read by anything. A number nobody compares to a
#:     threshold cannot fail, so it recorded the defect without ever acting on it.
#:
#: WHAT THE DEFECT IS
#:     A fold that never saw a class pads that column to a hard 0.0, while production - whose
#:     labels are augmented - emits a small positive there. The stacker then learns a column
#:     that is structurally zero at train time and non-zero at serve time. That is train/serve
#:     skew in the stacker's own input, and past a modest share of folds it is not a rounding
#:     difference, it is a different variable.
OOF_CLASS_SET_TOLERANCE = 0.20


def _purged_calibration_splits(
    labels: np.ndarray,
    horizon: int,
    n_splits: int = 3,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Build recent, chronological calibration folds with a label-overlap gap."""
    labels = np.asarray(labels, dtype=np.int64).reshape(-1)
    expected_classes = np.array([0, 1, 2], dtype=np.int64)
    if len(labels) < 300 or not np.array_equal(np.unique(labels), expected_classes):
        return []

    n_splits = max(2, int(n_splits))
    validation_size = max(50, len(labels) // 10)
    first_validation = len(labels) - n_splits * validation_size
    purge_gap = max(1, LOOKBACK + int(horizon))
    if first_validation <= purge_gap:
        return []

    splits = []
    for fold in range(n_splits):
        validation_start = first_validation + fold * validation_size
        validation_stop = min(validation_start + validation_size, len(labels))
        training_stop = validation_start - purge_gap
        train_idx = np.arange(0, training_stop, dtype=np.int64)
        validation_idx = np.arange(validation_start, validation_stop, dtype=np.int64)
        if len(train_idx) < 100 or len(validation_idx) < 30:
            continue
        if not np.array_equal(np.unique(labels[train_idx]), expected_classes):
            continue
        if not np.array_equal(np.unique(labels[validation_idx]), expected_classes):
            continue
        splits.append((train_idx, validation_idx))
    return splits if len(splits) >= 2 else []


def _regime_similarity_weights(
    X_model: np.ndarray,
    feature_names: list[str],
    split_idx: int,
    recent_rows: int = 1440,
) -> np.ndarray:
    """Causal similarity to the latest training regime using robust feature distance."""
    weights = np.ones(split_idx, dtype=np.float64)
    if split_idx < 100 or SAMPLE_WEIGHT_MODE not in {
        "similarity",
        "recency_similarity",
    }:
        return weights
    candidates = (
        "atr_norm",
        "adx_norm",
        "volume_ma_ratio",
        "rv_5m",
        "rv_15m",
        "ewma_vol",
        "variance_ratio",
        "rv_term_structure",
    )
    feature_idx = [
        feature_names.index(name) for name in candidates if name in feature_names
    ]
    if not feature_idx:
        return weights
    values = np.asarray(X_model[:split_idx, -1, feature_idx], dtype=np.float64)
    values = np.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0)
    recent = values[max(0, split_idx - recent_rows):split_idx]
    center = np.median(recent, axis=0)
    q25, q75 = np.quantile(values, [0.25, 0.75], axis=0)
    scale = np.maximum(q75 - q25, 1e-6)
    distance = np.mean(np.square(np.clip((values - center) / scale, -6.0, 6.0)), axis=1)
    weights = np.exp(-0.5 * distance)
    weights = np.clip(weights, 0.15, 1.0)
    return weights / max(float(np.mean(weights)), 1e-9)


def _balanced_sequence_indices(
    X_flat: np.ndarray,
    max_samples: int,
    lookback: int,
    input_dim: int,
) -> np.ndarray:
    """50% recent, 25% historical-regime, 25% historical-tail sampling.

    All scores use only each sample's input sequence. No label or future value is
    consulted, so the sampler cannot leak the target.
    """
    n_samples = len(X_flat)
    if not max_samples or n_samples <= max_samples:
        return np.arange(n_samples, dtype=np.int64)
    recent_n = max_samples // 2
    regime_n = max_samples // 4
    tail_n = max_samples - recent_n - regime_n
    recent = np.arange(n_samples - recent_n, n_samples, dtype=np.int64)
    historical = np.arange(0, n_samples - recent_n, dtype=np.int64)
    if not len(historical):
        return recent

    X_seq = np.asarray(X_flat).reshape(n_samples, lookback, input_dim)
    regime_score = np.nanmean(
        np.nanstd(X_seq[historical, -min(5, lookback):, :], axis=1),
        axis=1,
    )
    regime_score = np.nan_to_num(regime_score, nan=0.0, posinf=0.0, neginf=0.0)
    quantiles = np.quantile(regime_score, [0.25, 0.50, 0.75])
    bins = np.digitize(regime_score, quantiles, right=True)
    regime_parts = []
    per_bin = max(1, regime_n // 4)
    for bucket in range(4):
        members = historical[bins == bucket]
        if len(members):
            positions = np.linspace(
                0, len(members) - 1, min(per_bin, len(members)), dtype=np.int64
            )
            regime_parts.append(members[positions])
    regime_sample = (
        np.concatenate(regime_parts) if regime_parts else np.array([], dtype=np.int64)
    )

    temporal_change = np.nanmean(
        np.abs(X_seq[historical, -1, :] - X_seq[historical, 0, :]), axis=1
    )
    tail_order = np.argsort(
        np.nan_to_num(temporal_change, nan=-np.inf)
    )[::-1]
    tail_sample = historical[tail_order[:tail_n]]
    selected = np.unique(np.concatenate([recent, regime_sample, tail_sample]))
    if len(selected) < max_samples:
        remaining = np.setdiff1d(historical, selected, assume_unique=False)
        need = min(max_samples - len(selected), len(remaining))
        if need:
            positions = np.linspace(
                0, len(remaining) - 1, need, dtype=np.int64
            )
            selected = np.unique(np.concatenate([selected, remaining[positions]]))
    if len(selected) > max_samples:
        selected = selected[-max_samples:]
    return np.sort(selected.astype(np.int64))


def _atomic_joblib_dump(value, path: str) -> None:
    """Write one model artifact atomically AND record its integrity manifest.

    Every save in this file funnels through here, so recording the hash in one place gives the
    whole trainer verify-before-deserialize on the way back in. The matching read path is
    _verified_joblib_load below - never call joblib.load directly here again."""
    from verified_io import atomic_dump

    atomic_dump(value, path)


def _verified_joblib_load(path):
    """Hash-check against the sidecar manifest BEFORE deserializing.

    joblib.load executes arbitrary code while unpickling, so validating after loading has
    already lost. Artifacts written before this migration have no manifest; they still load
    while BTC_STRICT_ARTIFACT_IDENTITY is off, and each one is COUNTED as remaining debt."""
    from verified_io import verified_load

    return verified_load(path)


# Optional LightGBM
try:
    import lightgbm as lgb
    HAS_LGBM = True
except ImportError:
    HAS_LGBM = False
    logger.warning("LightGBM not installed. Ensemble will skip LightGBM.")

# LightGBM's Windows OpenCL backend is unstable on this machine: even a one-tree
# capability probe intermittently terminates Python with 0xC0000005 during native
# teardown. Do not run a fit at import time. CPU is the safe default; an operator
# may explicitly opt into the OpenCL path after validating the local wheel/driver.
_lgb_requested_device = os.environ.get("BTC_LGB_DEVICE", "cpu").strip().lower()
LGB_DEVICE = "gpu" if _lgb_requested_device in {"gpu", "opencl"} else "cpu"
if HAS_LGBM:
    logger.info(
        "LightGBM device=%s%s.",
        LGB_DEVICE,
        " (explicit opt-in)" if LGB_DEVICE == "gpu" else " (safe default)",
    )

# Optional CatBoost
try:
    from catboost import CatBoostClassifier
    HAS_CATBOOST = True
except ImportError:
    HAS_CATBOOST = False
    logger.warning("CatBoost not installed. Ensemble will skip CatBoost.")

# Probe XGBoost CUDA once at import (same pattern as the LightGBM probe above —
# pip xgboost wheels ship GPU support but need an NVIDIA/CUDA device; OpenCL-only
# GPUs serve LightGBM but not XGBoost). Falls back to CPU silently and safely.
XGB_DEVICE = "cpu"
try:
    import numpy as _np_xprobe
    _xp = xgb.XGBClassifier(n_estimators=1, tree_method="hist", device="cuda",
                            verbosity=0)
    _xp.fit(_np_xprobe.zeros((6, 2)), [0, 1, 2, 0, 1, 2])
    XGB_DEVICE = "cuda"
    logger.info("XGBoost CUDA support detected — training with device='cuda'.")
except Exception:
    XGB_DEVICE = "cpu"
    logger.info("XGBoost CUDA not available — training on CPU.")

# Probe CatBoost GPU once at import (CUDA-only, like XGBoost).
CAT_DEVICE = "CPU"
if HAS_CATBOOST:
    try:
        import numpy as _np_cprobe
        _cp = CatBoostClassifier(iterations=1, task_type="GPU", devices="0",
                                 verbose=False, allow_writing_files=False)
        _cp.fit(_np_cprobe.zeros((6, 2)), [0, 1, 2, 0, 1, 2])
        CAT_DEVICE = "GPU"
        logger.info("CatBoost GPU support detected — training with task_type='GPU'.")
    except Exception:
        CAT_DEVICE = "CPU"
        logger.info("CatBoost GPU not available — training on CPU.")

# Availability probe only. Serialization goes through verified_io via _atomic_joblib_dump
# and _verified_joblib_load, so joblib is never imported or called directly in this module -
# find_spec answers "is it installed?" without binding a name nothing here uses.
import importlib.util as _importlib_util

HAS_JOBLIB = _importlib_util.find_spec("joblib") is not None

# Optional PyTorch for Deep Learning sequence models
try:
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset
    HAS_TORCH = True

    class SequenceNet(nn.Module):
        def __init__(self, input_dim: int, hidden_dim: int = 64, num_layers: int = 1, num_classes: int = 3):
            super().__init__()
            self.lstm = nn.LSTM(input_dim, hidden_dim, num_layers, batch_first=True)
            self.gru = nn.GRU(hidden_dim, hidden_dim, num_layers, batch_first=True)
            self.fc = nn.Linear(hidden_dim, num_classes)
            
        def forward(self, x):
            out, _ = self.lstm(x)
            out, _ = self.gru(out)
            out = out[:, -1, :]
            out = self.fc(out)
            return out

    class TCNSequenceNet(nn.Module):
        """
        Lightweight temporal convolutional network for short-horizon sequence patterns.
        It uses dilated Conv1d blocks over the lookback window, then pools the temporal
        state into a 3-class direction head. This is usually faster than recurrent
        layers on laptop GPUs while still learning order/shape in the recent sequence.
        """
        def __init__(self, input_dim: int, hidden_dim: int = 64, num_classes: int = 3):
            super().__init__()
            self.net = nn.Sequential(
                nn.Conv1d(input_dim, hidden_dim, kernel_size=3, padding=1, dilation=1),
                nn.GELU(),
                nn.BatchNorm1d(hidden_dim),
                nn.Conv1d(hidden_dim, hidden_dim, kernel_size=3, padding=2, dilation=2),
                nn.GELU(),
                nn.BatchNorm1d(hidden_dim),
                nn.Conv1d(hidden_dim, hidden_dim, kernel_size=3, padding=4, dilation=4),
                nn.GELU(),
                nn.AdaptiveAvgPool1d(1),
            )
            self.fc = nn.Linear(hidden_dim, num_classes)

        def forward(self, x):
            x = x.transpose(1, 2)
            out = self.net(x).squeeze(-1)
            return self.fc(out)

    class PyTorchSequenceModel:
        def __init__(self, input_dim: int, lookback: int, device: str = "cuda" if torch.cuda.is_available() else "cpu", epochs: int = 12, batch_size: int = 1024):
            self.input_dim = input_dim
            self.lookback = lookback
            self.device = device
            self.epochs = epochs
            self.batch_size = batch_size
            self.arch = DL_ARCH
            self.model = (TCNSequenceNet(input_dim) if self.arch == "TCN" else SequenceNet(input_dim)).to(device)
            self.criterion = nn.CrossEntropyLoss()
            self.optimizer = torch.optim.AdamW(self.model.parameters(), lr=0.005)
            self.classes_ = np.array([0, 1, 2])
            
        def fit(self, X_flat: np.ndarray, y: np.ndarray, sample_weight: Optional[np.ndarray] = None):
            N = X_flat.shape[0]
            if N == 0:
                return self
            X_3d = X_flat.reshape(N, self.lookback, self.input_dim)
            if N > TCN_MAX_SAMPLES:
                selected = _balanced_sequence_indices(
                    X_flat,
                    TCN_MAX_SAMPLES,
                    self.lookback,
                    self.input_dim,
                )
                X_3d = X_3d[selected]
                y = np.asarray(y)[selected]
                if sample_weight is not None:
                    sample_weight = np.asarray(sample_weight)[selected]
                logger.info(
                    "[TRAIN DL] balanced sample: total=%s selected=%s "
                    "(50%% recent, 25%% historical regime, 25%% historical tail)",
                    N,
                    len(selected),
                )

            # v6 fix (operator-caught audit): sample_weight was ACCEPTED but silently
            # IGNORED — the class-balanced loss (v5's headline) never reached TCN while
            # the other five classifiers trained balanced. Fold the per-sample weights
            # into per-class weights for CrossEntropyLoss (mean weight per class —
            # exact for our weights, which are class-constant × recency).
            # v7 fix (2026-07-28): the v6 patch folded per-sample weights into a per-CLASS
            # mean and called that "exact for our weights, which are class-constant x
            # recency". It is not. Averaging over a class collapses the recency term:
            # mean(class_w[c] * recency_i) = class_w[c] * mean(recency), so every sample in
            # a class trained at the SAME weight and a six-month-old sequence counted as
            # much as yesterday's. The five tree models used the real per-sample vector, so
            # only the TCN was blind to recency. Weights are now carried per sample through
            # the loader and applied inside the loss.
            _sw_t = None
            if sample_weight is not None and len(sample_weight) == len(y):
                _sw = np.asarray(sample_weight, dtype=np.float32)
                _sw = _sw / max(1e-9, float(_sw.mean()))   # mean 1.0 -> LR stays comparable
                _sw_t = torch.tensor(_sw, dtype=torch.float32).to(self.device)
                # reduction='none' so each sample keeps its own weight; we reduce by hand.
                self.criterion = nn.CrossEntropyLoss(reduction="none")

            X_tensor = torch.tensor(X_3d, dtype=torch.float32).to(self.device)
            y_tensor = torch.tensor(y, dtype=torch.long).to(self.device)

            if _sw_t is not None:
                dataset = TensorDataset(X_tensor, y_tensor, _sw_t)
            else:
                dataset = TensorDataset(X_tensor, y_tensor)
            loader = DataLoader(dataset, batch_size=self.batch_size, shuffle=True)
            
            self.model.train()
            logger.info(
                "[TRAIN DL] %s fit start: samples=%s device=%s epochs=%s batch_size=%s",
                self.arch,
                len(dataset),
                self.device,
                self.epochs,
                self.batch_size,
            )
            for epoch in range(self.epochs):
                epoch_t0 = time.time()
                for _batch in loader:
                    if len(_batch) == 3:
                        batch_x, batch_y, batch_w = _batch
                    else:
                        batch_x, batch_y = _batch
                        batch_w = None
                    self.optimizer.zero_grad()
                    outputs = self.model(batch_x)
                    loss = self.criterion(outputs, batch_y)
                    if batch_w is not None:
                        # criterion is reduction='none' here: one loss per sample, each
                        # scaled by ITS OWN weight, then averaged over the batch.
                        loss = (loss * batch_w).sum() / batch_w.sum().clamp_min(1e-9)
                    loss.backward()
                    self.optimizer.step()
                logger.info(
                    "[TRAIN DL] %s epoch %s/%s complete in %.1fs",
                    self.arch,
                    epoch + 1,
                    self.epochs,
                    time.time() - epoch_t0,
                )
            return self

        def predict_proba(self, X_flat: np.ndarray) -> np.ndarray:
            N = X_flat.shape[0]
            X_3d = X_flat.reshape(N, self.lookback, self.input_dim)
            self.model.eval()
            # v7 (2026-07-28): this used to call self.model.to("cpu") on EVERY call, which
            # permanently migrated a CUDA-trained model to CPU on the first prediction and
            # then re-walked every parameter tensor on each subsequent tick. Inference now
            # stays on self.device and only the finished probability matrix crosses back.
            X_tensor = torch.tensor(X_3d, dtype=torch.float32).to(self.device)
            with torch.no_grad():
                outputs = self.model(X_tensor)
                probs = torch.softmax(outputs, dim=1).cpu().numpy()
            return probs
            
        def get_params(self, deep: bool = True) -> Dict:
            return {}

except ImportError:
    HAS_TORCH = False
    logger.warning("PyTorch not installed. Deep Learning models disabled.")


# Bump when the TRAINING PROCEDURE changes in a way that makes an older bundle
# non-comparable, even if the feature columns are identical. Distinct from
# features.FEATURE_SEMANTICS_VERSION, which tracks what the INPUTS mean.
TRAINING_SEMANTICS_VERSION = 3
TRAINING_SEMANTICS_CHANGELOG = {
    3: "2026-07-31 direction labels use a causal per-candle ATR threshold instead "
       "of one threshold derived from the final rows; same-bar dual barrier touches "
       "become conservative no-trade labels; XGBoost/LightGBM probability calibration "
       "uses purged chronological folds instead of shuffled stratified CV; OOF "
       "stacking excludes synthetic class-presence compatibility rows; class priors "
       "and PSI reference distributions use training rows only.",
    2: "2026-07-28 TCN: per-sample weighted loss (was per-class mean, which discarded "
       "recency) + device-stable inference. Bundles trained under v1 optimised a "
       "different objective and are not comparable.",
    1: "original: TCN folded per-sample weights into per-class means",
}


# Saved models live under <project>/data/saved_models (override with BTC_DATA_DIR),
# keeping all app-generated files in one place.
MODEL_DIR = os.path.join(
    os.environ.get("BTC_DATA_DIR")
    or os.path.join(os.path.dirname(os.path.dirname(__file__)), "data"),
    "saved_models",
)
# v2: target-construction fixes — label/serve alignment (entry = last-feature candle),
# cost-floored adaptive threshold, verification graded on the same band. Forces one
# retrain so saved bundles aren't silently loaded with the old (mis-aligned) target.
# v6 roster surgery (2026-06-12 §5ar): SGD retired (measured anti-signal: 0.124-0.23
# OOF in live-relevant buckets), TCN promoted to a full stacker seat with a real
# epoch budget, XGBoost/CatBoost GPU probes. "classbal" MUST stay in this string —
# the inference-time prior-division retirement is keyed on it.
CASCADE_MIN_ACCURACY = 0.62
CASCADE_MIN_PREDICTIONS = 30

from collections import deque

class CascadeMonitor:
    """
    Tracks whether the hierarchical cascade is improving or hurting
    accuracy on 3m and 5m horizons. Auto-disables if net negative.
    """

    def __init__(self, window: int = 50, min_samples: int = 20):
        self.window = window
        self.min_samples = min_samples

        # Pruned to {5,15} 2026-06-21. Only valid chain is 15m<-5m (cascade_map={15:5}); the TARGET
        # 15m is tracked here (compares 15m accuracy WITH vs WITHOUT the 5m bias). (was 3m+5m.)
        self.cascade_on_results = {
            '15m': deque(maxlen=window)
        }
        self.cascade_off_results = {
            '15m': deque(maxlen=window)
        }
        self.cascade_enabled = True
        self.avg_impact = 0.0

    def record_outcome(self, horizon: str, was_cascade_active: bool,
                       was_correct: bool):
        if horizon not in self.cascade_on_results:
            return
        if was_cascade_active:
            self.cascade_on_results[horizon].append(int(was_correct))
        else:
            self.cascade_off_results[horizon].append(int(was_correct))

    def evaluate_cascade(self) -> dict:
        report = {}
        net_impact_scores = []

        for horizon in ['15m']:   # cascade target of {15:5} (was 3m+5m); pruned 2026-06-21
            on_results = self.cascade_on_results[horizon]
            off_results = self.cascade_off_results[horizon]

            if (len(on_results) < self.min_samples or
                    len(off_results) < self.min_samples):
                report[horizon] = {
                    'status': 'insufficient_data',
                    'cascade_accuracy': None,
                    'baseline_accuracy': None,
                    'impact': None
                }
                continue

            cascade_acc = sum(on_results) / len(on_results)
            baseline_acc = sum(off_results) / len(off_results)
            impact = cascade_acc - baseline_acc

            report[horizon] = {
                'status': 'active' if impact > 0 else 'hurting',
                'cascade_accuracy': round(cascade_acc * 100, 1),
                'baseline_accuracy': round(baseline_acc * 100, 1),
                'impact': round(impact * 100, 1)
            }
            net_impact_scores.append(impact)

        if net_impact_scores:   # was hardcoded ==2 (3m+5m); now horizon-count-agnostic (5m-only)
            avg_impact = sum(net_impact_scores) / len(net_impact_scores)
            self.avg_impact = avg_impact
            if avg_impact < -0.02:
                self.cascade_enabled = False
                report['auto_disabled'] = True
                report['reason'] = (
                    f'Cascade net impact: {avg_impact*100:.1f}%. '
                    f'Auto-disabled to protect accuracy.'
                )
            elif avg_impact > 0.01 and not self.cascade_enabled:
                self.cascade_enabled = True
                report['auto_enabled'] = True

        return report

    @property
    def is_active(self) -> bool:
        return self.cascade_enabled


class MultiModelEnsemble:
    """
    Institutional-grade multi-model ensemble with stability controls.

    Direction models:
      - XGBoost
      - LightGBM, when installed
      - CatBoost, when installed
      - Random Forest
      - HistGradientBoosting
      - optional PyTorch TCN/sequence model
      - Logistic Regression

    Stability features:
      - Exponential smoothing on probabilities
      - Direction lock with hysteresis band
      - Minimum cooldown between direction changes
      - Regime-specific experts and live quality filters
    """

    def __init__(self, horizons=None, config=None, model_dir=None):
        if horizons is None:
            self.horizons = [5, 15]   # pruned 2026-06-21: dropped 3/7/10/30 (no market, coin-flip)
        else:
            self.horizons = horizons
            
        self.config = config or {}
        self.model_dir = os.path.abspath(model_dir or MODEL_DIR)
        self.enforce_quantile_skip = self.config.get("enforce_quantile_skip", False)
        self.model_bundle_id = self.config.get("model_bundle_id", f"bundle_{int(time.time())}")
        self.full_refit = False

        # Per-horizon lock durations (each prediction changes at its own cadence)
        self.horizon_lock_seconds = {
            1: 55,    # ~1 min
            3: 170,   # ~3 min
            5: 290,   # ~5 min
            7: 410,   # ~7 min
            10: 580,  # ~10 min
            15: 880,  # ~15 min
            30: 1780, # ~30 min
        }
        
        # Auto-learning state
        self.train_count = 0
        self.learning_adjustments = {}

        # Regime-Specific Experts: separate ensemble for each market regime
        self.regimes = ["TREND", "RANGE", "VOLATILE", "GLOBAL"]
        self.models_by_regime = {
            reg: {
                "xgb": {},
                "lgb": {},
                "cat": {},
                "histgb": {},
                "dl": {},
                "lr": {},
                "rf": {},      # operator-requested tabular RandomForest seat
                "mag": {},

            } for reg in self.regimes
        }
        
        self.stackers_by_regime = {reg: {} for reg in self.regimes}
        self.conformal_residuals = {reg: {} for reg in self.regimes}
        #: P0-1. The regime HMM this bundle's experts were partitioned by, carried with the
        #: bundle so a restart or a promotion routes rows the same way training did.
        self.hmm_state = None
        self.class_priors = {}  # per-horizon [DOWN, NEUTRAL, UP] training base rates
        self.is_trained = False
        self.training_history = {}
        # Historical/per-model accuracy cache. Older builds used mixed shapes here;
        # _get_dynamic_weights accepts both horizon-keyed and regime->horizon keyed
        # layouts so loaded bundles do not silently no-op after upgrades.
        self.model_accuracies = {}
        self.move_size_stats = {reg: {} for reg in self.regimes}  # cheap regime/horizon target-size prior
        self.feature_reference = {}  # training feature distribution for PSI drift detection
        self.calibration_provenance = {}

        # Base weights (adjusted dynamically based on backtest accuracy)
        self.base_weights = self.config.get("base_weights", {
            "xgboost": 0.40,
            "lightgbm": 0.25 if HAS_LGBM else 0.0,
            "catboost": 0.15 if HAS_CATBOOST else 0.0,
            "dl": 0.20 if HAS_TORCH else 0.0,
            "histgb": 0.10,
            "lr": 0.05,
            "rf": 0.08,
        })
        # Sub-signal weights
        self.sub_weights = {
            "models": 0.55,        # Combined ML models
            "orderFlow": 0.15,
            "derivatives": 0.12,
            "technical": 0.10,
            "sentiment": 0.05,
            "onChain": 0.03,
        }

        # Stability state
        self.smoothed_probs = {}
        self.locked_direction = {}    # {horizon: "UP" / "DOWN" / "NEUTRAL"}
        self.direction_lock_time = {} # {horizon: timestamp}
        self.opposing_ticks = {}      # {horizon: count of consecutive opposing signals}
        self.last_raw_directions = {} # {horizon: deque of last N raw directions}
        # Populated during the existing agreement pass, so the evidence ledger can retain each
        # base seat's full probability vector without running every model a second time.
        self.latest_model_probabilities = {}

        # Configuration
        self.smoothing_alpha = self.config.get("smoothing_alpha", 0.12)
        # Hysteresis for flipping the locked direction. The 3-class model's confidence
        # tops out near ~0.5, so p_up/p_down rarely differ by 0.05 — at that margin the
        # lock almost never flips and holds a stale direction. 0.015 matches this model's
        # actual probability spread so genuine reversals can register.
        self.hysteresis_margin = self.config.get("hysteresis_margin", 0.015)
        self.required_opposing_ticks = self.config.get("required_opposing_ticks", 3)
        # Calibrated to the model's real 3-class confidence scale (~0.40–0.55).
        self.confidence_threshold = self.config.get("confidence_threshold", 0.42)

        # Feature pruning is model-local. Full feature rows still flow through UI,
        # diagnostics, signal explanations, and historical replay; only train/predict
        # paths are narrowed here so train and live inference stay dimension-aligned.
        self.model_feature_indices = np.asarray(MODEL_FEATURE_INDICES, dtype=int)
        self.model_feature_names = list(MODEL_FEATURE_NAMES)
        self.model_num_features = int(MODEL_NUM_FEATURES)
        self.train_split_frac = 0.8
        self.train_split_idx = 0
        self.model_feature_schema_hash = MODEL_FEATURE_SCHEMA_HASH
        self.model_feature_pruning = MODEL_FEATURE_PRUNING
        self.feature_reference_names = list(self.model_feature_names)

    def _select_model_features(self, X: np.ndarray) -> np.ndarray:
        """Select the model's feature mask from full 2D/3D feature arrays."""
        arr = np.asarray(X)
        if arr.ndim == 3:
            if arr.shape[2] == self.model_num_features:
                return arr
            if arr.shape[2] == NUM_FEATURES:
                return arr[:, :, self.model_feature_indices]
            return arr
        if arr.ndim == 2:
            if arr.shape[1] == self.model_num_features:
                return arr
            if arr.shape[1] == NUM_FEATURES:
                return arr[:, self.model_feature_indices]
            if arr.shape[1] == LOOKBACK * self.model_num_features:
                return arr
            if arr.shape[1] == LOOKBACK * NUM_FEATURES:
                seq = arr.reshape(arr.shape[0], LOOKBACK, NUM_FEATURES)
                return seq[:, :, self.model_feature_indices].reshape(arr.shape[0], -1)
        return arr

    def _flatten_model_features(self, X: np.ndarray) -> np.ndarray:
        """Return a flat matrix using the exact schema the models trained on."""
        arr = self._select_model_features(X)
        if arr.ndim == 3:
            return arr.reshape(arr.shape[0], arr.shape[1] * arr.shape[2])
        return arr

    def train(self, X: np.ndarray, Y: dict[int, np.ndarray], Ymag: dict = None,
              valid_mask: dict[int, np.ndarray] = None,
              regime_labels: list = None, full_refit: bool = False,
              calibration_source=None):
        """
        Train all models for each horizon, split by market regime.

        regime_labels (P4.3): per-training-row coarse regime ("TREND"/"RANGE"/"VOLATILE")
        from the SAME HMM that routes at serving time. When provided, regime buckets are
        built from these labels so the regime experts train and serve on the IDENTICAL
        partition. When None, falls back to the legacy ADX/vol threshold clustering (which
        trained a different partition than serving used — the mismatch P4.3 fixes).
        """
        train_started = time.time()
        requested_days = _env_int(
            "BTC_HISTORICAL_DAYS", _env_int("BTC_BACKFILL_DAYS", 0)
        )
        self.training_identity = current_training_identity(
            requested_days=requested_days,
            feature_names=self.model_feature_names,
            code_paths=[
                __file__,
                os.path.join(os.path.dirname(__file__), "features.py"),
                os.path.join(os.path.dirname(__file__), "model_contract.py"),
            ],
            full_refit=full_refit,
        )
        identity_issues = training_identity_issues(self.training_identity)
        if identity_issues:
            raise RuntimeError(
                "training-data identity contract failed before training: "
                + "; ".join(identity_issues)
            )
        n_samples, lookback, n_features = X.shape
        X_model = self._select_model_features(X)
        _, _, model_n_features = X_model.shape
        X_flat = X_model.reshape((n_samples, lookback * model_n_features))

        # Train/holdout split. Configurable via BTC_TRAIN_SPLIT_FRAC (default 0.8).
        # CLAMPED to [0.5, 0.98]: a holdout is MANDATORY because the magnitude bands are
        # conformal-calibrated on the held-out slice (see §conformal below) and the OOS
        # backtest scores the held-out tail. A literal 1.0 ("100% of data") would leave the
        # bands uncalibrated (systematically too narrow) and the backtest in-sample. 0.9–0.95
        # is the way to "use almost all the data" while keeping the bands honest.
        try:
            _split_frac = float(os.environ.get("BTC_TRAIN_SPLIT_FRAC", "0.98"))
        except ValueError:
            _split_frac = 0.98
        _split_frac = min(max(_split_frac, 0.5), 0.98)
        split_idx = n_samples if full_refit else int(n_samples * _split_frac)
        self.full_refit = bool(full_refit)
        self.train_split_frac = 1.0 if full_refit else _split_frac
        self.train_split_idx = split_idx
        logger.info("[TRAIN] split_frac=%.3f (holdout=%s rows, full_refit=%s)",
                    self.train_split_frac, n_samples - split_idx, full_refit)
        logger.info(
            "[TRAIN] Starting ensemble training: samples=%s, train_samples=%s, lookback=%s, model_features=%s/%s, pruning=%s, horizons=%s",
            n_samples,
            split_idx,
            lookback,
            model_n_features,
            n_features,
            self.model_feature_pruning,
            self.horizons,
        )

        # Priors are model inputs for legacy non-class-balanced bundles, so they must
        # be estimated from fitted rows only. The untouched tail remains evaluation-only.
        self.class_priors = {}
        for _h in self.horizons:
            if _h in Y and len(Y[_h]) >= split_idx:
                try:
                    self.class_priors[_h] = np.asarray(Y[_h])[:split_idx].mean(axis=0).tolist()
                except Exception:
                    pass
        if self.class_priors:
            logger.info(
                "[TRAIN] Train-slice class priors (DOWN/NEUTRAL/UP): %s",
                {h: [round(x, 3) for x in p] for h, p in self.class_priors.items()},
            )
        
        half_life = max(50.0, split_idx / 3.0)
        _idx = np.arange(split_idx)
        recency_w = 0.5 ** ((split_idx - 1 - _idx) / half_life)
        similarity_w = _regime_similarity_weights(
            X_model, self.model_feature_names, split_idx
        )
        if SAMPLE_WEIGHT_MODE == "similarity":
            recency_w = similarity_w
        elif SAMPLE_WEIGHT_MODE == "recency_similarity":
            recency_w = recency_w * similarity_w
            recency_w = recency_w / max(float(np.mean(recency_w)), 1e-9)
        logger.info(
            "[TRAIN] sample_weight_mode=%s similarity_q10/q50/q90=%s",
            SAMPLE_WEIGHT_MODE,
            [
                round(float(value), 3)
                for value in np.quantile(similarity_w, [0.1, 0.5, 0.9])
            ],
        )

        # Cluster training indices by regime. P4.3: prefer the HMM labels (same partition
        # serving routes by); fall back to the legacy ADX/vol thresholds only if labels
        # are absent or misaligned.
        regime_indices = {reg: [] for reg in self.regimes}
        _use_hmm = (regime_labels is not None and len(regime_labels) >= split_idx)
        if _use_hmm:
            logger.info("[TRAIN] P4.3 regime alignment ACTIVE — bucketing by HMM labels.")
            for i in range(split_idx):
                regime_indices["GLOBAL"].append(i)
                c = regime_labels[i]
                regime_indices[c if c in ("TREND", "RANGE", "VOLATILE") else "RANGE"].append(i)
        else:
            if regime_labels is not None:
                logger.warning("[TRAIN] regime_labels length %s < split_idx %s — using "
                               "legacy threshold clustering.", len(regime_labels), split_idx)
            for i in range(split_idx):
                regime_indices["GLOBAL"].append(i)
                adx_idx = self.model_feature_names.index("adx_norm")
                vol_idx = self.model_feature_names.index("ewma_vol")
                adx_val = X_model[i, -1, adx_idx]
                vol_val = X_model[i, -1, vol_idx]
                if vol_val > 0.6:
                    regime_indices["VOLATILE"].append(i)
                elif adx_val > 0.25:
                    regime_indices["TREND"].append(i)
                else:
                    regime_indices["RANGE"].append(i)

        self._build_move_size_stats(Ymag, regime_indices, split_idx)

        direction_models = ["XGBoost", "RandomForest", "HistGradientBoosting", "LogisticRegression"]
        if HAS_LGBM:
            direction_models.append(f"LightGBM({LGB_DEVICE})")
        if HAS_CATBOOST:
            direction_models.append("CatBoost")
        if HAS_TORCH:
            direction_models.append(DL_ARCH)

        train_plan = []
        for h in self.horizons:
            if h not in Y or len(Y[h]) == 0:
                continue
            y_classes_plan = np.argmax(Y[h], axis=1)
            y_train_plan = y_classes_plan[:split_idx]
            for reg in self.regimes:
                reg_idx_plan = _representative_training_indices(
                    np.array(regime_indices[reg]), DIRECTION_MAX_SAMPLES)
                if reg != "GLOBAL" and len(reg_idx_plan) < 1000:
                    continue
                if len(reg_idx_plan) == 0:
                    continue
                y_reg_plan = y_train_plan[reg_idx_plan]
                if len(np.unique(y_reg_plan)) < 2:
                    continue
                component_count = len(direction_models)
                has_magnitude = Ymag and h in Ymag and len(Ymag[h]) >= split_idx
                if has_magnitude and (
                    MOVE_SIZE_REGIME_SCOPE == "ALL"
                    or (MOVE_SIZE_REGIME_SCOPE != "NONE" and reg == "GLOBAL")
                ):
                    component_count += 1
                if has_magnitude and (
                    QUANTILE_REGIME_SCOPE == "ALL"
                    or (QUANTILE_REGIME_SCOPE != "NONE" and reg == "GLOBAL")
                ):
                    component_count += 3
                component_count += 1  # OOF stacker attempt
                train_plan.append(
                    {
                        "horizon": h,
                        "regime": reg,
                        "samples": int(len(reg_idx_plan)),
                        "classes": sorted(int(c) for c in np.unique(y_reg_plan)),
                        "components": component_count,
                    }
                )

        total_jobs = sum(p["components"] for p in train_plan)
        logger.info(
            "[TRAIN] Plan: %s eligible horizon/regime buckets, ~%s model components. Direction models=%s",
            len(train_plan),
            total_jobs,
            ", ".join(direction_models),
        )
        logger.info(
            "[TRAIN] Target-size mode: direction_max_samples=%s move_scope=%s quantile_scope=%s move_max_samples=%s quantile_max_samples=%s quantile_max_iter=%s",
            DIRECTION_MAX_SAMPLES,
            MOVE_SIZE_REGIME_SCOPE,
            QUANTILE_REGIME_SCOPE,
            MOVE_SIZE_MAX_SAMPLES,
            QUANTILE_MAX_SAMPLES,
            QUANTILE_MAX_ITER,
        )
        for p in train_plan:
            logger.info(
                "[TRAIN] Bucket h=%sm reg=%s samples=%s classes=%s components=%s",
                p["horizon"],
                p["regime"],
                p["samples"],
                p["classes"],
                p["components"],
            )

        progress = {"done": 0, "total": max(total_jobs, 1)}

        def log_component_start(horizon: int, regime: str, name: str, samples: int) -> float:
            progress["done"] += 1
            logger.info(
                "[TRAIN %s/%s] h=%sm reg=%s model=%s samples=%s",
                progress["done"],
                progress["total"],
                horizon,
                regime,
                name,
                samples,
            )
            return time.time()

        def log_component_done(horizon: int, regime: str, name: str, started_at: float) -> None:
            logger.info(
                "[TRAIN DONE] h=%sm reg=%s model=%s elapsed=%.1fs",
                horizon,
                regime,
                name,
                time.time() - started_at,
            )

        def regression_slice(reg_idx: np.ndarray, max_samples: int):
            target_idx = np.asarray(reg_idx, dtype=np.int64)
            if max_samples and len(target_idx) > max_samples:
                target_idx = target_idx[-max_samples:]
            X_reg = X_flat[target_idx]
            sw_reg = recency_w[target_idx]
            return X_reg, sw_reg, target_idx

        def recent_classification_slice(X_local: np.ndarray, y_local: np.ndarray, sw_local: np.ndarray, max_samples: int):
            if max_samples and len(X_local) > max_samples:
                keep = np.arange(len(X_local))[-max_samples:]
                return X_local[keep], y_local[keep], sw_local[keep]
            return X_local, y_local, sw_local

        class SkipComponent(Exception):
            pass

        for h in self.horizons:
            if h not in Y or len(Y[h]) == 0:
                continue

            y_classes = np.argmax(Y[h], axis=1)
            y_train = y_classes[:split_idx]

            # AMBIGUOUS EXCLUSION. A bar that touched BOTH barriers has no knowable
            # first-touch order; it used to be labelled NEUTRAL, which asserts "price went
            # nowhere" about the most violent bars in the sample. Excluded by ZEROING the
            # sample weight rather than dropping the row, because X rows are index-aligned
            # with regime_labels and the train/test split - reindexing here would silently
            # desynchronise all three.
            h_weight = np.ones(split_idx, dtype=np.float64)
            if valid_mask and h in valid_mask:
                _vm = np.asarray(valid_mask[h], dtype=bool)
                if len(_vm) == len(y_classes):
                    h_weight = _vm[:split_idx].astype(np.float64)
                    _dropped = int((~_vm[:split_idx]).sum())
                    if _dropped:
                        logger.info(
                            "[TRAIN] h=%sm excluding %s AMBIGUOUS rows (%.2f%%) - both "
                            "barriers touched in one bar, first-touch order unknowable.",
                            h, _dropped, 100.0 * _dropped / max(split_idx, 1))
                else:
                    logger.warning(
                        "[TRAIN] h=%sm valid_mask length %s != labels %s - NOT applying it "
                        "rather than misaligning the weights.", h, len(_vm), len(y_classes))

            # V5 §1 — CLASS-BALANCED LOSS (root-cause fix for the one-sided lean).
            # Recency-only weights let the window's majority class dominate the loss,
            # so the model learned to fade the minority side (the measured DOWN-machine:
            # 89% DOWN leans out of a net-bearish window). Inverse-frequency weights
            # (mean-normalized, clipped so a thin class can't dominate) make a missed UP
            # cost exactly what a missed DOWN costs. Directional classifiers only — the
            # magnitude regressor below keeps recency-only weights (magnitude isn't
            # class-imbalanced). The inference-time prior division is retired with this
            # (see predict: dividing balanced outputs by DATA priors would re-bias them
            # the other way).
            # P1-4. Counted over the rows that actually TRAIN. Ambiguous rows carry zero
            # weight, so including them here let outcomes the model never learns from set the
            # class frequencies for the ones it does - and ambiguity is not uniform across
            # classes (a bar violent enough to touch both barriers is not a NEUTRAL-looking
            # bar), so the distortion is systematic rather than noise.
            _counted = y_train[h_weight > 0] if h_weight.any() else y_train
            _cnt = np.bincount(_counted, minlength=3).astype(float)
            _inv = _cnt.sum() / (3.0 * np.maximum(_cnt, 1.0))
            class_w = np.clip(_inv / _inv.mean(), 0.5, 2.0)
            logger.info("[TRAIN] h=%sm class weights (DOWN/NEUT/UP): %s",
                        h, [round(float(w), 3) for w in class_w])

            for reg in self.regimes:
                reg_idx_all = np.array(regime_indices[reg])
                if reg != "GLOBAL" and len(reg_idx_all) < 1000:
                    logger.info(f"Skipping {reg} for {h}m (only {len(reg_idx_all)} samples). Will fallback to GLOBAL.")
                    continue
                reg_idx = _representative_training_indices(reg_idx_all, DIRECTION_MAX_SAMPLES)

                X_train_h = X_flat[reg_idx]
                y_train_h = y_train[reg_idx]
                # h_weight is 0 for AMBIGUOUS rows, so they contribute nothing to the
                # loss while every index alignment is preserved.
                #
                # Precisely: nothing to the LOSS. A zero-weight row is not the same as an
                # absent one - HistGradientBoosting computes its feature bin edges from every
                # row it is handed regardless of weight, so the fitted probabilities differ
                # slightly from a fit with the rows removed. Measured in
                # test_sample_weight_coverage: identical hard decisions, ~0.004 mean
                # probability drift, versus ~0.25 when the weights are ignored altogether.
                # Stated because "contributes nothing" is the kind of claim that quietly
                # becomes untrue.
                sw = recency_w[reg_idx] * class_w[y_train_h] * h_weight[reg_idx]

                if len(np.unique(y_train_h)) < 2:
                    continue

                # Calibration must see the real chronological rows, never the synthetic
                # class-presence samples appended below for estimator compatibility.
                X_calibration = X_train_h
                y_calibration = y_train_h
                sw_calibration = sw
                calibration_cv = _purged_calibration_splits(y_calibration, h)
                if not calibration_cv:
                    logger.info(
                        "[CALIBRATION] h=%sm reg=%s unavailable: insufficient "
                        "three-class chronological folds; using uncalibrated estimator.",
                        h,
                        reg,
                    )

                # Class-balancing augmentation (per regime bucket). Thin regimes often
                # contain only {DOWN, UP} and no NEUTRAL, which makes multiclass models
                # configured with num_class=3 fail on the non-contiguous label set
                # ("Expected [0 1], got [0 2]"). Top every class up to >=3 tiny-noise
                # samples so XGBoost/LightGBM/CatBoost train reliably in every regime.
                _aug_rng = np.random.RandomState(42)
                _extra_X, _extra_y, _extra_w = [], [], []
                _template = X_train_h[0] if len(X_train_h) else np.zeros(X_flat.shape[1])
                _med_w = float(np.median(sw)) if len(sw) else 1.0
                for _c in (0, 1, 2):
                    _need = 3 - int(np.sum(y_train_h == _c))
                    for _ in range(max(0, _need)):
                        _extra_X.append(_template + _aug_rng.normal(0, 1e-6, _template.shape))
                        _extra_y.append(_c)
                        _extra_w.append(_med_w)
                if _extra_X:
                    X_train_h = np.vstack([X_train_h, np.array(_extra_X)])
                    y_train_h = np.append(y_train_h, _extra_y)
                    sw = np.append(sw, _extra_w)

                # 1. XGBoost
                try:
                    _t0 = log_component_start(h, reg, "XGBoost", len(X_train_h))
                    base_xgb = xgb.XGBClassifier(
                        n_estimators=100, max_depth=4, learning_rate=0.03,
                        objective='multi:softprob', num_class=3, n_jobs=TRAIN_THREADS,
                        random_state=42, eval_metric="mlogloss",
                        subsample=0.8, colsample_bytree=0.8,
                        tree_method='hist', device=XGB_DEVICE
                    )
                    try:
                        if not calibration_cv:
                            raise ValueError("purged chronological calibration unavailable")
                        xgb_model = CalibratedClassifierCV(
                            estimator=base_xgb,
                            method='isotonic',
                            cv=calibration_cv,
                        )
                        xgb_model.fit(
                            X_calibration,
                            y_calibration,
                            sample_weight=sw_calibration,
                        )
                        for cc in getattr(xgb_model, "calibrated_classifiers_", []):
                            fitted = getattr(cc, "estimator", getattr(cc, "base_estimator", None))
                            if fitted is not None:
                                fitted.set_params(device='cpu')
                    except ValueError:
                        base_xgb.fit(X_train_h, y_train_h, sample_weight=sw)
                        base_xgb.set_params(device='cpu')
                        xgb_model = base_xgb
                    self.models_by_regime[reg]["xgb"][h] = xgb_model
                    log_component_done(h, reg, "XGBoost", _t0)
                    
                    # Log SHAP for XGBoost (Trend regime only, to avoid spam)
                    if reg == "TREND":
                        try:
                            import shap
                            import database
                            fitted = base_xgb
                            if hasattr(xgb_model, "calibrated_classifiers_") and xgb_model.calibrated_classifiers_:
                                cc = xgb_model.calibrated_classifiers_[0]
                                fitted = getattr(cc, "estimator", getattr(cc, "base_estimator", base_xgb))

                            explainer = shap.TreeExplainer(fitted)
                            sample_n = min(200, X_train_h.shape[0])
                            shap_values = explainer.shap_values(X_train_h[:sample_n])

                            arr = np.abs(shap_values)
                            per_col = arr.mean(axis=0).mean(axis=-1) if arr.ndim == 3 else arr.mean(axis=0)
                            per_feat = (
                                per_col.reshape(lookback, self.model_num_features).sum(axis=0)
                                if per_col.shape[0] == lookback * self.model_num_features
                                else per_col[:self.model_num_features]
                            )

                            top10 = sorted(zip(self.model_feature_names, per_feat), key=lambda x: x[1], reverse=True)[:10]
                            for rank, (feature, importance) in enumerate(top10):
                                database.insert_feature_importance(h, rank + 1, feature, float(importance))
                            logger.info(f"Logged SHAP importance for {h}m (top feature: {top10[0][0]})")
                        except Exception:
                            pass
                except Exception as e:
                    logger.error(f"XGBoost training failed for h={h} reg={reg}: {e}")

                # 1b. RandomForest (operator-requested tabular seat). NB: on DIRECTION this is
                # a coin-flip like every learner — the OOF stacker weights it accordingly; it
                # adds a small decorrelated lift only on the timing/selectivity question.
                # max_features='sqrt' keeps it fast on the flattened (lookback*NUM_FEATURES) matrix.
                try:
                    from sklearn.ensemble import RandomForestClassifier
                    _t0 = log_component_start(h, reg, "RandomForest", len(X_train_h))
                    rf_model = RandomForestClassifier(
                        n_estimators=150, max_depth=8, min_samples_leaf=50,
                        max_features="sqrt", class_weight="balanced",
                        n_jobs=TRAIN_THREADS, random_state=42)
                    rf_model.fit(X_train_h, y_train_h, sample_weight=sw)
                    self.models_by_regime[reg]["rf"][h] = rf_model
                    log_component_done(h, reg, "RandomForest", _t0)
                except Exception as e:
                    logger.error(f"RandomForest training failed for h={h} reg={reg}: {e}")

                # 2. LightGBM
                if HAS_LGBM:
                    try:
                        _t0 = log_component_start(h, reg, f"LightGBM({LGB_DEVICE})", len(X_train_h))
                        base_lgb = lgb.LGBMClassifier(
                            n_estimators=100, max_depth=4, learning_rate=0.03,
                            objective='multiclass', num_class=3, n_jobs=TRAIN_THREADS,
                            random_state=43, subsample=0.8, colsample_bytree=0.8,
                            verbose=-1, device_type=LGB_DEVICE  # gpu if probed OK, else cpu
                        )
                        try:
                            if not calibration_cv:
                                raise ValueError("purged chronological calibration unavailable")
                            lgb_model = CalibratedClassifierCV(
                                estimator=base_lgb,
                                method='isotonic',
                                cv=calibration_cv,
                            )
                            lgb_model.fit(
                                X_calibration,
                                y_calibration,
                                sample_weight=sw_calibration,
                            )
                        except ValueError:
                            base_lgb.fit(X_train_h, y_train_h, sample_weight=sw)
                            lgb_model = base_lgb
                        self.models_by_regime[reg]["lgb"][h] = lgb_model
                        log_component_done(h, reg, f"LightGBM({LGB_DEVICE})", _t0)
                    except Exception as e:
                        logger.error(f"LightGBM training failed for h={h} reg={reg}: {e}")

                # 2.5 CatBoost (robust noisy-tabular specialist)
                if HAS_CATBOOST:
                    try:
                        _t0 = log_component_start(h, reg, "CatBoost", len(X_train_h))
                        cat_model = CatBoostClassifier(
                            iterations=120,
                            depth=4,
                            learning_rate=0.03,
                            loss_function="MultiClass",
                            random_seed=47,
                            verbose=False,
                            allow_writing_files=False,
                            task_type=CAT_DEVICE,
                            **({"thread_count": TRAIN_THREADS} if CAT_DEVICE == "CPU" else {"devices": "0"}),
                        )
                        cat_model.fit(X_train_h, y_train_h, sample_weight=sw)
                        self.models_by_regime[reg]["cat"][h] = cat_model
                        log_component_done(h, reg, "CatBoost", _t0)
                    except Exception as e:
                        logger.error(f"CatBoost training failed for h={h} reg={reg}: {e}")

                # 3. HistGradientBoosting (CPU baseline anchor)
                try:
                    _t0 = log_component_start(h, reg, "HistGradientBoosting", len(X_train_h))
                    base_histgb = HistGradientBoostingClassifier(
                        learning_rate=0.05, max_iter=100, max_depth=5,
                        random_state=44, min_samples_leaf=5
                    )
                    # P1-4. `sample_weight=sw` was missing here and present on every other
                    # seat. This one estimator therefore ignored recency, ignored class
                    # balancing, and TRAINED ON THE AMBIGUOUS ROWS the rest of the ensemble
                    # excludes - optimising a different empirical distribution from its
                    # neighbours while the stacker combined all of them as if they agreed on
                    # what the training set was.
                    base_histgb.fit(X_train_h, y_train_h, sample_weight=sw)
                    self.models_by_regime[reg]["histgb"][h] = base_histgb
                    log_component_done(h, reg, "HistGradientBoosting", _t0)
                except Exception as e:
                    logger.error(f"HistGBM training failed for h={h} reg={reg}: {e}")

                # 3.5 Deep Learning (TCN by default; LSTM/GRU available via BTC_DL_ARCH=LSTM_GRU)
                if HAS_TORCH:
                    try:
                        _t0 = log_component_start(h, reg, DL_ARCH, len(X_train_h))
                        dl_model = PyTorchSequenceModel(input_dim=self.model_num_features, lookback=LOOKBACK)
                        dl_model.fit(X_train_h, y_train_h, sample_weight=sw)
                        self.models_by_regime[reg]["dl"][h] = dl_model
                        log_component_done(h, reg, DL_ARCH, _t0)
                    except Exception as e:
                        logger.error(f"Deep Learning training failed for h={h} reg={reg}: {e}")

                # 4. Logistic Regression
                try:
                    X_lr, y_lr, sw_lr = recent_classification_slice(X_train_h, y_train_h, sw, LINEAR_MAX_SAMPLES)
                    _t0 = log_component_start(h, reg, "LogisticRegression", len(X_lr))
                    base_lr = LogisticRegression(
                        max_iter=800, solver='lbfgs', C=0.1, random_state=45
                    )
                    base_lr.fit(X_lr, y_lr, sample_weight=sw_lr)
                    self.models_by_regime[reg]["lr"][h] = base_lr
                    log_component_done(h, reg, "LogisticRegression", _t0)
                except Exception as e:
                    logger.error(f"LR training failed for h={h} reg={reg}: {e}")

                # (SGD retired in v6 — measured anti-signal, see MODEL_ROSTER_PLAN R2.
                # Its linear seat stays with LR; its diversity seat went to TCN.)

                # 5. Magnitude regressor
                if Ymag and h in Ymag and len(Ymag[h]) >= split_idx:
                    try:
                        if MOVE_SIZE_REGIME_SCOPE == "NONE" or (
                            MOVE_SIZE_REGIME_SCOPE != "ALL" and reg != "GLOBAL"
                        ):
                            logger.info(
                                "[TRAIN SKIP] h=%sm reg=%s model=MoveSizeRegressor reason=GLOBAL-only target-size mode",
                                h,
                                reg,
                            )
                            raise SkipComponent()
                        # Regressors use the ORIGINAL (non-augmented) regime rows —
                        # the classifier class-balancing dummies must not skew move size.
                        X_reg, sw_reg, target_idx = regression_slice(
                            reg_idx_all, MOVE_SIZE_MAX_SAMPLES)
                        mag_target = np.asarray(Ymag[h][:split_idx])[target_idx]
                        _t0 = log_component_start(h, reg, "MoveSizeRegressorFast", len(X_reg))
                        reg_mag = HistGradientBoostingRegressor(
                            loss="squared_error",
                            max_iter=MOVE_SIZE_MAX_ITER,
                            max_leaf_nodes=15,
                            learning_rate=0.06,
                            l2_regularization=0.05,
                            random_state=46,
                        )
                        reg_mag.fit(X_reg, mag_target, sample_weight=sw_reg)
                        self.models_by_regime[reg]["mag"][h] = reg_mag

                        # Conformal residuals on the HELD-OUT slice (V5 §2.5b): residuals
                        # on the model's own training rows made the band systematically
                        # too narrow (overstating the "projects past the line" confidence).
                        # Validation rows for this regime when HMM labels cover them;
                        # falls back to in-sample (logged) when the held-out cut is thin.
                        _val_idx = np.arange(split_idx, n_samples)
                        if reg != "GLOBAL":
                            _val_idx = (np.array([i for i in _val_idx
                                                  if regime_labels[i] == reg], dtype=int)
                                        if _use_hmm and len(regime_labels) >= n_samples
                                        else np.array([], dtype=int))
                        # P1-6. NO IN-SAMPLE FALLBACK. Residuals on the model's own training
                        # rows are not a conformal interval - they are a measure of fit, and
                        # they are narrowest exactly where the held-out cut was too thin to
                        # use, i.e. where overfitting is most likely. The old fallback
                        # therefore produced its most confident bands in its least trustworthy
                        # regimes, and recorded that only in a log line the serving path never
                        # saw.
                        #
                        # The ladder is: this regime's untouched rows -> the GLOBAL untouched
                        # rows -> no interval at all. A missing interval is a state the caller
                        # can handle; a fabricated one is not.
                        _conf_src = None
                        residuals = None
                        if len(_val_idx) >= 200 and len(Ymag[h]) >= n_samples:
                            _vp = reg_mag.predict(X_flat[_val_idx])
                            residuals = np.asarray(Ymag[h])[_val_idx] - _vp
                            _conf_src = "held-out-regime"
                        else:
                            _global_idx = np.arange(split_idx, n_samples)
                            if len(_global_idx) >= 200 and len(Ymag[h]) >= n_samples:
                                _vp = reg_mag.predict(X_flat[_global_idx])
                                residuals = np.asarray(Ymag[h])[_global_idx] - _vp
                                _conf_src = "held-out-global"
                        if residuals is None:
                            logger.info(
                                "[TRAIN] h=%sm reg=%s conformal interval UNAVAILABLE - no "
                                "untouched slice of at least 200 rows. Recording none rather "
                                "than in-sample residuals, which would be narrowest where "
                                "they are least earned.", h, reg)
                        else:
                            self.conformal_residuals[reg][h] = {
                                "q25": float(np.quantile(residuals, 0.25)),
                                "q50": float(np.quantile(residuals, 0.50)),
                                "q75": float(np.quantile(residuals, 0.75)),
                                # Carried in the ARTIFACT, not just the log: a serving path
                                # cannot honour a caveat it was never told about.
                                "source": _conf_src,
                                "n": int(len(residuals)),
                            }
                            log_component_done(
                                h, reg, f"MoveSizeRegressorFast_conformal[{_conf_src}]", _t0)
                    except SkipComponent:
                        pass
                    except Exception as e:
                        logger.error(f"Magnitude regressor failed for h={h} reg={reg}: {e}")



                # 6. Train Logistic Stacker using Out-of-Fold (OOF) predictions
                try:
                    # P1-5. The weights were already returned here and thrown away. The
                    # production seats are fitted with recency, class balancing and ambiguity
                    # exclusion baked into `sw`; the OOF folds were fitted unweighted, so the
                    # stacker learned to combine probabilities drawn from a different empirical
                    # distribution than the one it is served.
                    X_stack, y_stack, sw_stack = recent_classification_slice(
                        X_calibration,
                        y_calibration,
                        sw_calibration,
                        STACKER_MAX_SAMPLES,
                    )
                    _t0 = log_component_start(h, reg, "OOFStacker", len(X_stack))
                    # P1-5. The purge gap is a LEAKAGE requirement, not a preference. It used
                    # to be min(required, len//8), so a thin bucket silently shrank the gap
                    # below the label overlap and the "purged" folds stopped being purged -
                    # exactly where sample size makes leakage hardest to notice.
                    required_gap = max(LOOKBACK + int(h), 1)
                    max_supportable = max(1, len(X_stack) // 8)
                    if required_gap > max_supportable:
                        raise ValueError(
                            f"OOF stacker needs a purge gap of {required_gap} rows "
                            f"(LOOKBACK+{h}) but only {len(X_stack)} rows are available, which "
                            f"supports {max_supportable}. Refusing to shrink the gap - an "
                            f"under-purged fold leaks the label overlap into the stacker.")
                    purge_gap = required_gap
                    tscv = TimeSeriesSplit(n_splits=3, gap=purge_gap)
                    logger.info(
                        "[OOF] h=%sm reg=%s purged_gap=%s rows",
                        h,
                        reg,
                        purge_gap,
                    )
                    
                    reg_store = self.models_by_regime[reg]
                    base_models = {
                        "xgb": (reg_store.get("xgb") or {}).get(h),
                        "histgb": (reg_store.get("histgb") or {}).get(h),
                        "lr": (reg_store.get("lr") or {}).get(h),
                        "rf": (reg_store.get("rf") or {}).get(h),  # tabular RF seat
                    }
                    if HAS_LGBM:
                        base_models["lgb"] = (reg_store.get("lgb") or {}).get(h)
                    if HAS_CATBOOST:
                        base_models["cat"] = (reg_store.get("cat") or {}).get(h)
                    if HAS_TORCH:
                        # v6: TCN gets a FULL stacker seat — the only architecturally
                        # different learner; previously trained but never stacked.
                        base_models["dl"] = (reg_store.get("dl") or {}).get(h)

                    valid_models = {k: v for k, v in base_models.items() if v is not None}
                    
                    if len(valid_models) >= 2 and len(X_stack) > 50:
                        oof_features = []
                        feature_names = []
                        for name, model in valid_models.items():
                            # P1-5 CALIBRATION PARITY.
                            #
                            # `.estimator` on a CalibratedClassifierCV is the UNCALIBRATED inner
                            # model. Extracting it meant the OOF folds produced raw probabilities
                            # while serving produces isotonic-calibrated ones - so the stacker was
                            # trained on one probability distribution and served another. Not
                            # leakage, but a substantial covariate shift in the single component
                            # whose whole job is combining the others.
                            #
                            # A calibrated seat now yields calibrated folds. Where a fold is too
                            # small to calibrate honestly the seat is DROPPED from the stacker for
                            # this bucket rather than silently substituting a raw one - the
                            # mismatch is the defect, and a quiet fallback would reinstate it.
                            base_est = getattr(model, "estimator", getattr(model, "base_estimator", model))
                            seat_is_calibrated = isinstance(model, CalibratedClassifierCV)
                            if seat_is_calibrated:
                                _cal_method = getattr(model, "method", "isotonic")
                                logger.info(
                                    "[OOF] h=%sm reg=%s seat=%s is calibrated (%s) - folds will "
                                    "be calibrated to match serving", h, reg, name, _cal_method)
                            if hasattr(base_est, "predict_proba"):
                                try:
                                    preds = np.full((len(X_stack), 3), np.nan)
                                    # Counted PER SEAT so a rate can be formed. The previous
                                    # counter had no denominator, so "3 mismatches" could not
                                    # be compared to anything.
                                    _cs_short = _cs_total = 0
                                    for fold_idx, (tr_idx, val_idx) in enumerate(tscv.split(X_stack), start=1):
                                        X_tr, y_tr_global = X_stack[tr_idx], y_stack[tr_idx]
                                        fit_classes = np.array(sorted(int(c) for c in np.unique(y_tr_global)))
                                        if len(fit_classes) < 2:
                                            continue

                                        # Some sklearn-compatible wrappers, especially XGBoost,
                                        # reject binary folds encoded as {0, 2} because binary
                                        # classifiers expect contiguous labels {0, 1}. Remap the
                                        # fold-local labels to contiguous ids, then map probability
                                        # columns back to the global [DOWN, NEUTRAL, UP] order.
                                        class_to_local = {int(c): i for i, c in enumerate(fit_classes)}
                                        y_tr_local = np.array([class_to_local[int(c)] for c in y_tr_global])

                                        if name == "dl":
                                            # The PyTorch wrapper isn't sklearn-clonable —
                                            # build a fresh fold instance (half budget: the
                                            # fold sets are small and refit 3x per bucket).
                                            # P1-5. THE SAME EPOCH BUDGET AS PRODUCTION.
                                            #
                                            # This was epochs*0.5 "because the fold sets are
                                            # small and it refits 3x per bucket" - a speed
                                            # decision with a correctness cost. A half-trained
                                            # TCN is a DIFFERENT model: the stacker learned how
                                            # much to trust the weaker one and is then served
                                            # the fully trained one. Every other seat is cloned
                                            # with its production hyperparameters; the deep
                                            # seat was the only one quietly downgraded.
                                            fold_model = type(model)(
                                                model.input_dim, model.lookback,
                                                epochs=model.epochs,
                                                batch_size=model.batch_size)
                                        else:
                                            fold_model = clone(base_est)
                                        try:
                                            if isinstance(fold_model, xgb.XGBClassifier):
                                                xgb_params = fold_model.get_params()
                                                shared_params = {
                                                    "n_estimators": xgb_params.get("n_estimators", 100),
                                                    "max_depth": xgb_params.get("max_depth", 3),
                                                    "learning_rate": xgb_params.get("learning_rate", 0.05),
                                                    "subsample": xgb_params.get("subsample", 0.8),
                                                    "colsample_bytree": xgb_params.get("colsample_bytree", 0.8),
                                                    "n_jobs": xgb_params.get("n_jobs", -1),
                                                    "random_state": xgb_params.get("random_state", 42),
                                                    "tree_method": xgb_params.get("tree_method", "hist"),
                                                    # The post-fit inference downgrade sets the saved
                                                    # model's device to 'cpu' — inheriting it here kept
                                                    # every OOF fold refit OFF the GPU (the stacker is
                                                    # the single biggest training block). Train folds
                                                    # on the probed device instead.
                                                    "device": XGB_DEVICE,
                                                }
                                                if len(fit_classes) <= 2:
                                                    fold_model = xgb.XGBClassifier(
                                                        **shared_params,
                                                        objective="binary:logistic",
                                                        eval_metric="logloss",
                                                    )
                                                else:
                                                    fold_model = xgb.XGBClassifier(
                                                        **shared_params,
                                                        objective="multi:softprob",
                                                        num_class=len(fit_classes),
                                                        eval_metric="mlogloss",
                                                    )
                                        except Exception:
                                            pass

                                        # P1-5. WEIGHTS, RECOMPUTED FOLD-LOCALLY.
                                        #
                                        # Slicing the global `sw` into a fold is the obvious
                                        # repair and it is wrong. The global weights reference
                                        # the END of the whole training slice:
                                        #
                                        #   recency_w    = 0.5 ** ((split_idx - 1 - i) / hl)
                                        #   similarity_w = f(X, ..., split_idx)
                                        #
                                        # For an early OOF fold, split_idx lies in that fold's
                                        # FUTURE, so sliced weights would rank its rows by
                                        # distance from a point it must not know about. The
                                        # unweighted fit was one mismatch; a future-referenced
                                        # weight would have been a second, subtler one.
                                        #
                                        # Recency and class balance are therefore rebuilt
                                        # against this fold's own last training row. Ambiguity
                                        # exclusion is row-local (a bar either touched both
                                        # barriers or it did not), so it is carried across as
                                        # a mask.
                                        _sw_global = np.asarray(sw_stack, dtype=np.float64)
                                        _keep = (_sw_global[tr_idx] > 0).astype(np.float64)
                                        _pos = np.asarray(tr_idx, dtype=np.float64)
                                        _fold_end = _pos.max() if len(_pos) else 0.0
                                        _half_life = max(1.0, len(tr_idx) / 4.0)
                                        _rec = 0.5 ** ((_fold_end - _pos) / _half_life)
                                        _cnt = np.bincount(
                                            y_tr_local[_keep > 0].astype(int),
                                            minlength=len(fit_classes)).astype(np.float64)
                                        _inv = _cnt.sum() / (len(fit_classes)
                                                             * np.maximum(_cnt, 1.0))
                                        _cls = np.clip(_inv / max(_inv.mean(), 1e-9), 0.5, 2.0)
                                        # P0. THE REGIME-SIMILARITY TERM, REBUILT FOLD-LOCALLY.
                                        #
                                        # SAMPLE_WEIGHT_MODE defaults to `recency_similarity`,
                                        # so production fits every seat under
                                        # recency * similarity * class * ambiguity. The fold
                                        # rebuilt recency and class and DROPPED similarity, so
                                        # the stacker learned to combine probabilities from
                                        # seats fitted under a different objective than the one
                                        # they serve under. The comment above named
                                        # `similarity_w` and explained why SLICING it is wrong,
                                        # which read as though the term had been dealt with.
                                        #
                                        # Rebuilt the same way recency is: referenced to THIS
                                        # fold's last training row, never to the global
                                        # split_idx that lies in an early fold's future.
                                        #
                                        # X_stack is a tail slice of the regime-filtered
                                        # X_calibration, so a stack row maps back to its model
                                        # row through reg_idx. X_calibration is bound BEFORE the
                                        # synthetic class-presence rows are appended, so that
                                        # mapping stays 1:1.
                                        _sim = np.ones(len(tr_idx), dtype=np.float64)
                                        if SAMPLE_WEIGHT_MODE in {"similarity",
                                                                  "recency_similarity"}:
                                            _off = len(X_calibration) - len(X_stack)
                                            _reg = np.asarray(reg_idx, dtype=np.int64)
                                            _want = _off + np.asarray(tr_idx, dtype=np.int64)
                                            if _off < 0 or _want.max(initial=-1) >= len(_reg):
                                                raise ValueError(
                                                    f"seat '{name}': cannot map stack rows back "
                                                    f"to model rows (offset={_off}, "
                                                    f"reg_idx={len(_reg)}); refusing to fit it "
                                                    f"under a different objective than serving")
                                            _rows = _reg[_want]
                                            _sim_full = _regime_similarity_weights(
                                                X_model, self.model_feature_names,
                                                int(_rows.max()) + 1)
                                            _sim = np.asarray(
                                                _sim_full, dtype=np.float64)[_rows]
                                        # Production normalises the recency*similarity product
                                        # before indexing it; matched here so the fold weights
                                        # differ from serving only by their fold-local anchor.
                                        _rs = _rec * _sim
                                        _rs = _rs / max(float(np.mean(_rs)), 1e-9)
                                        sw_tr = _rs * _cls[y_tr_local.astype(int)] * _keep
                                        if not np.any(sw_tr > 0):
                                            raise ValueError(
                                                f"fold for seat '{name}' has no positively "
                                                f"weighted rows after ambiguity exclusion")

                                        # P1-5. CALIBRATION. Match the served wrapper, or drop
                                        # the seat. `cv=2` inside an already-purged outer fold
                                        # keeps the nesting affordable; below the floor there is
                                        # not enough signal to calibrate honestly.
                                        if seat_is_calibrated:
                                            _per_class = np.bincount(
                                                y_tr_local, minlength=len(fit_classes)).min()
                                            if _per_class < 10:
                                                raise ValueError(
                                                    f"fold too small to calibrate seat '{name}' "
                                                    f"({_per_class} rows in its thinnest class); "
                                                    f"refusing an uncalibrated substitute")
                                            # The SERVED wrapper calibrates on purged
                                            # chronological folds with a label-overlap gap.
                                            # An integer `cv=2` shuffles that away: it proves
                                            # neither chronology, purge, embargo nor absence of
                                            # label overlap, so the OOF probabilities came from
                                            # a different calibration protocol than production.
                                            # Use the SAME splitter, and drop the seat when the
                                            # fold cannot support it rather than substituting a
                                            # weaker one.
                                            _fold_splits = _purged_calibration_splits(
                                                y_tr_local, h)
                                            if not _fold_splits:
                                                raise ValueError(
                                                    f"fold cannot support the production purged "
                                                    f"calibration protocol for seat '{name}' "
                                                    f"({len(y_tr_local)} rows); refusing a "
                                                    f"cv=2 substitute that proves no purge")
                                            fold_model = CalibratedClassifierCV(
                                                estimator=fold_model,
                                                method=_cal_method,
                                                cv=_fold_splits,
                                            )

                                        try:
                                            fold_model.fit(X_tr, y_tr_local, sample_weight=sw_tr)
                                        except TypeError as _wexc:
                                            # DROP THE SEAT. The previous version logged that
                                            # "this seat's OOF probabilities do NOT match its
                                            # served fitting pipeline" and then fitted it
                                            # anyway - a warning is not a safety boundary, and
                                            # the mismatched probabilities still reached the
                                            # stacker. A seat that cannot satisfy the training
                                            # contract does not belong in the meta-model.
                                            raise ValueError(
                                                f"seat '{name}' does not accept sample_weight "
                                                f"({_wexc}); dropping it rather than training "
                                                f"the stacker on probabilities from a "
                                                f"different objective") from _wexc
                                        # P1-5. CLASS-SET PARITY, RECORDED.
                                        #
                                        # Production tops every class up to >=3 rows with
                                        # tiny-noise synthetic samples so multiclass estimators
                                        # accept a non-contiguous label set. OOF deliberately
                                        # does NOT (TRAINING_SEMANTICS v3), and that exclusion
                                        # is right: injecting synthetic rows into the
                                        # probabilities the stacker learns from would be worse
                                        # than the mismatch it avoids.
                                        #
                                        # But it has a consequence worth naming. On a thin
                                        # bucket the fold fits 2 classes while production fits
                                        # 3, so the padding below writes EXACTLY 0.0 into the
                                        # absent column while production emits a small positive
                                        # there. The stacker then learns from a column that is
                                        # structurally zero.
                                        #
                                        # Judged not worth dropping the seat over - production's
                                        # own NEUTRAL mass on such a bucket comes from three
                                        # synthetic rows and is near-zero too - so the honest
                                        # action is to COUNT it rather than either paper over it
                                        # or delete a seat the bucket needs.
                                        _cs_total += 1
                                        if len(fit_classes) < 3:
                                            _cs_short += 1
                                            _mismatch = getattr(self, "_oof_class_set_mismatch", None)
                                            if _mismatch is None:
                                                _mismatch = {}
                                                self._oof_class_set_mismatch = _mismatch
                                            _key = f"{reg}|h{h}|{name}"
                                            _mismatch[_key] = _mismatch.get(_key, 0) + 1
                                            logger.info(
                                                "[OOF] h=%sm reg=%s seat=%s fold saw %s of 3 "
                                                "classes; the absent column is padded to a hard "
                                                "0.0 while production (augmented) emits a small "
                                                "positive there",
                                                h, reg, name, len(fit_classes))
                                        fold_probs = fold_model.predict_proba(X_stack[val_idx])
                                        padded = []
                                        local_classes = getattr(
                                            fold_model,
                                            "classes_",
                                            np.arange(fold_probs.shape[1] if hasattr(fold_probs, "shape") else 0),
                                        )
                                        for row in fold_probs:
                                            out = np.zeros(3)
                                            for col_idx, local_c in enumerate(local_classes):
                                                if col_idx >= len(row):
                                                    continue
                                                local_idx = int(local_c)
                                                if 0 <= local_idx < len(fit_classes):
                                                    global_c = int(fit_classes[local_idx])
                                                    if 0 <= global_c < 3:
                                                        out[global_c] = float(row[col_idx])
                                            if out.sum() <= 0:
                                                out[1] = 1.0
                                            else:
                                                out /= out.sum()
                                            padded.append(out)
                                        preds[val_idx] = np.array(padded)

                                    # THE GATE. Measured against the preregistered tolerance,
                                    # not merely recorded. A seat whose folds mostly lacked a
                                    # class contributes a column that is structurally zero in
                                    # training and positive in production; dropping it is the
                                    # same remedy already used when a fold cannot be calibrated.
                                    _cs_rate = (_cs_short / _cs_total) if _cs_total else 0.0
                                    _cs_key = f"{reg}|h{h}|{name}"
                                    _rates = getattr(self, "_oof_class_set_rate", None)
                                    if _rates is None:
                                        _rates = {}
                                        self._oof_class_set_rate = _rates
                                    _rates[_cs_key] = {
                                        "short_folds": int(_cs_short),
                                        "total_folds": int(_cs_total),
                                        "rate": float(_cs_rate),
                                        "tolerance": float(OOF_CLASS_SET_TOLERANCE),
                                        "within_tolerance": bool(
                                            _cs_rate <= OOF_CLASS_SET_TOLERANCE),
                                    }
                                    if _cs_rate > OOF_CLASS_SET_TOLERANCE:
                                        logger.warning(
                                            "[OOF] h=%sm reg=%s seat=%s DROPPED: %s/%s folds "
                                            "fitted fewer than 3 classes (rate %.2f > "
                                            "tolerance %.2f). Its stacker column would be "
                                            "structurally zero where production is positive.",
                                            h, reg, name, _cs_short, _cs_total, _cs_rate,
                                            OOF_CLASS_SET_TOLERANCE)
                                        continue
                                    oof_features.append(preds)
                                    feature_names.append(name)
                                except Exception as e:
                                    logger.error(f"OOF generation failed for {name}: {e}")
                        
                        if len(oof_features) >= 2:
                            valid_oof = np.ones(len(X_stack), dtype=bool)
                            for preds in oof_features:
                                valid_oof &= np.isfinite(preds).all(axis=1)
                            if valid_oof.sum() < 50 or len(np.unique(y_stack[valid_oof])) < 2:
                                raise ValueError("OOF stacker has too few valid purged rows")

                            # P3.1 — populate per-model OOF directional accuracy so
                            # _get_dynamic_weights() reads real numbers instead of {} (the
                            # weighting was previously inert because nothing filled this).
                            # OOF probs are leak-free (purged CV), so this is an honest
                            # per-model skill estimate. Stored flat-by-horizon (GLOBAL is the
                            # canonical default the runtime falls back to) AND nested-by-regime.
                            # Keys are remapped short->long to match base_weights/model_names.
                            try:
                                _oof_name_map = {"xgb": "xgboost", "lgb": "lightgbm", "cat": "catboost"}
                                _y_true_oof = y_stack[valid_oof]
                                # P1-5. SKILL, NOT RAW ACCURACY.
                                #
                                # These numbers become the dynamic ensemble weights. Raw
                                # multiclass accuracy pays a seat for predicting the dominant
                                # class: on a bucket that is 70% NEUTRAL, a model that answers
                                # NEUTRAL every time scores 0.70 and outranks one that makes
                                # real directional calls at 0.55 - so the blend was tilted
                                # toward the most abstaining seat, which is the opposite of
                                # what the weights are meant to express.
                                #
                                # Multiclass log-loss SKILL against the class-prior baseline
                                # instead: 0 means "no better than predicting the base rates",
                                # positive means genuine information, and a seat that only
                                # repeats the prior can no longer win. Accuracy is retained
                                # alongside for the panels, under its own name, so nothing
                                # reads a skill score as an accuracy.
                                _prior = np.bincount(
                                    _y_true_oof.astype(int),
                                    minlength=3).astype(np.float64)
                                _prior = np.maximum(_prior / max(_prior.sum(), 1.0), 1e-6)
                                _prior_ll = float(
                                    -np.mean(np.log(_prior[_y_true_oof.astype(int)])))

                                def _log_loss(_p, _y):
                                    _p = np.clip(np.asarray(_p, dtype=np.float64), 1e-6, 1.0)
                                    _p = _p / np.maximum(_p.sum(axis=1, keepdims=True), 1e-9)
                                    return float(-np.mean(
                                        np.log(_p[np.arange(len(_y)), _y.astype(int)])))

                                _acc_map = {}
                                _raw_acc_map = {}
                                for _sname, _spreds in zip(feature_names, oof_features):
                                    _sel = _spreds[valid_oof]
                                    _pred_cls = np.argmax(_sel, axis=1)
                                    _canon = _oof_name_map.get(_sname, _sname)
                                    _raw_acc_map[_canon] = float(
                                        np.mean(_pred_cls == _y_true_oof))
                                    _skill = (1.0 - _log_loss(_sel, _y_true_oof) / _prior_ll
                                              if _prior_ll > 1e-9 else 0.0)
                                    # Clipped at 0: a seat WORSE than the prior contributes
                                    # nothing to the blend rather than a negative weight.
                                    _acc_map[_canon] = float(max(0.0, _skill))
                                logger.info(
                                    "[OOF] h=%sm reg=%s log-loss skill vs prior: %s "
                                    "(raw accuracy, for the panels only: %s)",
                                    h, reg,
                                    {k: round(v, 4) for k, v in _acc_map.items()},
                                    {k: round(v, 4) for k, v in _raw_acc_map.items()})
                                if _acc_map:
                                    self.model_accuracies.setdefault(reg, {})[h] = dict(_acc_map)
                                    if reg == "GLOBAL":
                                        self.model_accuracies[h] = dict(_acc_map)
                                    logger.info(
                                        "[OOF ACC] h=%sm reg=%s per-model: %s",
                                        h, reg,
                                        ", ".join(f"{k}={v:.3f}" for k, v in _acc_map.items()),
                                    )
                            except Exception as _acc_e:
                                logger.warning("OOF accuracy populate failed h=%s reg=%s: %s", h, reg, _acc_e)

                            X_meta = np.hstack([preds[valid_oof] for preds in oof_features])

                            # NOTE: do NOT `import xgboost as xgb` here — a function-local
                            # import makes `xgb` a local for the WHOLE method, so the earlier
                            # base-model `xgb.XGBClassifier(...)` (line ~604) hits an
                            # UnboundLocalError. The module-level import (top of file) is used.
                            meta_classes = np.array(sorted(int(c) for c in np.unique(y_stack[valid_oof])))
                            meta_class_to_local = {int(c): i for i, c in enumerate(meta_classes)}
                            y_meta_local = np.array([meta_class_to_local[int(c)] for c in y_stack[valid_oof]])
                            if len(meta_classes) <= 2:
                                meta_xgb = xgb.XGBClassifier(
                                    n_estimators=100,
                                    max_depth=3,
                                    learning_rate=0.05,
                                    subsample=0.8,
                                    random_state=42,
                                    objective="binary:logistic",
                                    eval_metric="logloss",
                                )
                            else:
                                meta_xgb = xgb.XGBClassifier(
                                    n_estimators=100,
                                    max_depth=3,
                                    learning_rate=0.05,
                                    subsample=0.8,
                                    random_state=42,
                                    objective="multi:softprob",
                                    num_class=len(meta_classes),
                                    eval_metric="mlogloss",
                                )
                            meta_xgb.fit(X_meta, y_meta_local)
                            self.stackers_by_regime[reg][h] = {
                                "model": meta_xgb,
                                "features": feature_names,
                                "classes": meta_classes.tolist(),
                            }
                            logger.info(f"Trained XGBoost Stacker for {h}m in {reg} with features: {feature_names}")
                    log_component_done(h, reg, "OOFStacker", _t0)
                except Exception as e:
                    logger.error(f"Stacker training failed for h={h} reg={reg}: {e}")

        # The production refit learns from every row. Keep calibration/conformal objects from
        # the leak-free evaluated candidate rather than replacing them with in-sample residuals.
        if full_refit and calibration_source is not None:
            self.conformal_residuals = copy.deepcopy(
                getattr(calibration_source, "conformal_residuals", {}) or {}
            )
            self.calibration_provenance = {
                "source_bundle_id": getattr(calibration_source, "model_bundle_id", ""),
                "source_split_frac": float(getattr(calibration_source, "train_split_frac", 0.98)),
                "method": "evaluated-holdout-plus-purged-oof-stacker",
            }
        else:
            self.calibration_provenance = {
                "source_bundle_id": self.model_bundle_id,
                "source_split_frac": float(self.train_split_frac),
                "method": "native-training-split",
            }

        self._build_feature_reference(X_model[:split_idx])
        self.is_trained = True
        self.train_count += 1
        if not self._save_models():
            raise RuntimeError("Ensemble trained in memory but the saved bundle was not committed")
        logger.info(
            "[TRAIN] Ensemble training finished in %.1fs. Completed/attempted components=%s/%s",
            time.time() - train_started,
            progress["done"],
            progress["total"],
        )

    def _build_move_size_stats(self, Ymag: Optional[dict], regime_indices: dict, split_idx: int):
        """
        Build a near-zero-cost target-size prior by horizon and regime.
        Ymag is stored as fractional realized move size, so live predictions multiply
        the prior by the current BTC price.
        """
        self.move_size_stats = {reg: {} for reg in self.regimes}
        if not Ymag:
            return

        built = 0
        for h in self.horizons:
            if h not in Ymag or len(Ymag[h]) < split_idx:
                continue
            mag = np.asarray(Ymag[h][:split_idx], dtype=float)
            for reg in self.regimes:
                idx = np.asarray(regime_indices.get(reg, []), dtype=int)
                if idx.size == 0:
                    continue
                idx = idx[idx < len(mag)]
                vals = np.abs(mag[idx])
                vals = vals[np.isfinite(vals)]
                if len(vals) < 30:
                    continue
                self.move_size_stats[reg][h] = {
                    "n": int(len(vals)),
                    "median_frac": float(np.median(vals)),
                    "q25_frac": float(np.quantile(vals, 0.25)),
                    "q75_frac": float(np.quantile(vals, 0.75)),
                    "q90_frac": float(np.quantile(vals, 0.90)),
                }
                built += 1
        if built:
            logger.info("[TRAIN] Built regime move-size priors for %s horizon/regime buckets", built)

    def _build_feature_reference(self, X: np.ndarray):
        """Store the training feature distribution (per-feature decile bins) so live
        drift can be measured via PSI. Uses the last timestep of each sequence."""
        try:
            flat = X[:, -1, :]  # (N, n_features)
        except Exception:
            return
        ref = {}
        n_bins = 10
        for j in range(flat.shape[1]):
            col = flat[:, j]
            col = col[np.isfinite(col)]
            if len(col) < 50 or np.std(col) < 1e-9:
                continue  # constant/empty column — PSI not meaningful
            edges = np.quantile(col, np.linspace(0, 1, n_bins + 1))
            edges[0] = -np.inf
            edges[-1] = np.inf
            counts, _ = np.histogram(col, bins=edges)
            pct = np.clip(counts / max(1, counts.sum()), 1e-4, None)
            ref[int(j)] = {"edges": edges.tolist(), "ref_pct": pct.tolist()}
        self.feature_reference = ref
        self.feature_reference_names = list(self.model_feature_names[:flat.shape[1]])

    def compute_psi(self, recent_features) -> dict:
        """Population Stability Index of recent live features vs training reference.
        PSI < 0.1 stable, 0.1-0.25 moderate, > 0.25 significant drift."""
        ref = self.feature_reference or {}
        if not ref or recent_features is None or len(recent_features) < 50:
            return {"status": "insufficient_data", "max_psi": 0.0, "drifted": [], "n_features_tracked": 0}
        rf = self._select_model_features(np.asarray(recent_features))
        if rf.ndim == 3:
            rf = rf[:, -1, :]
        psi_by_feature = {}
        drifted = []
        names = self.feature_reference_names or self.model_feature_names
        for j, r in ref.items():
            if j >= rf.shape[1]:
                continue
            col = rf[:, j]
            col = col[np.isfinite(col)]
            if len(col) < 20:
                continue
            edges = np.array(r["edges"])
            counts, _ = np.histogram(col, bins=edges)
            live_pct = np.clip(counts / max(1, counts.sum()), 1e-4, None)
            ref_pct = np.array(r["ref_pct"])
            psi = float(np.sum((live_pct - ref_pct) * np.log(live_pct / ref_pct)))
            name = names[j] if j < len(names) else str(j)
            psi_by_feature[name] = round(psi, 4)
            if psi > 0.25:
                drifted.append({"feature": name, "psi": round(psi, 3)})
        if not psi_by_feature:
            return {"status": "insufficient_data", "max_psi": 0.0, "drifted": [], "n_features_tracked": 0}
        max_psi = max(psi_by_feature.values())
        status = "stable" if max_psi < 0.1 else ("moderate" if max_psi < 0.25 else "significant_drift")
        return {
            "status": status,
            "max_psi": round(max_psi, 4),
            "drifted": sorted(drifted, key=lambda x: -x["psi"])[:8],
            "n_features_tracked": len(psi_by_feature),
        }

    def _get_dynamic_weights(self, horizon: int, data_state: dict = None) -> dict:
        """Compute model weights dynamically based on backtest accuracy and real-time live performance, and regime."""
        model_names = ["xgboost", "lightgbm", "catboost", "histgb", "dl", "lr", "rf"]
        weights = {
            model_name: max(0.0, float(self.base_weights.get(model_name, 0.0)))
            for model_name in model_names
        }

        # Regime-specific adjustments
        regime = "RANGE"
        if data_state and isinstance(data_state.get("regime_info"), dict):
            regime = data_state["regime_info"].get("regime", "RANGE")

        accs = {}
        try:
            regime_accs = self.model_accuracies.get(regime, {})
            if isinstance(regime_accs, dict):
                accs = regime_accs.get(horizon) or regime_accs.get(str(horizon)) or {}
            if not accs:
                accs = self.model_accuracies.get(horizon) or self.model_accuracies.get(str(horizon)) or {}
        except Exception:
            accs = {}

        # Mix in live feedback if available
        live_acc = 0.5
        adj = {}
        try:
            by_h = self.learning_adjustments.get("by_horizon", {})
            adj = by_h.get(horizon) or by_h.get(str(horizon)) or {}
        except Exception:
            adj = {}
        if adj and "real_time_accuracy" in adj:
            live_acc = adj["real_time_accuracy"]

        total = sum(float(v) for v in accs.values()) if accs else 0.0
        if total > 0:
            blended = {}
            for model_name in model_names:
                base = self.base_weights.get(model_name, 0.0)
                backtest_component = float(accs.get(model_name, 0.0)) / total
                live_boost = base * (live_acc / 0.5) if live_acc > 0 else base
                if "TREND" in regime and model_name in ["xgboost", "lightgbm", "catboost"]:
                    live_boost *= 1.2
                elif "RANGE" in regime and model_name in ["histgb", "dl"]:
                    live_boost *= 1.2
                blended[model_name] = base * 0.3 + backtest_component * 0.4 + live_boost * 0.3
            weights = blended
        else:
            # No per-model backtest history is currently populated. Still apply a
            # light regime prior and normalize, so optional models do not distort
            # the denominator and live regime weights can still take effect.
            if "TREND" in regime:
                for model_name in ["xgboost", "lightgbm", "catboost"]:
                    weights[model_name] *= 1.1
            elif "RANGE" in regime:
                for model_name in ["histgb", "dl"]:
                    weights[model_name] *= 1.1  # (sgd removed in v6)

        # Learned regime-specific weights (from per-model-per-regime live accuracy).
        # When available these replace the hand-coded TREND/RANGE heuristic above with
        # weights derived from what each model has actually gotten right in this regime.
        rmw = (data_state or {}).get("regime_model_weights") if data_state else None
        if rmw:
            for k in weights:
                if k in rmw and rmw[k] is not None:
                    weights[k] = 0.5 * weights[k] + 0.5 * float(rmw[k])

        # Normalise
        w_total = sum(weights.values())
        if w_total > 0:
            weights = {k: v / w_total for k, v in weights.items()}

        return weights

    def _get_regime_from_state(self, data_state: dict) -> str:
        regime_info = (data_state or {}).get("regime_info") or {}
        r = regime_info.get("regime", "RANGE")
        if r.startswith("TREND"):
            return "TREND"
        if r == "HIGH_VOLATILITY":
            return "VOLATILE"
        return "RANGE"

    def _get_regime_confidence_vector(self, data_state: dict) -> dict:
        """
        Return mapping from our 3 regime keys (TREND/RANGE/VOLATILE) to posterior
        probability. Uses the HMM confidence vector when available.
        """
        ri = (data_state or {}).get("regime_info") or {}
        cv = ri.get("confidence_vector", {})
        if not cv:
            # Fallback: 100% in the hard-routed regime
            return {self._get_regime_from_state(data_state): 1.0}
        # Map the 5 fine-grained labels to our 3 model buckets
        mapped = {"TREND": 0.0, "RANGE": 0.0, "VOLATILE": 0.0}
        for label, prob in cv.items():
            if label.startswith("TRENDING"):
                mapped["TREND"] += prob
            elif label == "HIGH_VOLATILITY":
                mapped["VOLATILE"] += prob
            else:  # RANGE, LOW_VOLATILITY
                mapped["RANGE"] += prob
        total = sum(mapped.values()) + 1e-12
        return {k: v / total for k, v in mapped.items()}

    def _predict_model_probs(self, X_flat: np.ndarray, horizon: int, data_state: dict = None) -> tuple[float, float, float]:
        """
        Get weighted ensemble probability. Uses proportional blending across regime
        experts when no single regime exceeds 0.6 confidence.
        """
        regime_probs = self._get_regime_confidence_vector(data_state)
        max_regime_conf = max(regime_probs.values()) if regime_probs else 1.0
        weights = self._get_dynamic_weights(horizon, data_state)

        if max_regime_conf >= 0.6:
            # High-confidence: hard-route to the dominant regime (original behavior)
            reg = max(regime_probs, key=regime_probs.get)
            return self._predict_from_regime(X_flat, horizon, reg, weights)
        else:
            # Ambiguous: proportional blend across all regime experts
            combined = np.array([0.0, 0.0, 0.0])
            total_weight = 0.0
            for reg, reg_prob in regime_probs.items():
                if reg_prob < 0.05:
                    continue  # skip negligible regimes
                probs = self._predict_from_regime(X_flat, horizon, reg, weights)
                combined += np.array(probs) * reg_prob
                total_weight += reg_prob
            if total_weight > 0:
                combined /= total_weight
            total = combined.sum()
            if total > 0:
                combined /= total
            return float(combined[0]), float(combined[1]), float(combined[2])

    def _predict_from_regime(self, X_flat: np.ndarray, horizon: int, reg: str, weights: dict) -> tuple[float, float, float]:
        """Predict from a specific regime's models."""
        store = self.models_by_regime.get(reg) or {}

        # CRITICAL fallback to GLOBAL. Training frequently fits ONLY the GLOBAL bucket
        # (per-regime buckets like TREND/RANGE/VOLATILE get skipped for too few samples),
        # but inference routes by HMM regime label (TRENDING_UP, LOW_VOLATILITY, ...).
        # Those stores are empty, so without this fallback the lookup returns [0,0,0] and
        # the caller's zero-sum safety net forces NEUTRAL on EVERY prediction — the model
        # never actually gets consulted. Route to GLOBAL whenever the chosen regime has
        # no usable models for this horizon.
        _base_names = ["xgb", "lgb", "cat", "histgb", "dl", "lr", "rf"]
        _has_stacker = bool((self.stackers_by_regime.get(reg) or {}).get(horizon))
        _has_base = any(horizon in (store.get(n) or {}) for n in _base_names)
        if not _has_stacker and not _has_base and reg != "GLOBAL":
            reg = "GLOBAL"
            store = self.models_by_regime.get("GLOBAL") or {}

        # Check if we have a trained stacker
        stacker_info = (self.stackers_by_regime.get(reg) or {}).get(horizon)
        if stacker_info and "model" in stacker_info and "features" in stacker_info:
            meta_features = []
            for name in stacker_info["features"]:
                model_bucket = store.get(name) or {}
                if horizon in model_bucket:
                    try:
                        probs = model_bucket[horizon].predict_proba(X_flat)[0]
                        probs = self._pad_probs(probs, model_bucket[horizon])
                        meta_features.extend(probs)
                    except Exception:
                        meta_features.extend([0.0, 1.0, 0.0]) # Neutral fallback
                else:
                    meta_features.extend([0.0, 1.0, 0.0])
                    
            try:
                X_meta = np.array([meta_features])
                stacker_model = stacker_info["model"]
                probs = stacker_model.predict_proba(X_meta)[0]
                meta_classes = stacker_info.get("classes")
                if meta_classes:
                    mapped = np.zeros(3)
                    local_classes = getattr(stacker_model, "classes_", np.arange(len(probs)))
                    for col_idx, local_c in enumerate(local_classes):
                        if col_idx >= len(probs):
                            continue
                        local_idx = int(local_c)
                        if 0 <= local_idx < len(meta_classes):
                            global_c = int(meta_classes[local_idx])
                            if 0 <= global_c < 3:
                                mapped[global_c] = float(probs[col_idx])
                    probs = mapped if mapped.sum() > 0 else np.array([0.0, 1.0, 0.0])
                
                # Soft-cap DL contribution by blending the stacker output with PyTorch.
                #
                # P1-5 DOUBLE COUNT. This blend predates v6, when the TCN was trained but NOT
                # stacked - capping it afterwards was the only way to bound its influence. v6
                # promoted the TCN to a full stacker seat, so the meta-model now learns how
                # much to trust it from purged OOF data, and this 15% blend was overriding part
                # of that learned decision with a hardcoded constant. The seat was being
                # counted twice.
                #
                # Applied only when the TCN is NOT already a stacker feature, which keeps the
                # cap correct for a bundle whose stacker never received it.
                _dl_in_stacker = "dl" in (stacker_info.get("features") or ())
                dl_weight = (0.15 if HAS_TORCH and not _dl_in_stacker
                             and horizon in (store.get("dl") or {}) else 0.0)
                if _dl_in_stacker:
                    logger.debug(
                        "[PREDICT] h=%s dl is a stacker seat; skipping the 0.15 post-hoc blend "
                        "that would count it twice", horizon)
                if dl_weight > 0:
                    try:
                        dl_probs = store["dl"][horizon].predict_proba(X_flat)[0]
                        dl_probs = self._pad_probs(dl_probs, store["dl"][horizon])
                        probs = (probs * (1.0 - dl_weight)) + (np.array(dl_probs) * dl_weight)
                    except Exception:
                        pass
                
                total = probs.sum()
                if total > 0:
                    probs /= total
                return float(probs[0]), float(probs[1]), float(probs[2])
            except Exception as e:
                logger.error(f"Stacker predict failed: {e}")
                
        # Fallback to hardcoded/dynamic naive weights
        combined = np.array([0.0, 0.0, 0.0])
        for name, weight_key in [
            ("xgb", "xgboost"),
            ("lgb", "lightgbm"),
            ("cat", "catboost"),
            ("histgb", "histgb"),
            ("dl", "dl"),
            ("lr", "lr"),
            ("rf", "rf"),
        ]:
            if horizon in store[name]:
                try:
                    probs = store[name][horizon].predict_proba(X_flat)[0]
                    probs = self._pad_probs(probs, store[name][horizon])
                    
                    w = weights.get(weight_key, 0)
                    if name == "dl" and w > 0.15:
                        w = 0.15  # Cap LSTM/GRU manually
                    
                    combined += np.array(probs) * w
                except Exception:
                    pass
        total = combined.sum()
        if total > 0:
            combined /= total
        return float(combined[0]), float(combined[1]), float(combined[2])

    def _agreement_from_dirs(self, dirs: dict) -> float:
        """Ensemble agreement = fraction of base models sharing the majority direction.
        Derived from already-computed per-model directions, so it costs nothing extra
        (previously this re-ran predict_proba on all 7 base models a second time)."""
        vals = list((dirs or {}).values())
        if len(vals) < 2:
            return 0.0
        counts = {}
        for d in vals:
            counts[d] = counts.get(d, 0) + 1
        return max(counts.values()) / len(vals)

    def _model_directions(self, X_flat: np.ndarray, horizon: int, data_state: dict = None) -> dict:
        """Return each argmax and retain its probability vector from the same inference pass."""
        reg = self._get_regime_from_state(data_state)
        store = self.models_by_regime.get(reg) or {}
        _base_names = ["xgb", "lgb", "cat", "histgb", "dl", "lr", "rf"]
        # SAME GLOBAL fallback as _predict_from_regime: per-regime buckets (esp. VOLATILE)
        # are frequently empty (training skips <1000-sample regimes), but inference routes by
        # HMM label. Without this, _model_directions returns {} → agreement 0 → the meta-trust
        # filter forces NEUTRAL on EVERY volatile-regime prediction (the model goes blind).
        if not any(horizon in (store.get(n) or {}) for n in _base_names) and reg != "GLOBAL":
            store = self.models_by_regime.get("GLOBAL") or {}
        dirs = {}
        model_probabilities = {}
        for name in _base_names:
            if horizon in (store.get(name) or {}):
                try:
                    probs = self._pad_probs(store[name][horizon].predict_proba(X_flat)[0], store[name][horizon])
                    dirs[name] = int(np.argmax(probs))
                    total = float(np.sum(probs))
                    normalized = probs / total if total > 0 else np.array([0.0, 1.0, 0.0])
                    model_probabilities[name] = {
                        "probDown": round(float(normalized[0]), 6),
                        "probNeutral": round(float(normalized[1]), 6),
                        "probUp": round(float(normalized[2]), 6),
                    }
                except Exception:
                    pass
        self.latest_model_probabilities[horizon] = model_probabilities
        return dirs

    def _pairwise_disagreement(self, dirs: dict) -> dict:
        """Pairwise concordance between models (1 = agree, 0 = disagree)."""
        pairs = [
            ("xgb", "lgb"), ("xgb", "cat"), ("xgb", "histgb"), ("xgb", "dl"), ("xgb", "lr"),
            ("lgb", "cat"), ("lgb", "dl"), ("cat", "dl"),
            ("histgb", "lr"), ("dl", "lr"),
        ]
        out = {}
        for a, b in pairs:
            if a in dirs and b in dirs:
                out[f"{a}_vs_{b}"] = int(dirs[a] == dirs[b])
        return out

    def _agreement_threshold(self, model_count: int, min_fraction: float = 0.60) -> float:
        """
        Majority threshold adjusted for the number of available models.
        Optional models such as CatBoost and PyTorch change the denominator
        honestly, so 4/7 and 5/7 are treated differently in the analysis UI.
        """
        if model_count <= 1:
            return 1.0
        majority_votes = model_count // 2 + 1
        return max(min_fraction, majority_votes / model_count)

    def _kronos_direction(self, data_state, h, last_price):
        """Direction implied by the Kronos forecast path at horizon h."""
        fc = data_state.get("kronos_forecasts") or []
        if not fc or last_price <= 0:
            return "NONE"
        idx = min(len(fc), max(1, int(h))) - 1
        try:
            p = float(fc[idx].get("close", last_price)) if isinstance(fc[idx], dict) else float(fc[idx])
        except Exception:
            return "NONE"
        chg = (p - last_price) / last_price
        return "UP" if chg > 0.0003 else ("DOWN" if chg < -0.0003 else "FLAT")

    def _flow_direction(self, data_state):
        """Direction implied by live order flow (CVD + book imbalance + OBI)."""
        of = data_state.get("order_flow", {}) or {}
        score = 0
        cvd = of.get("cvd_change", 0) or 0
        imb = of.get("imbalance", 0) or 0
        obi = of.get("obi_5", 0) or 0
        score += 1 if cvd > 0 else (-1 if cvd < 0 else 0)
        score += 1 if imb > 0.05 else (-1 if imb < -0.05 else 0)
        score += 1 if obi > 0.05 else (-1 if obi < -0.05 else 0)
        return "UP" if score >= 1 else ("DOWN" if score <= -1 else "FLAT")

    def _signal_quality(self, conf, agreement, direction, data_state, seq, exp_move, last_price, h=5):
        """
        Tradeability + CONVICTION. Conviction is the win-rate lever: a directional
        call only becomes 'actionable' when independent sources CONFLUE — the
        ensemble, Kronos's forecast, live order flow and the regime all leaning the
        same way. Trade fewer, higher-quality signals (the Citadel/Jane-St principle),
        rather than acting on every weak lean.
        """
        # Regime score: alignment of the call with the current regime.
        ri = data_state.get("regime_info", {}) or {}
        regime = ri.get("regime", "RANGE")
        rconf = ri.get("confidence", 0.5) or 0.5
        regime_score = 0.5 + 0.5 * rconf
        if direction == "UP" and regime == "TRENDING_UP":
            regime_score = min(1.0, regime_score + 0.1)
        elif direction == "DOWN" and regime == "TRENDING_DOWN":
            regime_score = min(1.0, regime_score + 0.1)
        elif direction == "UP" and regime == "TRENDING_DOWN":
            regime_score *= 0.6   # counter-trend
        elif direction == "DOWN" and regime == "TRENDING_UP":
            regime_score *= 0.6
        if regime == "HIGH_VOLATILITY":
            regime_score *= 0.8
        regime_score = max(0.0, min(1.0, regime_score))

        # Liquidity score from spread (15) and vacuum flag (56).
        try:
            spread_norm = float(seq[-1, 15])
            vacuum = float(seq[-1, 56])
        except Exception:
            spread_norm = 0.5
            vacuum = 0.0
        liquidity_score = max(0.0, min(1.0, 1.0 - spread_norm))
        if vacuum > 0.5:
            liquidity_score *= 0.7

        agreement_eff = agreement if agreement and agreement > 0 else 0.5

        if direction == "NEUTRAL":
            tradeability = round(20.0 * regime_score, 1)
        else:
            prod = max(1e-6, conf * agreement_eff * regime_score * liquidity_score)
            tradeability = round(100.0 * (prod ** 0.25), 1)

        if tradeability >= 85:
            grade = "A+"
        elif tradeability >= 72:
            grade = "A"
        elif tradeability >= 60:
            grade = "B"
        elif tradeability >= 48:
            grade = "C"
        else:
            grade = "D"
        if direction == "NEUTRAL":
            grade = "—"

        # Expected edge: directional edge × expected move, net of round-trip cost.
        cost_usd = last_price * 0.0005 if last_price else 0.0  # ~0.05% round-trip
        dir_edge = (2.0 * conf - 1.0)  # 0.5->0, 0.75->0.5
        expected_edge_usd = (exp_move * dir_edge - cost_usd) if direction != "NEUTRAL" else 0.0
        expected_edge_pct = (expected_edge_usd / last_price * 100.0) if last_price else 0.0

        # ── Confluence: count independent confirmations of the call ──
        kronos_dir = self._kronos_direction(data_state, h, last_price)
        # Gate Kronos's DECISION influence on proven live skill — the same rule the
        # probability nudge already uses (>53% over >=20 verified samples). Without this,
        # the FALLBACK forecaster (measured ~45%, below chance) voted in confluence and
        # could VETO `actionable` via contradicted_by_kronos whenever the ensemble wasn't
        # strong — i.e. a below-chance model was blocking real signals. Display keeps the
        # raw direction (kronosDirection); decisions only see a proven Kronos.
        _kacc = ((data_state.get("kronos_accuracy") or {}).get(h)
                 or (data_state.get("kronos_accuracy") or {}).get(str(h)) or {})
        kronos_proven = (int(_kacc.get("total", 0) or 0) >= 20
                         and float(_kacc.get("accuracy", 0.0) or 0.0) > 0.53)
        kronos_dir_decision = kronos_dir if kronos_proven else "NONE"
        flow_dir = self._flow_direction(data_state)
        confirms = 0
        possible = 0
        detail = {}
        if direction in ("UP", "DOWN"):
            possible += 1
            ok = agreement_eff >= 0.6
            confirms += int(ok)
            detail["models_agree"] = ok
            if kronos_dir_decision in ("UP", "DOWN"):
                possible += 1
                ok = kronos_dir_decision == direction
                confirms += int(ok)
                detail["kronos_agree"] = ok
            if flow_dir in ("UP", "DOWN"):
                possible += 1
                ok = flow_dir == direction
                confirms += int(ok)
                detail["flow_agree"] = ok
            possible += 1
            ok = regime_score >= 0.55
            confirms += int(ok)
            detail["regime_favorable"] = ok
        confluence = (confirms / possible) if possible else 0.0

        # Conviction 0–100: tradeability AND confluence must both be strong.
        if direction in ("UP", "DOWN"):
            conviction = round(0.55 * tradeability + 0.45 * (confluence * 100), 1)
        else:
            conviction = 0.0
            
        ensemble_is_strong = (tradeability >= 72)
        
        contradicted_by_kronos = (kronos_dir_decision in ("UP", "DOWN")
                                  and kronos_dir_decision != direction)
        contradicted_by_flow = (flow_dir in ("UP", "DOWN") and flow_dir != direction and confluence < 0.5)
        
        contradicted = False
        if contradicted_by_kronos and not ensemble_is_strong:
            contradicted = True
        if contradicted_by_flow:
            contradicted = True
            
        actionable = bool(
            direction in ("UP", "DOWN")
            and conviction >= 62
            and confluence >= 0.5
            and not contradicted
        )
        if conviction >= 80:
            conv_grade = "A+"
        elif conviction >= 68:
            conv_grade = "A"
        elif conviction >= 55:
            conv_grade = "B"
        elif conviction >= 45:
            conv_grade = "C"
        else:
            conv_grade = "WATCH"

        return {
            "tradeability": tradeability,
            "signalGrade": grade,
            "regimeScore": round(regime_score, 3),
            "liquidityScore": round(liquidity_score, 3),
            "expectedEdge": round(expected_edge_usd, 2),
            "expectedEdgePct": round(expected_edge_pct, 4),
            "conviction": conviction,
            "convictionGrade": conv_grade,
            "modelConfluenceScore": round(confluence, 3),
            "modelConfluenceDetail": detail,
            "confluence": round(confluence, 3),
            "confluenceDetail": detail,
            "actionable": actionable,
            "kronosDirection": kronos_dir,
            "kronosProven": kronos_proven,
            "flowDirection": flow_dir,
        }

    def _pad_probs(self, probs, model):
        """Ensure probs has exactly 3 FINITE values for DOWN/NEUTRAL/UP."""
        # Sanitize NaN/inf first (sklearn linear models can emit NaN when a row's
        # class probabilities sum to ~0). A NaN here would silently poison the stacker
        # and the fused prediction.
        probs = np.nan_to_num(np.asarray(probs, dtype=np.float64),
                              nan=0.0, posinf=0.0, neginf=0.0)
        if len(probs) >= 3:
            out = probs[:3]
        else:
            out = np.zeros(3)
            classes = getattr(model, "classes_", [0, 1, 2])
            for idx, c in enumerate(classes):
                if int(c) < 3:
                    out[int(c)] = probs[idx]
        # If everything zeroed out, return a safe uniform instead of [0,0,0].
        return out if out.sum() > 0 else np.array([1.0 / 3, 1.0 / 3, 1.0 / 3])

    def predict_base(self, seq_batch: np.ndarray, horizon: int, data_state: dict = None) -> tuple[float, float, float]:
        """Raw prediction from multi-model ensemble. Returns (prob_down, prob_neutral, prob_up)."""
        X_flat = self._flatten_model_features(seq_batch)
        return self._predict_model_probs(X_flat, horizon, data_state)

    def generate_ensemble_prediction(self, h: int, seq: np.ndarray, data_state: dict, acc_cache: dict = None, cascade_data: dict = None) -> dict:
        """
        Generate final prediction with stability controls.
        Combines ML models, applies smoothing + direction lock.
        Heuristic sub-signal fusion has been removed in favor of full ML dependence.
        """
        # ML model prediction
        ml_down, ml_neutral, ml_up = self.predict_base(np.expand_dims(seq, axis=0), h, data_state)

        # Final NaN/inf safety net: a linear model can emit NaN probabilities (the sklearn
        # "invalid value encountered in divide" case). Never let that become a live
        # prediction — fall back to a safe NEUTRAL so the app stays up and just abstains.
        if not all(np.isfinite([ml_down, ml_neutral, ml_up])) or (ml_down + ml_neutral + ml_up) <= 0:
            ml_down, ml_neutral, ml_up = 0.0, 1.0, 0.0

        # Ensemble agreement + pairwise disagreement
        _xflat = self._flatten_model_features(np.expand_dims(seq, axis=0))
        # Compute per-model directions ONCE, then derive agreement from them — this avoids
        # a second full predict_proba pass over all 7 base models every cycle (it was ~half
        # the per-cycle inference cost and a big contributor to the event-loop blocking). (perf)
        model_dirs = self._model_directions(_xflat, h, data_state)
        agreement = self._agreement_from_dirs(model_dirs)
        model_count = len(model_dirs)
        agreement_threshold = self._agreement_threshold(model_count)
        agreement_votes = int(round(agreement * model_count)) if model_count else 0
        pairwise = self._pairwise_disagreement(model_dirs)

        raw_prob_up = ml_up
        raw_prob_down = ml_down
        raw_prob_neutral = ml_neutral

        # ──── Direction de-bias: tempered prior correction ────
        # In an up-drifting training window the model learns an UP bias (it called UP
        # ~6x more than DOWN). Dividing each class prob by its training base-rate
        # (tempered by alpha) removes the systematic prior so the model will commit to
        # DOWN when the data warrants — without flattening genuine asymmetry to 50/50.
        # RETIRED for class-balanced bundles (v5+): the loss-level fix already removes
        # the prior; dividing balanced outputs by the DATA priors would re-bias them
        # the opposite way. alpha=0 keeps the code path for A/B archaeology.
        priors = getattr(self, "class_priors", {}).get(h)
        if priors and len(priors) == 3 and "classbal" not in MODEL_ARCH_VERSION:
            alpha = 0.5
            eps = 1e-3
            pd_, pn_, pu_ = priors  # [DOWN, NEUTRAL, UP] fractions
            raw_prob_down /= max(eps, pd_) ** alpha
            raw_prob_neutral /= max(eps, pn_) ** alpha
            raw_prob_up /= max(eps, pu_) ** alpha
            _tot = raw_prob_up + raw_prob_down + raw_prob_neutral
            if _tot > 0:
                raw_prob_up /= _tot
                raw_prob_down /= _tot
                raw_prob_neutral /= _tot

        # ──── Hierarchical Cascade Bias ────
        cascade_applied = False
        if cascade_data and acc_cache:
            # Pruned to {5,15} 2026-06-21: the only valid hierarchical chain is 15m<-5m.
            # (was {3:1,5:3,7:5,10:7,15:10}; those lowers 1/3/7/10 are gone -> cascade never fired.)
            cascade_map = {15: 5}
            if h in cascade_map:
                h_lower = cascade_map[h]
                lower_pred = cascade_data.get(h_lower)
                lower_acc_stats = acc_cache.get(h_lower)
                
                # Check monitor if we have one attached. (Assume true if not).
                cascade_enabled = True
                if hasattr(self, 'cascade_monitor'):
                    cascade_enabled = self.cascade_monitor.is_active

                if cascade_enabled and lower_pred and lower_acc_stats and lower_pred["direction"] != "NEUTRAL":
                    recent_accuracy = lower_acc_stats.get("accuracy", 0.0)
                    predictions_count = lower_acc_stats.get("total", lower_acc_stats.get("total_predictions", 0))
                    
                    if recent_accuracy >= CASCADE_MIN_ACCURACY and predictions_count >= CASCADE_MIN_PREDICTIONS:
                        cascade_applied = True
                        bias_strength = (recent_accuracy - 0.5) * 0.6
                        if hasattr(self, 'cascade_monitor') and hasattr(self.cascade_monitor, 'avg_impact') and self.cascade_monitor.avg_impact > 0:
                            # Scale bias dynamically. If impact is exactly 0.02, multiplier is 1.0. Cap at 2.5x.
                            impact_multiplier = min(2.5, max(0.2, self.cascade_monitor.avg_impact / 0.02))
                            bias_strength *= impact_multiplier
                        
                        if lower_pred["direction"] == "UP":
                            raw_prob_up += bias_strength
                            raw_prob_down = max(0.01, raw_prob_down - bias_strength * 0.5)
                            raw_prob_neutral = max(0.01, raw_prob_neutral - bias_strength * 0.5)
                        elif lower_pred["direction"] == "DOWN":
                            raw_prob_down += bias_strength
                            raw_prob_up = max(0.01, raw_prob_up - bias_strength * 0.5)
                            raw_prob_neutral = max(0.01, raw_prob_neutral - bias_strength * 0.5)
                        
                        # re-normalize
                        tot = raw_prob_up + raw_prob_down + raw_prob_neutral
                        raw_prob_up /= tot
                        raw_prob_down /= tot
                        raw_prob_neutral /= tot

        # ──── Kronos soft confirmation (accuracy-weighted) ────
        # Use Kronos as a learned signal, not just a confluence vote: nudge the
        # ensemble probabilities toward Kronos's direction ONLY when Kronos has proven
        # live skill at this horizon (>53% over >=20 samples). The nudge scales with
        # Kronos's edge, so an unreliable Kronos has ~zero effect — self-correcting.
        try:
            _kl = (data_state.get("klines") or [{}])[-1].get("close", 0) if data_state.get("klines") else 0
            kacc = (data_state.get("kronos_accuracy") or {}).get(h) or (data_state.get("kronos_accuracy") or {}).get(str(h)) or {}
            k_acc = float(kacc.get("accuracy", 0.0) or 0.0)
            k_n = int(kacc.get("total", 0) or 0)
            kdir = self._kronos_direction(data_state, h, _kl) if _kl else "NONE"
            if k_n >= 20 and k_acc > 0.53 and kdir in ("UP", "DOWN"):
                nudge = min(0.06, (k_acc - 0.5) * 0.3)
                if kdir == "UP":
                    raw_prob_up += nudge
                    raw_prob_down = max(0.01, raw_prob_down - nudge * 0.5)
                    raw_prob_neutral = max(0.01, raw_prob_neutral - nudge * 0.5)
                else:
                    raw_prob_down += nudge
                    raw_prob_up = max(0.01, raw_prob_up - nudge * 0.5)
                    raw_prob_neutral = max(0.01, raw_prob_neutral - nudge * 0.5)
                tot = raw_prob_up + raw_prob_down + raw_prob_neutral
                raw_prob_up /= tot
                raw_prob_down /= tot
                raw_prob_neutral /= tot
        except Exception:
            pass

        # ──── Smoothing ────
        if h not in self.smoothed_probs:
            self.smoothed_probs[h] = {"up": raw_prob_up, "down": raw_prob_down, "neutral": raw_prob_neutral}
        else:
            a = self.smoothing_alpha
            self.smoothed_probs[h]["up"] = self.smoothed_probs[h]["up"] * (1 - a) + raw_prob_up * a
            self.smoothed_probs[h]["down"] = self.smoothed_probs[h]["down"] * (1 - a) + raw_prob_down * a
            self.smoothed_probs[h]["neutral"] = self.smoothed_probs[h]["neutral"] * (1 - a) + raw_prob_neutral * a

        prob_up = self.smoothed_probs[h]["up"]
        prob_down = self.smoothed_probs[h]["down"]
        prob_neutral = self.smoothed_probs[h]["neutral"]

        # ──── Raw direction determination ────
        # Symmetric up-vs-down dead-zone (BTC_DIR_MARGIN). A bare `prob_up > prob_down`
        # fired a directional call on ANY offset, so a ~1-2% systematic UP bias in the
        # calibrated ensemble probs became an 81% UP-lean skew on 5m (probe_direction_tilt
        # 2026-06-23) -- the +0.02 margin only guarded NEUTRAL, never UP-vs-DOWN. Requiring
        # a real margin BETWEEN the two directional classes sends marginal/coin-flip calls to
        # NEUTRAL instead of defaulting to the slightly-higher side -> balanced, honest leans
        # + more abstention (on-brand). Symmetric, so no DOWN favoritism; 15m is already
        # balanced and barely affected. Serving-only; frozen weights untouched; reversible.
        # Default 0.0 == prior behavior; set BTC_DIR_MARGIN (~0.015) to neutralize the tilt,
        # then re-check with probe_direction_tilt before trusting it (measure-before-gate).
        # Per-horizon first (BTC_DIR_MARGIN_5 / _15), then global BTC_DIR_MARGIN, then 0.0.
        # The tilt is 5m-SPECIFIC (15m leans are already balanced, tilt -1.7pt), so we target
        # 5m and leave the balanced + more-tradeable 15m untouched by default.
        _dir_margin = float(os.environ.get(f"BTC_DIR_MARGIN_{h}",
                                           os.environ.get("BTC_DIR_MARGIN", "0.0")) or 0.0)
        if prob_up > prob_down + _dir_margin and prob_up > prob_neutral + 0.02:
            raw_direction = "UP"
            conf = prob_up
        elif prob_down > prob_up + _dir_margin and prob_down > prob_neutral + 0.02:
            raw_direction = "DOWN"
            conf = prob_down
        else:
            raw_direction = "NEUTRAL"
            conf = prob_neutral

        # ──── Direction Lock with Hysteresis ────
        locked_direction = self._apply_direction_lock(h, raw_direction, prob_up, prob_down, prob_neutral)
        direction = locked_direction

        if direction == "UP":
            conf = prob_up
        elif direction == "DOWN":
            conf = prob_down
        else:
            conf = prob_neutral

        # Volatility & Spread features for Meta-Model
        # ewma_vol is Feature 50, vol_accel is Feature 49, spread is Feature 15
        if seq.shape[1] > 50:
            vol_accel = seq[-1, 49]
            ewma_vol = seq[-1, 50]
            spread = seq[-1, 15]

            # Meta-Model Trust Filter
            if agreement < agreement_threshold and (vol_accel > 0.3 or spread > 0.8):
                direction = "NEUTRAL"
                conf = prob_neutral
            
            # Confidence Scaling
            # If EWMA Vol is high (e.g., >0.5), scale down conf.
            if ewma_vol > 0.5:
                # E.g. if ewma_vol is 1.0 (very high), we reduce conf by max 25%
                scaling = max(0.75, 1.0 - (ewma_vol - 0.5) * 0.5)
                conf *= scaling
            elif ewma_vol < 0.1:
                # low vol
                conf *= 0.92

        # ──── Per-regime confidence calibration ────
        # Make confidence honest *within the current regime*: scale by the learned
        # (realized hit rate / stated confidence) factor so conviction is comparable
        # across regimes and overconfident regimes are demoted.
        conf_raw = conf
        regime_now = (data_state.get("regime_info") or {}).get("regime", "UNKNOWN")
        cal_factor = float((data_state.get("regime_calibration") or {}).get(regime_now, 1.0) or 1.0)
        if direction in ("UP", "DOWN") and cal_factor != 1.0:
            conf = max(0.0, min(0.99, conf * cal_factor))

        # Live isotonic confidence recalibration: map raw confidence to the realized
        # hit rate so 0.55 confidence actually means ~55% live accuracy. Shrinks toward
        # the model's own value until ~120 verified samples exist.
        cc = (data_state.get("confidence_calibrators") or {}).get(h)
        if direction in ("UP", "DOWN") and cc and cc.get("iso") is not None:
            try:
                mapped = float(cc["iso"].predict([conf])[0])
                w = min(1.0, float(cc.get("n", 0)) / 120.0)
                conf = max(0.0, min(0.99, conf * (1.0 - w) + mapped * w))
            except Exception:
                pass

        # ──── Signal strength ────
        # (Removed the h==1 noise-threshold bump 2026-06-22: 1m was pruned, so h is never 1 — dead branch.)
        threshold = self.confidence_threshold

        if conf < threshold:
            signal = "NEUTRAL"
        elif direction == "UP":
            signal = "STRONG LONG" if conf >= 0.72 else "LONG"
        elif direction == "DOWN":
            signal = "STRONG SHORT" if conf >= 0.72 else "SHORT"
        else:
            signal = "NEUTRAL"

        # ──── Target price / expected move ────
        last_price = data_state["klines"][-1]["close"] if data_state["klines"] else 0
        atr_val = self._compute_live_atr(data_state["klines"])
        exp_move = atr_val * np.sqrt(h) * 0.4  # ATR-based fallback
        # Prefer the learned magnitude regressor when available (predicts realized
        # move size as a fraction of price), clamped to a sane band around the ATR
        # estimate so a bad regressor output can't produce absurd targets.
        reg = self._get_regime_from_state(data_state)
        move_range = None
        # P1-6. WHERE the range came from, carried to the caller. A conformal band earned on
        # untouched rows and an empirical regime prior are different claims, and the old code
        # emitted both under one key with the distinction living only in a training log line.
        move_range_source = None
        mag_reg = reg if h in self.models_by_regime[reg]["mag"] else "GLOBAL"
        if h in self.models_by_regime[mag_reg]["mag"] and last_price > 0:
            try:
                frac = float(self.models_by_regime[mag_reg]["mag"][h].predict(_xflat)[0])
                pred_move = abs(frac) * last_price
                if exp_move > 0:
                    exp_move = float(np.clip(pred_move, 0.1 * exp_move, 8.0 * exp_move))
                elif pred_move > 0:
                    exp_move = pred_move
            except Exception:
                pass
        if last_price > 0 and h in self.conformal_residuals.get(mag_reg, {}):
            try:
                resids = self.conformal_residuals[mag_reg][h]
                # Older bundles predate the source field; "unknown" is honest, "held-out" is a
                # claim the artifact does not support.
                move_range_source = str(resids.get("source") or "unknown")
                pred_frac = float(self.models_by_regime[mag_reg]["mag"][h].predict(_xflat)[0])
                
                low_frac = pred_frac + resids["q25"]
                median_frac = pred_frac + resids["q50"]
                high_frac = pred_frac + resids["q75"]
                
                low_val = abs(low_frac) * last_price
                median_val = abs(median_frac) * last_price
                high_val = abs(high_frac) * last_price
                
                if median_val > 0:
                    if exp_move > 0:
                        exp_move = float(np.clip(median_val, 0.1 * exp_move, 8.0 * exp_move))
                    else:
                        exp_move = median_val
                
                move_range = {
                    "low": round(float(min(low_val, high_val)), 2),
                    "median": round(float(median_val), 2),
                    "high": round(float(max(low_val, high_val)), 2),
                }
            except Exception:
                pass

        move_size_prior = None
        stat_reg = reg if h in (self.move_size_stats.get(reg) or {}) else "GLOBAL"
        stat = (self.move_size_stats.get(stat_reg) or {}).get(h)
        if last_price > 0 and stat:
            try:
                stat_low = abs(float(stat.get("q25_frac", 0.0))) * last_price
                stat_median = abs(float(stat.get("median_frac", 0.0))) * last_price
                stat_high = abs(float(stat.get("q75_frac", 0.0))) * last_price
                if stat_median > 0:
                    if exp_move > 0:
                        blended = (0.85 * exp_move) + (0.15 * stat_median)
                        exp_move = float(np.clip(blended, 0.1 * exp_move, 8.0 * exp_move))
                    else:
                        exp_move = stat_median

                if stat_low > 0 or stat_median > 0 or stat_high > 0:
                    prior_range = {
                        "low": round(float(min(stat_low or exp_move, stat_high or exp_move)), 2),
                        "median": round(float(stat_median or exp_move), 2),
                        "high": round(float(max(stat_low or exp_move, stat_high or exp_move)), 2),
                    }
                    if move_range:
                        # Global quantile models can be fast but less regime-specific.
                        # Blend in the cheap regime prior so target-error tracking
                        # remains sensitive to TREND/RANGE/VOLATILE differences.
                        move_range = {
                            "low": round((0.80 * float(move_range["low"])) + (0.20 * prior_range["low"]), 2),
                            "median": round((0.80 * float(move_range["median"])) + (0.20 * prior_range["median"]), 2),
                            "high": round((0.80 * float(move_range["high"])) + (0.20 * prior_range["high"]), 2),
                        }
                        move_range_source = f"{move_range_source}+regime_prior"
                    else:
                        # No conformal band was earned for this regime/horizon. The empirical
                        # prior is an honest STATEMENT ABOUT REALISED MOVES, not a claim about
                        # this model's precision - so it is served, and it is labelled.
                        move_range = prior_range
                        move_range_source = "regime_prior_only"
                    move_size_prior = {
                        "regime": stat_reg,
                        "samples": int(stat.get("n", 0)),
                        "median": prior_range["median"],
                    }
            except Exception:
                pass

        # Magnitude uncertainty metrics
        quantile_width_pct = 0.0
        quantile_asymmetry = 0.0
        quantile_spread = 0.0
        if move_range:
            width = move_range["high"] - move_range["low"]
            median = max(float(move_range.get("median", 0.0) or 0.0), 1e-9)
            quantile_width_pct = round(
                width / max(last_price, 1e-9), 6
            )
            quantile_asymmetry = round(
                (move_range["high"] + move_range["low"] - 2 * move_range.get("median", 0)), 2
            )
            quantile_spread = round(width / median, 4)

        if direction == "UP":
            target_price = last_price + exp_move
        elif direction == "DOWN":
            target_price = last_price - exp_move
        else:
            target_price = last_price

        res = {
            "horizon": h,
            "direction": direction,
            "modelRawDirection": raw_direction,
            "rawDirection": raw_direction,
            "lockedDirection": locked_direction,
            "modelFilteredDirection": direction,
            "preServerDirection": direction,
            "signal": signal,
            "confidence": round(conf, 4),
            "confidenceRaw": round(conf_raw, 4),
            "regimeCalibration": round(cal_factor, 3),
            "probUp": round(prob_up, 4),
            "probDown": round(prob_down, 4),
            "probNeutral": round(prob_neutral, 4),
            "targetPrice": round(target_price, 2),
            "expectedMove": round(exp_move, 2),
            "expectedMoveRange": move_range,
            "expectedMoveRangeSource": move_range_source,
            "moveSizePrior": move_size_prior,
            "quantile_width_pct": quantile_width_pct,
            "quantile_asymmetry": quantile_asymmetry,
            "quantileSpread": quantile_spread,
            "agreement": round(agreement, 3),
            "agreementVotes": agreement_votes,
            "agreementModelCount": model_count,
            "agreementThreshold": round(agreement_threshold, 3),
            "pairwise": pairwise,
            "modelDirs": model_dirs,
            "modelProbabilities": dict(self.latest_model_probabilities.get(h, {})),
            **self._signal_quality(conf, agreement, direction, data_state, seq, exp_move, last_price, h),
            "lastPrice": last_price,
            # Neutral band used to grade this prediction at verify time — SAME cost-floored
            # adaptive formula as the training labels, so verification judges the model on
            # the target it was actually trained for (not a hardcoded 0.01%).
            "neutralBand": max(
                float(os.environ.get("BTC_LABEL_COST_FLOOR", "0.0008")),
                min(0.003, (atr_val / last_price * 0.15) if last_price > 0 else 0.0008),
            ),
            "positionSize": int(min(max(0, (conf - 0.5) * 200), 100)) if conf >= self.confidence_threshold else 0,
            "stopLoss": round(last_price - atr_val * 1.5, 2) if direction == "UP" else round(last_price + atr_val * 1.5, 2),
            "takeProfit": round(last_price + atr_val * 2.0, 2) if direction == "UP" else round(last_price - atr_val * 2.0, 2),
            "timestamp": int(time.time() * 1000),
            "modelWeights": self._get_dynamic_weights(h, data_state),
            "cascade_active": cascade_applied,
            "model_bundle_id": self.model_bundle_id,
        }

        if self.enforce_quantile_skip and quantile_spread >= 3.0:
            res["direction"] = "NEUTRAL"
            res["signal"] = "NEUTRAL"
            res["qualityMessage"] = "Avoid: Extreme Variance (Challenger Rule)"
            res["skipReason"] = res["qualityMessage"]
            res["neutralReasonCode"] = "wide_target_range"
            res["neutralReason"] = res["qualityMessage"]
            
        return res

    def _apply_direction_lock(self, h: int, raw_direction: str, p_up: float, p_down: float, p_neutral: float) -> str:
        """
        Apply direction lock + hysteresis to prevent rapid signal flipping.
        
        Rules:
        1. If no lock exists, set the raw direction
        2. If locked, only change if:
           a) The opposing probability exceeds current by hysteresis_margin
           b) We've seen required_opposing_ticks consecutive opposing signals
           c) At least direction_lock_seconds have passed
        """
        now = time.time()

        if h not in self.locked_direction:
            self.locked_direction[h] = raw_direction
            self.direction_lock_time[h] = now
            self.opposing_ticks[h] = 0
            return raw_direction

        current_locked = self.locked_direction[h]
        time_since_lock = now - self.direction_lock_time.get(h, 0)

        # If same direction, reset opposing counter
        if raw_direction == current_locked:
            self.opposing_ticks[h] = 0
            return current_locked

        # If NEUTRAL, allow transition more easily (after cooldown)
        if raw_direction == "NEUTRAL" and current_locked != "NEUTRAL":
            lock_secs = self.horizon_lock_seconds.get(h, 55)
            if time_since_lock >= lock_secs * 0.5:
                self.opposing_ticks[h] += 1
                if self.opposing_ticks[h] >= 2:
                    self.locked_direction[h] = "NEUTRAL"
                    self.direction_lock_time[h] = now
                    self.opposing_ticks[h] = 0
                    return "NEUTRAL"
            return current_locked

        # For directional flip (UP↔DOWN), require strong evidence
        self.opposing_ticks[h] += 1

        # Check all conditions for direction change
        lock_secs = self.horizon_lock_seconds.get(h, 55)
        if time_since_lock >= lock_secs:
            if self.opposing_ticks[h] >= self.required_opposing_ticks:
                # Check hysteresis
                if raw_direction == "UP" and p_up > p_down + self.hysteresis_margin:
                    self.locked_direction[h] = "UP"
                    self.direction_lock_time[h] = now
                    self.opposing_ticks[h] = 0
                    return "UP"
                elif raw_direction == "DOWN" and p_down > p_up + self.hysteresis_margin:
                    self.locked_direction[h] = "DOWN"
                    self.direction_lock_time[h] = now
                    self.opposing_ticks[h] = 0
                    return "DOWN"

        return current_locked

    def _compute_live_atr(self, klines: list[dict], period: int = 14) -> float:
        """Compute current ATR from klines."""
        if not klines or len(klines) < period + 1:
            return klines[-1]["close"] * 0.003 if klines else 100

        closes = np.array([k["close"] for k in klines[-period * 2:]])
        highs = np.array([k["high"] for k in klines[-period * 2:]])
        lows = np.array([k["low"] for k in klines[-period * 2:]])

        atr_arr = atr(highs, lows, closes, period)
        valid = atr_arr[~np.isnan(atr_arr)]
        return float(valid[-1]) if len(valid) > 0 else closes[-1] * 0.003

    def _sigmoid(self, x):
        return 1 / (1 + np.exp(-x * 3))

    def _compute_order_flow_signal(self, of):
        if not of:
            return {"up": 0.5, "down": 0.5}
        score = 0
        if of.get("cvd_change", 0) > 0:
            score += 0.3
        elif of.get("cvd_change", 0) < 0:
            score -= 0.3
        score += of.get("imbalance", 0) * 0.4
        score += (of.get("buy_ratio", 0.5) - 0.5) * 0.6
        score += of.get("imbalance_momentum", 0) * 0.3
        p = self._sigmoid(score)
        return {"up": p, "down": 1 - p}

    def _compute_derivatives_signal(self, dr):
        if not dr:
            return {"up": 0.5, "down": 0.5}
        score = 0
        funding_rate = dr.get("funding_rate") or {}
        fr = funding_rate.get("rate", 0) if isinstance(funding_rate, dict) else float(funding_rate or 0)
        if 0 < fr < 0.001:
            score += 0.15
        elif fr >= 0.001:
            score -= 0.1
        elif fr < -0.0005:
            score += 0.2

        ls = dr.get("long_short_ratio") or []
        if ls and isinstance(ls[0], dict):
            ratio = ls[0].get("ratio", 1.0)
            if ratio > 1.5:
                score -= 0.15
            elif ratio < 0.7:
                score += 0.15
            else:
                score += (ratio - 1) * 0.1

        p = self._sigmoid(score)
        return {"up": p, "down": 1 - p}

    def _compute_sentiment_signal(self, sent):
        fg = (sent or {}).get("fear_greed") if isinstance(sent, dict) else None
        if not isinstance(fg, dict):
            return {"up": 0.5, "down": 0.5}
        v = fg.get("value", 50)
        if v < 20:
            score = 0.3
        elif v < 40:
            score = 0.1
        elif v > 80:
            score = -0.3
        elif v > 60:
            score = -0.1
        else:
            score = 0
        p = self._sigmoid(score)
        return {"up": p, "down": 1 - p}

    def _compute_ta_signal(self, klines):
        if not klines or len(klines) < 50:
            return {"up": 0.5, "down": 0.5}
        closes = np.array([k["close"] for k in klines])
        r = rsi(closes)
        last_rsi = r[-1] if not np.isnan(r[-1]) else 50

        score = 0
        if last_rsi < 30:
            score += 0.25
        elif last_rsi > 70:
            score -= 0.25
        else:
            score += (50 - last_rsi) * 0.005

        # EMA crossover
        ema9 = ema(closes, 9)
        ema21 = ema(closes, 21)
        if not np.isnan(ema9[-1]) and not np.isnan(ema21[-1]):
            if ema9[-1] > ema21[-1]:
                score += 0.1
            else:
                score -= 0.1

        p = self._sigmoid(score)
        return {"up": p, "down": 1 - p}

    def _compute_on_chain_signal(self, of):
        if not of or not of.get("whale_trades"):
            return {"up": 0.5, "down": 0.5}
        wt = of["whale_trades"]
        if not wt:
            return {"up": 0.5, "down": 0.5}
        buys = sum(1 for w in wt if w["is_buy"])
        ratio = buys / len(wt) if len(wt) > 0 else 0.5
        return {"up": ratio, "down": 1 - ratio}

    def get_regime(self, data_state):
        klines = data_state["klines"]
        if not klines or len(klines) < 30:
            return {"regime": "RANGING", "confidence": 0.5}

        closes = np.array([k["close"] for k in klines[-30:]])
        x = np.arange(len(closes))
        slope, _ = np.polyfit(x, closes, 1)
        mean_p = np.mean(closes)
        n_slope = slope / mean_p if mean_p > 0 else 0

        if abs(n_slope) < 0.0001:
            return {"regime": "RANGING", "confidence": 0.6}
        elif n_slope > 0:
            return {"regime": "TRENDING UP", "confidence": min(0.95, 0.55 + n_slope * 5000)}
        else:
            return {"regime": "TRENDING DOWN", "confidence": min(0.95, 0.55 + abs(n_slope) * 5000)}

    def compute_health_score(self, backtest_results, is_connected, model_trained):
        """Compute model health score (A-F)."""
        score = 0
        if model_trained:
            score += 30
        if is_connected:
            score += 20
        if backtest_results:
            accs = []
            for h in self.horizons:
                if h in backtest_results and isinstance(backtest_results[h], dict):
                    accs.append(backtest_results[h].get("accuracy", 0))
            if accs:
                avg_acc = sum(accs) / len(accs)
                score += avg_acc * 40
            sharpe = backtest_results.get("sharpe", 0)
            if isinstance(sharpe, (int, float)) and sharpe > 1:
                score += 10

        if score >= 85:
            return "A"
        if score >= 70:
            return "B"
        if score >= 55:
            return "C"
        if score >= 40:
            return "D"
        return "F"

    def get_model_inventory(self) -> dict:
        """Expose installed/trained model count so agreement math is interpretable."""
        installed = {
            "xgboost": True,
            "lightgbm": HAS_LGBM,
            "catboost": HAS_CATBOOST,
            "histgb": True,
            "dl": HAS_TORCH,
            "lr": True,
            "rf": True,
        }
        trained_by_regime = {}
        for reg, stores in self.models_by_regime.items():
            trained_by_regime[reg] = {
                name: len(stores.get(name, {}))
                for name in ["xgb", "lgb", "cat", "histgb", "dl", "lr", "rf"]
            }
        move_size_prior_count = sum(len(hs) for hs in self.move_size_stats.values())
        return {
            "installed": installed,
            "trained_by_regime": trained_by_regime,
            "available_model_keys": ["xgb", "lgb", "cat", "histgb", "dl", "lr", "rf"],
            "move_size_prior_count": move_size_prior_count,
            "raw_feature_count": NUM_FEATURES,
            "model_feature_count": self.model_num_features,
            "retired_from_model_count": max(0, NUM_FEATURES - self.model_num_features),
            "feature_pruning": self.model_feature_pruning,
            "model_feature_schema_hash": self.model_feature_schema_hash,
            "catboost_available": HAS_CATBOOST,
            "torch_available": HAS_TORCH,
            "deep_model_arch": DL_ARCH if HAS_TORCH else "n/a",
            "lightgbm_available": HAS_LGBM,
            "lightgbm_device": LGB_DEVICE if HAS_LGBM else "n/a",
        }

    def _save_models(self, model_dir=None):
        """Persist trained models atomically. Return True only after the version commits."""
        if not HAS_JOBLIB:
            return False
        target_dir = os.path.abspath(model_dir or self.model_dir)
        try:
            requested_days = _env_int(
                "BTC_MODEL_TRAINING_DAYS",
                _env_int("BTC_BACKFILL_DAYS", _env_int("BTC_HISTORICAL_DAYS", 0)),
            )
            current_identity = current_training_identity(
                requested_days=requested_days,
                feature_names=self.model_feature_names,
                code_paths=[
                    __file__,
                    os.path.join(os.path.dirname(__file__), "features.py"),
                    os.path.join(os.path.dirname(__file__), "model_contract.py"),
                ],
                full_refit=self.full_refit,
            )
            base_identity = copy.deepcopy(
                getattr(self, "training_identity", None) or current_identity
            )
            identity_issues = training_identity_issues(base_identity)
            if identity_issues:
                raise RuntimeError(
                    "training-data identity contract failed before save: "
                    + "; ".join(identity_issues)
                )
            identity_keys = (
                "requested_days",
                "matrix_requested_days",
                "actual_start_ts_ms",
                "actual_end_ts_ms",
                "actual_span_days",
                "row_count",
                "training_data_hash",
                "source_manifest_hash",
                "feature_schema_hash",
                "code_hash",
                "runtime_dependency_hash",
                "matrix_monthly_quality_passed",
            )
            changed = [
                key for key in identity_keys
                if base_identity.get(key) != current_identity.get(key)
            ]
            if changed:
                raise RuntimeError(
                    "training identity changed while models were fitting; refusing "
                    "to stamp or save the stale in-memory bundle (fields: "
                    + ", ".join(changed)
                    + ")"
                )

            os.makedirs(target_dir, exist_ok=True)
            saved_count = 0
            saved_files = []
            for reg in self.regimes:
                reg_dir = os.path.join(target_dir, reg)
                os.makedirs(reg_dir, exist_ok=True)
                for name in ["xgb", "lgb", "cat", "histgb", "dl", "lr", "rf", "mag"]:
                    store = self.models_by_regime[reg][name]
                    for h in self.horizons:
                        if h in store:
                            model_path = os.path.join(reg_dir, f"{name}_{h}.pkl")
                            _atomic_joblib_dump(store[h], model_path)
                            saved_files.append(os.path.relpath(model_path, target_dir))
                            saved_count += 1
            def save_bundle_value(value, name):
                path = os.path.join(target_dir, name)
                _atomic_joblib_dump(value, path)
                saved_files.append(name)

            save_bundle_value(self.model_accuracies, "accuracies.pkl")
            save_bundle_value(self.conformal_residuals, "conformal_residuals.pkl")
            # P0-1. The HMM travels WITH the bundle. The experts were trained on HMM-derived
            # TREND/RANGE/VOLATILE partitions, so a bundle whose regime parameters are missing
            # is served by the heuristic fallback - a train/serve partition mismatch that shows
            # up as nothing at all, because the fallback answers every call happily.
            save_bundle_value(getattr(self, "hmm_state", None), "hmm_state.pkl")
            save_bundle_value(self.feature_reference, "feature_reference.pkl")
            save_bundle_value(self.feature_reference_names, "feature_reference_names.pkl")
            _atomic_joblib_dump(
                {
                    "mode": self.model_feature_pruning,
                    "raw_count": NUM_FEATURES,
                    "model_count": self.model_num_features,
                    "hash": self.model_feature_schema_hash,
                    "names": self.model_feature_names,
                },
                os.path.join(target_dir, "model_feature_schema.pkl"),
            )
            saved_files.append("model_feature_schema.pkl")
            save_bundle_value(self.move_size_stats, "move_size_stats.pkl")
            save_bundle_value(self.class_priors, "class_priors.pkl")
            save_bundle_value(self.stackers_by_regime, "stackers.pkl")
            save_bundle_value(
                getattr(self, "calibration_provenance", {}),
                "calibration_provenance.pkl",
            )
            _atomic_joblib_dump(
                {
                    "model_bundle_id": self.model_bundle_id,
                    "train_split_frac": float(self.train_split_frac),
                    "train_split_idx": int(self.train_split_idx),
                    "full_refit": bool(self.full_refit),
                    "horizons": [int(h) for h in self.horizons],
                    "saved_at": time.time(),
                },
                os.path.join(target_dir, "bundle_metadata.pkl"),
            )
            saved_files.append("bundle_metadata.pkl")
            # Commit the architecture marker last. Launch preflight never sees the new
            # version before every component and metadata artifact has been replaced.
            _atomic_joblib_dump(MODEL_ARCH_VERSION, os.path.join(target_dir, "architecture_version.pkl"))
            saved_files.append("architecture_version.pkl")

            start_ts = base_identity.get("actual_start_ts_ms")
            end_ts = base_identity.get("actual_end_ts_ms")
            split_ts = None
            if (
                not self.full_refit
                and start_ts is not None
                and end_ts is not None
            ):
                split_ts = int(
                    int(start_ts)
                    + (int(end_ts) - int(start_ts)) * float(self.train_split_frac)
                )
            base_identity["split_timestamps"] = {
                "train_start_ts_ms": start_ts,
                "train_end_ts_ms": end_ts if self.full_refit else split_ts,
                "holdout_start_ts_ms": None if self.full_refit else split_ts,
                "holdout_end_ts_ms": None if self.full_refit else end_ts,
                "estimated_from_matrix_span": True,
            }
            base_identity["calibration_timestamps"] = {
                **base_identity["split_timestamps"],
                "source": getattr(self, "calibration_provenance", {}).get(
                    "method", "unknown"
                ),
            }
            write_artifact_manifest(
                target_dir,
                base_identity,
                artifact_type="multi_model_ensemble",
                extra={
                    "artifact_files": saved_files,
                    "model_arch_version": MODEL_ARCH_VERSION,
                    "model_bundle_id": self.model_bundle_id,
                    "horizons": [int(h) for h in self.horizons],
                    "direction_max_samples": DIRECTION_MAX_SAMPLES,
                    "stacker_max_samples": STACKER_MAX_SAMPLES,
                    "tcn_max_samples": TCN_MAX_SAMPLES,
                    "sample_weight_mode": SAMPLE_WEIGHT_MODE,
                },
            )
            logger.info("[MODEL SAVE] Saved %s model components to %s", saved_count, target_dir)
            return True
        except Exception as e:
            logger.error(f"Failed to save models: {e}")
            return False

    def load_models(self) -> bool:
        """Load persisted models from disk. Returns True if successful."""
        model_dir = self.model_dir
        if not HAS_JOBLIB or not os.path.exists(model_dir):
            return False
        try:
            strict_identity = os.environ.get(
                "BTC_STRICT_ARTIFACT_IDENTITY", "1"
            ).strip().lower() not in ("0", "false", "no")
            manifest_path = artifact_manifest_path(model_dir)
            if strict_identity and not manifest_path.exists():
                logger.warning(
                    "[MODEL LOAD] Rejecting legacy bundle without identity manifest: %s",
                    model_dir,
                )
                return False
            expected_identity = current_training_identity(
                requested_days=configured_model_training_days(),
                feature_names=self.model_feature_names,
                code_paths=[
                    __file__,
                    os.path.join(os.path.dirname(__file__), "features.py"),
                    os.path.join(os.path.dirname(__file__), "model_contract.py"),
                ],
            )
            compatible, incompatibilities = artifact_compatibility(
                model_dir, expected_identity, strict=strict_identity
            )
            if not compatible:
                logger.warning(
                    "[MODEL LOAD] Rejecting incompatible bundle %s: %s",
                    model_dir,
                    "; ".join(incompatibilities),
                )
                return False

            loaded_any = False
            loaded_count = 0
            for reg in self.regimes:
                reg_dir = os.path.join(model_dir, reg)
                if not os.path.exists(reg_dir):
                    continue
                for name in ["xgb", "lgb", "cat", "histgb", "dl", "lr", "rf", "mag"]:
                    for h in self.horizons:
                        path = os.path.join(reg_dir, f"{name}_{h}.pkl")
                        if os.path.exists(path):
                            self.models_by_regime[reg][name][h] = _verified_joblib_load(path)
                            loaded_any = True
                            loaded_count += 1

            acc_path = os.path.join(model_dir, "accuracies.pkl")
            if os.path.exists(acc_path):
                self.model_accuracies = _verified_joblib_load(acc_path)
            
            res_path = os.path.join(model_dir, "conformal_residuals.pkl")
            if os.path.exists(res_path):
                try:
                    self.conformal_residuals = _verified_joblib_load(res_path)
                except Exception:
                    self.conformal_residuals = {reg: {} for reg in self.regimes}

            # P0-1. Restored onto the bundle; the SERVER installs it into the live regime
            # engine after load_models(). Absent (an older bundle) stays None rather than
            # becoming an empty dict, so "this bundle carries no HMM" is distinguishable from
            # "this bundle carries an HMM that failed to fit".
            hmm_path = os.path.join(model_dir, "hmm_state.pkl")
            self.hmm_state = None
            if os.path.exists(hmm_path):
                try:
                    self.hmm_state = _verified_joblib_load(hmm_path)
                except Exception:
                    self.hmm_state = None

            ref_path = os.path.join(model_dir, "feature_reference.pkl")
            if os.path.exists(ref_path):
                try:
                    self.feature_reference = _verified_joblib_load(ref_path)
                except Exception:
                    self.feature_reference = {}

            ref_names_path = os.path.join(model_dir, "feature_reference_names.pkl")
            if os.path.exists(ref_names_path):
                try:
                    loaded_names = _verified_joblib_load(ref_names_path)
                    if isinstance(loaded_names, list):
                        self.feature_reference_names = loaded_names
                except Exception:
                    self.feature_reference_names = list(self.model_feature_names)

            priors_path = os.path.join(model_dir, "class_priors.pkl")
            if os.path.exists(priors_path):
                try:
                    self.class_priors = _verified_joblib_load(priors_path)
                except Exception:
                    self.class_priors = {}

            stats_path = os.path.join(model_dir, "move_size_stats.pkl")
            if os.path.exists(stats_path):
                try:
                    self.move_size_stats = _verified_joblib_load(stats_path)
                except Exception:
                    self.move_size_stats = {reg: {} for reg in self.regimes}

            stackers_path = os.path.join(model_dir, "stackers.pkl")
            if os.path.exists(stackers_path):
                try:
                    loaded_stackers = _verified_joblib_load(stackers_path)
                    if isinstance(loaded_stackers, dict):
                        self.stackers_by_regime = {
                            reg: loaded_stackers.get(reg, {})
                            for reg in self.regimes
                        }
                        logger.info(
                            "[MODEL LOAD] Loaded OOF stackers for regimes: %s",
                            {
                                reg: sorted(list((self.stackers_by_regime.get(reg) or {}).keys()))
                                for reg in self.regimes
                                if self.stackers_by_regime.get(reg)
                            },
                        )
                except Exception as e:
                    logger.warning("[MODEL LOAD] Stacker load skipped: %s", e)
                    self.stackers_by_regime = {reg: {} for reg in self.regimes}

            provenance_path = os.path.join(model_dir, "calibration_provenance.pkl")
            if os.path.exists(provenance_path):
                try:
                    loaded_provenance = _verified_joblib_load(provenance_path)
                    if isinstance(loaded_provenance, dict):
                        self.calibration_provenance = loaded_provenance
                except Exception:
                    self.calibration_provenance = {}

            metadata_path = os.path.join(model_dir, "bundle_metadata.pkl")
            if os.path.exists(metadata_path):
                try:
                    metadata = _verified_joblib_load(metadata_path)
                    if isinstance(metadata, dict):
                        self.model_bundle_id = str(
                            metadata.get("model_bundle_id") or self.model_bundle_id
                        )
                        self.train_split_frac = float(
                            metadata.get("train_split_frac", self.train_split_frac)
                        )
                        self.train_split_idx = int(
                            metadata.get("train_split_idx", self.train_split_idx)
                        )
                        self.full_refit = bool(metadata.get("full_refit", False))
                except Exception as exc:
                    logger.warning("[MODEL LOAD] Bundle metadata load skipped: %s", exc)

            if loaded_any:
                version_path = os.path.join(model_dir, "architecture_version.pkl")
                saved_version = None
                if os.path.exists(version_path):
                    try:
                        saved_version = _verified_joblib_load(version_path)
                    except Exception:
                        saved_version = None
                if saved_version != MODEL_ARCH_VERSION:
                    logger.warning(
                        "[MODEL LOAD] Saved model architecture is stale (%s != %s). Retraining once for current ensemble.",
                        saved_version,
                        MODEL_ARCH_VERSION,
                    )
                    for r in self.regimes:
                        for n in ["xgb", "lgb", "cat", "histgb", "dl", "lr", "rf", "mag"]:
                            self.models_by_regime[r][n].clear()
                    self.is_trained = False
                    return False

                # Verify feature dimension compatibility
                dummy_input = np.zeros((1, LOOKBACK * self.model_num_features))
                for reg in self.regimes:
                    for h in self.horizons:
                        if h in self.models_by_regime[reg]["xgb"]:
                            try:
                                self.models_by_regime[reg]["xgb"][h].predict(dummy_input)
                            except Exception as e:
                                logger.warning(f"Saved models are incompatible with current features count: {e}. Purging saved models to trigger retraining.")
                                for r in self.regimes:
                                    for n in ["xgb", "lgb", "cat", "histgb", "dl", "lr", "rf", "mag"]:
                                        self.models_by_regime[r][n].clear()
                                self.is_trained = False
                                return False
                            break
                    else:
                        continue
                    break

                self.is_trained = True
                logger.info(
                    "[MODEL LOAD] Loaded and validated %s model components from %s",
                    loaded_count,
                    model_dir,
                )

            return loaded_any
        except Exception as e:
            logger.error(f"Failed to load models: {e}")
            return False

    def apply_learning_feedback(self, feedback: dict):
        """
        Auto-learning: adjust model parameters based on live verification accuracy.
        Called periodically from the server loop.
        
        Adjustments:
        1. If a horizon's accuracy is degrading → flag for retraining
        2. If UP accuracy << DOWN accuracy → boost DOWN weight, vice versa
        3. Adjust smoothing alpha based on how often direction flips are correct
        4. Adjust confidence threshold based on high-conf accuracy
        """
        retrain_horizons = []
        
        for h, data in feedback.items():
            if not isinstance(data, dict):
                continue
                
            # Flag horizons that need retraining
            if data.get("needs_retrain", False):
                retrain_horizons.append(h)
                logger.info(f"Auto-learning: horizon {h}m flagged for retraining (acc={data['accuracy']:.3f}, trend={data['trend']})")
            
            # Adjust smoothing: if accuracy is low, increase smoothing (more conservative)
            acc = data.get("accuracy", 0.5)
            if acc < 0.4 and data.get("total", 0) >= 10:
                self.smoothing_alpha = min(0.20, self.smoothing_alpha + 0.005)
                logger.info(f"Auto-learning: increased smoothing to {self.smoothing_alpha:.3f} (low accuracy)")
            elif acc > 0.6 and data.get("total", 0) >= 10:
                self.smoothing_alpha = max(0.08, self.smoothing_alpha - 0.003)
                logger.info(f"Auto-learning: decreased smoothing to {self.smoothing_alpha:.3f} (good accuracy)")
            
            # Adjust confidence threshold
            # If high-confidence predictions aren't doing well, raise the bar.
            # (Uses the live verification total, not the per-model accuracy dict,
            # which has no "total" key — previously this branch never fired.)
            if data.get("total", 0) >= 15:
                if acc < 0.42:
                    self.confidence_threshold = min(0.52, self.confidence_threshold + 0.01)
                elif acc > 0.58:
                    self.confidence_threshold = max(0.38, self.confidence_threshold - 0.005)
        
        self.learning_adjustments = {
            "smoothing_alpha": self.smoothing_alpha,
            "confidence_threshold": self.confidence_threshold,
            "retrain_flagged": retrain_horizons,
            "train_count": self.train_count,
            "by_horizon": {
                h: {
                    "real_time_accuracy": float(d.get("accuracy", 0.5)),
                    "total": int(d.get("total", 0) or 0),
                    "trend": d.get("trend", "stable"),
                    "needs_retrain": bool(d.get("needs_retrain", False)),
                }
                for h, d in feedback.items()
                if isinstance(d, dict)
            },
        }
        
        return retrain_horizons
