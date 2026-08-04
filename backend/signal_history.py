"""
Live Signal History Buffer
==========================
Records a snapshot of live microstructure / derivative / cross-exchange signals
at the close of each 1-minute candle, so those values can be *replayed per-bar*
during training.

Why this exists: features like order-flow, liquidations and derivatives used to be
written as a single scalar broadcast to every row of the training matrix. A column
with the same value in every row has zero variance, so the tree models could never
split on it — they were "dead" features. By recording a real per-candle history and
aligning it to the training klines, those columns finally carry variance and become
learnable.

The buffer fills going forward only: candles that predate buffer coverage are
returned as per-feature neutral defaults (usually 0.0, but 1.0 for ratio features).
After a few days of continuous operation the buffer covers thousands of candles and
the features become genuinely informative.
"""

from collections import deque
import os
import numpy as np


def _atomic_write(payload: dict, path: str) -> None:
    """Write + fsync + rename, and publish the integrity manifest verified_load requires.

    The previous writer used a plain pickle.dump to a SHARED `path + ".tmp"`, so two
    overlapping writes raced on the same temporary file, and it never wrote the sidecar - which
    meant strict mode would refuse the artifact even once the load path was fixed."""
    import sys as _sys
    from pathlib import Path as _Path

    _backend = str(_Path(__file__).resolve().parent)
    if _backend not in _sys.path:
        _sys.path.insert(0, _backend)
    from verified_io import atomic_dump

    os.makedirs(os.path.dirname(path), exist_ok=True)
    # atomic_dump writes to `path.tmp.<pid>` (so concurrent writers do not collide on a shared
    # ".tmp" as the old writer did), replaces atomically, THEN writes the integrity manifest
    # from the file on disk. A separate write_manifest call here would be redundant - verified
    # by mutation: removing it changed nothing.
    atomic_dump(payload, path)


