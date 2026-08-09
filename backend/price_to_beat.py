"""
Price-to-Beat 5m/15m Tracker (Pyth anchor, fixed clock windows)
===============================================================
A self-contained directional game (replaces the broken Polymarket "Value Engine",
which could only see long-dated BTC markets).

Design:
- Uses **fixed clock-aligned windows**, not arbitrary prediction timing:
  5m windows snap to :00, :05, :10 … and 15m windows snap to :00, :15, :30, :45.
- The **price to beat** is the BTC/USD price captured at the window start from whatever
  feed `server.price_to_beat_ticker` passes as `ref_price` — currently **Pyth BTC/USD**
  (matches Polymarket's Chainlink settlement family within a few $), falling back to the
  live Binance price *converted into Pyth units* if Pyth is stale. Resolution at the window
  end is against the SAME feed (same-feed rule — see `update()` and the ticker docstring).
- At window start we record our ensemble's UP/DOWN call + action + Kronos's call; once the
  window closes we check whether the anchor feed finished above or below the price to beat
  and whether our call was right.

Persists to DuckDB (`price_to_beat`); surfaced as
`payload.price_to_beat = {latest, accuracy, recent}`.
"""

import logging
import time
from collections import deque

import numpy as np

import os

import database
import decision_champion
import round_state_panel
from polymarket_paper import calibrated_fair_value as _pm_fv
from polymarket_paper import calibration_loader as _pm_cal_loader
from trade_forecast import live_forecaster as complete_trade_forecaster
from polymarket.model_dynamic_paper import (
    RULE_ID as CHAMPION_DYNAMIC_RULE,
    entry_decision as champion_dynamic_entry,
    exit_decision as champion_dynamic_exit,
)
from open_position_action_recorder import recorder as open_position_action_recorder
from artifact_identity import artifact_matches_current_training
from check_feature_contract import verdict_for as feature_contract_verdict

logger = logging.getLogger(__name__)

# Throttle the per-tick SPECIALIST-head inference (big-move / big-drop / directional / activity).
# Those 4-model ensembles run on slow vol keepers (rv_15m/30m/compression/shock) and barely change
# second-to-second, but recomputing them every tick for every horizon × both trackers was the bulk
# of the price-to-beat CPU and blocked the asyncio event loop (→ frozen live price, laggy Pyth).
# Recompute at most this often PER ROUND; P(hold) (time-sensitive) + champion stay per-tick.
_HEADS_THROTTLE_MS = max(0, int(float(os.environ.get("BTC_PTB_HEADS_THROTTLE_SEC", "4")) * 1000))
_OPEN_ACTION_CAPTURE_MS = max(
    1_000, int(float(os.environ.get("BTC_OPEN_ACTION_CAPTURE_SEC", "5")) * 1_000)
)

# ── A1 / T3 persistence model (P(hold)) — separate head, lazy + crash-safe ──────────
# Calibrated P(side-currently-ahead holds to close | abs_distance, seconds_left, vol,
# horizon). Trained offline by train_persistence_model.py on 1.9M snapshots (test AUC
# 0.747; P(hold)>=0.93 -> 95.3% realized at ~30% coverage). It does NOT touch the frozen
# v6 ensemble or the feature schema; a missing/unreadable file simply disables P(hold)
# (the card falls back to prior behavior). Loaded once, cached at module scope.
_PERSIST_MODEL = None
_PERSIST_MODEL_ERROR = ""
_PERSIST_MODEL_PATH = ""
_PERSIST_MODEL_MTIME = -2.0
_PERSIST_MODEL_CHECKED = 0.0
_PERSIST_MODEL_HASH = None


# ── FROZEN-ARTIFACT IMMUTABILITY (PR1, 2026-07-26) ────────────────────────────────────────────
# BTC_FREEZE_MODEL=1 exists so an evidence run measures ONE bundle. But every loader below
# hot-reloads on mtime, so any other process replacing a .pkl silently swapped the model
# mid-run -- and the live evidence would then describe two different models with no record of
# the change. Under freeze, the FIRST successfully loaded artifact is authoritative for the
# life of the process; a later change is refused and alerted, never adopted.
_FROZEN = os.environ.get("BTC_FREEZE_MODEL", "1") != "0"
_FROZEN_LOADED = {}          # path -> (mtime, sha256[:16]) of the artifact this process serves
_FROZEN_ALERTED = set()
_IDENTITY_ALERTED = set()
_HEAD_IDENTITY_CACHE = {}


def _artifact_sha(path):
    import hashlib
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()[:16]
    except Exception:
        return "?"


def _active_head_identity() -> dict:
    """Exact artifact identity for every specialist head currently serving this process."""
    data_dir = os.environ.get("BTC_DATA_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "data"
    )
    model_dir = os.path.join(data_dir, "saved_models")
    candidates = {
        "p_hold": (_PERSIST_MODEL, "persistence_model.pkl"),
        "big_move": (_BIGMOVE_MODEL, "bigmove_keeper_model.pkl"),
        "big_drop": (_BIGDROP_MODEL, "bigdrop_keeper_model.pkl"),
        "directional": (_DIRECTIONAL_MODEL, "directional_keeper_model.pkl"),
        "activity": (_ACTIVITY_MODEL, "activity_keeper_model.pkl"),
        "path": (_PATH_FORECASTER, "path_forecaster.pkl"),
        "signed_quantile": (_SIGNED_QMODEL, "signed_quantile_model.pkl"),
    }
    result = {}
    for label, (bundle, filename) in candidates.items():
        if not isinstance(bundle, dict):
            continue
        path = os.path.join(model_dir, filename)
        try:
            mtime = os.path.getmtime(path)
            cache_key = (path, mtime)
            identity = _HEAD_IDENTITY_CACHE.get(cache_key)
            if identity is None:
                from verified_io import file_sha256
                identity = {
                    "sha256": file_sha256(path),
                    "version": str(bundle.get("version") or bundle.get("model_version") or ""),
                    "label_basis": str(bundle.get("label_basis") or ""),
                }
                _HEAD_IDENTITY_CACHE[cache_key] = identity
            result[label] = dict(identity)
        except Exception as exc:
            result[label] = {"error": str(exc)}

    round_status = round_state_panel.status()
    if round_status.get("loaded") and round_status.get("artifact"):
        path = str(round_status["artifact"])
        try:
            mtime = os.path.getmtime(path)
            cache_key = (path, mtime)
            identity = _HEAD_IDENTITY_CACHE.get(cache_key)
            if identity is None:
                from verified_io import file_sha256
                identity = {
                    "sha256": file_sha256(path),
                    "version": str(round_status.get("version") or ""),
                    "label_basis": "",
                }
                _HEAD_IDENTITY_CACHE[cache_key] = identity
            result["round_state"] = dict(identity)
        except Exception as exc:
            result["round_state"] = {"error": str(exc)}
    return result


def _freeze_blocks_reload(path, mtime, label):
    """True when a frozen process must REFUSE this artifact change.

    First load under freeze is recorded (path, mtime, sha) and permitted. Any later mtime for
    the same path is refused and alerted ONCE -- loudly, because a silent swap invalidates every
    measurement taken after it.
    """
    if not _FROZEN:
        return False
    prev = _FROZEN_LOADED.get(path)
    if prev is None:
        _FROZEN_LOADED[path] = (mtime, _artifact_sha(path))
        logger.info("[frozen] %s pinned: sha=%s mtime=%.0f", label,
                    _FROZEN_LOADED[path][1], mtime)
        return False
    if mtime == prev[0]:
        return False
    if path not in _FROZEN_ALERTED:
        _FROZEN_ALERTED.add(path)
        logger.error(
            "[frozen] ARTIFACT CHANGED WHILE FROZEN - refusing to load: %s | "
            "serving sha=%s (mtime %.0f), on disk sha=%s (mtime %.0f) | "
            "evidence from this process describes the SERVING artifact | "
            "restart deliberately to adopt the new one, or unset BTC_FREEZE_MODEL",
            label, prev[1], prev[0], _artifact_sha(path), mtime)
    return True


def _identity_blocks_load(path, label):
    """Fail closed when an artifact was trained on different or unidentified data."""
    contract_code, contract_detail = feature_contract_verdict(path)
    if contract_code:
        alert_key = (path, contract_code, contract_detail)
        if alert_key not in _IDENTITY_ALERTED:
            _IDENTITY_ALERTED.add(alert_key)
            logger.error(
                "[feature contract] refusing %s: %s (%s)",
                label,
                contract_code,
                contract_detail,
            )
        return True
    ok, reasons = artifact_matches_current_training(path)
    if ok:
        return False
    alert_key = (path, tuple(reasons))
    if alert_key not in _IDENTITY_ALERTED:
        _IDENTITY_ALERTED.add(alert_key)
        logger.error(
            "[artifact identity] refusing %s: %s",
            label,
            "; ".join(reasons),
        )
    return True


def _load_persistence_model():
    """Return the persistence-model dict {clf, iso, features, ...} or None. HOT-RELOADS when the
    .pkl changes (mtime, throttled 30s) so a nightly refit goes live WITHOUT a restart; keeps the
    PRIOR model on any load failure (crash-safe). P(hold) silently disabled if absent."""
    global _PERSIST_MODEL, _PERSIST_MODEL_ERROR, _PERSIST_MODEL_PATH, _PERSIST_MODEL_MTIME, _PERSIST_MODEL_CHECKED, _PERSIST_MODEL_HASH
    import os
    import time
    now = time.time()
    if _PERSIST_MODEL_CHECKED and (now - _PERSIST_MODEL_CHECKED) < 30.0:
        return _PERSIST_MODEL                       # throttle: re-check the file at most every 30s
    _PERSIST_MODEL_CHECKED = now
    try:
        data_dir = os.environ.get("BTC_DATA_DIR") or os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "data")
        path = os.path.join(data_dir, "saved_models", "persistence_model.pkl")
        _PERSIST_MODEL_PATH = path
        mt = os.path.getmtime(path) if os.path.exists(path) else -1.0
        if mt == _PERSIST_MODEL_MTIME:
            return _PERSIST_MODEL                    # unchanged (incl. still-missing) — no work
        if _freeze_blocks_reload(path, mt, "persistence_model (P(hold))"):
            return _PERSIST_MODEL                    # frozen: keep serving the pinned artifact
        if mt < 0:
            _PERSIST_MODEL_ERROR = "missing"; _PERSIST_MODEL_MTIME = mt
            _PERSIST_MODEL_HASH = None
            if _PERSIST_MODEL is None:
                logger.info(f"A1 persistence model absent at {path} — P(hold) disabled.")
            return _PERSIST_MODEL
        if _identity_blocks_load(path, "persistence_model (P(hold))"):
            _PERSIST_MODEL_ERROR = "artifact identity mismatch"
            _PERSIST_MODEL_MTIME = mt
            _PERSIST_MODEL_HASH = None
            return _PERSIST_MODEL
        from verified_io import file_sha256
        hash_before = file_sha256(path)
        loaded = _verified_load(path)                   # only reaches here when the file CHANGED
        hash_after = file_sha256(path)
        if hash_before != hash_after:
            raise RuntimeError("persistence artifact changed while it was being loaded")
        _PERSIST_MODEL = loaded; _PERSIST_MODEL_MTIME = mt; _PERSIST_MODEL_ERROR = ""
        _PERSIST_MODEL_HASH = hash_after
        logger.info("A1 persistence model (re)loaded (P(hold) live): test_auc="
                    f"{_PERSIST_MODEL.get('test_auc')}, features={_PERSIST_MODEL.get('features')}")
    except Exception as e:
        _PERSIST_MODEL_ERROR = str(e)               # keep the PRIOR model — never break serving
        logger.warning(f"A1 persistence model reload failed — keeping prior: {e}")
    return _PERSIST_MODEL


_SIGNED_QMODEL = None
_SIGNED_QMODEL_MTIME = -2.0
_SIGNED_QMODEL_CHECKED = 0.0


_BIGMOVE_MODEL = None
_BIGMOVE_CHECKED = False


def _load_bigmove_keeper_model():
    """P(big_move) keeper head {pipe, features, tiers, auc} or None — load-once, crash-safe.
    Served on the 4 parity-proven keepers only, so it never needs new live features."""
    global _BIGMOVE_MODEL, _BIGMOVE_CHECKED
    if _BIGMOVE_CHECKED:
        return _BIGMOVE_MODEL
    # PINNED ON SUCCESS, not on ATTEMPT. Setting this before the load meant one
    # transient failure - file not yet written by the trainer, a momentary lock, a
    # partial write - permanently disabled this head for the life of the process, and
    # the only symptom was a head that silently stopped contributing. The flag now
    # moves only after a verified load, so a later attempt can still succeed.
    try:
        import os
        data_dir = os.environ.get("BTC_DATA_DIR") or os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "data")
        path = os.path.join(data_dir, "saved_models", "bigmove_keeper_model.pkl")
        if os.path.exists(path) and not _identity_blocks_load(path, "bigmove_keeper"):
            _BIGMOVE_MODEL = _verified_load(path)
            _BIGMOVE_CHECKED = True
            logger.info(f"Big-move keeper head loaded: AUC={_BIGMOVE_MODEL.get('auc'):.3f}, "
                        f"features={_BIGMOVE_MODEL.get('features')}")
    except Exception as _e:
        logger.debug(f"big-move keeper model load skipped: {_e}")
    return _BIGMOVE_MODEL


_BIGDROP_MODEL = None
_BIGDROP_CHECKED = False


def _load_bigdrop_keeper_model():
    """Big-Drop-Risk keeper head {pipe, features, tiers, auc, top5_prec} or None — load-once, crash-safe."""
    global _BIGDROP_MODEL, _BIGDROP_CHECKED
    if _BIGDROP_CHECKED:
        return _BIGDROP_MODEL
    # PINNED ON SUCCESS, not on ATTEMPT. Setting this before the load meant one
    # transient failure - file not yet written by the trainer, a momentary lock, a
    # partial write - permanently disabled this head for the life of the process, and
    # the only symptom was a head that silently stopped contributing. The flag now
    # moves only after a verified load, so a later attempt can still succeed.
    try:
        import os
        data_dir = os.environ.get("BTC_DATA_DIR") or os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "data")
        path = os.path.join(data_dir, "saved_models", "bigdrop_keeper_model.pkl")
        if os.path.exists(path) and not _identity_blocks_load(path, "bigdrop_keeper"):
            _BIGDROP_MODEL = _verified_load(path)
            _BIGDROP_CHECKED = True
            logger.info(f"Big-drop keeper head loaded: AUC={_BIGDROP_MODEL.get('auc'):.3f}, "
                        f"top5%={_BIGDROP_MODEL.get('top5_prec'):.2f}")
    except Exception as _e:
        logger.debug(f"big-drop keeper model load skipped: {_e}")
    return _BIGDROP_MODEL


_DIRECTIONAL_MODEL = None
_DIRECTIONAL_CHECKED = False


def _load_directional_keeper_model():
    """Directional big-up/down keeper heads or None. Confirmation only."""
    global _DIRECTIONAL_MODEL, _DIRECTIONAL_CHECKED
    if _DIRECTIONAL_CHECKED:
        return _DIRECTIONAL_MODEL
    # PINNED ON SUCCESS, not on ATTEMPT. Setting this before the load meant one
    # transient failure - file not yet written by the trainer, a momentary lock, a
    # partial write - permanently disabled this head for the life of the process, and
    # the only symptom was a head that silently stopped contributing. The flag now
    # moves only after a verified load, so a later attempt can still succeed.
    try:
        import os
        data_dir = os.environ.get("BTC_DATA_DIR") or os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "data")
        path = os.path.join(data_dir, "saved_models", "directional_keeper_model.pkl")
        if os.path.exists(path) and not _identity_blocks_load(path, "directional_keeper"):
            _DIRECTIONAL_MODEL = _verified_load(path)
            _DIRECTIONAL_CHECKED = True
            logger.info("Directional keeper heads loaded: %s", list((_DIRECTIONAL_MODEL.get("models") or {}).keys()))
    except Exception as _e:
        logger.debug(f"directional keeper model load skipped: {_e}")
    return _DIRECTIONAL_MODEL


_ACTIVITY_MODEL = None
_ACTIVITY_CHECKED = False


def _load_activity_keeper_model():
    """Activity/range keeper head or None. Proxy for participation, not true future volume."""
    global _ACTIVITY_MODEL, _ACTIVITY_CHECKED
    if _ACTIVITY_CHECKED:
        return _ACTIVITY_MODEL
    # PINNED ON SUCCESS, not on ATTEMPT. Setting this before the load meant one
    # transient failure - file not yet written by the trainer, a momentary lock, a
    # partial write - permanently disabled this head for the life of the process, and
    # the only symptom was a head that silently stopped contributing. The flag now
    # moves only after a verified load, so a later attempt can still succeed.
    try:
        import os
        data_dir = os.environ.get("BTC_DATA_DIR") or os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "data")
        path = os.path.join(data_dir, "saved_models", "activity_keeper_model.pkl")
        if os.path.exists(path) and not _identity_blocks_load(path, "activity_keeper"):
            _ACTIVITY_MODEL = _verified_load(path)
            _ACTIVITY_CHECKED = True
            logger.info(f"Activity keeper head loaded: AUC={_ACTIVITY_MODEL.get('auc'):.3f}")
    except Exception as _e:
        logger.debug(f"activity keeper model load skipped: {_e}")
    return _ACTIVITY_MODEL


_PATH_FORECASTER = None
_PATH_MODEL_MTIME = -2.0
_PATH_MODEL_CHECKED = 0.0
_PATH_MODEL_ERROR = ""
_PATH_MODEL_VERSION = "2026-06-30-path-v3-usd-early"   # informational; the loader gates on threshold_units, not exact version


def _load_path_forecaster():
    """Intra-window PATH head (Layer 2): quantile high/low band + conformal + touch odds, or None.
    Served on the SAME parity-proven keepers as big-move (rv_15m/rv_30m/rv_60m/compression_ratio/
    shock_magnitude). A SEPARATE head -- never merged into the frozen direction ensemble."""
    global _PATH_FORECASTER, _PATH_MODEL_MTIME, _PATH_MODEL_CHECKED, _PATH_MODEL_ERROR
    import time
    now = time.time()
    if _PATH_MODEL_CHECKED and now - _PATH_MODEL_CHECKED < 30.0:
        return _PATH_FORECASTER
    _PATH_MODEL_CHECKED = now
    try:
        import os
        data_dir = os.environ.get("BTC_DATA_DIR") or os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "data")
        path = os.path.join(data_dir, "saved_models", "path_forecaster.pkl")
        mt = os.path.getmtime(path) if os.path.exists(path) else -1.0
        if mt == _PATH_MODEL_MTIME:
            return _PATH_FORECASTER
        if _freeze_blocks_reload(path, mt, "path_forecaster"):
            return _PATH_FORECASTER                  # frozen: keep the pinned artifact
        if mt < 0:
            _PATH_MODEL_MTIME = mt
            _PATH_MODEL_ERROR = "missing"
            return _PATH_FORECASTER
        if _identity_blocks_load(path, "path_forecaster"):
            _PATH_MODEL_MTIME = mt
            _PATH_MODEL_ERROR = "artifact identity mismatch"
            return _PATH_FORECASTER
        loaded = _verified_load(path)
        if loaded.get("threshold_units") != "usd":   # accept any usd-barriers bundle (v2/v3); units is the real safety guard
            _PATH_MODEL_MTIME = mt
            _PATH_MODEL_ERROR = "incompatible path-forecaster schema"
            logger.warning("Path forecaster ignored: saved schema is stale; run train_path_forecaster.py.")
            return _PATH_FORECASTER
        _PATH_FORECASTER = loaded
        _PATH_MODEL_MTIME = mt
        _PATH_MODEL_ERROR = ""
        _m5 = (_PATH_FORECASTER.get("horizons") or {}).get(5, {}).get("metrics", {})
        logger.info(f"Path forecaster head (re)loaded: features={_PATH_FORECASTER.get('features')}, "
                    f"5m touch_auc={_m5.get('touch_auc')}")
    except Exception as _e:
        _PATH_MODEL_ERROR = str(_e)
        logger.warning(f"Path forecaster reload failed; keeping prior model: {_e}")
    return _PATH_FORECASTER


_FADE_MODEL = None
_FADE_MTIME = -2.0
_FADE_CHECKED = 0.0
_FADE_ERROR = ""

# Reversal WINDOW favorability (from backtest_reversal_strategy.py --export-windows): per-CEST-hour + per-weekday
# fade-reach-anchor rate / base. SOFT, informational prior surfaced on the card ("strong/weak reversal window"),
# NOT a hard gate -- the time-of-day edge is real but modest and partly selection bias.
_WINFAV = None
_WINFAV_MTIME = -2.0
_WINFAV_CHECKED = 0.0

_PM_QUOTES = None
_PM_QUOTES_MTIME = -2.0
_PM_QUOTES_CHECKED = 0.0


def _load_fade_model():
    """FADE ENTRY head: P(this touch reverts to the anchor TP before the 2x stop) -- the HONEST
    causal strict-label model (train_fade_model.py v5). Same keepers + timing + pre-touch context.
    Used to grade a LIVE early touch; None until trained. mtime-reload, crash-safe."""
    global _FADE_MODEL, _FADE_MTIME, _FADE_CHECKED, _FADE_ERROR
    import time
    now = time.time()
    if _FADE_CHECKED and now - _FADE_CHECKED < 30.0:
        return _FADE_MODEL
    _FADE_CHECKED = now
    try:
        import os
        data_dir = os.environ.get("BTC_DATA_DIR") or os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "data")
        path = os.path.join(data_dir, "saved_models", "fade_model.pkl")
        mt = os.path.getmtime(path) if os.path.exists(path) else -1.0
        if mt == _FADE_MTIME:
            return _FADE_MODEL
        if _freeze_blocks_reload(path, mt, "fade_model"):
            return _FADE_MODEL                  # frozen: keep the pinned artifact
        _FADE_MTIME = mt
        if mt < 0:
            _FADE_ERROR = "missing"
            return _FADE_MODEL
        if _identity_blocks_load(path, "fade_model"):
            _FADE_ERROR = "artifact identity mismatch"
            return _FADE_MODEL
        loaded = _verified_load(path)
        from train_fade_model import HEAD_VERSION as expected_version
        if not (loaded.get("features") and loaded.get("horizons")):
            _FADE_MODEL = None
            _FADE_ERROR = "incompatible fade-model schema"
            return _FADE_MODEL
        if (loaded.get("version") != expected_version
                or not loaded.get("causal_touch_context")
                or not loaded.get("ambiguous_touch_bars_excluded")
                or loaded.get("live_supported") is not True):
            _FADE_MODEL = None
            _FADE_ERROR = (f"unsafe/stale fade model {loaded.get('version')}; "
                           f"expected {expected_version}")
            logger.warning(_FADE_ERROR + " -- live fade entry disabled until a challenger passes its frozen gate.")
            return _FADE_MODEL
        _FADE_MODEL = loaded
        _FADE_ERROR = ""
        logger.info(f"Fade entry head (re)loaded: version={loaded.get('version')}, features={loaded.get('features')}")
    except Exception as _e:
        _FADE_ERROR = str(_e)
        logger.warning(f"Fade model reload failed; keeping prior model: {_e}")
    return _FADE_MODEL


def _load_window_favorability():
    """Load the reversal window-favorability table (mtime-reload, crash-safe). None until exported."""
    global _WINFAV, _WINFAV_MTIME, _WINFAV_CHECKED
    import time
    now = time.time()
    if _WINFAV_CHECKED and now - _WINFAV_CHECKED < 300.0:
        return _WINFAV
    _WINFAV_CHECKED = now
    try:
        import json
        import os
        data_dir = os.environ.get("BTC_DATA_DIR") or os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "data")
        path = os.path.join(data_dir, "reversal_window_favorability.json")
        mt = os.path.getmtime(path) if os.path.exists(path) else -1.0
        if mt == _WINFAV_MTIME:
            return _WINFAV
        if _freeze_blocks_reload(path, mt, "window_favorability"):
            return _WINFAV                  # frozen: keep the pinned artifact
        _WINFAV_MTIME = mt
        if mt < 0:
            return _WINFAV
        with open(path, "r", encoding="utf-8") as f:
            _WINFAV = json.load(f)
    except Exception as _e:
        logger.debug(f"window-favorability load skipped: {_e}")
    return _WINFAV


def _window_quality(horizon, window_start_ms):
    """Soft Europe/Warsaw local-hour x weekday reversal prior for display only.

    Returns a
    display dict or None. score>1 = historically above-average reversal window. NOT a hard gate."""
    fav = _load_window_favorability()
    if not fav or not window_start_ms:
        return None
    hz = (fav.get("horizons") or {}).get(str(int(horizon)))
    if not hz:
        return None
    try:
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo
        dt = datetime.fromtimestamp(int(window_start_ms) / 1000, tz=timezone.utc).astimezone(
            ZoneInfo("Europe/Warsaw"))
        hx = float((hz.get("hour") or {}).get(str(dt.hour), 1.0))
        dx = float((hz.get("dow") or {}).get(str(dt.weekday()), 1.0))
        score = hx * 0.7 + dx * 0.3          # hour is the stronger effect; weekday a lighter tilt
        label = "STRONG" if score >= 1.10 else "WEAK" if score <= 0.90 else "OK"
        tier = "Above-average" if score >= 1.10 else "Below-average" if score <= 0.90 else "Average"
        return {"score": round(score, 2), "hour_x": round(hx, 2), "dow_x": round(dx, 2), "label": label,
                "note": (f"{tier} reversal window ({dt.hour:02d}h Europe/Warsaw). "
                         f"Soft prior — be more/less selective; not a hard gate.")}
    except Exception:
        return None


def _market_quote_for_round(round_data, now_ms):
    """Return a fresh executable quote from the recorder's atomic JSON bridge.

    Matching fails closed on horizon, exact clock anchor, and quote age. The JSON bridge
    avoids sharing the recorder's DuckDB writer lock with the backend process.
    """
    global _PM_QUOTES, _PM_QUOTES_MTIME, _PM_QUOTES_CHECKED
    import json
    import time
    now = time.time()
    if not _PM_QUOTES_CHECKED or now - _PM_QUOTES_CHECKED >= 0.5:
        _PM_QUOTES_CHECKED = now
        try:
            data_dir = os.environ.get("BTC_DATA_DIR") or os.path.join(
                os.path.dirname(os.path.dirname(__file__)), "data")
            path = os.path.join(data_dir, "pm_live_quotes.json")
            mt = os.path.getmtime(path) if os.path.exists(path) else -1.0
            if mt != _PM_QUOTES_MTIME:
                _PM_QUOTES_MTIME = mt
                if mt >= 0:
                    with open(path, "r", encoding="utf-8") as f:
                        _PM_QUOTES = json.load(f)
        except Exception:
            _PM_QUOTES = None
    payload = _PM_QUOTES or {}
    quote = (payload.get("markets") or {}).get(str(int(round_data.get("horizon") or 0)))
    if not quote:
        return None
    try:
        expected_anchor = int(round_data.get("window_start") or 0) // 1000
        quote_ts = float(quote.get("ts") or 0.0)
        if int(quote.get("anchor_ts") or 0) != expected_anchor:
            return None
        if abs(float(now_ms) / 1000.0 - quote_ts) > 5.0:
            return None
        position = round_data.get("current_position")
        if position not in ("UP", "DOWN"):
            return None
        prefix = "up" if position == "UP" else "down"
        ask = float(quote[f"{prefix}_ask"])
        bid = float(quote[f"{prefix}_bid"])
        if not (0.0 <= bid <= ask < 1.0) or ask <= 0.0:
            return None
        spread = float(quote.get(f"{prefix}_spread") or ask - bid)
        if spread < 0.0:
            return None
        return {
            "slug": quote.get("slug"), "ts": quote_ts, "side": position,
            "ask": ask, "bid": bid,
            "spread": spread,
            "depth": float(quote.get(f"{prefix}_top_ask_size") or 0.0),
            "fees_enabled": quote.get("fees_enabled") is not False,
            "fee_rate": float(quote.get("fee_rate") or 0.07),
            "age_seconds": round(max(0.0, float(now_ms) / 1000.0 - quote_ts), 3),
        }
    except Exception:
        return None


# Read from the strategy module, never restated. The paper table and the ledger must name the
# same thing, and the name itself now says the evidence status: this is a frozen forward
# BENCHMARK, not the "candidate" it was called while quoting a result that is now retracted.
_PM_STRATEGY_ID = _pm_fv.STRATEGY_ID

def _seconds_to_ms(seconds) -> int:
    """Bridge seconds -> ledger milliseconds, ROUNDED rather than truncated.

    int(1025.321 * 1000.0) is 1025320, not 1025321: the product lands just below the integer
    and int() floors it. One millisecond is harmless on its own, but these values are the
    causal timestamps a decision row is judged by, and a conversion that is silently wrong in
    one direction is not something to leave in the evidence path."""
    return int(round(float(seconds or 0.0) * 1000.0))


_PM_CAL_CACHE: dict[int, object] = {}
_PM_CAL_LOGGED: set[int] = set()


def _pm_calibration(horizon: int):
    """Cached P(hold) calibrator for PM_CALIBRATED_FAIR_VALUE_FORWARD_BENCHMARK_V1, or None.

    Cached because the file does not change while the process runs and this is called on a
    per-round hot path. The unavailability reason is logged ONCE per horizon rather than every
    round: a strategy that is disabled must be visible, but it must not flood the log."""
    if horizon in _PM_CAL_CACHE:
        return _PM_CAL_CACHE[horizon]
    calibration = None
    try:
        calibration = _pm_cal_loader.load(int(horizon))
    except Exception as exc:                      # never let a loader fault break the round loop
        logger.debug(f"P(hold) calibrator load failed for {horizon}m: {exc}")
    _PM_CAL_CACHE[horizon] = calibration
    if calibration is None and horizon not in _PM_CAL_LOGGED:
        _PM_CAL_LOGGED.add(horizon)
        logger.info(
            f"{_PM_STRATEGY_ID} disabled for {horizon}m: no deployable P(hold) "
            "calibrator. The challenger refuses to deploy one while the source artifacts fail "
            "identity enforcement (SOURCE_MODEL_REQUIRES_RETRAINING). A retrain that writes "
            "manifests is what enables this strategy."
        )
    return calibration


# ── DECISION PROVENANCE ────────────────────────────────────────────────────────────────────
# A causally-timestamped decision still proves nothing if it cannot say WHAT produced its
# number. These four hashes are what let a later run re-derive a stored decision without
# querying any table that did not exist at the decision instant - which is the whole point of
# the ledger, and was the one thing the first wiring left as None.
_PM_CAL_HASH_CACHE: dict[int, str] = {}


def _phold_artifact_hash() -> str | None:
    """Identity of the model that produced p_leader_holds, or None if it cannot be proven."""
    _load_persistence_model()          # refreshes _PERSIST_MODEL_PATH / _PERSIST_MODEL_MTIME
    if _PERSIST_MODEL is None or not _PERSIST_MODEL_PATH or not _PERSIST_MODEL_HASH:
        return None
    # This is the hash captured when the in-memory object was deserialized. Hashing the current
    # path here would misidentify the served model during the 30-second hot-reload throttle.
    return _PERSIST_MODEL_HASH


def _pm_calibration_hash(horizon: int, calibration) -> str | None:
    """Identity of the exact monotone map used, INCLUDING its leakage boundary.

    The knots are the calibrator. Two files with the same knots and the same
    fitted_through_ms are the same calibrator regardless of how they were serialised, and a
    refit that moves the boundary is a different one even if the knots barely move."""
    if calibration is None:
        return None
    if horizon not in _PM_CAL_HASH_CACHE:
        try:
            from artifact_identity import hash_json
            _PM_CAL_HASH_CACHE[horizon] = hash_json({
                "x": list(calibration.x), "y": list(calibration.y),
                "fitted_through_ms": int(calibration.fitted_through_ms),
                "horizon": int(calibration.horizon)})
        except Exception as exc:
            logger.debug(f"calibrator hash unavailable for {horizon}m: {exc}")
            return None
    return _PM_CAL_HASH_CACHE[horizon]


def _pm_policy_hash() -> str | None:
    """Identity of the DECLARED policy - every constant that can move a decision.

    Read from the strategy module rather than restated here, so editing a margin changes the
    hash automatically. A restated copy would drift and quietly certify the wrong policy."""
    try:
        from artifact_identity import hash_json
        return hash_json({
            "strategy": _PM_STRATEGY_ID,
            "entry_margin": _pm_fv.ENTRY_MARGIN, "exit_margin": _pm_fv.EXIT_MARGIN,
            "stop_drop": _pm_fv.STOP_DROP,
            "min_seconds_left": _pm_fv.MIN_SECONDS_LEFT,
            "max_seconds_left": _pm_fv.MAX_SECONDS_LEFT,
            "eval_window_s": [_pm_fv.EVAL_MIN_SECONDS_LEFT,
                              _pm_fv.EVAL_MAX_SECONDS_LEFT]})
    except Exception as exc:
        logger.debug(f"policy hash unavailable: {exc}")
        return None


_PM_LEDGER = None
_PM_LEDGER_FAILED = False


def _pm_ledger():
    """The atomic causal decision ledger, or None. Never breaks the round loop."""
    global _PM_LEDGER, _PM_LEDGER_FAILED
    if _PM_LEDGER is not None or _PM_LEDGER_FAILED:
        return _PM_LEDGER
    try:
        from opportunity_ledger.ledger import OpportunityLedger
        _PM_LEDGER = OpportunityLedger(
            os.path.join(os.environ.get("BTC_DATA_DIR",
                                        os.path.join(os.path.dirname(
                                            os.path.dirname(os.path.abspath(__file__))), "data")),
                         "opportunity_ledger.duckdb"))
    except Exception as exc:
        _PM_LEDGER_FAILED = True
        logger.warning(f"opportunity ledger unavailable, decisions will not be recorded: {exc}")
    return _PM_LEDGER


def _pm_ledger_settle_round(round_id, outcome, outcome_ms, *, price=None,
                            kind="SETTLEMENT_PROXY", source=""):
    """Append settlement lifecycle rows without mutating the original decisions."""
    ledger = _pm_ledger()
    if ledger is None:
        return 0
    try:
        return ledger.append_settlement_for_round(
            str(round_id), settled_direction=str(outcome), outcome_ts=int(outcome_ms),
            kind=str(kind), settlement_price=price, source=str(source))
    except Exception as exc:
        logger.warning(f"opportunity-ledger settlement append failed for {round_id}: {exc}")
        return 0


def _pm_ledger_record(round_data, now_ms, horizon, quote, action, reason, probability,
                      *, calibration=None, feature_values=None):
    """One immutable decision row, with the timestamps AND the provenance that decided it.

    decision_ts       = now, the instant this branch ran
    quote_recv_ts     = when the bridge observed the price (from the quote itself)
    quote_exchange_ts = the VENUE's own stamp for that book - a different clock, so the ledger
                        stores the difference as skew rather than asserting an ordering
    state_snapshot_ts = when the complete state used here was composed and persisted
    feature_cutoff_ts = the same instant - no feature here postdates the state

    An evaluated action (ENTER/WAIT) additionally carries four hashes naming the model, the
    calibrator, the declared policy and the exact feature values. Without them a row proves
    only that something ran; the ledger refuses such a row, so if the identity cannot be
    resolved this records UNAVAILABLE - which is the truth - instead of a WAIT that cannot be
    re-derived.

    A refusal raised from here is a REAL FINDING about the live path. It is logged at WARNING
    and never silently dropped, because that silence is exactly how the original defect
    survived five studies.
    """
    ledger = _pm_ledger()
    if ledger is None:
        return
    # Imported BEFORE the try: naming it only inside would make `except LedgerRefusal`
    # raise NameError if the import itself failed - an error handler that cannot run.
    try:
        from opportunity_ledger.ledger import (
            EVALUATED_ACTIONS, Action, Decision, LedgerRefusal, stable_hash)
    except Exception as exc:
        logger.debug(f"opportunity ledger types unavailable: {exc}")
        return
    try:
        state_ms = int(round_data.get("_round_state_composed_ms") or 0) or None
        quote_ms = int((quote or {}).get("quote_ts_ms") or 0) or None
        chosen = Action(action)
        identity = {"model_artifact_hash": None, "calibrator_hash": None,
                    "policy_hash": None, "feature_values_hash": None}
        context = None
        if chosen in EVALUATED_ACTIONS:
            # stable_hash comes from the ledger itself, not a second implementation of the
            # same canonical JSON. The ledger re-hashes decision_context.feature_values and
            # compares; two hashers that agree today are a drift waiting to happen.
            identity = {
                "model_artifact_hash": _phold_artifact_hash(),
                "calibrator_hash": _pm_calibration_hash(int(horizon), calibration),
                "policy_hash": _pm_policy_hash(),
                # The exact inputs decide() consumed, not a re-read of them later.
                "feature_values_hash": (stable_hash(feature_values)
                                        if feature_values is not None else None),
            }
            # The hash proves nothing changed; the payload is what lets a later run actually
            # re-derive the decision without touching any table that postdates it.
            context = {"feature_values": feature_values or {},
                       "model_outputs": {"calibrated_probability": probability,
                                         "raw_p_hold": (feature_values or {}).get("raw_p_hold")}}
            missing = [name for name, value in identity.items() if not value]
            if missing:
                # Downgrading is not hiding the problem - UNAVAILABLE is precisely what
                # happened: the strategy produced a number it cannot account for, so it has
                # no usable decision to record.
                logger.warning(
                    f"{_PM_STRATEGY_ID} evaluated but cannot prove its provenance "
                    f"({', '.join(missing)}); recording UNAVAILABLE instead of {action}")
                chosen, reason = Action.UNAVAILABLE, f"unprovable identity: {','.join(missing)}"
                identity = {key: None for key in identity}
                context = None
        # The quote and the probability are recorded for WAIT as well as ENTER. "What did it
        # see, and what did it think, when it declined?" is the question forward evidence has
        # to answer; a WAIT row with no price is just a timestamp.
        acting = chosen in EVALUATED_ACTIONS
        record = Decision(
            round_id=str(round_data.get("id")),
            strategy_id=_PM_STRATEGY_ID,
            market_id=str(round_data.get("condition_id") or round_data.get("id")),
            venue="polymarket",
            decision_ts=int(now_ms),
            quote_exchange_ts=(quote or {}).get("quote_exchange_ts_ms"),
            quote_recv_ts=quote_ms,
            # The EXACT state used, identified rather than reconstructed later by an as-of join.
            state_snapshot_id=(f"{round_data.get('id')}:{state_ms}" if state_ms else None),
            state_snapshot_ts=state_ms,
            feature_cutoff_ts=state_ms,
            side=(quote or {}).get("side") if acting else None,
            ask=(quote or {}).get("ask") if acting else None,
            bid=(quote or {}).get("bid") if acting else None,
            fee=(quote or {}).get("fee") if acting else None,
            probability=probability if acting else None,
            **identity,
            decision_context=context,
            action=chosen, reason=str(reason)[:400],
            risk_state="PAPER_ONLY")
        ledger.record(record)
    except LedgerRefusal as exc:
        logger.warning(f"LEDGER REFUSED a live decision - the serving path cannot prove its "
                       f"own causality or provenance: {exc}")
    except Exception as exc:
        logger.debug(f"opportunity ledger write skipped: {exc}")


def _leader_quote(round_data, now_ms):
    """BOTH-sides view of the fresh bridge quote, picking the MARKET's leader (higher bid) --
    the side LATE_LEADER_30S_V1 buys. Same fail-closed anchor/age checks as _market_quote_for_round
    (which keeps the _PM_QUOTES cache warm every tick). Returns dict or None."""
    payload = _PM_QUOTES or {}
    quote = (payload.get("markets") or {}).get(str(int(round_data.get("horizon") or 0)))
    if not quote:
        return None
    try:
        expected_anchor = int(round_data.get("window_start") or 0) // 1000
        if int(quote.get("anchor_ts") or 0) != expected_anchor:
            return None
        if abs(float(now_ms) / 1000.0 - float(quote.get("ts") or 0.0)) > 5.0:
            return None
        ub, db_ = float(quote["up_bid"]), float(quote["down_bid"])
        side = "UP" if ub > db_ else "DOWN"
        pfx = "up" if side == "UP" else "down"
        ask, bid = float(quote[f"{pfx}_ask"]), float(quote[f"{pfx}_bid"])
        if not (0.0 <= bid <= ask < 1.0) or ask <= 0.0:
            return None
        fee_rate = float(quote.get("fee_rate") or 0.07)
        fee = round(fee_rate * ask * (1.0 - ask), 5) if quote.get("fees_enabled") is not False else 0.0
        return {"side": side, "ask": ask, "bid": bid, "fee": fee,
                "spread": round(ask - bid, 4),
                "depth": float(quote.get(f"{pfx}_top_ask_size") or 0.0),
                # WHEN this price was observed. Without it a decision row cannot prove the
                # quote preceded the decision, which is the defect that invalidated five
                # studies. The bridge stamps `ts` in SECONDS; the ledger works in ms.
                "quote_ts_ms": _seconds_to_ms(
                    quote.get(f"{pfx}_quote_recv_ts") or quote.get("ts")),
                # The VENUE's own stamp for this book, which the recorder already captures and
                # the bridge already carries - the app was simply dropping it. It is a
                # DIFFERENT clock from `ts`, so the ledger stores their difference as skew
                # rather than treating it as an ordering claim. 0/absent means the venue did
                # not supply one; None keeps that distinguishable from "at the epoch".
                "quote_exchange_ts_ms": (
                    _seconds_to_ms(quote.get(f"{pfx}_book_ts")) or None)}
    except Exception:
        return None


def _live_share_prices_for_round(round_data, now_ms):
    """Return both executable Polymarket contract quotes for the exact live round."""
    import json

    def validated_levels(raw_levels, *, ascending, expected_top):
        levels = []
        for raw in raw_levels or []:
            if not isinstance(raw, (list, tuple)) or len(raw) < 2:
                return None
            try:
                price, size = float(raw[0]), float(raw[1])
            except (TypeError, ValueError):
                return None
            if not np.isfinite(price) or not np.isfinite(size):
                return None
            if not (0.0 <= price <= 1.0) or size <= 0.0:
                return None
            levels.append([price, size])
        if not levels:
            return None
        ordered = sorted(levels, key=lambda item: item[0], reverse=not ascending)
        if levels != ordered or abs(float(levels[0][0]) - float(expected_top)) > 1e-6:
            return None
        return levels

    payload = _PM_QUOTES or {}
    quote = (payload.get("markets") or {}).get(str(int(round_data.get("horizon") or 0)))
    if not quote:
        return None
    try:
        expected_anchor = int(round_data.get("window_start") or 0) // 1000
        quote_ts = float(quote.get("ts") or 0.0)
        if int(quote.get("anchor_ts") or 0) != expected_anchor:
            return None
        age = float(now_ms) / 1000.0 - quote_ts
        if abs(age) > 5.0:
            return None
        fee_rate = float(quote.get("fee_rate") or 0.07)
        fees_enabled = quote.get("fees_enabled") is not False
        sides = {}
        for side, prefix in (("UP", "up"), ("DOWN", "down")):
            bid = float(quote[f"{prefix}_bid"])
            ask = float(quote[f"{prefix}_ask"])
            if not (0.0 <= bid <= ask <= 1.0) or ask <= 0.0:
                return None
            try:
                # Prefer the complete public ladder carried by the atomic bridge. The capped
                # 12-level ladder remains a compatibility fallback for older recorder builds.
                ladder = (quote.get(f"{prefix}_full_ladder") or
                          quote.get(f"{prefix}_ladder"))
                ladder = json.loads(ladder) if isinstance(ladder, str) else ladder
                bids = validated_levels(
                    (ladder or {}).get("b"),
                    ascending=False,
                    expected_top=bid,
                )
                asks = validated_levels(
                    (ladder or {}).get("a"),
                    ascending=True,
                    expected_top=ask,
                )
            except (TypeError, ValueError):
                bids, asks = None, None
            if bids is None or asks is None:
                return None
            sides[side.lower()] = {
                "bid": round(bid, 4),
                "ask": round(ask, 4),
                "spread": round(float(quote.get(f"{prefix}_spread") or ask - bid), 4),
                "ask_size": round(float(quote.get(f"{prefix}_top_ask_size") or 0.0), 2),
                "bid_size": round(float(quote.get(f"{prefix}_top_bid_size") or 0.0), 2),
                "bid_depth_1c": round(float(quote.get(f"{prefix}_b1") or 0.0), 2),
                "bid_depth_5c": round(float(quote.get(f"{prefix}_b5") or 0.0), 2),
                "bid_ladder": bids,
                "ask_ladder": asks,
                "quote_recv_ts": quote.get(f"{prefix}_quote_recv_ts") or quote_ts,
                "book_ts": quote.get(f"{prefix}_book_ts"),
                "book_hash": quote.get(f"{prefix}_book_hash"),
                "buy_fee": round(fee_rate * ask * (1.0 - ask), 5) if fees_enabled else 0.0,
            }
        return {
            "slug": quote.get("slug"),
            "ts": quote_ts,
            "age_seconds": round(max(0.0, age), 3),
            "fees_enabled": fees_enabled,
            "fee_rate": fee_rate,
            "artifact_hash": quote.get("artifact_hash"),
            **sides,
        }
    except Exception:
        return None