class LiveSignalHistoryBuffer:
    # Keys captured per candle. These map 1:1 to the raw signal names that
    # features.build_features_from_klines normalizes via its `series()` helper.
    KEYS = [
        "cvd_change", "cvd_1m", "cvd_5m", "imbalance", "obi_5", "obi_10", "obi_20",
        "trade_intensity", "spread_bps", "funding_rate", "oi_change", "ls_ratio",
        "fear_greed", "coinbase_premium", "global_oi_change",
        "coinbase_premium_velocity", "oi_divergence", "long_liq_vol",
        "short_liq_vol", "liq_imbalance", "chainlink_price", "wall_imbalance",
        "dist_bid_wall", "dist_ask_wall", "spread_expansion", "vacuum_detected",
        "bid_wall_persistence", "ask_wall_persistence", "bid_wall_growth",
        "ask_wall_growth", "queue_depletion_rate", "liquidity_sweep_bullish",
        "liquidity_sweep_bearish", "spoof_score", "absorption_ratio",
        "bid_consume_rate", "ask_consume_rate", "queue_pressure",
        "bids_added", "asks_added", "bids_canceled", "asks_canceled",
        "bids_executed", "asks_executed", "book_replenishment_rate",
        "regime_transition_prob", "regime_entropy", "vol_forecast_1m",
        "vol_forecast_5m", "vol_forecast_15m", "put_call_ratio",
        "options_skew_25d", "max_pain", "atm_iv", "basis_spread",
        "basis_velocity", "stablecoin_flow", "exchange_netflow",
        "eth_price", "sol_price", "eth_volume", "sol_volume",
        "eth_imbalance", "sol_imbalance", "macro_dxy", "macro_us10y",
        # Large-trade flow (recorded live by order_flow, backfilled by trade_features) —
        # train/serve consistent, so the overlay merges them like CVD.
        "large_trade_delta", "large_trade_imbalance",
        # Streaming VPIN (order_flow) — same fixed bucket constants as the backfill.
        "vpin",
        # L2 microstructure (recorded only, NOT yet model features) — accrue for a future
        # "V2" retrain. Live-only (not cheaply backfillable on Binance).
        "microprice_edge_bps", "ofi_best",
    ]

    NEUTRAL_DEFAULTS = {
        "trade_intensity": 50.0,
        "spread_bps": 2.5,
        "ls_ratio": 1.0,
        "fear_greed": 50.0,
        "spread_expansion": 1.0,
        "bid_wall_growth": 1.0,
        "ask_wall_growth": 1.0,
        "queue_depletion_rate": 1.0,
        "book_replenishment_rate": 1.0,
        "put_call_ratio": 1.0,
        "macro_dxy": 104.5,
        "macro_us10y": 4.25,
    }

    def __init__(self, maxlen: int = 140000):
        self.by_ts: dict[int, dict] = {}
        self.order: deque = deque(maxlen=maxlen)
        self.last_ts: int = 0
        self._dirty_count = 0

    def record(self, candle_ts: int, data_state: dict) -> None:
        """Record the live signal snapshot for a just-closed candle (called once per candle)."""
        if candle_ts is None or candle_ts <= self.last_ts:
            return  # duplicate or out-of-order — skip
        self.last_ts = candle_ts
        # Evict oldest if the deque is full (keep by_ts in sync).
        if len(self.order) == self.order.maxlen:
            oldest = self.order[0]
            self.by_ts.pop(oldest, None)
        self.order.append(candle_ts)
        self.by_ts[candle_ts] = self._snapshot(data_state)
        self._dirty_count += 1

    def _snapshot(self, data_state: dict) -> dict:
        of = data_state.get("order_flow", {}) or {}
        der = data_state.get("derivatives", {}) or {}
        sent = data_state.get("sentiment", {}) or {}
        walls = of.get("liquidity_walls", {}) or {}
        vac = of.get("liquidity_vacuum", {}) or {}
        liqs = der.get("liquidations", {})
        liqs = liqs if isinstance(liqs, dict) else {}
        regime = data_state.get("regime_info", {}) or der.get("regime_data", {}) or {}
        vol_fc = regime.get("vol_forecast", {}) or {}
        inst = der.get("institutional", {}) or {}
        options = inst.get("options", {}) or {}
        basis = inst.get("basis", {}) or {}
        stable = inst.get("stablecoin", {}) or {}
        exflow = inst.get("exchange_flow", {}) or {}

        funding = 0.0
        bybit_fr = der.get("bybit_funding_rate")
        if bybit_fr is not None:
            funding = bybit_fr.get("rate", 0.0) if isinstance(bybit_fr, dict) else float(bybit_fr)
        elif der.get("funding_rate"):
            fr = der["funding_rate"]
            funding = fr.get("rate", 0.0) if isinstance(fr, dict) else float(fr)

        ls = 1.0
        long_short = der.get("long_short_ratio") or []
        first_ls = long_short[0] if long_short and isinstance(long_short[0], dict) else {}
        if first_ls:
            ls = first_ls.get("ratio", 1.0)

        fg_data = sent.get("fear_greed")
        fg = fg_data.get("value", 50) if isinstance(fg_data, dict) else 50

        # Cross-asset ETH/SOL are written as FLAT keys by the WS handlers
        # (handle_cross_asset_* in server.py), not a nested {"ETH": {...}} dict.
        macro = data_state.get("macro") or {}

        return {
            "cvd_change": of.get("cvd_change", 0.0),
            "cvd_1m": of.get("cvd_1m", 0.0),
            "cvd_5m": of.get("cvd_5m", 0.0),
            "imbalance": of.get("imbalance", 0.0),
            "obi_5": of.get("obi_5", 0.0),
            "obi_10": of.get("obi_10", 0.0),
            "obi_20": of.get("obi_20", 0.0),
            "trade_intensity": of.get("trade_intensity", 50.0),
            "spread_bps": of.get("spread_bps", 2.5),
            "funding_rate": funding,
            "oi_change": der.get("binance_oi_change", 0.0),
            "ls_ratio": ls,
            "fear_greed": fg,
            "coinbase_premium": der.get("coinbase_premium", 0.0),
            "global_oi_change": der.get("global_oi_change", 0.0),
            "coinbase_premium_velocity": der.get("coinbase_premium_velocity", 0.0),
            "oi_divergence": der.get("oi_divergence", 0.0),
            "long_liq_vol": liqs.get("long_vol", 0.0),
            "short_liq_vol": liqs.get("short_vol", 0.0),
            "liq_imbalance": liqs.get("imbalance", 0.0),
            "chainlink_price": der.get("chainlink_price", 0.0) or 0.0,
            "wall_imbalance": walls.get("wall_imbalance", 0.0),
            "dist_bid_wall": walls.get("distance_to_bid_wall", 0.0),
            "dist_ask_wall": walls.get("distance_to_ask_wall", 0.0),
            "spread_expansion": vac.get("spread_expansion_ratio", 1.0),
            "vacuum_detected": 1.0 if vac.get("vacuum_detected", False) else 0.0,
            "bid_wall_persistence": walls.get("bid_wall_persistence", 0.0),
            "ask_wall_persistence": walls.get("ask_wall_persistence", 0.0),
            "bid_wall_growth": walls.get("bid_wall_growth", 1.0),
            "ask_wall_growth": walls.get("ask_wall_growth", 1.0),
            "queue_depletion_rate": of.get("queue_depletion_rate", 1.0),
            "liquidity_sweep_bullish": of.get("liquidity_sweep_bullish", 0.0),
            "liquidity_sweep_bearish": of.get("liquidity_sweep_bearish", 0.0),
            "spoof_score": of.get("spoof_score", 0.0),
            "absorption_ratio": of.get("absorption_ratio", 0.0),
            "bid_consume_rate": of.get("bid_consume_rate", 0.0),
            "ask_consume_rate": of.get("ask_consume_rate", 0.0),
            "queue_pressure": of.get("queue_pressure", 0.0),
            "bids_added": of.get("bids_added", 0.0),
            "asks_added": of.get("asks_added", 0.0),
            "bids_canceled": of.get("bids_canceled", 0.0),
            "asks_canceled": of.get("asks_canceled", 0.0),
            "bids_executed": of.get("bids_executed", 0.0),
            "asks_executed": of.get("asks_executed", 0.0),
            "book_replenishment_rate": of.get("book_replenishment_rate", 1.0),
            "regime_transition_prob": regime.get("transition_probability", 0.0),
            "regime_entropy": regime.get("regime_entropy", 0.0),
            "vol_forecast_1m": vol_fc.get("vol_forecast_1m", 0.0),
            "vol_forecast_5m": vol_fc.get("vol_forecast_5m", 0.0),
            "vol_forecast_15m": vol_fc.get("vol_forecast_15m", 0.0),
            "put_call_ratio": options.get("put_call_ratio", 1.0),
            "options_skew_25d": options.get("skew_25d", 0.0),
            "max_pain": options.get("max_pain", 0.0),
            "atm_iv": options.get("atm_iv", 0.0),
            "basis_spread": basis.get("basis_spread", 0.0),
            "basis_velocity": basis.get("basis_velocity", 0.0),
            "stablecoin_flow": stable.get("stablecoin_flow", 0.0),
            "exchange_netflow": exflow.get("exchange_netflow", 0.0),
            "eth_price": data_state.get("eth_price", 0.0) or 0.0,
            "sol_price": data_state.get("sol_price", 0.0) or 0.0,
            "eth_volume": data_state.get("eth_volume", 0.0) or 0.0,
            "sol_volume": data_state.get("sol_volume", 0.0) or 0.0,
            "eth_imbalance": data_state.get("eth_imbalance", 0.0) or 0.0,
            "sol_imbalance": data_state.get("sol_imbalance", 0.0) or 0.0,
            "macro_dxy": macro.get("dxy", 0.0),
            "macro_us10y": macro.get("us10y", 0.0),
            "large_trade_delta": of.get("large_trade_delta", 0.0),
            "large_trade_imbalance": of.get("large_trade_imbalance", 0.0),
            "vpin": of.get("vpin", 0.0),
            "microprice_edge_bps": of.get("microprice_edge_bps", 0.0),
            "ofi_best": of.get("ofi_best", 0.0),
        }

    def get_aligned_series(self, candle_timestamps: list) -> dict:
        """
        Return a dict of numpy arrays aligned to `candle_timestamps`.
        Candles with no recorded snapshot get a per-feature neutral default.
        """
        out = {k: [] for k in self.KEYS}
        for ts in candle_timestamps:
            row = self.by_ts.get(ts)
            for k in self.KEYS:
                default = self.NEUTRAL_DEFAULTS.get(k, 0.0)
                out[k].append(row.get(k, default) if row else default)
        return {k: np.asarray(v, dtype=np.float64) for k, v in out.items()}

    def overlay_backfill(self, sig_hist: dict, candle_timestamps: list,
                         parquet_path: str, keys=None) -> int:
        """Train-time overlay: fill historical per-bar signals from a backfill parquet for
        candles that have NO live snapshot (live recording stays authoritative where present).

        DEFENSIVE by design — a missing / empty / unreadable parquet is a clean no-op, so it
        can NEVER break a retrain. Keys are matched by the parquet's `candle_ts` (milliseconds)
        converted to SECONDS, because every other path here keys candles by the kline `time`
        field which is UNIX SECONDS (data_ingestion stores `int(k[0]) // 1000`).

        Merged by default: `cvd_change/cvd_1m/cvd_5m`, `large_trade_delta/large_trade_imbalance`,
        and `vpin`. Each is now recorded live by order_flow with the SAME definitions/constants
        the backfill reproduces (CVD via the shared trade_features rolling windows; large-trade
        via the same EWMA threshold; vpin via the same fixed equal-volume buckets —
        trade_features.DEFAULT_BUCKET_VOLUME_BTC / DEFAULT_ROLLING_BUCKETS, see order_flow.py),
        so they are train/serve consistent. `funding_velocity` is still intentionally NOT merged
        — the live recorder does not yet produce it identically, and merging it would create
        train/serve skew. (Historical note: vpin was excluded here until the streaming-VPIN
        recorder was aligned to the backfill constants; it is parity-safe now.)

        Returns the number of (bar, key) cells filled (0 = no-op).
        """
        import os as _os
        if not parquet_path or not _os.path.exists(parquet_path):
            return 0
        try:
            import pandas as pd
            df = pd.read_parquet(parquet_path)
        except Exception:
            return 0
        if df.empty or "candle_ts" not in df.columns:
            return 0
        if keys is None:
            keys = [k for k in ("cvd_change", "cvd_1m", "cvd_5m",
                                "large_trade_delta", "large_trade_imbalance", "vpin")
                    if k in df.columns and k in self.KEYS and k in sig_hist]
        if not keys:
            return 0
        cvals = df["candle_ts"].to_numpy()
        kcols = {k: df[k].to_numpy() for k in keys}
        bf = {int(cvals[j]) // 1000: j for j in range(len(cvals))}  # ms -> s
        filled = 0
        for i, ts in enumerate(candle_timestamps):
            tsi = int(ts)
            if tsi in self.by_ts:        # authoritative live snapshot present — keep it
                continue
            j = bf.get(tsi)
            if j is None:
                continue
            for k in keys:
                v = kcols[k][j]
                if v == v:               # not NaN
                    sig_hist[k][i] = float(v)
                    filled += 1
        return filled

    def coverage(self, candle_timestamps: list) -> float:
        """Fraction of the given candles that have a recorded snapshot (0..1)."""
        if not candle_timestamps:
            return 0.0
        hits = sum(1 for ts in candle_timestamps if ts in self.by_ts)
        return hits / len(candle_timestamps)

    def coverage_report(self, candle_timestamps: list = None, recent: int = 300) -> dict:
        """READ-ONLY feature-coverage / feed-health audit. Pure measurement: it does not
        alter any signal, threshold, or model behavior (freeze-safe). For each live field
        it reports how often it is present, non-zero and varying, and tags it
        alive / sparse / dead / absent so a data-starved feed is visible at a glance.
        """
        import statistics
        import time as _t

        recent_ts = list(self.order)[-recent:]
        rows = [self.by_ts[t] for t in recent_ts if t in self.by_ts]
        n = len(rows)

        # Unit-robust staleness: candle ts may be in seconds or ms.
        last = self.last_ts or 0
        last_s = (last / 1000.0) if last > 1e11 else float(last)
        age_s = (_t.time() - last_s) if last_s else None

        fields = {}
        alive = sparse = dead = absent = 0
        for k in self.KEYS:
            vals = [float(r[k]) for r in rows
                    if k in r and isinstance(r[k], (int, float))]
            present = (sum(1 for r in rows if k in r) / n) if n else 0.0
            if not vals:
                status = "absent"
                fields[k] = {"present": round(present * 100, 1), "nonzero": 0.0,
                             "variance": 0.0, "status": status}
                absent += 1
                continue
            nonzero = sum(1 for v in vals if abs(v) > 1e-12) / len(vals)
            var = statistics.pvariance(vals) if len(vals) > 1 else 0.0
            if present < 0.02:
                status = "absent"; absent += 1
            elif nonzero < 0.02 or var == 0.0:
                status = "dead"; dead += 1
            elif nonzero < 0.5:
                status = "sparse"; sparse += 1
            else:
                status = "alive"; alive += 1
            fields[k] = {"present": round(present * 100, 1),
                         "nonzero": round(nonzero * 100, 1),
                         "variance": round(var, 8), "status": status}

        return {
            "snapshots": len(self.order),
            "sample": n,
            "candle_coverage_pct": (round(self.coverage(candle_timestamps) * 100, 2)
                                    if candle_timestamps else None),
            "last_snapshot_age_s": round(age_s, 1) if age_s is not None else None,
            "stale": (age_s is not None and age_s > 180),
            "summary": {"alive": alive, "sparse": sparse, "dead": dead,
                        "absent": absent, "total": len(self.KEYS)},
            "fields": fields,
        }

    def snapshot_payload(self) -> dict:
        """Fast in-loop snapshot for async saving: shallow-copies the containers (~ms even
        at 140k entries) so a worker thread can pickle them without racing the live
        recorder. Resets the dirty counter — caller is committing to a write."""
        payload = {
            "maxlen": self.order.maxlen,
            "order": list(self.order),
            "by_ts": dict(self.by_ts),
            "last_ts": self.last_ts,
        }
        # NOT cleared here. This only builds the payload; the write happens later, in an
        # executor, and may fail. Clearing the counter now told the buffer a snapshot was
        # durable when nothing had been written. `mark_saved()` is called by the writer.
        return payload


    def mark_saved(self) -> None:
        """Called by the WRITER after a successful write, never by the payload builder."""
        self._dirty_count = 0

    @staticmethod
    def write_payload(payload: dict, path: str) -> bool:
        """Heavy pickle write — run this OFF the event loop (executor thread). The
        synchronous in-loop version of this write fired every 5th candle close, i.e.
        EXACTLY at every 5m window boundary, freezing the price-to-beat ticker and
        capturing late/wrong reference prices."""
        try:
            _atomic_write(payload, path)
            return True
        except Exception:
            return False

    def save(self, path: str, force: bool = False) -> bool:
        """Persist live signal history so restarts keep microstructure coverage."""
        if not force and self._dirty_count < 5:
            return False
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            payload = {
                "maxlen": self.order.maxlen,
                "order": list(self.order),
                "by_ts": self.by_ts,
                "last_ts": self.last_ts,
            }
            _atomic_write(payload, path)
            # Cleared only AFTER a successful write. snapshot_payload() used to clear it while
            # merely BUILDING the payload, so a failed asynchronous write left the buffer
            # believing it had been saved.
            self._dirty_count = 0
            return True
        except Exception:
            return False

    def load(self, path: str) -> int:
        """Load persisted live signal history. Returns number of snapshots loaded."""
        if not os.path.exists(path):
            return 0
        try:
            # verified_load takes a PATH: it derives the sidecar manifest from it and hashes
            # the file before deserializing. Handing it an open file object made it build
            # sidecar paths from "<_io.BufferedReader ...>", raise, and be swallowed by the
            # except below - so load() silently returned 0 and EVERY restart lost all signal
            # history. Proven empirically: save() True, load() 0.
            payload = _verified_load(path)
            maxlen = int(payload.get("maxlen") or self.order.maxlen or 140000)
            order = payload.get("order") or []
            by_ts = payload.get("by_ts") or {}
            self.order = deque(order[-maxlen:], maxlen=maxlen)
            allowed = set(self.order)
            self.by_ts = {int(k): v for k, v in by_ts.items() if int(k) in allowed}
            self.last_ts = int(payload.get("last_ts") or (self.order[-1] if self.order else 0))
            self._dirty_count = 0
            return len(self.order)
        except Exception:
            return 0

    def __len__(self):
        return len(self.order)


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