def _capture_open_position_action_evidence(round_data, now_ms, seconds_left, btc_side):
    """Record action counterfactuals without granting the recorder decision authority."""
    round_id = round_data.get("id")
    last_capture = int(round_data.get("_open_action_capture_ms") or 0)
    if not round_id or int(now_ms) - last_capture < _OPEN_ACTION_CAPTURE_MS:
        return None
    positions = database.fetch_open_rule_paper_positions(round_id)
    round_data["_open_action_capture_ms"] = int(now_ms)
    result = open_position_action_recorder().record_positions(
        positions,
        market_snapshot=round_data.get("share_prices"),
        recorded_ts=int(now_ms),
        context={
            "mode": "PAPER_RESEARCH_ONLY",
            "round_id": str(round_id),
            "horizon_min": int(round_data.get("horizon") or 0),
            "seconds_left": int(seconds_left),
            "btc_price": float(round_data.get("current_price") or 0.0),
            "anchor_price": float(round_data.get("price_to_beat") or 0.0),
            "btc_side": str(btc_side or ""),
            "p_hold": (
                float(round_data["p_hold"])
                if round_data.get("p_hold") is not None else None
            ),
            "champion_action": str(
                (round_data.get("champion") or {}).get("action") or ""
            ),
            "state_snapshot_ts": int(round_data.get("_round_state_composed_ms") or 0),
        },
    )
    if result.get("refused"):
        logger.warning(
            "Open-position action evidence refused %s/%s positions for %s",
            result["refused"], result["positions"], round_id,
        )
    return result


def _public_round_view(round_data):
    """Remove internal execution ladders from a round before WebSocket serialization."""
    if not isinstance(round_data, dict):
        return round_data
    public = dict(round_data)
    prices = round_data.get("share_prices")
    if isinstance(prices, dict):
        public_prices = dict(prices)
        for side in ("up", "down"):
            side_quote = public_prices.get(side)
            if isinstance(side_quote, dict):
                clean = dict(side_quote)
                clean.pop("bid_ladder", None)
                clean.pop("ask_ladder", None)
                public_prices[side] = clean
        public["share_prices"] = public_prices
    return public


def _side_quote(round_data, now_ms, side):
    """Fresh bridge quote for ONE side ('UP'/'DOWN') -- used by the live SHADOW strategies to
    track their entered side's bid after entry (the leader may have changed since). Same
    fail-closed anchor/age checks as _leader_quote. Returns {ask,bid,fee} or None."""
    payload = _PM_QUOTES or {}
    quote = (payload.get("markets") or {}).get(str(int(round_data.get("horizon") or 0)))
    if not quote:
        return None
    try:
        if int(quote.get("anchor_ts") or 0) != int(round_data.get("window_start") or 0) // 1000:
            return None
        if abs(float(now_ms) / 1000.0 - float(quote.get("ts") or 0.0)) > 5.0:
            return None
        if side not in ("UP", "DOWN"):
            return None
        pfx = "up" if side == "UP" else "down"
        ask, bid = float(quote[f"{pfx}_ask"]), float(quote[f"{pfx}_bid"])
        if not (0.0 <= bid <= ask < 1.0):
            return None
        rate = float(quote.get("fee_rate") or 0.07)
        fees_on = quote.get("fees_enabled") is not False
        fee_in = round(rate * ask * (1 - ask), 5) if fees_on else 0.0
        return {"side": side, "ask": ask, "bid": bid, "fee": fee_in,
                "fee_in": fee_in,
                "fee_out": round(rate * bid * (1 - bid), 5) if fees_on else 0.0,
                "spread": ask - bid,
                "ask_size": float(quote.get(f"{pfx}_top_ask_size") or 0.0),
                "depth": float(quote.get(f"{pfx}_top_ask_size") or 0.0),
                "quote_ts_ms": _seconds_to_ms(
                    quote.get(f"{pfx}_quote_recv_ts") or quote.get("ts")),
                "quote_exchange_ts_ms": (
                    _seconds_to_ms(quote.get(f"{pfx}_book_ts")) or None)}
    except Exception:
        return None


def _predict_path_plan(bundle, horizon, keepers, price):
    """STABLE intra-window trade plan from live keepers: ENSEMBLE high/low band + touch / round-trip
    odds + net-drift + chop/trend style. Pure numpy; crash-safe at the call site. Once per window."""
    # A missing horizon is not interchangeable with 5m. Falling back silently gives
    # a valid-looking forecast with the wrong label window and calibration.
    hz = (bundle.get("horizons") or {}).get(horizon)
    if not hz or bundle.get("threshold_units") != "usd":
        return None
    values = [float(keepers[f]) for f in bundle["features"]]
    if not all(np.isfinite(v) for v in values) or not np.isfinite(price) or price <= 0:
        return None
    x = np.array([values], dtype=float)
    _epred = lambda models: float(np.mean([m.predict(x)[0] for m in models]))
    _eproba = lambda models: float(np.mean([m.predict_proba(x)[:, 1][0] for m in models]))
    # Headline HIGH/LOW = the likely REACH IF price moves that way = P75 of max_up / P25 of max_down.
    # NOT the unconditional median (P50): a window is one-sided, so the median of each side blends in the
    # opposite-direction windows (where that side's excursion is tiny) and badly understates the reach --
    # which read as inconsistent with P(moves>=$X) on the card (operator-caught 2026-06-30).
    up50 = _epred(hz["qhi"][0.5]); dn50 = _epred(hz["qlo"][0.5])
    up75 = _epred(hz["qhi"][0.75])
    dn25 = _epred(hz["qlo"][0.25])
    up_hi = up75 + hz["conformal"]["up"]
    dn_lo = dn25 - hz["conformal"]["dn"]
    _bps = lambda v: price * v / 1e4

    def _p_move(dollars):
        lvl = min(bundle["touch_usd"], key=lambda L: abs(float(L) - float(dollars)))
        t = hz["touch"][lvl]
        return float(t["iso"].transform([_eproba(t["models"])])[0])

    _rt, _asym, _nm = hz.get("roundtrip"), hz.get("touch_asym"), hz.get("net_mag")
    p_rt = float(_rt["iso"].transform([_eproba(_rt["models"])])[0]) if _rt else None
    p_asym = float(_asym["iso"].transform([_eproba(_asym["models"])])[0]) if _asym else None
    net_mag = _bps(_epred(_nm["models"])) if _nm else None
    _te = hz.get("touch_early")   # v3+ only; backward-compatible (None on the v2 bundle)
    p_early = float(_te["iso"].transform([_eproba(_te["models"])])[0]) if _te else None
    p_move_50 = _p_move(50.0)
    def _effective_touch_usd(nominal):
        levels = bundle.get("touch_bps") or {}
        if not levels:
            return float(nominal)
        key = min(levels, key=lambda value: abs(float(value) - float(nominal)))
        return price * float(levels[key]) / 1e4

    move_50_threshold_usd = _effective_touch_usd(50.0)
    move_100_threshold_usd = _effective_touch_usd(100.0)
    style = None
    play = "WATCH"   # composed live engine: SKIP / FADE-SETUP / RIDE / WATCH
    if p_rt is not None:
        rt_base = (_rt or {}).get("base", 0.25) or 0.25
        move50 = hz["touch"][min(bundle["touch_usd"], key=lambda L: abs(float(L) - 50.0))]
        move_base = move50.get("base", 0.5) or 0.5
        if p_move_50 < move_base * 0.8:
            style = "quiet"
        elif p_rt >= rt_base * 1.25:
            style = "two_sided"
        elif p_move_50 >= move_base * 1.1 and p_rt <= rt_base * 0.75:
            style = "one_sided"
        else:
            style = "mixed"
        # Composed live play (validated engine): fade the early extreme in active chop, ride trends, skip quiet.
        if style == "quiet":
            play = "SKIP"                                   # no room, no fade
        elif style == "two_sided" and (p_early or 0.0) >= 0.5:
            play = "FADE-SETUP"                             # active chop + early touch likely -> fade the extreme
        elif style == "one_sided":
            play = "RIDE"                                   # trend -> ride, don't fade
        else:
            play = "WATCH"                                  # mixed -> wait for the touch
    return {
        "pred_high": round(price + _bps(up75)),          # likely up-reach (if it moves up), not the blended median
        "pred_low": round(price + _bps(dn25)),           # likely down-reach (if it moves down)
        "high_band": [round(price + _bps(up50)), round(price + _bps(up_hi))],   # typical -> extended up-reach
        "low_band": [round(price + _bps(dn_lo)), round(price + _bps(dn50))],    # extended -> typical down-reach
        "pred_range_usd": round(_bps(up50 - dn50)),      # typical TOTAL travel (one-sided; not high-low of the reaches)
        "p_move_50": round(p_move_50, 3),
        "p_move_100": round(_p_move(100.0), 3),
        "move_50_threshold_usd": round(move_50_threshold_usd, 1),
        "move_100_threshold_usd": round(move_100_threshold_usd, 1),
        "p_roundtrip": round(p_rt, 3) if p_rt is not None else None,
        "p_touch_asym": round(p_asym, 3) if p_asym is not None else None,
        "net_move_usd": round(net_mag) if net_mag is not None else None,
        "p_early": round(p_early, 3) if p_early is not None else None,
        "style": style,
        "play": play,
        "threshold_units": "bps_labels_live_usd",
    }


# Primary LIVE fade barrier. $30 (not $50) matches how Polymarket UP/DOWN shares actually reprice: a $20-30 move
# near the anchor already swings the share price enough to fade, and $30 touches happen ~2x as often as $50 (more
# setups). The multi-barrier fade model (train_fade_model.py v4) has a matching $30 head. The path STYLE / round-
# trip context stays $50-based (a $50 chop window is also a $30 chop window). Set BTC_FADE_BARRIER to override.
try:
    FADE_L = float(os.environ.get("BTC_FADE_BARRIER", "30") or 30)
except Exception:
    FADE_L = 30.0


def _path_touch_state(plan, side, touch_secs_left, horizon, both):
    """LIVE read once price has touched the +/-$FADE_L fade barrier this window. The fade (revert to
    anchor) is only playable on an EARLY touch: on the honest strict grade only ~27% of touches ever
    reach the anchor, and a LATE touch almost never does -- no time to revert before expiry (6.9%
    strict at $50/5m vs 41% for an early touch). So 'bias' (the fade LEAN) is emitted ONLY for an
    early touch in a chop/round-trip window; a late touch is watch-only. Pure / crash-safe.
    NOTE: which side ultimately wins is still coin-flip -- 'bias' is the conditional reversal LEAN
    (increase vs decrease from here), not a guaranteed direction; a real entry edge needs mispricing.
    (Corrected 2026-07-01: the old 'early reverts ~2x' claim was a mislabel + settle-rule artifact.)"""
    dur = max(1, int(horizon) * 60)
    phase = "early" if touch_secs_left > dur * 0.5 else "late"
    style = (plan or {}).get("style")
    p_rt = (plan or {}).get("p_roundtrip") or 0.0
    if both:
        return {"side": "BOTH", "phase": phase, "bias": None,
                "call": "Round-trip in progress — both sides touched; fade the SECOND leg (see leg2)."}
    fade = (style == "two_sided") or (p_rt >= 0.30)
    if fade and phase == "early":
        return {"side": side, "phase": phase,
                "bias": ("DOWN" if side == "HIGH" else "UP"),
                "call": "PAPER fade candidate — confirm model grade and executable share-price edge"}
    if fade and phase == "late":
        return {"side": side, "phase": phase, "bias": None,
                "call": "Chop, but LATE touch — usually too late to revert to anchor before close; don't fade"}
    if style == "one_sided":
        return {"side": side, "phase": phase,
                "bias": ("UP" if side == "HIGH" else "DOWN"),
                "call": "CONTINUATION more likely — ride it, low reversal odds"}
    return {"side": side, "phase": phase, "bias": None,
            "call": "MIXED — watch for follow-through vs fade"}


def _grade_fade(side, touch_secs_left, pre_hi, pre_lo, anchor, horizon, keepers, L=50.0):
    """LIVE P(this fade reaches the anchor TP before the stop), from the honest fade model. Rebuilds
    the 3 touch-context features EXACTLY as train_fade_model does, from the pre-touch running hi/lo
    frozen at the touch. side='HIGH' = an up-touch (BUY DOWN, side_up=1). Returns float or None."""
    fm = _load_fade_model()
    if not fm or anchor <= 0 or not keepers:
        return None
    if any(keepers.get(k) is None for k in fm.get("keepers", [])):
        return None
    dur = max(1, int(horizon) * 60)
    touch_frac = max(0.0, min(1.0, float(touch_secs_left) / dur))     # high = early touch
    # v5 is trained from completed bars before the 1m touch candle. Represent the
    # crossing at the exact barrier (zero overshoot); do not serve post-touch extrema.
    if side == "HIGH":                                                # up-touch -> BUY DOWN
        side_up = 1
        known_hi = anchor + L
        known_lo = min(anchor, pre_lo)
        overshoot = 0.0
        pre_opp = (anchor - known_lo) / anchor * 1e4
    else:                                                             # down-touch -> BUY UP
        side_up = 0
        known_hi = max(anchor, pre_hi)
        known_lo = anchor - L
        overshoot = 0.0
        pre_opp = (known_hi - anchor) / anchor * 1e4
    pre_range = (known_hi - known_lo) / anchor * 1e4
    try:
        from train_fade_model import predict_fade
        # forward L so the matching per-barrier head ($30 or $50) is used -- NOT just the feature math
        return round(float(predict_fade(fm, int(horizon), keepers, touch_frac, side_up,
                                        overshoot, pre_opp, pre_range, L=L)), 3)
    except Exception:
        return None


def _trade_signal(plan, p_hold, cur_pos, cur_move, secs_left, horizon, ptb):
    """Unified ENTRY/EXIT from the VALIDATED edges only: (1) late-entry HOLD (ahead + late + P(hold)>=0.93
    -> ~95% holds, the one proven entry), (2) FADE an EARLY touch in chop, gated on the honest fade model
    P(reach anchor) -- fade only the top touches (P>=0.55; base reach-rate is ~0.27). Pure / crash-safe.
    Honest: the side is the already-ahead side (late-hold) or the cheap side (fade), NOT a direction
    prediction; true +EV per trade still needs the Polymarket price mispriced vs P(hold)/fair."""
    if not isinstance(plan, dict):
        return None
    dur = max(60, int(horizon) * 60)
    late_win = min(120, int(dur * 0.4))
    late_entry = 15 < secs_left <= late_win           # the proven late-entry window -- NOT the final 15s
    ahead = cur_pos in ("UP", "DOWN") and abs(cur_move) >= 10
    ts = plan.get("touch_state") or {}
    hi, lo = plan.get("pred_high"), plan.get("pred_low")
    # 1. Late-entry HOLD -- the one proven entry (buy the already-ahead side inside the late window)
    if ahead and late_entry and p_hold is not None and p_hold >= 0.93:
        entry_fair = min(p_hold, decision_champion.DEFAULT_ENTRY_FAIR_CAP)
        max_ask = decision_champion.max_taker_ask(entry_fair)
        return {"do": "CHECK EDGE", "tone": "warn", "side": cur_pos,
                "max_taker_ask": round(max_ask, 4),
                "short": f"BUY {cur_pos} if ask <= {max_ask*100:.0f}c",
                "text": f"Late-hold setup on {cur_pos}: ahead ${abs(cur_move):.0f}, {secs_left}s left, "
                        f"P(hold)={p_hold*100:.0f}%, conservative entry fair={entry_fair*100:.0f}c. "
                        f"Do not buy above ~{max_ask*100:.0f}c taker ask (crypto fee + 3c safety "
                        f"buffer included). Confirm the live champion edge first."}
    # 2. Too late to ENTER (final ~15s): if already ahead, just hold to close -- don't enter fresh
    if ahead and secs_left <= 15 and p_hold is not None and p_hold >= 0.80:
        return {"do": "HOLD", "tone": "good", "side": cur_pos,
                "short": f"HOLD {cur_pos} to close",
                "text": f"HOLD to close -- ahead ${abs(cur_move):.0f}, only {secs_left}s left, P(hold)={p_hold*100:.0f}%. "
                        f"Too late to enter fresh; ride your position out."}
    # 3. Fade an EARLY touch in active chop, GATED on the honest fade model (P reaches anchor before stop).
    #    ts.bias is set only for an early touch in chop (late touches can't revert in time). We fade only
    #    the touches the model ranks high -- base reach-rate ~0.27, so P>=0.55 is roughly the top quartile.
    if plan.get("play") == "FADE-SETUP" and ts.get("bias") and secs_left > 20:
        fade = "DOWN" if ts.get("side") == "HIGH" else "UP"
        p_fade = ts.get("p_fade")
        if p_fade is None:
            return {"do": "WAIT", "tone": "muted", "short": "WAIT -- fade model offline",
                    "text": "Fade model is missing, stale, or warming up. No fade entry is allowed."}
        if p_fade < 0.45:
            return {"do": "SKIP", "tone": "muted", "short": "SKIP this touch",
                    "text": f"{ts.get('side')} touched but the fade model says only {p_fade*100:.0f}% reach ${ptb:,.0f} "
                            f"(anchor) before extending -- skip this fade."}
        if p_fade >= 0.55:
            return {"do": "PAPER ONLY", "tone": "warn", "side": fade,
                    "short": f"PAPER: fade -> buy {fade} ({p_fade*100:.0f}%)",
                    "text": f"FADE {fade} paper setup: model P(reach anchor)={p_fade*100:.0f}%. "
                            f"BTC reach probability is not a binary-share fair value; entry/exit bids, "
                            f"two taker fees, and stop value are unproven. Track on paper only."}
        return {"do": "WATCH", "tone": "muted", "short": "WAIT -- borderline touch",
                "text": f"{ts.get('side')} touched, fade model P(reach anchor)={p_fade*100:.0f}% -- borderline; "
                        f"wait for a cleaner (higher-P) early touch."}
    # 3b. ROUND-TRIP RETURN (the two-sided play): after leg 1, the OPPOSITE side also spiked -> fade the 2nd
    #     leg (buy the now-cheap side, exit at anchor). ts.leg2 is set once both sides touched at $FADE_L;
    #     it is graded like leg 1 on the opposite side. Honest: gate on the model, same as leg 1.
    leg2 = ts.get("leg2")
    if leg2 and leg2.get("side") and secs_left > 20:
        p2 = leg2.get("p_fade")
        if p2 is not None and p2 >= 0.55:
            return {"do": "PAPER ONLY", "tone": "warn", "side": leg2["fade"],
                    "short": f"PAPER: buy {leg2['fade']} (2nd leg, {p2*100:.0f}%)",
                    "text": f"ROUND-TRIP RETURN -> FADE BUY {leg2['fade']} (2nd leg, now-cheap side). "
                            f"model P(reach anchor)={p2*100:.0f}%. EXIT near ${ptb:,.0f}. Two-sided play -- you "
                            f"faded leg 1, this is the return. Track ask, exit bid, fees, and stop on paper."}
        if p2 is not None and p2 < 0.45:
            return {"do": "SKIP", "tone": "muted", "short": "SKIP 2nd leg",
                    "text": f"Round-trip return, but the 2nd-leg fade model says only {p2*100:.0f}% reach "
                            f"${ptb:,.0f} -- skip this leg."}
    # 4. Ahead but not yet in the late-entry window -> watch, with an exit target
    if ahead:
        tp = hi if cur_pos == "UP" else lo
        tp_txt = f" take profit near ${int(tp):,};" if tp is not None else ""
        return {"do": "WATCH", "tone": "muted", "short": "WAIT for the late window",
                "text": f"In {cur_pos}?{tp_txt} or wait for the late-entry window (P(hold)>=93% at 15-{late_win}s left)."}
    if plan.get("style") == "quiet":
        return {"do": "SKIP", "tone": "muted", "short": "SIT OUT -- quiet round",
                "text": "QUIET -- no range to capture; skip this window. At 50/50 share prices a coin-flip round "
                        "is exactly fairly priced -- buying either side is -EV after fees."}
    # 5. WINDOW OPEN game-plan (no leader yet, shares ~50/50). Direction is coin-flip, so 50c shares are
    #    FAIR -- buying either side now is -EV after fees. The honest open action is a PLAN: what event
    #    this round type is waiting for. (Round type = the frozen path head; HF audit: TREND rounds hold
    #    95%+, CHOP fades revert -- but both need their trigger EVENT, not an at-open direction buy.)
    if secs_left > late_win:
        style = plan.get("style")
        p_rt = plan.get("p_roundtrip")
        p_early = plan.get("p_early")
        fifty = ("At ~50/50 shares a direction buy is -EV after fees -- the edge only appears when the market "
                 "must reprice (a spike to fade, or a late leader cheaper than its hold odds).")
        if style == "two_sided":
            return {"do": "PLAN", "tone": "muted", "short": "PLAN: wait for a spike, then fade it",
                    "text": f"CHOP round expected (round-trip {round((p_rt or 0)*100)}%, early-touch "
                            f"{round((p_early or 0)*100)}%). Do NOT buy at 50/50. Wait for the first ~$30 spike; "
                            f"if the fade model grades it >=55%, buy the CHEAP side and exit near the anchor. {fifty}"}
        if style == "one_sided":
            return {"do": "PLAN", "tone": "muted", "short": "PLAN: let a leader form, buy it late",
                    "text": f"TREND-ish round expected. Do NOT buy at 50/50 -- wait for a clear leader, then enter in "
                            f"the late window (15-{late_win}s left) only if P(hold)>=93% and the ask is under the "
                            f"fee-adjusted cap. Historically late leaders in trend rounds hold ~95%. {fifty}"}
        return {"do": "PLAN", "tone": "muted", "short": "PLAN: no 50/50 buys -- wait for the event",
                "text": f"MIXED round -- let it show its hand. Two triggers to watch: a ~$30 spike (fade if the model "
                        f"grades >=55%) or a clear late leader (buy only under the ask cap). {fifty}"}
    # 6. Late window but NO clear leader -> a flat finish; there is nothing to buy at fair 50/50.
    return {"do": "WAIT", "tone": "muted", "short": "WAIT -- no clear leader",
            "text": f"Late window ({secs_left}s left) but no side is clearly ahead -- a near-anchor finish is a "
                    f"coin-flip and 50/50 shares price it fairly. No entry."}


def _log_path_plan_outcome(rnd, plan, end_price):
    """Append one served-plan-vs-realized row for the live path-plan scorecard (the verifier's
    'ongoing live recording' step). Self-contained CSV append; crash-safe at the call site."""
    import csv
    import os
    import time
    anchor = float(rnd.get("price_to_beat") or 0.0)
    if anchor <= 0 or not isinstance(plan, dict):
        return
    rhi = float(rnd.get("_tp_run_hi") or end_price); rlo = float(rnd.get("_tp_run_lo") or end_price)
    tu = int((rhi - anchor) >= 50.0); td = int((anchor - rlo) >= 50.0)
    tu30 = int((rhi - anchor) >= 30.0); td30 = int((anchor - rlo) >= 30.0)   # $30 = the live fade barrier
    hb = plan.get("high_band") or [None, None]
    cov = (int(hb[0] <= rhi <= hb[1]) if (hb and hb[0] is not None) else "")
    ts = plan.get("touch_state") or {}                                       # the FADE grade (the profit signal)
    leg2 = ts.get("leg2") or {}
    wq = plan.get("window_quality") or {}
    data_dir = os.environ.get("BTC_DATA_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "data")
    path = os.path.join(data_dir, "path_plan_outcomes.csv")
    row = {"ts": int(time.time()), "horizon": rnd.get("horizon"), "anchor": round(anchor, 2),
           "pred_high": plan.get("pred_high"), "pred_low": plan.get("pred_low"),
           "p_move_50": plan.get("p_move_50"), "p_roundtrip": plan.get("p_roundtrip"),
           "p_early": plan.get("p_early"), "style": plan.get("style"), "play": plan.get("play"),
           "realized_hi": round(rhi, 2), "realized_lo": round(rlo, 2),
           "end_price": round(float(end_price), 2), "touched_up_50": tu, "touched_dn_50": td,
           "touched_up_30": tu30, "touched_dn_30": td30,
           "roundtrip_realized": int(tu and td), "band_cover_hi": cov,
           # FADE prediction tracking: the grade assigned at the touch (leg 1 + round-trip leg 2), the window
           # prior, and a COARSE outcome (did it close back within $30 of the anchor = reverted). The STRICT
           # reach-anchor-before-stop outcome is validated offline (REVERSAL_STRATEGY_BACKTEST); this records
           # the live grade so a forward scorecard can be built.
           "fade_side": ts.get("side"), "p_fade": ts.get("p_fade"),
           "leg2_side": leg2.get("side"), "p_fade2": leg2.get("p_fade"),
           "window_score": wq.get("score"), "window_label": wq.get("label"),
           "reverted_to_anchor": int(abs(float(end_price) - anchor) <= 30.0),
           "net_usd": round(abs(float(end_price) - anchor), 2)}
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if new:
            w.writeheader()
        w.writerow(row)


def _select_keeper_config(bundle: dict, model_key: str = None, horizon: int = None):
    """Pick a keeper-head config from legacy, per-head, or per-horizon bundles."""
    if not bundle:
        return None
    models = bundle.get("models") or {}
    cfg = None
    if horizon is not None and models:
        hcfg = models.get(int(horizon)) or models.get(str(int(horizon)))
        if model_key:
            cfg = (hcfg or {}).get(model_key) if isinstance(hcfg, dict) else None
        else:
            cfg = hcfg if isinstance(hcfg, dict) and "pipe" in hcfg else None
    if cfg is None and model_key:
        cfg = models.get(model_key)
    if cfg is None and not model_key:
        cfg = bundle
    return cfg if isinstance(cfg, dict) and "pipe" in cfg else None


def _keeper_move_buckets(bundle: dict, horizon: int):
    """Return {meaningful, large, extreme} dollar buckets from a keeper bundle."""
    if not bundle:
        return None
    buckets = bundle.get("move_buckets_usd_by_horizon") or {}
    vals = buckets.get(int(horizon)) or buckets.get(str(int(horizon)))
    if vals is None:
        thresholds = bundle.get("move_threshold_usd_by_horizon") or bundle.get("drop_threshold_usd_by_horizon") or {}
        base = thresholds.get(int(horizon)) or thresholds.get(str(int(horizon)))
        vals = (base, float(base) * 2.0, float(base) * 4.0) if base is not None else None
    if isinstance(vals, dict):
        out = {
            "meaningful": vals.get("meaningful"),
            "large": vals.get("large"),
            "extreme": vals.get("extreme"),
        }
    else:
        try:
            a, b, c = list(vals)[:3]
            out = {"meaningful": a, "large": b, "extreme": c}
        except Exception:
            return None
    try:
        return {k: round(float(v), 2) for k, v in out.items() if v is not None}
    except Exception:
        return None


def _score_keeper_head(bundle: dict, keepers: dict, model_key: str = None, horizon: int = None):
    """Score one keeper-head bundle. Returns calibrated score or None."""
    if not bundle or not keepers:
        return None
    cfg = _select_keeper_config(bundle, model_key=model_key, horizon=horizon)
    if not cfg:
        return None
    features = cfg.get("features") or bundle.get("features") or []
    if not features or not all(keepers.get(k) is not None for k in features):
        return None
    raw = float(cfg["pipe"].predict_proba([[float(keepers[k]) for k in features]])[:, 1][0])
    iso = cfg.get("iso") or bundle.get("iso")
    if iso is not None:
        try:
            return float(iso.predict([raw])[0])
        except Exception:
            return raw
    return raw


def _tier(score: float, tiers: dict, labels=("LOW", "ELEVATED", "HIGH")):
    if score is None or not tiers:
        return None
    low, mid, high = labels
    return high if score >= tiers.get("t3", 1.0) else mid if score >= tiers.get("t2", 1.0) else low


def _load_signed_quantile_model():
    """Signed-quantile band head {models:{h:{q10,q50,q90,cqr}}, features, horizons} or None.
    HOT-RELOADS on .pkl change (mtime, throttled 30s) so a nightly recalibration goes live without a
    restart; keeps the PRIOR band on any failure (symmetric-band fallback if never loaded)."""
    global _SIGNED_QMODEL, _SIGNED_QMODEL_MTIME, _SIGNED_QMODEL_CHECKED
    import os
    import time
    now = time.time()
    if _SIGNED_QMODEL_CHECKED and (now - _SIGNED_QMODEL_CHECKED) < 30.0:
        return _SIGNED_QMODEL
    _SIGNED_QMODEL_CHECKED = now
    try:
        data_dir = os.environ.get("BTC_DATA_DIR") or os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "data")
        path = os.path.join(data_dir, "saved_models", "signed_quantile_model.pkl")
        mt = os.path.getmtime(path) if os.path.exists(path) else -1.0
        if mt == _SIGNED_QMODEL_MTIME:
            return _SIGNED_QMODEL
        if _freeze_blocks_reload(path, mt, "signed_quantile_model"):
            return _SIGNED_QMODEL                  # frozen: keep the pinned artifact
        if mt < 0:
            _SIGNED_QMODEL_MTIME = mt
            if _SIGNED_QMODEL is None:
                logger.info("Signed-quantile band absent — using symmetric band.")
            return _SIGNED_QMODEL
        if _identity_blocks_load(path, "signed_quantile_model"):
            _SIGNED_QMODEL_MTIME = mt
            return _SIGNED_QMODEL
        _SIGNED_QMODEL = _verified_load(path); _SIGNED_QMODEL_MTIME = mt
        logger.info(f"Signed-quantile band (re)loaded: horizons={_SIGNED_QMODEL.get('horizons')}, "
                    f"features={_SIGNED_QMODEL.get('features')}")
    except Exception as e:
        logger.warning(f"Signed-quantile band reload failed — keeping prior: {e}")
    return _SIGNED_QMODEL


# ── T2/T3 precision-tier PROOF panel — data/phold_tier.json (phold_tier_scorecard.py) ──────
# The historical hold-rate evidence for the late-entry tier, shown on the live card. Lazy +
# crash-safe; RELOADS when the operator re-runs the scorecard (mtime check). {} if absent →
# the card still shows the live P(hold), just without the historical-proof numbers.
def persistence_model_status() -> dict:
    """Return P(hold) model status for the UI/API."""
    mdl = _load_persistence_model()
    status = "loaded" if mdl is not None else ("missing" if _PERSIST_MODEL_ERROR == "missing" else "disabled")
    return {
        "status": status,
        "loaded": mdl is not None,
        "tried": bool(_PERSIST_MODEL_CHECKED),
        "path": _PERSIST_MODEL_PATH,
        "mtime": _PERSIST_MODEL_MTIME if _PERSIST_MODEL_MTIME >= 0 else None,
        "error": "" if _PERSIST_MODEL_ERROR == "missing" else _PERSIST_MODEL_ERROR,
        "test_auc": mdl.get("test_auc") if isinstance(mdl, dict) else None,
        "features": mdl.get("features") if isinstance(mdl, dict) else None,
    }


_TIER_PROOF = {}
_TIER_PROOF_MTIME = 0


def _load_tier_proof() -> dict:
    global _TIER_PROOF, _TIER_PROOF_MTIME
    try:
        import os
        import json
        data_dir = os.environ.get("BTC_DATA_DIR") or os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "data")
        path = os.path.join(data_dir, "phold_tier.json")
        if not os.path.exists(path):
            return _TIER_PROOF
        mt = os.path.getmtime(path)
        if mt != _TIER_PROOF_MTIME:
            with open(path, "r", encoding="utf-8") as f:
                _TIER_PROOF = json.load(f) or {}
            _TIER_PROOF_MTIME = mt
    except Exception:
        pass
    return _TIER_PROOF


def _hms(ms: int) -> str:
    """HH:MM clock label for a window boundary, in US Eastern (ET) to match Polymarket's
    market clock. NOTE: for 5m/15m windows the *boundary* is identical in any whole-hour
    timezone (ET is UTC-4/-5), so this only changes the displayed hour, never the actual
    reference-price lock time or resolution time."""
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        return datetime.fromtimestamp(ms / 1000.0, ZoneInfo("America/New_York")).strftime("%H:%M ET")
    except Exception:
        import time as _t
        return _t.strftime("%H:%M", _t.localtime(ms / 1000.0))


class PriceToBeatTracker:
    def __init__(self, horizons=(5, 15), neutral_band=0.0, persist=True, source="pyth"):  # 0.0 = strict up/down (Polymarket mirror)
        # neutral_band = 0.0 makes resolution STRICT (any close above the open = UP, below
        # = DOWN), mirroring Polymarket's BTC up/down markets — where even a +$50 move on
        # $63k (0.08%) is a clear UP, not "stayed near reference". The cost-floored band
        # belongs on the trade-decision side, not on this market-outcome mirror.
        self.horizons = list(horizons)
        self.neutral_band = neutral_band
        # persist=True → write rounds to the shared `price_to_beat` table. A SECONDARY tracker
        # (e.g. the Binance-priced mirror) is disambiguated by `source` + a source-prefixed round
        # id, so it persists alongside the Pyth rows without id collision. Pyth-only auxiliary
        # recorders (persistence/champion snapshots) stay gated on `source == "pyth"` below, and the
        # boot rehydration filters source='pyth', so the mirror never poisons the Pyth model's data.
        self.persist = persist
        self.source = source
        self.pending: list[dict] = []
        self.history = {h: deque(maxlen=1000) for h in self.horizons}
        self.recent_rounds = deque(maxlen=1000)        # resolved rounds for the UI feed
        # (250: with 6 mirror horizons, 1m floods the buffer — 250 keeps ~25+ of each
        # slower timeframe visible/scrollable in the per-TF log tabs)
        self.latest_round = {}                        # horizon -> current/most-recent round
        self.current_window = {}                      # horizon -> window-start ms we already opened
        self._last_snap = {}                          # A1: round_id -> last snapshot ms (15s dedupe)
        # Rolling (ts_ms, price) buffer for LIVE trailing-60s realized vol — the vol_60s_pct
        # feature the A1 P(hold) model needs. update() fires ~1/s, so 180 covers ~3 min.
        self._px_buf = deque(maxlen=180)
        self._last_keepers = None                     # live vol keepers (set per update tick)

    def restore_pending(self, rows: list[dict], now_ms: int | None = None) -> int:
        """Rehydrate still-open rounds without fabricating a mid-window anchor.

        Restored rounds resolve and manage persisted exits, but paper entries are
        disabled until the next clean boundary because the process did not observe
        the earlier part of the window.
        """
        now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
        restored = 0
        known = {str(p.get("id")) for p in self.pending}
        for row in rows or []:
            try:
                horizon = int(row.get("horizon") or 0)
                start = int(row.get("timestamp") or 0)
                verify_at = int(row.get("verify_at") or 0)
                round_id = str(row.get("id") or "")
                if (not round_id or round_id in known or horizon not in self.horizons
                        or verify_at <= now_ms or start <= 0
                        or str(row.get("source") or "pyth") != self.source):
                    continue
                grade = str(row.get("confluence_grade") or "")
                state = {
                    **row, "status": "pending", "window_start": start,
                    "window_end": verify_at, "window_label": f"{_hms(start)}-{_hms(verify_at)}",
                    "our_action": row.get("signal") or "NEUTRAL",
                    "confluence": {"grade": grade} if grade else None,
                    "_restored_mid_round": True, "_paper_entries_disabled": True,
                }
                self.pending.append(state)
                self.latest_round[horizon] = state
                self.current_window[horizon] = start
                known.add(round_id)
                restored += 1
            except Exception:
                continue
        return restored

    @staticmethod
    def _bet_lean(p: dict, horizon: int | None = None) -> str:
        """The side to bet on a Polymarket binary up/down market. Polymarket has NO
        neutral outcome — the window ALWAYS settles UP or DOWN — so we always surface
        the side the model favors. Prefer the 3-class lean when it actually picks a
        direction; otherwise fall back to the model's TWO-WAY probability (P(UP) vs
        P(DOWN)), which still has a favored side even when the NEUTRAL/timeout class
        wins the argmax (which it usually does at 5-15m, since BTC rarely clears the
        cost floor that fast). NEUTRAL only when there is no probability signal at all.

        This deliberately separates 'which side' (shown every window, here) from 'is it
        worth betting' (decided later by fair_value_cents = value betting). Before, the
        bet used the raw 3-class lean directly, so most windows showed NEUTRAL/'No bet'
        even though the model still favored a side — making directional calls look rare."""
        raw = p.get("rawDirection", p.get("direction", "NEUTRAL"))
        if raw in ("UP", "DOWN"):
            return raw
        pu = float(p.get("probUp", 0.0) or 0.0)
        pd = float(p.get("probDown", 0.0) or 0.0)
        if pu <= 0.0 and pd <= 0.0:
            return "NEUTRAL"
        # THE SAME DEAD ZONE THE HEAD APPLIES, APPLIED HERE TOO.
        #
        # `model.generate_ensemble_prediction` refuses to commit when prob_up and prob_down
        # are within `BTC_DIR_MARGIN` of each other, precisely so that a bare
        # `prob_up > prob_down` cannot turn a systematic tilt into a directional call. This
        # function then performed that bare comparison - on exactly the rows the head had
        # just sent to NEUTRAL. A margin applied at one site and bypassed at the next is not
        # a margin.
        #
        # It changes nothing at the shipped default of 0.0, and the tilt it was written for
        # is NOT fixed by any margin (see the measurement at the knob in model.py: the skew
        # gets worse as the margin widens). This is a consistency fix, not a cure.
        import os as _os
        _m = float(_os.environ.get(f"BTC_DIR_MARGIN_{horizon}",
                                   _os.environ.get("BTC_DIR_MARGIN", "0.0")) or 0.0)
        if abs(pu - pd) <= _m:
            # Inside the noise floor the two sides are indistinguishable, which is what
            # this function's own last line calls "no probability signal at all".
            return "NEUTRAL"
        return "UP" if pu >= pd else "DOWN"

    def _grade_provenance(self, p: dict, h: int, win_start: int) -> dict:
        """Everything needed to know what a graded round actually compared.

        `contract_match` is False under the shipped configuration, and that is the point:
        it makes a standing cross-contract comparison visible instead of implicit. An
        unknown contract is recorded as None and counted as unknown - never as a match.
        """
        pred_ts = int(p.get("timestamp") or 0) or None
        offset_ms = (win_start - pred_ts) if pred_ts else None
        win_len = h * 60_000
        overlap = (max(0.0, 1.0 - abs(offset_ms) / win_len)
                   if offset_ms is not None else None)
        pred_contract = p.get("targetContract") or None
        return {
            "pred_ts_ms": pred_ts,
            "pred_offset_ms": offset_ms,
            "horizon_overlap": round(overlap, 4) if overlap is not None else None,
            "pred_contract": pred_contract,
            "grading_contract": self.GRADING_CONTRACT,
            "contract_match": (pred_contract == self.GRADING_CONTRACT
                               if pred_contract else None),
            # Unknown overlap is NOT usable: a round whose forecast cannot be dated
            # cannot be shown to describe the same interval.
            "grade_usable": bool(overlap is not None
                                 and overlap >= self.MIN_HORIZON_OVERLAP),
        }

    def _direction(self, price: float, ref_price: float) -> str:
        """Polymarket BTC up/down resolution rule: UP if the price is GREATER THAN OR EQUAL
        to the reference (start) price, else DOWN. There is no NEUTRAL outcome — a tie
        resolves UP. (NEUTRAL only when we have no valid reference to anchor against.)"""
        if ref_price <= 0:
            return "NEUTRAL"
        return "UP" if price >= ref_price else "DOWN"

    @staticmethod
    def _price_at_boundary(ts_ms: int, klines, fallback: float) -> float:
        """True price AT a window boundary, recovered from 1m klines (kline `time` is in
        SECONDS). The boundary price = the OPEN of the candle starting there (or the CLOSE
        of the candle just before). Used when the ticker fires LATE (event-loop stall):
        without this, a late tick anchored/resolved the round at whatever the price was
        seconds AFTER the boundary — the operator's 'reference price isn't actual' bug."""
        if not klines:
            return fallback
        t = int(ts_ms // 1000)
        for k in reversed(list(klines)[-20:]):
            kt = int(k.get("time", 0) or 0)
            if kt == t and k.get("open"):
                return float(k["open"])
            if kt == t - 60 and k.get("close"):
                return float(k["close"])
            if kt < t - 120:
                break
        return fallback

    LATE_MS = 3000  # ticks later than this use the kline-recovered boundary price

    #: WHAT THIS MIRROR'S GRADER ANSWERS. `_direction` resolves a round on the sign of
    #: (end price - anchor) on the anchor feed. That is the venue's own rule and it is the
    #: right question for tradeability - but it is NOT the question the model was trained
    #: on. The training contract is `first_touch_triple_barrier_v1`, under which "UP" means
    #: "touches +band before -band", which is correlated with but not identical to "ends
    #: above the anchor". So every row in the win-rate strip is a CROSS-CONTRACT grade.
    #: Nothing here changes the grading; the contract on both sides is now recorded so the
    #: number is labelled with the question it answers.
    GRADING_CONTRACT = "endpoint_sign_vs_anchor_v1"

    #: A prediction and a round are each `h` minutes long. Offset them by `a` and they
    #: share (1 - a/h) of their window; a round graded against a prediction that barely
    #: overlaps it is measuring a forecast of a different interval. `_ptb_preds` holds
    #: whatever the heavy loop last produced, so this offset is normally one cycle (a few
    #: seconds) and occasionally much larger - a retrain, a throttled machine, a stall.
    MIN_HORIZON_OVERLAP = 0.90

    def update(self, now_ms: int, ref_price: float, predictions_by_h: dict,
               kronos_dir_by_h: dict, klines=None, feed_fresh: bool = True, keepers=None):
        """Open new clock-aligned rounds and resolve elapsed ones. Call once per tick.

        ref_price: the live BTC/USD anchor feed chosen by the caller — currently Pyth
            (sub-second), with an offset-corrected Binance fallback when Pyth is stale. This
            anchors the window, drives the live position, and resolves the round (UP if
            end >= start). `klines` is passed as None whenever the anchor is NOT raw Binance,
            so boundary recovery never mixes feeds (same-feed rule).
        predictions_by_h: {horizon: ensemble prediction dict}.
        kronos_dir_by_h: {horizon: kronos direction string}.
        """
        if not ref_price or ref_price <= 0:
            # Can't anchor a round without a reference price; still try to resolve.
            self._resolve(now_ms, ref_price, klines)
            return

        # Feed the rolling price buffer for the live trailing-60s vol (A1 P(hold) feature).
        self._px_buf.append((int(now_ms), float(ref_price)))
        # Live volatility keepers (from server via live_keepers; None when unavailable) — used
        # by the keeper P(hold) model and the signed-quantile band, both fallback-guarded.
        self._last_keepers = keepers

        for h in self.horizons:
            win_len = h * 60_000
            win_start = (now_ms // win_len) * win_len
            win_end = win_start + win_len
            p = (predictions_by_h or {}).get(h) or {}

            if self.current_window.get(h) != win_start:
                if not feed_fresh:
                    # Frozen feed (e.g. retrain stall): an anchor taken now would be a
                    # stale price masquerading as the window open. Skip opening this
                    # round entirely — resolution/refresh below still run with kline
                    # recovery. Mark the window consumed so we don't open it mid-way.
                    self.current_window[h] = win_start
                    continue
                # Open a new clock-aligned round at the window boundary. If this tick is
                # LATE (event-loop stall), anchor at the TRUE boundary price recovered
                # from the 1m klines instead of the current (drifted) live price.
                self.current_window[h] = win_start
                late_ms = now_ms - win_start
                anchor = ref_price
                if late_ms > self.LATE_MS:
                    if not klines:
                        # Pyth has no same-feed candle from which to recover a missed
                        # boundary. Using the current tick would fabricate the anchor.
                        logger.warning(
                            "Skipping %sm %s round: settlement-feed anchor arrived %sms "
                            "late and no same-feed boundary history exists.",
                            h, self.source, int(late_ms),
                        )
                        continue
                    anchor = self._price_at_boundary(win_start, klines, ref_price)
                entry = {
                    "id": f"ptb_{'' if self.source == 'pyth' else self.source + '_'}{h}m_{win_start}",
                    "source": self.source,
                    "timestamp": win_start,
                    "horizon": h,
                    "price_to_beat": round(anchor, 2),
                    "ref_captured_late_ms": int(late_ms) if late_ms > self.LATE_MS else 0,
                    # Grade the model's directional LEAN (rawDirection) — the bet you'd
                    # place on a binary Polymarket up/down market. The final gated action
                    # (signal) is usually WAIT and can't be expressed on a binary market,
                    # so keep it as our_action for the "lean UP, action WAIT" display.
                    "our_direction": self._bet_lean(p, h),
                    # Which rule produced the lean. EVIDENCE (DuckDB 2026-06-10, 9.6h): the
                    # model's committed 3-class leans win ~64% at 5m, but the two-way
                    # probability FALLBACK leans are ~coin-flip — mixing them dragged the
                    # mirror from 58.7% to 51.5%. Track separately so the betting guidance
                    # can say "bet model leans, skip fallback leans".
                    #
                    # REFUTED 2026-08-08 on 22 days instead of 9.6 hours. The original
                    # measurement had a few hundred rounds; `research/live_round_edge_audit.py`
                    # now has 2,404 resolved 5m rounds recorded live, and the two sources are
                    # INDISTINGUISHABLE:
                    #
                    #     model    n=1228  win 0.4935  95% CI [0.4656, 0.5214]
                    #     fallback n=1176  win 0.4932  95% CI [0.4647, 0.5217]
                    #
                    # Both straddle 0.50, both sit below the ~0.52 needed to clear a 2c round
                    # trip, and a label shuffle reaches the observed rate 82% of the time. The
                    # field is KEPT - it costs nothing, it is real provenance, and it is what
                    # made the refutation measurable - but it is not a betting signal and the
                    # guidance above must not be acted on.
                    "lean_source": ("model" if p.get("rawDirection") in ("UP", "DOWN")
                                    else "fallback"),
                    # Stage-3 setup grade computed server-side (model lean + regime +
                    # flow confirmations). A ≈ the high-precision subset; C ≈ skip.
                    "confluence": p.get("confluence"),
                    "regime": p.get("regime", "UNKNOWN"),
                    "our_action": p.get("signal", p.get("direction", "NEUTRAL")),
                    "signal": p.get("signal", p.get("direction", "NEUTRAL")),
                    "conviction": float(p.get("conviction", 0.0) or 0.0),
                    "actionable": bool(p.get("actionable", False)),
                    "kronos_direction": (kronos_dir_by_h or {}).get(h) or "NONE",
                    "target_price": p.get("targetPrice"),
                    "model_bundle_id": str(p.get("model_bundle_id") or ""),
                    "verify_at": win_end,
                    "window_start": win_start,
                    "window_end": win_end,
                    "window_label": f"{_hms(win_start)}-{_hms(win_end)}",
                    # WHICH QUESTION, AND OVER WHICH INTERVAL. Both sides of the grade are
                    # recorded on the row itself: the contract the forecast was made under,
                    # the contract this mirror grades by, and how much of the model's own
                    # forecast window this round actually covers. Without them the strip
                    # published one number for two different questions over two different
                    # intervals.
                    **self._grade_provenance(p, h, win_start),
                }
                # Keep one shared round object. Live fields such as late_entry and the frozen
                # path plan must still be present when _resolve grades/persists this pending row.
                round_state = {**entry, "status": "pending"}
                self.pending.append(round_state)
                self.latest_round[h] = round_state
                if self.persist:
                    try:
                        database.log_price_to_beat(entry)
                    except Exception as e:
                        logger.debug(f"Price-to-beat log failed: {e}")

            # Live in-window decision support: refresh the OPEN round every tick so a user
            # holding a Polymarket bet can decide hold-vs-early-exit.
            rnd = self.latest_round.get(h)
            if rnd and rnd.get("status") == "pending":
                self._refresh_live(rnd, now_ms, ref_price, p, win_end)

        self._resolve(now_ms, ref_price, klines)

    def _compute_specialist_heads(self, rnd: dict):
        """Score the THROTTLED specialist heads (big-move/drop/directional/activity) onto an open
        round. Called from _refresh_live at most every _HEADS_THROTTLE_MS. Pure keeper inference on
        self._last_keepers + rnd['horizon']; each head is crash-safe. P(hold) is intentionally NOT
        here — it stays per-tick in _refresh_live because it is time-sensitive (seconds_left)."""
        # P(big_move) — servable keeper head (timing/tradability; rank-calibrated tiers, not a
        # calibrated probability). Uses ONLY the 4 parity-proven keepers, so it never needs new
        # live features. Crash-safe: any failure leaves both fields None and the card hides it.
        rnd["p_big_move"] = None
        rnd["big_move_tier"] = None
        rnd["move_buckets_usd"] = None
        rnd["move_threshold_usd"] = None
        try:
            _bm = _load_bigmove_keeper_model()
            _kp = self._last_keepers
            _hh = int(rnd.get("horizon", 5) or 5)
            _bk = _keeper_move_buckets(_bm, _hh)
            if _bk:
                rnd["move_buckets_usd"] = _bk
                rnd["move_threshold_usd"] = _bk.get("meaningful")
            _bcfg = _select_keeper_config(_bm, horizon=_hh)
            _bfeats = (_bcfg or {}).get("features") or (_bm or {}).get("features") or []
            if _bm and _kp and _bcfg and all(_kp.get(k) is not None for k in _bfeats):
                _bs = _score_keeper_head(_bm, _kp, horizon=_hh)
                if _bs is not None:
                    _t = _bcfg["tiers"]
                    rnd["p_big_move"] = round(_bs, 4)
                    rnd["big_move_tier"] = ("likely" if _bs >= _t["t3"] else "elevated" if _bs >= _t["t2"]
                                            else "moderate" if _bs >= _t["t1"] else "quiet")
        except Exception as _bme:
            logger.debug(f"big-move compute skipped: {_bme}")
        # Intra-window PATH plan (Layer-2 keeper head): a STABLE high/low band + touch odds for
        # Polymarket early-exit. Computed ONCE per window here (throttled), NOT per-tick, on the
        # same parity-proven keepers. Crash-safe: any failure leaves trade_plan None and the card hides.
        # Freeze the path plan after its first successful open-window computation. The
        # surrounding specialist heads refresh every few seconds, but recomputing this
        # forecast with later keepers while still anchoring it to the opening price would
        # mix timestamps and turn a stable plan into a moving target.
        if rnd.get("trade_plan") is None and not rnd.get("ref_captured_late_ms"):
            try:
                _pf = _load_path_forecaster()
                _kp = self._last_keepers
                _hh = int(rnd.get("horizon", 5) or 5)
                _ref = rnd.get("price_to_beat")
                if _pf and _kp and _ref and all(_kp.get(k) is not None for k in _pf.get("features", [])):
                    _plan = _predict_path_plan(_pf, _hh, _kp, float(_ref))
                    if _plan:
                        _plan["generated_at_ms"] = int(rnd.get("_heads_ts") or 0)
                        # SOFT reversal-window prior (time-of-day/weekday favorability from the backtest). Display
                        # only -- informs how selective to be; does NOT gate the fade. Crash-safe.
                        try:
                            _plan["window_quality"] = _window_quality(
                                _hh, rnd.get("window_start") or rnd.get("timestamp"))
                        except Exception:
                            _plan["window_quality"] = None
                        rnd["trade_plan"] = _plan
                        # SHADOW LOG (record-forward, no decision change): persist the frozen plan
                        # onto this round's price_to_beat row so the path head can be verified on
                        # LIVE rounds and the lift probe gets a real out-of-sample holdout. Once only.
                        if self.persist and not rnd.get("_plan_logged"):
                            try:
                                database.log_path_plan(rnd.get("id"), _plan)
                                rnd["_plan_logged"] = True
                            except Exception as _ple:
                                logger.debug(f"path-plan log failed: {_ple}")
            except Exception as _tpe:
                logger.debug(f"trade-plan compute skipped: {_tpe}")
        # P(big_drop) — downside path-risk head (parity-safe keepers; gated AUC 0.751 / top-5% 63.5%).
        # A RISK/WARNING head, NOT a trade trigger: HIGH = avoid-long / flag a DOWN-side setup.
        rnd["p_big_drop"] = None
        rnd["big_drop_risk"] = None
        try:
            _bd = _load_bigdrop_keeper_model()
            _kp = self._last_keepers
            _dcfg = _select_keeper_config(_bd, horizon=int(rnd.get("horizon", 5) or 5))
            _dfeats = (_dcfg or {}).get("features") or (_bd or {}).get("features") or []
            if _bd and _kp and _dcfg and all(_kp.get(k) is not None for k in _dfeats):
                _ds = _score_keeper_head(_bd, _kp, horizon=int(rnd.get("horizon", 5) or 5))
                if _ds is not None:
                    _dt = _dcfg["tiers"]
                    rnd["p_big_drop"] = round(_ds, 4)
                    rnd["big_drop_risk"] = ("HIGH" if _ds >= _dt["t3"] else "ELEVATED" if _ds >= _dt["t2"] else "LOW")
        except Exception as _bde:
            logger.debug(f"big-drop compute skipped: {_bde}")
        # Directional big-up/down confirmation heads. These are deliberately confirmation-only;
        # raw side prediction was weak in research, so they never authorize a trade by themselves.
        rnd["p_big_up"] = None
        rnd["p_big_down"] = None
        rnd["big_up_tier"] = None
        rnd["big_down_tier"] = None
        try:
            _dh = _load_directional_keeper_model()
            _kp = self._last_keepers
            _models = (_dh or {}).get("models") or {}
            if _dh and _kp and _models:
                for _key, _prob_key, _tier_key in [
                    ("big_up", "p_big_up", "big_up_tier"),
                    ("big_down", "p_big_down", "big_down_tier"),
                ]:
                    _cfg = _select_keeper_config(_dh, model_key=_key, horizon=int(rnd.get("horizon", 5) or 5))
                    _feats = (_cfg or {}).get("features") or (_dh or {}).get("features") or []
                    if _cfg and all(_kp.get(k) is not None for k in _feats):
                        _ps = _score_keeper_head(_dh, _kp, _key, horizon=int(rnd.get("horizon", 5) or 5))
                        if _ps is not None:
                            rnd[_prob_key] = round(_ps, 4)
                            rnd[_tier_key] = _tier(_ps, _cfg.get("tiers", {}))
        except Exception as _de:
            logger.debug(f"directional keeper compute skipped: {_de}")
        # Activity/range head: deployable proxy for volume/activity until a future-volume
        # matrix exists. It predicts unusually active 5m range, not exact volume.
        rnd["p_activity"] = None
        rnd["activity_tier"] = None
        try:
            _ah = _load_activity_keeper_model()
            _kp = self._last_keepers
            _acfg = _select_keeper_config(_ah, horizon=int(rnd.get("horizon", 5) or 5))
            _afeats = (_acfg or {}).get("features") or (_ah or {}).get("features") or []
            if _ah and _kp and _acfg and all(_kp.get(k) is not None for k in _afeats):
                _as = _score_keeper_head(_ah, _kp, horizon=int(rnd.get("horizon", 5) or 5))
                if _as is not None:
                    _at = _acfg["tiers"]
                    rnd["p_activity"] = round(_as, 4)
                    rnd["activity_tier"] = ("likely" if _as >= _at["t3"] else "elevated" if _as >= _at["t2"]
                                            else "moderate" if _as >= _at["t1"] else "quiet")
        except Exception as _ae:
            logger.debug(f"activity keeper compute skipped: {_ae}")

    def _refresh_live(self, rnd: dict, now_ms: int, ref_price: float, p: dict, win_end: int):
        """Attach live intra-window state + hold/exit advice to an open round."""
        ptb = rnd.get("price_to_beat") or 0.0
        cur_move = round(ref_price - ptb, 2)
        cur_pos = self._direction(ref_price, ptb)          # where it stands RIGHT NOW (strict)
        # Model's CURRENT lean — must use the SAME _bet_lean rule that opened the bet
        # (two-way prob fallback). Using rawDirection here while the bet used _bet_lean made
        # the advice see "lean faded to NEUTRAL" on nearly every tick (rawDirection is usually
        # NEUTRAL at 5-15m) and wrongly push LOCK-IN/EXIT while the model still favored the side.
        live_lean = self._bet_lean(p)
        live_exp = p.get("expectedMove")                   # model's expected move (magnitude)
        secs_left = max(0, int((win_end - now_ms) // 1000))
        rnd["current_price"] = round(ref_price, 2)
        rnd["current_move"] = cur_move                     # signed $ vs price-to-beat
        rnd["current_position"] = cur_pos                  # UP/DOWN/NEUTRAL right now
        rnd["seconds_left"] = secs_left
        rnd["regime"] = p.get("regime", rnd.get("regime", "UNKNOWN"))
        rnd["live_lean"] = live_lean
        rnd["live_expected_move"] = (round(float(live_exp), 2) if live_exp is not None else None)
        # Round-state SHADOW inputs. Historical labels use completed 30-second
        # observations, so side occupancy/recrosses are sampled at the same cadence;
        # high/low travel is updated every tick to retain intra-bucket extremes.
        _rs = rnd.setdefault("_round_state_live", {
            "run_high": float(ref_price), "run_low": float(ref_price),
            "last_sample_ms": 0, "last_side": None, "samples": 0,
            "above_samples": 0, "recrosses": 0,
        })
        _rs["run_high"] = max(float(_rs.get("run_high") or ref_price), float(ref_price))
        _rs["run_low"] = min(float(_rs.get("run_low") or ref_price), float(ref_price))
        if not _rs.get("last_sample_ms") or now_ms - int(_rs["last_sample_ms"]) >= 30_000:
            if _rs.get("last_side") in ("UP", "DOWN") and _rs["last_side"] != cur_pos:
                _rs["recrosses"] = int(_rs.get("recrosses") or 0) + 1
            _rs["last_side"] = cur_pos
            _rs["last_sample_ms"] = int(now_ms)
            _rs["samples"] = int(_rs.get("samples") or 0) + 1
            if cur_pos == "UP":
                _rs["above_samples"] = int(_rs.get("above_samples") or 0) + 1
        # ── A1 / T3 P(hold): calibrated prob the side price is CURRENTLY ahead on holds to
        # close. Mirrors the offline label (pos==actual) and feature recipe EXACTLY:
        # abs_distance_pct, seconds_left, vol_60s_pct (trailing-60s std/anchor*100), horizon,
        # dist_vol_ratio. Crash-safe — any failure leaves p_hold None and the card + the ⚡
        # late-entry gate fall back to the pre-existing heuristic. Separate frozen head.
        p_hold = None
        rnd["vol_60s_pct"] = None
        try:
            mdl = _load_persistence_model()
            if mdl and ptb > 0 and secs_left > 0:
                cutoff = now_ms - 60_000
                seg = [px for (t, px) in self._px_buf if t >= cutoff]
                if len(seg) > 2:
                    vol_60s_pct = float(np.std(seg) / ptb * 100.0)
                    rnd["vol_60s_pct"] = round(vol_60s_pct, 5)   # persisted in the A1 snapshot
                    abs_dist_pct = abs(cur_move) / ptb * 100.0
                    dist_vol_ratio = abs_dist_pct / (vol_60s_pct + 1e-6)
                    fvals = {
                        "abs_distance_pct": abs_dist_pct,
                        "seconds_left": float(secs_left),
                        "vol_60s_pct": vol_60s_pct,
                        "horizon": float(rnd.get("horizon", 5) or 5),
                        "dist_vol_ratio": dist_vol_ratio,
                    }
                    # Keeper model (validated +0.019 AUC on the late T3 region): use it when the
                    # live volatility keepers are available this tick; else fall back to the base
                    # 5-feature model — never breaks if keepers/keeper-model absent.
                    _feats, _clf, _iso, _src = mdl["features"], mdl["clf"], mdl["iso"], "base"
                    _kp = self._last_keepers
                    _kf = ("rv_15m", "rv_30m", "rv_60m", "vpin", "compression_ratio", "shock_magnitude")
                    if (mdl.get("clf_keeper") is not None and _kp
                            and all(k in _kp and _kp[k] is not None for k in _kf)):
                        fvals.update({k: float(_kp[k]) for k in _kf})
                        _feats, _clf, _iso, _src = (mdl["features_keeper"], mdl["clf_keeper"],
                                                    mdl["iso_keeper"], "keeper")
                    feats = [[fvals[k] for k in _feats]]
                    raw = _clf.predict_proba(feats)[:, 1]
                    # GLOBAL isotonic: per-horizon iso was MEASURED a wash offline and slightly worse on
                    # 5m/15m (train_persistence_model diagnostic) -> the global mapping is already well
                    # calibrated per-horizon; live 1m drift is a train/serve gap, cured by retraining on
                    # fresher data, not a mapping swap. (bundle still carries iso_by_horizon for diagnostics.)
                    p_hold = float(_iso.predict(raw)[0])
                    rnd["p_hold_source"] = _src
        except Exception as _pe:
            logger.debug(f"P(hold) compute skipped: {_pe}")
        rnd["p_hold"] = (round(p_hold, 4) if p_hold is not None else None)
        # ── Layer-3 SIMILAR-SETUP MEMORY ("markets like this"): once per round, on entering the late
        # window, look up this exact live state (horizon/seconds-left/P(hold)/lead/regime) in the app's
        # OWN graded ledger and surface n / held% / Wilson-LB. Validated vs the global bucket on a
        # temporal holdout (Brier 0.0483 vs 0.0515, calibrated quartiles). Evidence, not a prediction;
        # crash-safe; recomputed once more if P(hold) has moved a lot since the first lookup.
        try:
            if (self.persist and p_hold is not None and 10 <= secs_left <= 150
                    and cur_pos in ("UP", "DOWN")
                    and (rnd.get("_simstats_ph") is None
                         or abs(p_hold - rnd["_simstats_ph"]) >= 0.10)):
                rnd["_simstats_ph"] = p_hold
                _ss = database.similar_setup_stats(int(rnd.get("horizon", 5) or 5), float(secs_left),
                                                   float(p_hold), float(cur_move or 0.0),
                                                   str(rnd.get("regime") or ""))
                rnd["similar_setups"] = _ss    # None when <30 neighbors even relaxed -> UI hides it
        except Exception as _se:
            logger.debug(f"similar-setup lookup skipped: {_se}")
        # ── Specialist heads (big-move / big-drop / directional / activity): THROTTLED. Their
        # 4-model ensembles are the bulk of the per-tick CPU but barely move second-to-second, so
        # recompute at most every _HEADS_THROTTLE_MS PER ROUND (last values stay on rnd between).
        # This keeps the asyncio event loop free so the live price stays realtime. P(hold) above +
        # the band/champion below stay per-tick. First tick of a round always computes (ts is None).
        if (rnd.get("_heads_ts") is None
                or (now_ms - int(rnd.get("_heads_ts") or 0)) >= _HEADS_THROTTLE_MS):
            rnd["_heads_ts"] = now_ms
            self._compute_specialist_heads(rnd)
        # Path-plan LIVE touch state (early-exit): track the window's running high/low and, once price
        # touches the +/-$FADE_L fade barrier, freeze the touch timing and surface the validated
        # reversal/continuation read. Additive + crash-safe; mutates only trade_plan["touch_state"],
        # never the frozen predictions. Per-tick (cheap comparisons).
        _pl = rnd.get("trade_plan")
        if isinstance(_pl, dict) and ptb > 0 and ref_price > 0:
            rnd["_tp_run_hi"] = max(rnd.get("_tp_run_hi") or ref_price, ref_price)
            rnd["_tp_run_lo"] = min(rnd.get("_tp_run_lo") or ref_price, ref_price)
            _hz = int(rnd.get("horizon", 5) or 5)
            _uh = (rnd["_tp_run_hi"] - ptb) >= FADE_L      # up-spike reached anchor+$FADE_L (Polymarket-relevant)
            _dh = (ptb - rnd["_tp_run_lo"]) >= FADE_L      # down-spike reached anchor-$FADE_L
            # LEG 1 = the FIRST fadeable spike (either side) at the $FADE_L barrier.
            if (_uh or _dh) and rnd.get("_tp_touch_secs") is None:
                rnd["_tp_touch_secs"] = secs_left          # freeze WHEN the first touch happened
                rnd["_tp_touch_side"] = ("HIGH" if (rnd["_tp_run_hi"] - ptb) >= (ptb - rnd["_tp_run_lo"]) else "LOW")
                rnd["_tp_touch_pre_hi"] = rnd["_tp_run_hi"]   # freeze pre-touch hi/lo for the fade features
                rnd["_tp_touch_pre_lo"] = rnd["_tp_run_lo"]
            # LEG 2 = the OPPOSITE-side spike AFTER leg 1 = the round-trip RETURN (the two-sided play): price
            # spiked one way (faded), reverted through the anchor, now spikes the other way -> fade again.
            if rnd.get("_tp_touch_secs") is not None and rnd.get("_tp_leg2_secs") is None:
                _opp_hit = _dh if rnd.get("_tp_touch_side") == "HIGH" else _uh
                if _opp_hit:
                    rnd["_tp_leg2_secs"] = secs_left
                    rnd["_tp_leg2_side"] = "LOW" if rnd.get("_tp_touch_side") == "HIGH" else "HIGH"
            if rnd.get("_tp_touch_secs") is not None:
                try:
                    _both = bool(_uh and _dh)
                    _pl["touch_state"] = _path_touch_state(_pl, rnd.get("_tp_touch_side"),
                                                           int(rnd["_tp_touch_secs"]), _hz, _both)
                    # LEG 1 honest fade grade at $FADE_L: P(this early touch reaches the anchor TP before the 2L stop).
                    _pf1 = _grade_fade(rnd.get("_tp_touch_side"), int(rnd["_tp_touch_secs"]),
                                       float(rnd["_tp_touch_pre_hi"]), float(rnd["_tp_touch_pre_lo"]),
                                       ptb, _hz, self._last_keepers, L=FADE_L)
                    if _pf1 is not None:
                        _pl["touch_state"]["p_fade"] = _pf1
                    # LEG 2 honest fade grade (the round-trip return). Graded from the CURRENT running hi/lo at the
                    # opposite touch, which correctly encodes leg 1's spike as the 'stretched spring' pre_opp
                    # feature (a big opposite excursion reverts harder) -- exactly as train_fade_model builds it.
                    if rnd.get("_tp_leg2_secs") is not None:
                        _pf2 = _grade_fade(rnd.get("_tp_leg2_side"), int(rnd["_tp_leg2_secs"]),
                                           float(rnd["_tp_run_hi"]), float(rnd["_tp_run_lo"]),
                                           ptb, _hz, self._last_keepers, L=FADE_L)
                        _pl["touch_state"]["leg2"] = {
                            "side": rnd.get("_tp_leg2_side"),
                            "fade": ("DOWN" if rnd.get("_tp_leg2_side") == "HIGH" else "UP"),
                            "p_fade": _pf2,
                            "call": ("Round-trip RETURN paper candidate — the opposite side touched after leg 1. "
                                     "Record the second leg's ask, exit bid, fees, and outcome."
                                     if (_pf2 is None or _pf2 >= 0.45) else
                                     "Round-trip return, but the model says the 2nd leg is unlikely to revert — skip.")}
                except Exception:
                    pass
            # Unified ENTRY/EXIT signal composed from the validated edges (late-entry hold + chop fade).
            try:
                _pl["trade_signal"] = _trade_signal(_pl, rnd.get("p_hold"), cur_pos, cur_move,
                                                    secs_left, int(rnd.get("horizon", 5) or 5), ptb)
            except Exception:
                pass
        # Magnitude FORECAST for the Polymarket card: the model's directional move-size
        # regressor (conformal low/median/high band) projected onto a close estimate vs
        # the price-to-beat. Lets the bettor see "expected drop/rise of ~$X" and whether
        # that PROJECTS to clear the line, not just the UP/DOWN sign.
        _emr = p.get("expectedMoveRange") or {}
        if _emr.get("low") is not None and _emr.get("high") is not None:
            rnd["expected_move_range"] = {
                "low": round(float(_emr["low"]), 2),
                "median": round(float(_emr.get("median") or live_exp or 0.0), 2),
                "high": round(float(_emr["high"]), 2),
            }
            # Signed projection: which way the model leans × the magnitude, added to NOW.
            _sgn = 1.0 if live_lean == "UP" else -1.0 if live_lean == "DOWN" else 0.0
            if _sgn != 0.0 and live_exp is not None:
                rnd["projected_close"] = round(ref_price + _sgn * abs(float(live_exp)), 2)
                rnd["projected_vs_beat"] = round(rnd["projected_close"] - ptb, 2)
                # p_up / "fair value" REMOVED (operator 2026-06-13): it was Φ(edge/σ)
                # built on the FLAT ~$40 mean-move magnitude — fake-precise cents on an
                # unreliable base, misleading rather than useful. The proper p_up
                # returns with A3 (conditional quantile magnitude that breathes with
                # volatility) → A2, in Retrain #2. Until then we show no probability.
        # Signed-quantile band override (A3): calibrated ASYMMETRIC expected drop/high + a
        # no-drift projection. Uses the live keepers; falls back to the symmetric band above
        # when the head/keepers are absent. Median≈0 stops the manufactured lean-direction
        # drift that caused the "DOWN lean projects above the line" contradiction.
        try:
            _sq = _load_signed_quantile_model()
            _kp = self._last_keepers
            _hh = int(rnd.get("horizon", 5) or 5)
            if (_sq and _kp and _hh in _sq.get("models", {})
                    and all(f in _kp and _kp[f] is not None for f in _sq["features"])):
                _xv = [[float(_kp[f]) for f in _sq["features"]]]
                _m = _sq["models"][_hh]
                _cqr = float(_m.get("cqr", 0.0))
                _drop = ref_price * (float(_m["q10"].predict(_xv)[0]) - _cqr) / 1e4
                _med = ref_price * float(_m["q50"].predict(_xv)[0]) / 1e4
                _high = ref_price * (float(_m["q90"].predict(_xv)[0]) + _cqr) / 1e4
                rnd["expected_move_range"] = {"low": round(_drop, 2),
                                              "median": round(_med, 2), "high": round(_high, 2)}
                rnd["projected_close"] = round(ref_price + _med, 2)
                rnd["projected_vs_beat"] = round(rnd["projected_close"] - ptb, 2)
                rnd["band_source"] = "signed_quantile"
        except Exception as _be:
            logger.debug(f"signed band skipped: {_be}")
        # PATH OUTLOOK — a plain-English "how will price travel vs the line" forecast:
        # built from the model's expected move vs the distance to the line, with odds from
        # MEASURED precision (expectedPrecision) when available, else the two-way prob.
        rnd["path_outlook"] = self._path_outlook(
            cur_move, cur_pos, live_lean, p, rnd.get("lean_source"), rnd.get("p_hold"))
        rnd["advice"] = self._advice(rnd.get("our_direction", "NEUTRAL"),
                                     cur_pos, live_lean, secs_left, cur_move, rnd.get("p_hold"))
        # COHERENCE override (operator-caught, 2026-06-12): when the path outlook says
        # STRETCH — expected travel can't plausibly cover the gap to the line — the
        # advice must NOT say "hold, reversal possible". The lean can be RIGHT about
        # direction and still unable to cross in time; for THIS binary window that is
        # a losing hold, and the math two boxes up already says so. Keep the boxes
        # agreeing with each other.
        _po = rnd.get("path_outlook") or {}
        if _po.get("scenario") == "STRETCH" and isinstance(rnd.get("advice"), dict):
            _gap = abs(float(cur_move or 0.0))
            _trav = abs(float(live_exp)) if live_exp is not None else 0.0
            # SIDE-AWARE (operator-caught bug, 2026-06-13): the outlook describes the
            # LIVE lean. If that stretched lean IS the bet side → it can't cross →
            # exit/skip. But if the bet side is the OPPOSITE one (lean flipped while
            # the bet is ahead), the stretch means the OPPONENT is counted out — the
            # held side is near-certain. The old text told a winning DOWN holder
            # (ahead $91) to "exit; do not enter". Exactly backwards.
            if rnd.get("our_direction") == live_lean:
                rnd["advice"]["action"] = "EXIT / SKIP"
                rnd["advice"]["tone"] = "bad"
                rnd["advice"]["text"] = (
                    f"Counted out for THIS window: typical travel ~${_trav:.0f} vs a "
                    f"${_gap:.0f} gap with {secs_left}s left — the lean may be right about "
                    f"direction but cannot cross the line in time. Exit if holding; do not enter."
                )
            elif (rnd.get("our_direction") in ("UP", "DOWN")
                  and live_lean in ("UP", "DOWN") and rnd.get("our_direction") != live_lean):
                rnd["advice"]["action"] = "HOLD — opponent counted out"
                rnd["advice"]["tone"] = "good"
                rnd["advice"]["text"] = (
                    f"Your {rnd['our_direction']} side leads by ${_gap:.0f} and the flipped "
                    f"{live_lean} lean only expects ~${_trav:.0f} of travel with {secs_left}s "
                    f"left — it cannot cross back in time. Strong hold."
                )
        # Weak-lean warning: a "fallback" lean is the two-way tilt of an otherwise-NEUTRAL
        # model — live evidence shows it's near coin-flip, unlike committed model leans.
        if rnd.get("lean_source") == "fallback" and isinstance(rnd.get("advice"), dict):
            rnd["advice"]["text"] = ("[Weak lean — model is near-neutral, side from probability "
                                     "tilt only. Best skipped as a bet.] ") + rnd["advice"].get("text", "")
        # Setup grade (Stage 3): surfaced in the advice so the bettor sees signal quality.
        # LABELED as "live": this is the CURRENT prediction's grade and can legitimately
        # differ from the grade-at-open shown in the card header (flows shift mid-round).
        # Unlabeled, the two looked like a contradiction (header C vs advice A).
        cfl = p.get("confluence") or rnd.get("confluence")
        if isinstance(cfl, dict) and isinstance(rnd.get("advice"), dict):
            _open_g = (rnd.get("confluence") or {}).get("grade") if isinstance(rnd.get("confluence"), dict) else None
            _live_g = cfl.get("grade")
            _delta = (f", opened {_open_g}" if _open_g and _open_g != _live_g else "")
            rnd["advice"]["text"] += f" [Live grade {_live_g} ({cfl.get('score')}/5{_delta}).]"
        # Stage-4 LATE ENTRY: with little time left and price already ahead on the leaned
        # side, the bet becomes "no reversal in N seconds" — conditional win probability is
        # far higher than at window open (the most reliable 80%+ mechanism that exists).
        # The share price reflects some of this; the flag marks the favorable structure.
        # Late-entry window scales with the horizon (fixed 15-120s covered nearly a
        # whole 1m round once the 1m/3m practice mirrors were added): final ~40% of
        # the window, capped at 120s; min-ahead distance softer for the fast mirrors.
        _h_secs = int(rnd.get("horizon", 5)) * 60
        _late_win = min(120, int(_h_secs * 0.4))
        _min_ahead = 10.0 if rnd.get("horizon", 5) >= 5 else 5.0
        # ⚡ gated to COMMITTED model leans (2026-06-13): flagging late-entry on a
        # ⚠ fallback lean put "persistence odds strongly favor X" on a card whose
        # own badge says "WEAK — skip". Contradictory coaching; model leans only.
        # CALIBRATED gate (2026-06-13): the structural heuristic (window/min-ahead) is now
        # a PRE-FILTER; the ⚡ flag fires only when the A1/T3 model says the currently-ahead
        # side holds with calibrated P(hold) >= 0.93 (its honest 95%-tier — 95.3% realized
        # at ~30% coverage on unseen test). If P(hold) is unavailable (model absent / buffer
        # < 3 ticks at boot), ⚡ stays OFF rather than firing on the weaker heuristic alone.
        late = (live_lean in ("UP", "DOWN") and cur_pos == live_lean
                and rnd.get("lean_source") == "model"
                and 15 < secs_left <= _late_win and abs(cur_move) >= _min_ahead
                and p_hold is not None and p_hold >= 0.93)
        rnd["late_entry"] = bool(late)
        if late and isinstance(rnd.get("advice"), dict):
            rnd["advice"]["text"] += (f" [LATE-ENTRY WINDOW: {secs_left}s left, already "
                                      f"{cur_move:+.0f} on the {live_lean} side — calibrated "
                                      f"P(hold)={p_hold*100:.0f}% favors {live_lean} holding to close.]")
        # T2/T3 PRECISION TIER + proof panel (the precision card). `late` already requires the
        # structural late-entry zone + committed model lean + P(hold)>=0.93, so it is at least T2.
        # T3 (the high-precision, surfaceable tier) additionally requires the HISTORICAL proof to
        # clear the gate: n>=100 AND hold>=90% AND Wilson-LB>=80% (phold_tier.json). Crash-safe.
        rnd["tier"] = None
        rnd["tier_proof"] = None
        if late:
            try:
                _tiers = (_load_tier_proof().get("tiers") or {})
                _ph = _tiers.get(str(rnd.get("horizon"))) or _tiers.get(rnd.get("horizon")) or {}
                _t2 = _ph.get("T2_structural") or {}
                _t3 = _ph.get("T3_phold>=0.93") or {}
                _is_t3 = (int(_t3.get("n") or 0) >= 100
                          and float(_t3.get("hold_pct") or 0) >= 90.0
                          and float(_t3.get("wilson_lb") or 0) >= 80.0)
                rnd["tier"] = "T3" if _is_t3 else "T2"
                rnd["tier_proof"] = {"t2": _t2, "t3": _t3}
            except Exception as _te:
                logger.debug(f"T2/T3 tier proof skipped: {_te}")
        # (EARLY-EXIT cents hint removed with p_up — same flat-magnitude basis. The
        # A13 exit guidance returns once A3/A2 give a trustworthy probability.)
        # ── CHAMPION DECISION VALIDATOR (Final Plan §3/P5) ────────────────────────
        # Strict rules-first combiner of every head above → one honest ACTION + reason.
        # The standalone recorder publishes a lock-free atomic quote snapshot. Only an exact
        # round match fresher than five seconds unlocks the fee-adjusted PAPER_BET edge gate.
        try:
            market_quote = _market_quote_for_round(rnd, now_ms)
            rnd["market_quote"] = market_quote
            rnd["share_prices"] = _live_share_prices_for_round(rnd, now_ms)
            rnd["champion"] = decision_champion.champion_decision(rnd, market=market_quote)
        except Exception as _ce:
            logger.debug(f"champion decision skipped: {_ce}")
            rnd["share_prices"] = None
            rnd["champion"] = None
        # Round-state decision-support snapshot. It consumes the Champion verdict
        # read-only and never feeds back into Champion, signal, tier, or trade gates.
        try:
            _rs_values = {
                **(self._last_keepers or {}),
                "seconds_left": float(secs_left),
                "distance_usd": float(cur_move),
                "abs_distance_usd": abs(float(cur_move)),
                "range_so_far_usd": float(_rs["run_high"] - _rs["run_low"]),
                "recrosses_so_far": float(_rs.get("recrosses") or 0),
                "time_above_so_far": (
                    float(_rs.get("above_samples") or 0) / max(1, int(_rs.get("samples") or 0))
                ),
                "current_side_up": 1.0 if cur_pos == "UP" else 0.0,
            }
            _last_score_ms = int(rnd.get("_round_state_score_ms") or 0)
            if not rnd.get("_round_state_scores") or now_ms - _last_score_ms >= _HEADS_THROTTLE_MS:
                _scores = round_state_panel.score_snapshot(int(rnd.get("horizon") or 5), _rs_values)
                if rnd.get("ref_captured_late_ms"):
                    for _score in _scores.values():
                        _score["probability"] = None
                        _score["status"] = "invalid_late_anchor"
                elif not (30 <= secs_left <= 120):
                    for _score in _scores.values():
                        _score["probability"] = None
                        _score["status"] = "outside_validated_final_30_120s"
                rnd["_round_state_scores"] = _scores
                rnd["_round_state_score_ms"] = int(now_ms)
            _opportunity = rnd.get("_round_state_opportunity")
            if not _opportunity or _opportunity.get("probability") is None:
                _opportunity = round_state_panel.score_opportunity(
                    int(rnd.get("horizon") or 5), self._last_keepers)
                if _opportunity.get("probability") is not None:
                    rnd["_round_state_opportunity"] = _opportunity
            rnd["round_state"] = round_state_panel.compose(
                rnd,
                rnd.get("_round_state_scores") or {},
                _opportunity or {"probability": None, "status": "unavailable"},
            )
            # Timestamp the fully composed state, not only the slower shadow-head score. P(hold),
            # side, distance and seconds-left are refreshed on this tick and did not all exist at
            # `_round_state_score_ms`; using that older time would misstate feature causality.
            rnd["_round_state_composed_ms"] = int(now_ms)
        except Exception as _rse:
            logger.debug(f"round-state shadow skipped: {_rse}")
            rnd["round_state"] = None
        # Complete-trade forecaster is a separate SHADOW/PILOT lane. It predicts
        # execution/path distributions from the fresh full ladders and can only
        # return NO_TRADE until its recorder and M0 evidence gates pass. It never
        # feeds Champion, our_direction, advice, or any production decision field.
        try:
            rnd["complete_trade_forecast"] = complete_trade_forecaster.score_round(
                rnd, rnd.get("share_prices"), int(now_ms)
            )
        except Exception as _ctf:
            logger.debug(f"complete-trade shadow skipped: {_ctf}")
            rnd["complete_trade_forecast"] = {
                "mode": "SHADOW_PILOT_ONLY",
                "status": "ERROR_FAIL_CLOSED",
                "action": "NO_TRADE",
                "plain_reason": "Complete-trade shadow unavailable; Champion is unchanged.",
            }
        # ── A1 persistence recorder (2026-06-13) ──────────────────────────────────
        # Log intra-window snapshots (distance to line, seconds left, side) for the
        # late-entry / T3 persistence model. Pyth tracker only (the settlement feed);
        # deduped to ~15s per round; label derived at TRAIN time by joining round_id ->
        # price_to_beat.actual_direction (no resolution hook — same pattern as B1).
        if self.persist and self.source == "pyth":
            _rid = rnd.get("id")
            if _rid and (now_ms - self._last_snap.get(_rid, 0)) >= 15000:
                self._last_snap[_rid] = now_ms
                try:
                    database.log_persistence_snapshot(
                        _rid, int(rnd.get("horizon", 0) or 0), int(now_ms),
                        int(secs_left), float(cur_move), cur_pos,
                        vol_60s_pct=rnd.get("vol_60s_pct"), p_hold=rnd.get("p_hold"))
                except Exception as _se:
                    logger.debug(f"A1 persistence snapshot skipped: {_se}")
                try:
                    rnd["head_identity"] = _active_head_identity()
                    database.log_champion_snapshot(rnd, int(now_ms))
                except Exception as _ce:
                    logger.debug(f"Champion snapshot skipped: {_ce}")
                # Round-state shadow-panel probabilities (flip/shock/opportunity) for later
                # live calibration. Outcomes join by round_id -> price_to_beat.actual_direction.
                try:
                    database.log_round_state_snapshot(rnd, int(now_ms))
                except Exception as _rse2:
                    logger.debug(f"Round-state snapshot skipped: {_rse2}")
            _paper_entries_allowed = not bool(rnd.get("_paper_entries_disabled"))
            # ── FORWARD PAPER LEDGER for the frozen rule LATE_LEADER_30S_V1 (2026-07-02): at ~30s
            # left in a 5m round, buy the MARKET's leader at its executable ask, skip ask<0.60,
            # one entry per round, hold to settlement. Evaluated ONCE per round from the same
            # fresh bridge quote the champion sees; SKIP/NO_QUOTE rows keep denominators honest.
            # This ledger IS the live validation of the rule -- no thresholds may be re-tuned.
            if (_paper_entries_allowed and int(rnd.get("horizon", 0) or 0) == 5 and 20 <= secs_left <= 32
                    and not rnd.get("_ll30_eval") and rnd.get("id")):
                rnd["_ll30_eval"] = True
                try:
                    _lq = _leader_quote(rnd, now_ms)
                    if _lq is None:
                        database.log_rule_paper_trade(rnd["id"], "LATE_LEADER_30S_V1", int(now_ms),
                                                      5, "", 0.0, 0.0, 0.0, 0.0, 0.0, "NO_QUOTE")
                    else:
                        _act = ("NO_DEPTH" if _lq["depth"] < 1.0 else
                                "ENTER" if 0.60 <= _lq["ask"] < 0.97
                                else "SKIP_LOW_ASK" if _lq["ask"] < 0.60 else "SKIP_HIGH_ASK")
                        database.log_rule_paper_trade(rnd["id"], "LATE_LEADER_30S_V1", int(now_ms),
                                                      5, _lq["side"], _lq["ask"], _lq["bid"],
                                                      _lq["fee"], _lq["spread"], _lq["depth"], _act,
                                                      btc_entry=rnd.get("current_price"))
                except Exception as _lle:
                    logger.debug(f"LATE_LEADER_30S paper log skipped: {_lle}")
            # ── PM_CALIBRATED_FAIR_VALUE_FORWARD_BENCHMARK_V1: buy the leader ONLY when a
            # CALIBRATED P(hold) exceeds the executable ask plus fee. Raw P(hold) cannot support
            # this comparison - it says 96% where 89% realizes, so every "cheap" call it makes
            # is biased by ~7 points.
            #
            # EVIDENCE: NONE. This rule once cited +0.0430/$1 with a day-block LCB of +0.0164
            # over 2 of 3 splits. That study joined a market state to a quote without requiring
            # the state to exist at the decision instant - 93.5% of its rows used a state from
            # AFTER the decision - and is RETRACTED. Rebuilt causally it is 0 of 3 with negative
            # bounds throughout. What runs here is a FROZEN BENCHMARK whose only purpose is to
            # emit causally recorded forward decisions; it is not a candidate and carries no
            # capital authority. See research/research_status.py.
            #
            # INERT until the source artifacts carry identity manifests: the challenger marks
            # its calibrators SOURCE_MODEL_REQUIRES_RETRAINING (12/12 artifacts fail identity),
            # and calibration_loader.load() returns None for a non-deployable file. A calibrator
            # for a model whose identity cannot be proven is not deployable. It then logs
            # CAL_UNAVAILABLE and takes no position - that row is deliberate, because a strategy
            # that is silently dead looks alive, which is a failure this repository has hit.
            # Window read from the strategy module, not restated here: it is part of the frozen
            # policy and feeds the policy hash, so a literal copy would let the recorded
            # provenance describe a window the code no longer uses.
            if (_paper_entries_allowed
                    and _pm_fv.EVAL_MIN_SECONDS_LEFT <= secs_left <= _pm_fv.EVAL_MAX_SECONDS_LEFT
                    and int(rnd.get("horizon", 0) or 0) in (5, 15)
                    and not rnd.get("_pmcfv_eval") and rnd.get("id")):
                rnd["_pmcfv_eval"] = True
                try:
                    _h = int(rnd.get("horizon") or 0)
                    _cal = _pm_calibration(_h)
                    # P(hold) describes the side currently ahead versus the BTC anchor. Price
                    # that SAME contract. The market's higher-bid token can temporarily be the
                    # opposite side; pairing its ask with this probability is a cross-contract
                    # logic error, not an arbitrage signal.
                    _lq = _side_quote(rnd, now_ms, rnd.get("current_position"))
                    _raw_hold = (rnd.get("round_state") or {}).get("p_leader_holds")
                    # Every branch below writes BOTH the paper row and one atomic causal ledger
                    # row, in the same block that made the decision, with the timestamps that
                    # decided it. No research script should ever again reconstruct which state
                    # belonged to which quote - that reconstruction is what invalidated five
                    # studies, with the state postdating the decision in 93.5% of rows.
                    #
                    # UNAVAILABLE and NO_QUOTE are separate ledger actions, never merged into
                    # WAIT: "declined" and "could not evaluate" produce the same empty P&L and
                    # mean opposite things.
                    if _cal is None:
                        database.log_rule_paper_trade(
                            rnd["id"], _PM_STRATEGY_ID, int(now_ms), _h,
                            "", 0.0, 0.0, 0.0, 0.0, 0.0, "CAL_UNAVAILABLE")
                        _pm_ledger_record(rnd, int(now_ms), _h, _lq, "UNAVAILABLE",
                                          "no deployable P(hold) calibrator "
                                          "(SOURCE_MODEL_REQUIRES_RETRAINING)", None)
                    elif _lq is None or _raw_hold is None:
                        database.log_rule_paper_trade(
                            rnd["id"], _PM_STRATEGY_ID, int(now_ms), _h,
                            "", 0.0, 0.0, 0.0, 0.0, 0.0,
                            "NO_QUOTE" if _lq is None else "NO_PHOLD")
                        _pm_ledger_record(rnd, int(now_ms), _h, _lq,
                                          "NO_QUOTE" if _lq is None else "BLOCKED",
                                          "no executable quote" if _lq is None
                                          else "round state carries no p_leader_holds", None)
                    else:
                        _state_ms = int(rnd.get("_round_state_composed_ms") or 0)
                        if not _state_ms or not database.log_round_state_snapshot(rnd, _state_ms):
                            database.log_rule_paper_trade(
                                rnd["id"], _PM_STRATEGY_ID, int(now_ms), _h,
                                "", 0.0, 0.0, 0.0, 0.0, 0.0, "STATE_UNAVAILABLE")
                            _pm_ledger_record(
                                rnd, int(now_ms), _h, _lq, "BLOCKED",
                                "exact composed state snapshot could not be persisted", None)
                            raise RuntimeError("exact composed state snapshot unavailable")
                        # The feature vector is built ONCE and both consumed by decide() and
                        # hashed into the ledger row. Rebuilding it for the hash would let the
                        # recorded provenance describe inputs the decision never saw.
                        _fvals = {"horizon": _h, "seconds_left": int(secs_left),
                                  "leader_side": str(_lq["side"]),
                                  "raw_p_hold": float(_raw_hold), "ask": float(_lq["ask"]),
                                  "bid": float(_lq["bid"]), "fee": float(_lq["fee"])}
                        _decision_ms = int(time.time() * 1000)
                        _d = _pm_fv.decide(_pm_fv.Quote(
                            round_id=str(rnd["id"]), timestamp_ms=_decision_ms, **_fvals), _cal)
                        database.log_rule_paper_trade(
                            rnd["id"], _PM_STRATEGY_ID, _decision_ms, _h,
                            _lq["side"],
                            _lq["ask"], _lq["bid"], _lq["fee"], _lq["spread"], _lq["depth"],
                            _d.action.value, btc_entry=rnd.get("current_price"))
                        _pm_ledger_record(
                            rnd, _decision_ms, _h, _lq,
                            "ENTER" if _d.action is _pm_fv.Action.ENTER else "WAIT",
                            _d.reason, _d.p_cal,
                            calibration=_cal, feature_values=_fvals)
                except _pm_fv.CalibrationRefused as _cre:
                    # The leakage guard fired. Never downgrade this to a trade.
                    logger.warning(f"PM_CALIBRATED_FAIR_VALUE refused (leakage guard): {_cre}")
                except Exception as _pmfve:
                    logger.debug(f"PM_CALIBRATED_FAIR_VALUE paper log skipped: {_pmfve}")
            # ── 15m VARIANT as a SEPARATE SHADOW (operator 2026-07-04): same mechanics on 15m
            # rounds, its own name + evidence. NOT the frozen rule — the Kaggle validation was
            # 5m-only (no 15m quote history existed in any archive), so the 15m question gets
            # answered here on live quotes instead of assumed.
            if (_paper_entries_allowed and int(rnd.get("horizon", 0) or 0) == 15 and 20 <= secs_left <= 32
                    and not rnd.get("_ll15_eval") and rnd.get("id")):
                rnd["_ll15_eval"] = True
                try:
                    _lq = _leader_quote(rnd, now_ms)
                    if _lq is None:
                        database.log_rule_paper_trade(rnd["id"], "LATE_LEADER_15M_SHADOW_V1",
                                                      int(now_ms), 15, "", 0.0, 0.0, 0.0, 0.0,
                                                      0.0, "NO_QUOTE")
                    else:
                        _act = ("NO_DEPTH" if _lq["depth"] < 1.0 else
                                "ENTER" if 0.60 <= _lq["ask"] < 0.97
                                else "SKIP_LOW_ASK" if _lq["ask"] < 0.60 else "SKIP_HIGH_ASK")
                        database.log_rule_paper_trade(rnd["id"], "LATE_LEADER_15M_SHADOW_V1",
                                                      int(now_ms), 15, _lq["side"], _lq["ask"],
                                                      _lq["bid"], _lq["fee"], _lq["spread"],
                                                      _lq["depth"], _act,
                                                      btc_entry=rnd.get("current_price"))
                except Exception as _lle2:
                    logger.debug(f"LATE_LEADER_15M shadow log skipped: {_lle2}")
            # ── LIVE SHADOW REPLICATION of the three MEASURED-DEAD strategies (2026-07-04) ──
            # Operator request: give the historical kills a standing live test on real executable
            # quotes -- entry at ASK, exits at BID, fees both legs, one entry per rule per round,
            # BOTH horizons (finally tests the 15m variants no archive could). Rules are paper-only
            # shadows; the dead-strategies panel shows their running live EV next to the historical
            # verdict. All state is per-round + crash-safe; a dead bridge simply means no entry.
            try:
                _dur = max(60, int(rnd.get("horizon", 5) or 5) * 60)
                _sh = rnd.setdefault("_shadow", {})
                if not rnd.get("_paper_state_restored") and rnd.get("id"):
                    rnd["_paper_state_restored"] = True
                    _state_keys = {
                        "MID_SCALP_LIVE_V1": "scalp",
                        "TP_OR_SETTLE_LIVE_V1": "tps",
                        "STRADDLE_LIVE_V1": "strad",
                        "MODEL_FADE_LIVE_V1": "mfade",
                        "MODEL_STRADDLE_LIVE_V1": "mstrad",
                        "MODEL_SEQUENTIAL_REVERSAL_V1": "mseq",
                        CHAMPION_DYNAMIC_RULE: "champdyn",
                    }
                    for _rule, _state in database.fetch_open_rule_paper_states(rnd["id"]).items():
                        _key = _state_keys.get(_rule)
                        if _key and _key not in _sh:
                            _sh[_key] = _state
                _nowq = _leader_quote(rnd, now_ms)
                # Model/head-driven Champion paper strategy. The Champion's default calibration
                # lockdown keeps this dormant unless its existing explicit PAPER_BET override,
                # head-permission and exact net-edge gates all pass. This block adds execution
                # and dynamic exit measurement; it does not weaken any decision gate.
                if _paper_entries_allowed and "champdyn" not in _sh and rnd.get("id"):
                    # Use the same BTC-side quote that Champion evaluated. `_nowq` is the
                    # market-price leader for older shadow rules and can legitimately be the
                    # opposite side when Polymarket temporarily disagrees with the anchor feed.
                    _champ_quote = _side_quote(rnd, now_ms, rnd.get("current_position"))
                    _entry = champion_dynamic_entry(rnd, _champ_quote)
                    rnd["champion_dynamic_status"] = {
                        "action": _entry.get("action"),
                        "execution_mode": "ENTRY_GATE",
                        "degraded": False,
                        "reason_codes": list(_entry.get("reason_codes") or []),
                    }
                    if _entry.get("action") == "ENTER":
                        _state = _entry["state"]
                        _sh["champdyn"] = _state
                        database.log_rule_paper_trade(
                            rnd["id"], CHAMPION_DYNAMIC_RULE, int(now_ms),
                            int(rnd.get("horizon") or 5), _state["side"],
                            float(_champ_quote["ask"]), float(_champ_quote["bid"]),
                            float(_champ_quote["fee_in"]), float(_champ_quote["spread"]),
                            float(_champ_quote["ask_size"]), "ENTER",
                            btc_entry=rnd.get("current_price"), state=_state,
                            model_bundle_id=rnd.get("model_bundle_id", ""),
                            target_contract=rnd.get("pred_contract", ""),
                            head_identity=(
                                rnd.get("head_identity") or _active_head_identity()
                            ),
                            decision={
                                "champion": rnd.get("champion") or {},
                                "entry": _entry,
                            },
                        )
                _champ_state = _sh.get("champdyn")
                if _champ_state and _champ_state.get("open"):
                    _exit_quote = _side_quote(rnd, now_ms, _champ_state["side"])
                    _exit = champion_dynamic_exit(_champ_state, rnd, _exit_quote)
                    _dynamic_status = {
                        "action": _exit.get("action"),
                        "execution_mode": _exit.get("execution_mode", "UNAVAILABLE"),
                        "degraded": bool(_exit.get("degraded")),
                        "reason_codes": list(_exit.get("reason_codes") or []),
                    }
                    rnd["champion_dynamic_status"] = _dynamic_status
                    if _champ_state.get("dynamic_status") != _dynamic_status:
                        _champ_state["dynamic_status"] = _dynamic_status
                        database.update_rule_paper_state(
                            rnd["id"], CHAMPION_DYNAMIC_RULE, _champ_state,
                        )
                    if _exit.get("action") == "EXIT":
                        _champ_state["open"] = False
                        _champ_state["exit_reason"] = _exit["exit_reason"]
                        database.close_rule_paper_trade(
                            rnd["id"], CHAMPION_DYNAMIC_RULE,
                            float(_exit["net_pnl"]), int(now_ms),
                            _exit["exit_reason"], btc_exit=rnd.get("current_price"),
                            exit_gross=float(_exit["exit_gross"]),
                            exit_fee=float(_exit["exit_fee"]), state=_champ_state,
                            settlement_source="live_bid",
                        )
                if (_paper_entries_allowed and "champdyn" not in _sh
                        and secs_left <= 3 and rnd.get("id")):
                    _status = rnd.get("champion_dynamic_status") or {}
                    _sh["champdyn"] = {
                        "open": False,
                        "skipped": True,
                        "reason_codes": list(_status.get("reason_codes") or ["entry_gate_never_passed"]),
                    }
                    database.log_rule_paper_trade(
                        rnd["id"], CHAMPION_DYNAMIC_RULE, int(now_ms),
                        int(rnd.get("horizon") or 5), "", 0.0, 0.0, 0.0, 0.0,
                        0.0, "NO_SIGNAL", btc_entry=rnd.get("current_price"),
                        state=_sh["champdyn"],
                    )
                # entries (each once per round, first qualifying tick in its window)
                if (_paper_entries_allowed and _nowq and _nowq["depth"] >= 1.0 and "scalp" not in _sh
                        and _dur * 0.2 < secs_left <= _dur * 0.6):
                    if 0.50 <= _nowq["ask"] <= 0.70 and _nowq["spread"] <= 0.02:
                        _sh["scalp"] = {"side": _nowq["side"], "entry": _nowq["ask"],
                                        "fee_in": _nowq["fee"], "t0": secs_left, "open": True}
                        database.log_rule_paper_trade(rnd["id"], "MID_SCALP_LIVE_V1", int(now_ms),
                                                      int(rnd.get("horizon") or 5), _nowq["side"],
                                                      _nowq["ask"], _nowq["bid"], _nowq["fee"],
                                                      _nowq["spread"], _nowq["depth"], "ENTER",
                                                      btc_entry=rnd.get("current_price"),
                                                      state=_sh["scalp"])
                if (_paper_entries_allowed and _nowq and _nowq["depth"] >= 1.0 and "tps" not in _sh
                        and _dur * 0.6 < secs_left <= _dur * 0.8):
                    if 0.50 <= _nowq["ask"] <= 0.70 and _nowq["spread"] <= 0.02:
                        _sh["tps"] = {"side": _nowq["side"], "entry": _nowq["ask"],
                                      "fee_in": _nowq["fee"], "open": True}
                        database.log_rule_paper_trade(rnd["id"], "TP_OR_SETTLE_LIVE_V1", int(now_ms),
                                                      int(rnd.get("horizon") or 5), _nowq["side"],
                                                      _nowq["ask"], _nowq["bid"], _nowq["fee"],
                                                      _nowq["spread"], _nowq["depth"], "ENTER",
                                                      btc_entry=rnd.get("current_price"),
                                                      state=_sh["tps"])
                if (_paper_entries_allowed and "strad" not in _sh
                        and _dur * 0.6 < secs_left <= _dur * 0.9):
                    _qu = _side_quote(rnd, now_ms, "UP")
                    _qd = _side_quote(rnd, now_ms, "DOWN")
                    if (_qu and _qd and max(_qu["bid"], _qd["bid"]) <= 0.55
                            and _qu["spread"] <= 0.02 and _qd["spread"] <= 0.02
                            and _qu["ask_size"] >= 1.0 and _qd["ask_size"] >= 1.0):
                        _sh["strad"] = {
                            "up": {"entry": _qu["ask"], "fee_in": _qu["fee_in"],
                                   "exit_bid": None, "exit_fee": 0.0},
                            "dn": {"entry": _qd["ask"], "fee_in": _qd["fee_in"],
                                   "exit_bid": None, "exit_fee": 0.0},
                        }
                        database.log_rule_paper_trade(rnd["id"], "STRADDLE_LIVE_V1", int(now_ms),
                                                      int(rnd.get("horizon") or 5), "BOTH",
                                                      _qu["ask"] + _qd["ask"], _qu["bid"] + _qd["bid"],
                                                      _qu["fee_in"] + _qd["fee_in"], 0.0, 0.0, "ENTER",
                                                      btc_entry=rnd.get("current_price"),
                                                      state=_sh["strad"])
                # exits (checked per tick while open)
                _sc = _sh.get("scalp")
                if _sc and _sc.get("open"):
                    _q = _side_quote(rnd, now_ms, _sc["side"])
                    if _q:
                        _hit = ("TP" if _q["bid"] >= _sc["entry"] + 0.05
                                else "SL" if _q["bid"] <= _sc["entry"] - 0.03
                                else "TIME" if (_sc["t0"] - secs_left) >= 30 else None)
                        if _hit:
                            _sc["open"] = False
                            _pnl = _q["bid"] - _sc["entry"] - _sc["fee_in"] - _q["fee_out"]
                            database.close_rule_paper_trade(rnd["id"], "MID_SCALP_LIVE_V1",
                                                            _pnl, int(now_ms), _hit,
                                                            btc_exit=rnd.get("current_price"),
                                                            exit_gross=_q["bid"],
                                                            exit_fee=_q["fee_out"], state=_sc,
                                                            settlement_source="live_bid")
                _tp = _sh.get("tps")
                if _tp and _tp.get("open"):
                    _q = _side_quote(rnd, now_ms, _tp["side"])
                    if _q and _q["bid"] >= _tp["entry"] * 1.20:
                        _tp["open"] = False
                        _pnl = _q["bid"] - _tp["entry"] - _tp["fee_in"] - _q["fee_out"]
                        database.close_rule_paper_trade(rnd["id"], "TP_OR_SETTLE_LIVE_V1",
                                                        _pnl, int(now_ms), "TP",
                                                        btc_exit=rnd.get("current_price"),
                                                        exit_gross=_q["bid"],
                                                        exit_fee=_q["fee_out"], state=_tp,
                                                        settlement_source="live_bid")
                for _stkey in ("strad", "mstrad", "mseq"):
                    _st = _sh.get(_stkey)
                    if _st:
                        for _leg, _sd in (("up", "UP"), ("dn", "DOWN")):
                            _leg_state = _st.get(_leg)
                            if _leg_state and _leg_state.get("exit_bid") is None:
                                _q = _side_quote(rnd, now_ms, _sd)
                                if _q and _q["bid"] >= _leg_state["entry"] * 1.20:
                                    _leg_state["exit_bid"] = _q["bid"]
                                    _leg_state["exit_fee"] = _q["fee_out"]
                                    _strule = {
                                        "strad": "STRADDLE_LIVE_V1",
                                        "mstrad": "MODEL_STRADDLE_LIVE_V1",
                                        "mseq": "MODEL_SEQUENTIAL_REVERSAL_V1",
                                    }[_stkey]
                                    database.update_rule_paper_state(rnd["id"], _strule, _st)
                # ── MODEL-GATED shadow rules (operator request 2026-07-04): the SAME mechanics, but
                # every entry is DECIDED BY THE MODELS -- the head predictions are the trigger, so
                # these measure whether our models add value over the blind versions above.
                _plx = rnd.get("trade_plan") or {}
                _tsx = _plx.get("touch_state") or {}
                # 1. MODEL FADE: path head said FADE-SETUP, a barrier touch happened, and the fade
                #    model grades the revert >=55% -> buy the CHEAP side; TP +20% or settle.
                if (_paper_entries_allowed and "mfade" not in _sh and _plx.get("play") == "FADE-SETUP"
                        and _tsx.get("side") in ("HIGH", "LOW")
                        and (_tsx.get("p_fade") or 0) >= 0.55 and secs_left > 20):
                    _cheap = "DOWN" if _tsx.get("side") == "HIGH" else "UP"
                    _q = _side_quote(rnd, now_ms, _cheap)
                    if _q and 0.03 <= _q["ask"] <= 0.90 and _q["ask_size"] >= 1.0:
                        _sh["mfade"] = {"side": _cheap, "entry": _q["ask"],
                                        "fee_in": _q["fee_in"], "open": True}
                        database.log_rule_paper_trade(rnd["id"], "MODEL_FADE_LIVE_V1", int(now_ms),
                                                      int(rnd.get("horizon") or 5), _cheap,
                                                      _q["ask"], _q["bid"], _q["fee_in"],
                                                      _q["spread"], 0.0, "ENTER",
                                                      btc_entry=rnd.get("current_price"),
                                                      state=_sh["mfade"])
                _mf = _sh.get("mfade")
                if _mf and _mf.get("open"):
                    _q = _side_quote(rnd, now_ms, _mf["side"])
                    if _q and _q["bid"] >= _mf["entry"] * 1.20:
                        _mf["open"] = False
                        database.close_rule_paper_trade(rnd["id"], "MODEL_FADE_LIVE_V1",
                                                        _q["bid"] - _mf["entry"] - _mf["fee_in"] - _q["fee_out"],
                                                        int(now_ms), "TP",
                                                        btc_exit=rnd.get("current_price"),
                                                        exit_gross=_q["bid"],
                                                        exit_fee=_q["fee_out"], state=_mf,
                                                        settlement_source="live_bid")
                # 1b. MODEL SEQUENTIAL REVERSAL: buy the cheap side after the first model-approved
                # extreme, then add the opposite cheap side only after a separately graded return
                # extreme. This is the operator's "one side, then the other side" strategy. It is
                # intentionally separate from MODEL_FADE (one leg) and MODEL_STRADDLE (both at once).
                # Each leg can TP +20%; all costs/exits remain in one crash-safe paper-ledger row.
                if (_paper_entries_allowed and "mseq" not in _sh and _plx.get("play") == "FADE-SETUP"
                        and _tsx.get("side") in ("HIGH", "LOW")
                        and (_tsx.get("p_fade") or 0) >= 0.55 and secs_left > 20):
                    _first_side = "DOWN" if _tsx.get("side") == "HIGH" else "UP"
                    _q = _side_quote(rnd, now_ms, _first_side)
                    if (_q and 0.03 <= _q["ask"] <= 0.90 and _q["spread"] <= 0.03
                            and _q["ask_size"] >= 1.0):
                        _first_key = "up" if _first_side == "UP" else "dn"
                        _sh["mseq"] = {
                            "up": None, "dn": None, "first_side": _first_side,
                            "second_added": False,
                            "first_p_fade": float(_tsx.get("p_fade") or 0.0),
                        }
                        _sh["mseq"][_first_key] = {
                            "entry": _q["ask"], "fee_in": _q["fee_in"],
                            "exit_bid": None, "exit_fee": 0.0,
                        }
                        database.log_rule_paper_trade(
                            rnd["id"], "MODEL_SEQUENTIAL_REVERSAL_V1", int(now_ms),
                            int(rnd.get("horizon") or 5), _first_side,
                            _q["ask"], _q["bid"], _q["fee_in"], _q["spread"], 0.0, "ENTER",
                            btc_entry=rnd.get("current_price"), state=_sh["mseq"])
                _ms = _sh.get("mseq")
                _leg2 = _tsx.get("leg2") or {}
                if (_paper_entries_allowed and _ms and not _ms.get("second_added")
                        and _leg2.get("fade") in ("UP", "DOWN")
                        and _leg2.get("fade") != _ms.get("first_side")
                        and (_leg2.get("p_fade") or 0) >= 0.55 and secs_left > 20):
                    _second_side = _leg2["fade"]
                    _q = _side_quote(rnd, now_ms, _second_side)
                    if (_q and 0.03 <= _q["ask"] <= 0.90 and _q["spread"] <= 0.03
                            and _q["ask_size"] >= 1.0):
                        _second_key = "up" if _second_side == "UP" else "dn"
                        _next_state = dict(_ms)
                        _next_state[_second_key] = {
                            "entry": _q["ask"], "fee_in": _q["fee_in"],
                            "exit_bid": None, "exit_fee": 0.0,
                        }
                        _next_state["second_added"] = True
                        _next_state["second_side"] = _second_side
                        _next_state["second_p_fade"] = float(_leg2.get("p_fade") or 0.0)
                        if database.add_rule_paper_leg(
                            rnd["id"], "MODEL_SEQUENTIAL_REVERSAL_V1",
                            _q["ask"], _q["bid"], _q["fee_in"], _next_state):
                            _sh["mseq"] = _next_state
                # 2. MODEL STRADDLE: only when the path head PREDICTS chop (two_sided, round-trip
                #    >=35%) -- vs the blind straddle above which fires on price shape alone.
                if (_paper_entries_allowed and "mstrad" not in _sh and _plx.get("style") == "two_sided"
                        and (_plx.get("p_roundtrip") or 0) >= 0.35
                        and _dur * 0.6 < secs_left <= _dur * 0.9):
                    _qu = _side_quote(rnd, now_ms, "UP")
                    _qd = _side_quote(rnd, now_ms, "DOWN")
                    if (_qu and _qd and max(_qu["bid"], _qd["bid"]) <= 0.55
                            and _qu["spread"] <= 0.02 and _qd["spread"] <= 0.02
                            and _qu["ask_size"] >= 1.0 and _qd["ask_size"] >= 1.0):
                        _sh["mstrad"] = {
                            "up": {"entry": _qu["ask"], "fee_in": _qu["fee_in"],
                                   "exit_bid": None, "exit_fee": 0.0},
                            "dn": {"entry": _qd["ask"], "fee_in": _qd["fee_in"],
                                   "exit_bid": None, "exit_fee": 0.0},
                        }
                        database.log_rule_paper_trade(rnd["id"], "MODEL_STRADDLE_LIVE_V1", int(now_ms),
                                                      int(rnd.get("horizon") or 5), "BOTH",
                                                      _qu["ask"] + _qd["ask"], _qu["bid"] + _qd["bid"],
                                                      _qu["fee_in"] + _qd["fee_in"], 0.0, 0.0, "ENTER",
                                                      btc_entry=rnd.get("current_price"),
                                                      state=_sh["mstrad"])
                # 3. MODEL RIDE: path head says TREND (one_sided RIDE) and the big-move timing head
                #    is elevated+ -> buy the leader mid-window, HOLD to settlement (trend thesis).
                if (_paper_entries_allowed and "mride" not in _sh and _plx.get("play") == "RIDE"
                        and str(rnd.get("big_move_tier") or "") in ("elevated", "likely")
                        and _dur * 0.2 < secs_left <= _dur * 0.6):
                    _q = _leader_quote(rnd, now_ms)
                    if (_q and 0.55 <= _q["ask"] <= 0.80 and _q["spread"] <= 0.02
                            and _q["depth"] >= 1.0):
                        _sh["mride"] = True
                        database.log_rule_paper_trade(rnd["id"], "MODEL_RIDE_LIVE_V1", int(now_ms),
                                                      int(rnd.get("horizon") or 5), _q["side"],
                                                      _q["ask"], _q["bid"], _q["fee"],
                                                      _q["spread"], _q["depth"], "ENTER",
                                                      btc_entry=rnd.get("current_price"))
                # 4. MODEL CROSS-FLIP (operator 2026-07-04): SEQUENTIAL two-leg play. Buy the
                #    leader when the path head predicts a two-sided/choppy round (a cross is
                #    likely); THEN, only if the anchor is actually crossed and the leader flips,
                #    buy the NEW leader as well. Each leg is its own honest ledger row (L1/L2)
                #    entered at its own ask and settled independently — the strategy's result is
                #    the SUM of both rows. Differs from the straddle: legs are staggered at
                #    different prices, and leg 2 fires ONLY when the predicted cross happens, so
                #    quiet/trend rounds cost one leg instead of two.
                if (_paper_entries_allowed and "xf1" not in _sh
                        and _plx.get("style") == "two_sided"
                        and (_plx.get("p_roundtrip") or 0) >= 0.30
                        and _dur * 0.25 < secs_left <= _dur * 0.85):
                    _qx = _leader_quote(rnd, now_ms)
                    # Require the MARKET leader (higher bid) and the BTC leader (price vs anchor)
                    # to AGREE at entry. They are different definitions; without this the leg-2
                    # "cross" test (cur_pos != leg-1 side) could fire instantly on a disagreement
                    # instead of on a real anchor cross.
                    if (_qx and _qx["side"] == cur_pos
                            and 0.50 <= _qx["ask"] <= 0.85 and _qx["spread"] <= 0.03
                            and _qx["depth"] >= 1.0):
                        _sh["xf1"] = {"side": _qx["side"]}
                        database.log_rule_paper_trade(rnd["id"], "MODEL_CROSSFLIP_L1_V1",
                                                      int(now_ms), int(rnd.get("horizon") or 5),
                                                      _qx["side"], _qx["ask"], _qx["bid"],
                                                      _qx["fee"], _qx["spread"], _qx["depth"],
                                                      "ENTER", btc_entry=rnd.get("current_price"))
                _xf = _sh.get("xf1")
                if (_paper_entries_allowed and _xf and "xf2" not in _sh
                        and cur_pos in ("UP", "DOWN") and cur_pos != _xf["side"]
                        and secs_left > 20):
                    _qx2 = _side_quote(rnd, now_ms, cur_pos)
                    if _qx2 and _qx2["ask"] <= 0.85:
                        _sh["xf2"] = True
                        database.log_rule_paper_trade(rnd["id"], "MODEL_CROSSFLIP_L2_V1",
                                                      int(now_ms), int(rnd.get("horizon") or 5),
                                                      cur_pos, _qx2["ask"], _qx2["bid"],
                                                      _qx2["fee_in"], _qx2["spread"], 0.0,
                                                      "ENTER", btc_entry=rnd.get("current_price"))
                # ── EDGE-CANDIDATE shadows (operator 2026-07-04, frozen specs — no re-tuning) ──
                # 1+2. LATE_LEADER ladder: 60s and 15s checkpoints (5m, same gates as the frozen
                # 30s rule). Measures the EV-vs-expiry gradient LIVE: calibration showed
                # 120s −0.1c → 60s +0.5c → 30s +2.1c LB; 15s was never measurable offline.
                if _paper_entries_allowed and int(rnd.get("horizon") or 5) == 5:
                    for _lkey, _lrule, _lo, _hi in (("ll60", "LATE_LEADER_60S_V1", 50, 65),
                                                    ("ll15s", "LATE_LEADER_15S_V1", 10, 17)):
                        if _lkey not in _sh and _lo <= secs_left <= _hi:
                            _sh[_lkey] = True
                            _lq2 = _leader_quote(rnd, now_ms)
                            if _lq2 is None:
                                database.log_rule_paper_trade(rnd["id"], _lrule, int(now_ms), 5,
                                                              "", 0.0, 0.0, 0.0, 0.0, 0.0,
                                                              "NO_QUOTE")
                            else:
                                _act2 = ("NO_DEPTH" if _lq2["depth"] < 1.0 else
                                         "ENTER" if 0.60 <= _lq2["ask"] < 0.97
                                         else "SKIP_LOW_ASK" if _lq2["ask"] < 0.60
                                         else "SKIP_HIGH_ASK")
                                database.log_rule_paper_trade(rnd["id"], _lrule, int(now_ms), 5,
                                                              _lq2["side"], _lq2["ask"],
                                                              _lq2["bid"], _lq2["fee"],
                                                              _lq2["spread"], _lq2["depth"],
                                                              _act2,
                                                              btc_entry=rnd.get("current_price"))
                    # 3. MAKER variant: at ~30s REST at the leader's bid instead of crossing.
                    # Conservative fill: only if a later ask trades DOWN TO our price (maker fee
                    # = 0). Unfilled by 3s left → NO_FILL row (honest denominator). This tests
                    # the biggest cost lever: the spread is ~half the whole taker edge.
                    if "maker" not in _sh and 20 <= secs_left <= 32:
                        _mq = _leader_quote(rnd, now_ms)
                        if _mq and _mq["depth"] >= 1.0 and 0.55 <= _mq["bid"] < 0.97:
                            _sh["maker"] = {"side": _mq["side"], "price": _mq["bid"], "done": False}
                        else:
                            _sh["maker"] = {"done": True}
                            database.log_rule_paper_trade(rnd["id"], "LATE_LEADER_MAKER_V1",
                                                          int(now_ms), 5, "", 0.0, 0.0, 0.0,
                                                          0.0, 0.0, "NO_QUOTE")
                    _mk = _sh.get("maker")
                    if _mk and not _mk.get("done"):
                        _q3 = _side_quote(rnd, now_ms, _mk["side"])
                        if _q3 and _q3["ask"] <= _mk["price"] + 1e-9:
                            _mk["done"] = True
                            database.log_rule_paper_trade(rnd["id"], "LATE_LEADER_MAKER_V1",
                                                          int(now_ms), 5, _mk["side"],
                                                          _mk["price"], _q3["bid"], 0.0, 0.0,
                                                          0.0, "ENTER",
                                                          btc_entry=rnd.get("current_price"))
                        elif secs_left <= 3:
                            _mk["done"] = True
                            database.log_rule_paper_trade(rnd["id"], "LATE_LEADER_MAKER_V1",
                                                          int(now_ms), 5, _mk["side"],
                                                          _mk["price"], 0.0, 0.0, 0.0, 0.0,
                                                          "NO_FILL")
                # 4. CHEAP-SAFE early leader (HF_CHEAP_LEADER_DANGER follow-up): cheap ask
                # 0.42–0.58 + SAFE lead (dist/vol ratio ≥ 1.5) in the early-mid window, hold to
                # settle. The shuffled-gate nulls say BTC-state gates are priced in → expectation
                # LOW; running it to close the question on live asks. Both horizons.
                if (_paper_entries_allowed and "csafe" not in _sh
                        and _dur * 0.4 < secs_left <= _dur * 0.8):
                    _cq = _leader_quote(rnd, now_ms)
                    _vol = float(rnd.get("vol_60s_pct") or 0.0)
                    _pxn = float(rnd.get("current_price") or 0.0)
                    if (_cq and _cq["depth"] >= 1.0 and _pxn > 0 and _vol > 0
                            and 0.42 <= _cq["ask"] <= 0.58):
                        if (abs(float(cur_move)) / _pxn * 100.0) / (_vol + 1e-6) >= 1.5:
                            _sh["csafe"] = True
                            database.log_rule_paper_trade(rnd["id"], "CHEAP_SAFE_EARLY_V1",
                                                          int(now_ms),
                                                          int(rnd.get("horizon") or 5),
                                                          _cq["side"], _cq["ask"], _cq["bid"],
                                                          _cq["fee"], _cq["spread"],
                                                          _cq["depth"], "ENTER",
                                                          btc_entry=rnd.get("current_price"))
                # 5. SHOCK SNIPER (1s approximation of the sub-second idea): BTC jumped ≥$20
                # within ~3–8s while the target side's ask did NOT move → buy the stale ask,
                # hold to settle. The 1s bridge cadence UNDERSTATES the true opportunity — the
                # exact test is the offline L2 replay (queued; needs recorders stopped).
                _hist = _sh.setdefault("hist", [])
                _qU = _side_quote(rnd, now_ms, "UP")
                _qD = _side_quote(rnd, now_ms, "DOWN")
                if _qU and _qD and rnd.get("current_price"):
                    _hist.append((int(now_ms), float(rnd["current_price"]),
                                  _qU["ask"], _qD["ask"]))
                    del _hist[:-8]
                if (_paper_entries_allowed and "snipe" not in _sh and len(_hist) >= 3
                        and secs_left > 20 and _qU and _qD):
                    _then = next((s for s in _hist if now_ms - s[0] >= 2500), None)
                    if _then is not None:
                        _dpx = float(rnd["current_price"]) - _then[1]
                        if abs(_dpx) >= 20.0:
                            _tgt = "UP" if _dpx > 0 else "DOWN"
                            _qn = _qU if _tgt == "UP" else _qD
                            _athen = _then[2] if _tgt == "UP" else _then[3]
                            if (abs(_qn["ask"] - _athen) < 0.005 and _qn["ask"] <= 0.90
                                    and _qn["ask_size"] >= 1.0):
                                _sh["snipe"] = True
                                database.log_rule_paper_trade(rnd["id"], "SHOCK_SNIPER_LIVE_V1",
                                                              int(now_ms),
                                                              int(rnd.get("horizon") or 5),
                                                              _tgt, _qn["ask"], _qn["bid"],
                                                              _qn["fee_in"], _qn["spread"],
                                                              0.0, "ENTER",
                                                              btc_entry=rnd.get("current_price"))
                # Same-time action evidence for every currently open paper position. The
                # recorder values HOLD/EXIT/REDUCE/SWITCH/LOCK from this exact paired ladder,
                # but cannot choose an arm or feed any result back into the strategy.
                try:
                    _capture_open_position_action_evidence(
                        rnd, int(now_ms), int(secs_left), cur_pos,
                    )
                except Exception as action_exc:
                    logger.warning(
                        "Open-position action evidence capture failed for %s: %s",
                        rnd.get("id"), action_exc,
                    )
            except Exception as _she:
                logger.debug(f"shadow strategies skipped: {_she}")

    @staticmethod
    def _path_outlook(cur_move: float, cur_pos: str, lean: str, p: dict,
                      lean_source: str, p_hold: float = None) -> dict:
        """Expected price JOURNEY vs the price-to-beat line, in plain English.
        Scenarios: HOLD (already on the leaned side, expect wobbles that hold),
        CROSS (on the wrong side but expected move covers the distance — dip/pop then
        cross), STRETCH (lean exists but expected move < distance — unlikely to make it),
        CHOP (weak/fallback lean — oscillation, coin-flip close). Odds quoted are the
        MEASURED win-rate for this setup (expectedPrecision) when available."""
        exp_move = abs(float(p.get("expectedMove") or 0.0))
        dist = abs(float(cur_move or 0.0))
        wobble = max(5.0, exp_move * 0.4)  # typical intra-window wiggle around the path

        # FIX (2026-06-16): quote P(hold) — the side-survival odds — NOT the direction model's
        # precision (expectedPrecision), which is ~coin-flip and contradicted the P(hold) header
        # (the 98%-vs-40% inconsistency). This outlook is position/P(hold)-framed, so P(hold) is the
        # only consistent "odds this side survives" number to show here.
        # Display cap at 99%: no calibrated probability is truly 100% — showing it reads as
        # overconfident/broken (and clashes with a HIGH big-drop flag). Underlying value untouched.
        odds_txt = f" P(hold) for this setup: {min(99.0, p_hold * 100):.0f}%." if p_hold is not None else ""

        # DIRECTION-HONEST (2026-06-15): 5m/15m direction is ~coin-flip, so the path outlook is framed
        # on the POSITION vs the line + the calibrated move SIZE — NOT a lean prediction. We never claim
        # "will cross and finish <lean>" (that manufactured the card contradiction). P(hold) carries the
        # only reliable read of whether the CURRENT side survives.
        if cur_pos not in ("UP", "DOWN"):
            return {"scenario": "AT LINE", "text":
                    f"Price is right at the line — expect ±${wobble:.0f} wobble and a near coin-flip "
                    f"close. Best skipped."}
        side = "above" if cur_pos == "UP" else "below"
        if dist >= max(exp_move, 1.0):
            return {"scenario": "HOLD", "text":
                    f"Price is {side} the line ({cur_move:+.0f}$) — further than a typical ±${exp_move:.0f} "
                    f"move, so it would take an above-average swing to cross back. P(hold) is the "
                    f"calibrated odds this {cur_pos} side survives to close.{odds_txt}"}
        return {"scenario": "NEAR LINE", "text":
                f"Price is {side} the line ({cur_move:+.0f}$) but within a typical ±${exp_move:.0f} move, "
                f"so it can wobble across — the close is near coin-flip. Lean on P(hold) + the calibrated "
                f"band, not a direction call.{odds_txt}"}

    @staticmethod
    def _advice(bet_dir: str, cur_pos: str, live_lean: str, secs_left: int, cur_move: float,
                p_hold: float = None) -> dict:
        """Hold / exit guidance for a placed bet. bet_dir = the lean locked at window open."""
        if bet_dir not in ("UP", "DOWN"):
            return {"action": "NO BET", "tone": "muted",
                    "text": "No directional lean for this window — sit this one out."}
        winning = (cur_pos == bet_dir)
        lean_agrees = (live_lean == bet_dir)
        urgent = secs_left <= 30
        mv = f"${abs(cur_move):.0f}"
        if winning and lean_agrees:
            return {"action": "HOLD", "tone": "good",
                    "text": f"Ahead {mv} and the model still leans {bet_dir}. Let it ride to settle."}
        if winning and not lean_agrees:
            return {"action": "LOCK IN", "tone": "warn",
                    "text": f"Ahead {mv} but the model's lean faded to {live_lean or 'NEUTRAL'}. "
                            f"{'Little time left — ' if urgent else ''}consider selling to lock the gain."}
        # BEHIND: a cross-back requires the currently-winning side to FAIL, i.e. reversal prob
        # = 1 - P(hold of cur_pos). When P(hold) is high, "reversal possible / hold" contradicts
        # the calibrated odds shown two boxes up (operator-caught 2026-06-29: a card read
        # P(hold UP)=99% yet advised "reversal possible, hold" the losing DOWN bet). Make the
        # advice agree with P(hold) instead of the coin-flip lean.
        rev = (1.0 - p_hold) if p_hold is not None else None
        if not winning and rev is not None and rev <= 0.15:
            return {"action": "LIKELY LOST", "tone": "bad",
                    "text": f"Behind {mv} — P(hold) puts a cross-back at only ~{rev * 100:.0f}%, so the "
                            f"{bet_dir} bet is likely lost despite the lean. "
                            f"{'Time is short; ' if urgent else ''}cut it to limit loss."}
        if not winning and lean_agrees:
            return {"action": "HOLD / WAIT", "tone": "warn",
                    "text": f"Behind {mv} but the model still leans {bet_dir} — reversal possible. "
                            f"{'Time is short, weigh an exit.' if urgent else 'Hold unless it worsens.'}"}
        return {"action": "EXIT", "tone": "bad",
                "text": f"Behind {mv} and the model flipped to {live_lean or 'NEUTRAL'}. "
                        f"Consider cutting the bet early to limit loss."}

    def _resolve(self, now_ms: int, ref_price: float, klines=None):
        if not ref_price or ref_price <= 0:
            return
        still = []
        for p in self.pending:
            if now_ms >= p["verify_at"]:
                # Late resolution (loop stall): grade against the TRUE window-end price
                # recovered from klines, not the drifted current price.
                end_price = ref_price
                if now_ms - p["verify_at"] > self.LATE_MS:
                    if not klines:
                        # A late Pyth close cannot be reconstructed from Binance
                        # candles without mixing feeds. Remove it from every metric.
                        logger.warning(
                            "Invalidating %s: settlement-feed resolution arrived %sms "
                            "late without same-feed boundary history.",
                            p.get("id"), int(now_ms - p["verify_at"]),
                        )
                        invalid = {**p, "status": "invalid", "invalid_reason": "late_resolution_no_boundary"}
                        if (self.latest_round.get(p["horizon"]) or {}).get("id") == p["id"]:
                            self.latest_round[p["horizon"]] = invalid
                        if self.persist:
                            try:
                                database.invalidate_price_to_beat(p["id"])
                            except Exception as exc:
                                logger.warning("Could not remove invalid price-to-beat round %s: %s", p.get("id"), exc)
                        continue
                    end_price = self._price_at_boundary(p["verify_at"], klines, ref_price)
                actual_dir = self._direction(end_price, p["price_to_beat"])
                move = round(end_price - p["price_to_beat"], 2)
                # Only count rounds where we actually had a directional lean to bet — a
                # NEUTRAL lean is "no play" on a binary market, so it must NOT drag down
                # the win-rate (otherwise the mirror looks 0% while the model abstains).
                bet = p["our_direction"] in ("UP", "DOWN")
                hit = (p["our_direction"] == actual_dir) if bet else None
                if bet:
                    # (hit, lean_source, usable) so accuracy() can split model-vs-fallback
                    # win rates AND separate rounds whose forecast actually covered the
                    # interval from those it barely overlapped.
                    self.history[p["horizon"]].append(
                        (1 if hit else 0, p.get("lean_source", "model"),
                         bool(p.get("grade_usable", False))))
                resolved = {**p, "actual_price": round(end_price, 2),
                            "actual_direction": actual_dir, "hit": hit,
                            "move": move, "status": "resolved"}
                self.recent_rounds.appendleft(resolved)
                # Path-plan LIVE metrics: log the served plan vs the realized window extremes (grab the
                # round dict BEFORE it is replaced below). Crash-safe; never blocks resolution.
                try:
                    _rd = self.latest_round.get(p["horizon"]) or {}
                    if _rd.get("trade_plan") and _rd.get("id") == p.get("id"):
                        _log_path_plan_outcome(_rd, _rd["trade_plan"], end_price)
                except Exception as _ple:
                    logger.debug(f"path-plan outcome log skipped: {_ple}")
                if (self.latest_round.get(p["horizon"]) or {}).get("id") == p["id"]:
                    self.latest_round[p["horizon"]] = resolved
                if self.persist:
                    try:
                        database.resolve_price_to_beat(p["id"], end_price, actual_dir, hit, move,
                                                       late_entry=bool(p.get("late_entry", False)))
                    except Exception as e:
                        logger.debug(f"Price-to-beat resolve failed: {e}")
                    # STRADDLE shadow: settle unexited legs at outcome value (winner 1 / loser 0)
                    # and close its row with the TOTAL pnl BEFORE the generic settler runs (the
                    # generic hold-to-settle formula is single-side and would misprice 'BOTH').
                    try:
                        for _stkey, _strule in (("strad", "STRADDLE_LIVE_V1"),
                                                ("mstrad", "MODEL_STRADDLE_LIVE_V1")):
                            _st = (p.get("_shadow") or {}).get(_stkey)
                            if not _st:
                                continue
                            _gross = 0.0
                            _exit_fees = 0.0
                            for _leg, _sd in (("up", "UP"), ("dn", "DOWN")):
                                if _st[_leg].get("exit_bid") is not None:
                                    _gross += float(_st[_leg]["exit_bid"])
                                    _exit_fees += float(_st[_leg].get("exit_fee") or 0.0)
                                else:
                                    _gross += 1.0 if actual_dir == _sd else 0.0
                            _cost = (_st["up"]["entry"] + _st["up"]["fee_in"]
                                     + _st["dn"]["entry"] + _st["dn"]["fee_in"])
                            database.close_rule_paper_trade(
                                p["id"], _strule,
                                _gross - _exit_fees - _cost, int(now_ms), "SETTLED",
                                btc_exit=end_price, exit_gross=_gross,
                                exit_fee=_exit_fees, state=_st,
                                settlement_source="pyth_proxy")
                    except Exception as _ste:
                        logger.debug(f"Straddle shadow settle skipped: {_ste}")
                    # Sequential strategy may contain one or two bought legs. Unlike a simultaneous
                    # straddle, an absent second leg has no cost and no settlement value. Close it
                    # explicitly so the generic BOTH fallback cannot invent an unbought winner.
                    try:
                        _sq = (p.get("_shadow") or {}).get("mseq")
                        if _sq:
                            _gross = 0.0
                            _exit_fees = 0.0
                            _cost = 0.0
                            for _leg, _sd in (("up", "UP"), ("dn", "DOWN")):
                                _ls = _sq.get(_leg)
                                if not _ls:
                                    continue
                                _cost += float(_ls["entry"]) + float(_ls.get("fee_in") or 0.0)
                                if _ls.get("exit_bid") is not None:
                                    _gross += float(_ls["exit_bid"])
                                    _exit_fees += float(_ls.get("exit_fee") or 0.0)
                                else:
                                    _gross += 1.0 if actual_dir == _sd else 0.0
                            database.close_rule_paper_trade(
                                p["id"], "MODEL_SEQUENTIAL_REVERSAL_V1",
                                _gross - _exit_fees - _cost, int(now_ms), "SETTLED",
                                btc_exit=end_price, exit_gross=_gross,
                                exit_fee=_exit_fees, state=_sq,
                                settlement_source="pyth_proxy")
                    except Exception as _seqe:
                        logger.debug(f"Sequential reversal settle skipped: {_seqe}")
                    # Settle any frozen-rule paper trades on this round (LATE_LEADER_30S_V1 ledger
                    # + still-open shadow rows): pnl = settle_value - ask - fee, hold-to-settle.
                    try:
                        database.settle_rule_paper_trades(
                            p["id"], actual_dir, int(now_ms), btc_exit=end_price,
                            settlement_source="pyth_proxy")
                    except Exception as _pse:
                        logger.debug(f"Rule paper settle skipped: {_pse}")
                    try:
                        open_position_action_recorder().record_settlement(
                            round_id=str(p["id"]),
                            settled_side=str(actual_dir),
                            settled_ts=int(now_ms),
                            settlement_source="pyth_proxy",
                        )
                    except Exception as action_settle_exc:
                        logger.warning(
                            "Open-position action proxy settlement failed for %s: %s",
                            p.get("id"), action_settle_exc,
                        )
                    _pm_ledger_settle_round(
                        p["id"], actual_dir, int(now_ms), price=float(end_price),
                        kind="SETTLEMENT_PROXY", source="pyth_proxy")
            else:
                still.append(p)
        self.pending = still

    def latest(self) -> dict:
        """Current open (or last resolved) round per horizon, for the UI cards."""
        return {h: _public_round_view(self.latest_round.get(h)) for h in self.horizons}

    def recent(self, n: int = 20) -> list:
        return [_public_round_view(row) for row in list(self.recent_rounds)[:n]]

    def accuracy(self) -> dict:
        out = {}
        for h in self.horizons:
            hh = list(self.history[h])
            # Back-compat over three shapes. A plain int predates lean_source; a 2-tuple
            # predates the interval check. Neither can be shown to have covered the round,
            # so both are UNUSABLE rather than usable-by-default - a rehydrated row must
            # not inherit a guarantee it was never measured against.
            norm = []
            for e in hh:
                if isinstance(e, int):
                    norm.append((e, "model", False))
                elif len(e) == 2:
                    norm.append((e[0], e[1], False))
                else:
                    norm.append((e[0], e[1], bool(e[2])))
            hh = norm
            n = len(hh)
            hits = sum(e[0] for e in hh)
            model = [e for e in hh if e[1] == "model"]
            fb = [e for e in hh if e[1] == "fallback"]
            usable = [e for e in hh if e[2]]
            model_usable = [e for e in model if e[2]]
            out[h] = {
                "total": n,
                "hits": int(hits),
                "accuracy": round(hits / n, 4) if n else 0.0,
                # The split that matters for betting: committed model leans vs weak fallback.
                "model_total": len(model),
                "model_accuracy": round(sum(e[0] for e in model) / len(model), 4) if model else 0.0,
                "fallback_total": len(fb),
                "fallback_accuracy": round(sum(e[0] for e in fb) / len(fb), 4) if fb else 0.0,
                # WHAT THIS NUMBER IS. The mirror grades endpoint sign against the anchor;
                # the model forecasts a first-touch barrier. The rate above is therefore a
                # cross-contract measurement, and the interval-covered subset is the only
                # part of it whose forecast window can be shown to match the round.
                "grading_contract": self.GRADING_CONTRACT,
                "min_horizon_overlap": self.MIN_HORIZON_OVERLAP,
                "interval_covered_total": len(usable),
                "interval_covered_accuracy": (
                    round(sum(e[0] for e in usable) / len(usable), 4) if usable else 0.0),
                "model_interval_covered_total": len(model_usable),
                "model_interval_covered_accuracy": (
                    round(sum(e[0] for e in model_usable) / len(model_usable), 4)
                    if model_usable else 0.0),
                "pending": sum(1 for p in self.pending if p["horizon"] == h),
            }
        return out


def _verified_load(path):
    """Hash-check against the sidecar manifest BEFORE deserializing.

    joblib.load executes arbitrary code while unpickling, so validating after loading has
    already lost. Artifacts written before this migration carry no manifest; they still load
    while BTC_STRICT_ARTIFACT_IDENTITY is off, and each one is counted as remaining debt."""
    import sys as _sys
    from pathlib import Path as _Path

    _backend = str(_Path(__file__).resolve().parent)
    if _backend not in _sys.path:
        _sys.path.insert(0, _backend)
    from verified_io import verified_load as _vl

    return _vl(path)
