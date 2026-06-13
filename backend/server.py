"""
FastAPI Backend Server + WebSocket Handler.
Orchestrates connections to Binance, maintains model state,
streams real-time predictions, indicators, and verification data to frontend.
"""

import asyncio
import functools
import requests  # used only by the lightweight Pyth price-to-beat anchor poller
import json
import time
import logging
import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import numpy as np
from contextlib import asynccontextmanager

from data_ingestion import (
    BinanceWebSocketClient,
    BinanceRESTClient,
    SentimentClient,
    CoinbaseWebSocketClient,
    BybitRESTClient,
    BinanceFuturesWebSocketClient,
    TradFiMacroClient,
    MultiExchangePriceClient,
    ChainlinkRESTClient,
    CrossAssetWebSocketClient,
)
from order_flow import OrderFlowAnalyzer
from features import (
    build_features_from_klines,
    build_sequences,
    compute_indicator_snapshot,
    compute_indicator_series,
    LOOKBACK,
    NUM_FEATURES,
    atr as compute_atr,
)
from model_verifier import PerModelVerifier
from price_to_beat import PriceToBeatTracker
from exchange_verifier import PerVenueVerifier
from model import MultiModelEnsemble, CascadeMonitor, MODEL_ARCH_VERSION
from backtester import Backtester
from prediction_verifier import PredictionVerifier
from regime import MarketRegime
from signal_history import LiveSignalHistoryBuffer
from meta_model import TrainedMetaModel
from trading_simulator import TradingSimulator
from institutional_feeds import (
    DeribitOptionsClient,
    CMEBasisClient,
    StablecoinFlowClient,
    ExchangeFlowClient,
)
from ab_testing import ABTestRunner, ModelVariant
from polymarket_client import PolymarketClient
from polymarket_model import PolymarketModel
from polymarket_simulator import PolymarketSimulator
from polymarket_verifier import PolymarketVerifier
from fsr_ppo_strategy import FSRPPOStrategy
import database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


HISTORICAL_DAYS = max(1, _env_int("BTC_HISTORICAL_DAYS", 30))
BACKTEST_MAX_ROWS = max(0, _env_int("BTC_BACKTEST_MAX_ROWS", 12000))
HISTORICAL_CACHE_VERSION = 1
HISTORICAL_CACHE_REFRESH_MAX_GAP_SECONDS = max(
    60, _env_int("BTC_HISTORICAL_CACHE_REFRESH_MAX_GAP_SECONDS", 12 * 60 * 60)
)
MAX_KLINES = HISTORICAL_DAYS * 24 * 60 + 1500
# All app-generated files live under <project>/data (override with BTC_DATA_DIR).
PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(PROJECT_ROOT, "data")
os.makedirs(DATA_DIR, exist_ok=True)
SIGNAL_HISTORY_PATH = os.path.join(DATA_DIR, "signal_history.pkl")
HISTORICAL_CACHE_DIR = os.path.join(DATA_DIR, "cache")
MODEL_BOOT_BACKTEST = os.getenv("BTC_RUN_STARTUP_BACKTEST", "1") != "0"
# Freeze the model for a clean EVIDENCE RUN: skip auto-learning + scheduled relearns so
# (a) the model stays STABLE — you can measure a fixed model's accuracy instead of a moving
# target — and (b) the live price/chart feed never freezes from a multi-hour background
# retrain saturating every CPU core. Manual relearn (POST /api/relearn) still works.
# DEFAULT TO FROZEN. On this 16GB/laptop a background retrain pegs every core for hours and
# starves the live feed (price freezes, Binance drops the WS on pong-timeout). So unless the
# operator EXPLICITLY sets BTC_FREEZE_MODEL=0, we do NOT auto/scheduled-retrain. Manual relearn
# (POST /api/relearn) still works. This default means a stray/missing env var can't silently
# kick off a 4-hour retrain again.
MODEL_FROZEN = os.getenv("BTC_FREEZE_MODEL", "1") != "0"
# Main prediction-loop tick interval (seconds). Inference is the heaviest per-tick work, but
# predictions are only RECORDED every 60s+ (per-horizon) and the live price/charts/Polymarket
# windows run on their own fast tickers (0.25s / 1s) — so the heavy loop can run slower with no
# UI impact. Raising this (e.g. 3-4s) cuts inference CPU proportionally on a constrained box.
MAIN_LOOP_SEC = max(1.0, float(os.getenv("BTC_MAIN_LOOP_SEC", "2.0")))
BACKTEST_CACHE_PATH = os.path.join(DATA_DIR, "saved_models", "backtest_cache.json")
BACKTEST_CACHE_VERSION = 2


def _fresh_status() -> dict:
    return {
        "running": False,
        "phase": "idle",
        "message": "Idle",
        "progress": 0.0,
        "started_at": None,
        "completed_at": None,
        "elapsed_seconds": 0.0,
        "error": None,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing Database...")
    database.init_db()
    loaded_signals = signal_buffer.load(SIGNAL_HISTORY_PATH)
    logger.info(f"Loaded {loaded_signals} persisted signal-history snapshots")
    restored = verifier.restore_from_database(
        database.fetch_unresolved_predictions(),
        database.get_last_prediction_timestamps(),
    )
    backend_state["restored_pending_predictions"] = restored
    logger.info(f"Restored {restored} pending predictions from DuckDB")
    ab_restored = ab_runner.restore_from_db()
    logger.info(f"Restored {ab_restored} A/B variant outcomes from DuckDB")
    # Rehydrate the price-to-beat win-rate history so the mirror's accuracy strip
    # (model X% | all Y%) survives restarts instead of resetting to "no rounds yet".
    try:
        _ptb_restored = 0
        for _h in price_to_beat_tracker.horizons:
            _hist = database.fetch_price_to_beat_history(_h, 500)
            if _hist:
                price_to_beat_tracker.history[_h].extend(_hist)
                _ptb_restored += len(_hist)
        if _ptb_restored:
            logger.info(f"Restored {_ptb_restored} price-to-beat outcomes from DuckDB")
        # Also rehydrate the resolved-rounds UI table (newest-first) — the win-rate
        # COUNTERS survived restarts but the table showed "No resolved rounds yet"
        # because recent_rounds was memory-only.
        # 200: enough that the per-timeframe log tabs aren't sparse right after a
        # restart (1m floods the buffer; slower TFs need depth to show ~25 each).
        _recent = database.fetch_price_to_beat_recent(200)
        for _r in _recent:
            price_to_beat_tracker.recent_rounds.append(_r)  # newest-first preserved
        if _recent:
            logger.info(f"Restored {len(_recent)} resolved price-to-beat rounds for the UI")
    except Exception as _pe:
        logger.debug(f"PTB history rehydrate skipped: {_pe}")
    # Data hygiene: purge pending rows orphaned by the previous shutdown (their
    # resolvers were memory-only; they can never resolve now).
    try:
        _orph = database.cleanup_orphan_pending_rows()
        _tot = sum(v for v in _orph.values() if v > 0)
        if _tot:
            logger.info(f"Cleaned {_tot} orphaned pending rows: {_orph}")
    except Exception as _oe:
        logger.debug(f"Orphan cleanup skipped: {_oe}")

    ws_client.on("trade", handle_trade)
    ws_client.on("depth", handle_depth)
    ws_client.on("kline", handle_kline)
    coinbase_client.on("ticker", handle_coinbase_ticker)
    futures_ws_client.on("liquidation", handle_liquidation)
    futures_ws_client.on("perp_bar", handle_perp_bar)   # A4 live perp-CVD parity recorder
    cross_asset_client.on("cross_asset_trade", handle_cross_asset_trade)
    cross_asset_client.on("cross_asset_depth", handle_cross_asset_depth)
    cross_asset_client.on("cross_asset_kline", handle_cross_asset_kline)

    logger.info("Starting background tasks...")
    logger.info("Discovering Polymarket Markets...")
    polymarket_client.discover_markets()
    asyncio.create_task(main_loop())
    asyncio.create_task(fast_price_broadcaster())
    asyncio.create_task(pyth_price_poller())       # price-to-beat anchor feed (Pyth)
    asyncio.create_task(price_to_beat_ticker())
    asyncio.create_task(ws_client.connect())
    asyncio.create_task(coinbase_client.connect())
    asyncio.create_task(futures_ws_client.connect())
    asyncio.create_task(polymarket_client.connect_ws())
    asyncio.create_task(cross_asset_client.connect())

    yield

    ws_client.stop()
    coinbase_client.stop()
    futures_ws_client.stop()
    cross_asset_client.stop()
    signal_buffer.save(SIGNAL_HISTORY_PATH, force=True)
    await rest_client.close()
    await bybit_client.close()
    await multi_exchange_client.close()
    await sentiment_client.close()
    await macro_client.close()
    await chainlink_client.close()
    await deribit_client.close()
    await cme_basis_client.close()
    await stablecoin_client.close()
    await exchange_flow_client.close()


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Connected Frontend Clients
clients: list[WebSocket] = []

# Global State
rest_client = BinanceRESTClient()
sentiment_client = SentimentClient()
ws_client = BinanceWebSocketClient()
futures_ws_client = BinanceFuturesWebSocketClient()
coinbase_client = CoinbaseWebSocketClient()
bybit_client = BybitRESTClient()
cross_asset_client = CrossAssetWebSocketClient()
order_flow = OrderFlowAnalyzer(whale_threshold_btc=0.5)

# Institutional alpha feeds
deribit_client = DeribitOptionsClient()
cme_basis_client = CMEBasisClient()
stablecoin_client = StablecoinFlowClient()
exchange_flow_client = ExchangeFlowClient()

# Macro
macro_client = TradFiMacroClient()

# Chainlink (CoinGecko BTC/USD proxy) — real oracle-reference spot for the consensus strip
chainlink_client = ChainlinkRESTClient()

# Polymarket
polymarket_client = PolymarketClient()

model = MultiModelEnsemble()
cascade_monitor = CascadeMonitor()
model.cascade_monitor = cascade_monitor
backtester = Backtester()
verifier = PredictionVerifier()
model_verifier = PerModelVerifier(horizons=(1, 3, 5, 7, 10, 15))
# 1m/3m/7m/10m are PRACTICE mirrors (Polymarket's real BTC windows are 5m/15m):
# same rule, same grading — they accrue evidence fast and map every horizon's
# betting behavior; only 5m/15m are real markets.
price_to_beat_tracker = PriceToBeatTracker(horizons=(1, 3, 5, 7, 10, 15))
# Binance-priced MIRROR of the same up/down game — anchored on the live Binance feed
# (the model's native data) instead of Pyth. In-memory only (persist=False) so it cannot
# collide with the Pyth tracker's rows in the shared `price_to_beat` table. Powers the
# "Binance — Price to Beat" tab; rebuilds live after a restart (no rehydration).
price_to_beat_binance_tracker = PriceToBeatTracker(horizons=(1, 3, 5, 7, 10, 15), persist=False)
exchange_verifier = PerVenueVerifier(horizons=(5, 15))
multi_exchange_client = MultiExchangePriceClient()
simulator = TradingSimulator()
regime_engine = MarketRegime()
signal_buffer = LiveSignalHistoryBuffer(
    maxlen=MAX_KLINES
)  # per-candle live-signal history for training
meta_models = {
    h: TrainedMetaModel() for h in model.horizons
}  # trust filter per horizon

# A/B Testing: Initialize Challenger Variant
challenger_config = {
    "base_weights": {
        "catboost": 0.45,
        "xgboost": 0.25,
        "histgb": 0.15,
        "dl": 0.10,
        "lr": 0.05,
        "sgd": 0.05,
    },
    "confidence_threshold": 0.68,  # elevated threshold for stability
    "enforce_quantile_skip": True,  # strictly skip wide variance
}
challenger_model = MultiModelEnsemble(horizons=model.horizons, config=challenger_config)

ab_runner = ABTestRunner(
    primary=ModelVariant("baseline_v9", model),
    challenger=ModelVariant("challenger_cat_v1", challenger_model),
)

# Polymarket Value Engine
pm_model = PolymarketModel()
pm_simulator = PolymarketSimulator()
pm_verifier = PolymarketVerifier()
fsr_ppo_strategy = FSRPPOStrategy()
# v6 R3: mothballed by default — a strategy challenger is premature pre-edge.
FSR_PPO_ENABLED = os.getenv("BTC_FSR_PPO", "0") == "1"

global_oi_history = []  # rolling history of combined OI in USD
binance_oi_history = []  # rolling history of Binance OI in USD
bybit_oi_history = []  # rolling history of Bybit OI in USD
coinbase_premium_history = []  # rolling (value, t) for premium velocity

backend_state = {
    "is_training": False,
    "last_backtest": None,
    "last_train_time": 0,
    "last_prediction_record_time": 0,
    "startup_start_time": time.time(),
    "ready_time": 0,
    "boot_seconds": 0.0,
    "restored_pending_predictions": 0,
    "last_analysis_snapshot_time": 0,
    "last_fsr_ppo_summary_time": 0,
    "last_fsr_ppo_summary": {},
    "backtest_status": _fresh_status(),
    "relearn_status": _fresh_status(),
}

backtest_task = None
relearn_task = None

data_state = {
    "klines": [],
    "klines_5m": [],
    "klines_15m": [],
    "order_flow": {},
    "derivatives": {},
    "sentiment": {},
    "coinbase_premium": 0.0,
    "bybit_data": {},
    "poor_regimes": {},
    "macro": {"dxy": 104.5, "us10y": 4.25},
    "eth_price": 0.0,
    "eth_volume": 0.0,
    "eth_imbalance": 0.0,
    "sol_price": 0.0,
    "sol_volume": 0.0,
    "sol_imbalance": 0.0,
}


async def fast_price_broadcaster():
    """High-frequency, lightweight price push, decoupled from the heavy prediction
    loop so the UI price stays responsive even while main_loop is busy/CPU-bound.
    Sends only {type:'price_tick', price, ts} when the price changes."""
    last_sent = None
    while True:
        try:
            # Prefer the real-time aggTrade price (sub-100ms); fall back to the forming
            # 1m candle close only if no trade has arrived yet.
            price = data_state.get("live_price")
            if price is None:
                kl = data_state.get("klines")
                price = kl[-1].get("close") if kl else None
            if price is not None and price != last_sent:
                last_sent = price
                await broadcast({
                    "type": "price_tick",
                    "price": price,
                    "ts": int(time.time() * 1000),
                })
        except Exception:
            pass
        await asyncio.sleep(0.25)


# Pyth BTC/USD feed — the price-to-beat ANCHOR oracle. Polymarket settles on the Chainlink
# BTC/USD data stream; the on-chain Chainlink feed lags (heartbeat/deviation) while Pyth is
# sub-second and tracks the real strike within ~$2-6 (operator-verified). Model inputs stay
# Binance — only the price-to-beat reference uses this.
PYTH_BTC_ID = "e62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43"


async def pyth_price_poller():
    """Poll Pyth BTC/USD ~every 1.5s for the price-to-beat anchor. Runs the blocking HTTP
    call in a worker thread so it never stalls the event loop. Stores price + timestamp;
    the ticker falls back to Binance if this goes stale, so the panel never freezes."""
    loop = asyncio.get_event_loop()

    def _fetch():
        r = requests.get("https://hermes.pyth.network/v2/updates/price/latest",
                         params={"ids[]": PYTH_BTC_ID}, timeout=8)
        p = r.json()["parsed"][0]["price"]
        return float(p["price"]) * (10 ** int(p["expo"]))

    while True:
        try:
            price = await loop.run_in_executor(None, _fetch)
            if price and price > 0:
                data_state["pyth_price"] = price
                data_state["pyth_price_ts"] = time.time()
        except Exception:
            pass
        await asyncio.sleep(1.5)


async def price_to_beat_ticker():
    """Resolve/open the Polymarket 5m/15m windows and refresh the HOLD/EXIT advice on a
    fast 1s cadence, DECOUPLED from the heavy prediction loop.

    ANCHOR = Pyth BTC/USD (matches Polymarket's Chainlink settlement family within a few $),
    so the displayed price-to-beat equals the real market. Falls back to the live Binance
    price if Pyth is stale (>10s) so the panel never freezes. SAME-FEED rule: when anchored
    on Pyth we pass klines=None (no Binance-kline boundary recovery — mixing feeds would
    re-introduce the very offset we're removing); the freshness guard + 1.5s polling keep
    captures accurate. Model inputs/feature pipeline are unchanged (Binance)."""
    _last_ref = None
    _last_change_t = time.time()
    _pyth_offset = None  # EWMA of (pyth - binance) while both feeds are fresh
    _last_binance = None         # freshness tracking for the Binance-priced mirror
    _last_binance_t = time.time()
    while True:
        try:
            pyth = data_state.get("pyth_price")
            pyth_fresh = bool(pyth and (time.time() - data_state.get("pyth_price_ts", 0)) < 10.0)
            binance_live = data_state.get("live_price")
            if pyth_fresh:
                ref = pyth
                kl = None  # same-feed: do NOT recover boundary from Binance klines
                # Track the venue offset while both feeds are healthy, so a later
                # Pyth outage can fall back to Binance CONVERTED INTO PYTH UNITS.
                if binance_live:
                    d = float(pyth) - float(binance_live)
                    _pyth_offset = d if _pyth_offset is None else (0.98 * _pyth_offset + 0.02 * d)
            else:
                ref = binance_live
                if ref is None and data_state.get("klines"):
                    ref = data_state["klines"][-1].get("close")
                # MID-ROUND VENUE-MIXING guard: rounds may have anchored on Pyth, so a raw
                # Binance fallback would resolve them against a different feed (the ~$40-80
                # venue offset the Pyth anchor exists to remove). Apply the learned offset
                # so the fallback stays in Pyth units; klines (Binance units) are then
                # unusable for boundary recovery, so pass None in that case.
                if ref is not None and _pyth_offset is not None:
                    ref = float(ref) + _pyth_offset
                    kl = None
                else:
                    kl = data_state.get("klines")
            if ref:
                # FEED-FRESHNESS guard: a ref unchanged for >10s means the anchor feed is
                # frozen; still resolve/refresh, but DO NOT open new rounds at a stale price.
                if ref != _last_ref:
                    _last_ref = ref
                    _last_change_t = time.time()
                feed_fresh = (time.time() - _last_change_t) < 10.0
                price_to_beat_tracker.update(
                    int(time.time() * 1000),
                    float(ref),
                    data_state.get("_ptb_preds") or {},
                    {},  # kronos removed in v6 — tracker records "NONE"
                    klines=kl,
                    feed_fresh=feed_fresh,
                )
            # Binance-priced MIRROR: always anchor on the live Binance feed (same-feed,
            # so klines boundary-recovery is valid). In-memory tracker → Binance PtB tab.
            if binance_live:
                if binance_live != _last_binance:
                    _last_binance = binance_live
                    _last_binance_t = time.time()
                price_to_beat_binance_tracker.update(
                    int(time.time() * 1000),
                    float(binance_live),
                    data_state.get("_ptb_preds") or {},
                    {},
                    klines=data_state.get("klines"),
                    feed_fresh=(time.time() - _last_binance_t) < 10.0,
                )
        except Exception:
            pass
        await asyncio.sleep(1.0)


async def broadcast(message: dict):
    # allow_nan=False so a non-finite float can't slip through. Python's json.dumps
    # defaults to allow_nan=True and emits a literal `NaN`/`Infinity`, which the
    # browser's JSON.parse REJECTS — silently dropping that whole update (and trapping
    # the loading splash on the very first one). Fast path assumes a clean payload;
    # only when a NaN/inf is present do we pay the recursive sanitize. (deep-scan)
    try:
        msg_str = json.dumps(message, default=_json_serialize, allow_nan=False)
    except (ValueError, TypeError):
        msg_str = json.dumps(_sanitize_nonfinite(message), default=_json_serialize, allow_nan=False)
    disconnected = []
    # Iterate a snapshot: main_loop and fast_price_broadcaster both call broadcast(),
    # so the live `clients` list can be mutated mid-iteration by the other coroutine.
    for ws in list(clients):
        try:
            await ws.send_text(msg_str)
        except Exception:
            disconnected.append(ws)

    for ws in disconnected:
        if ws in clients:
            clients.remove(ws)


def _json_serialize(obj):
    """Handle numpy types in JSON serialization."""
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    return str(obj)


def _finite_or_none(f):
    # NaN: f != f. inf/-inf: direct compare. Both are invalid JSON for the browser.
    return f if (f == f and f != float("inf") and f != float("-inf")) else None


def _sanitize_nonfinite(obj):
    """Recursively replace NaN/inf with None so the payload is browser-parseable JSON.
    Only called as a fallback when a non-finite float was detected in broadcast()."""
    if isinstance(obj, dict):
        return {k: _sanitize_nonfinite(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_nonfinite(v) for v in obj]
    if isinstance(obj, float):
        return _finite_or_none(obj)
    if isinstance(obj, np.floating):
        return _finite_or_none(float(obj))
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.ndarray):
        return _sanitize_nonfinite(obj.tolist())
    return obj


def _safe_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _safe_list(value) -> list:
    return value if isinstance(value, list) else []


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _interval_seconds(interval: str) -> int:
    return {
        "1m": 60,
        "3m": 180,
        "5m": 300,
        "15m": 900,
        "1h": 3600,
    }.get(interval, 60)


def _historical_cache_path(interval: str, days: int) -> str:
    safe_interval = interval.replace("/", "_")
    return os.path.join(
        HISTORICAL_CACHE_DIR, f"btcusdt_{safe_interval}_{int(days)}d.json"
    )


def _merge_klines(rows: list[dict]) -> list[dict]:
    by_time = {}
    for row in rows:
        if isinstance(row, dict) and row.get("time") is not None:
            by_time[int(row["time"])] = row
    return [by_time[t] for t in sorted(by_time)]


def _trim_klines_window(rows: list[dict], days: int) -> list[dict]:
    if not rows:
        return []
    cutoff = int(time.time()) - int(days) * 24 * 60 * 60
    trimmed = [r for r in rows if int(r.get("time", 0)) >= cutoff]
    return trimmed or rows


def _load_historical_cache(interval: str, days: int):
    path = _historical_cache_path(interval, days)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if payload.get("cache_version") != HISTORICAL_CACHE_VERSION:
            return None
        if payload.get("interval") != interval or payload.get("days") != days:
            return None
        rows = payload.get("klines")
        if not isinstance(rows, list) or not rows:
            return None
        return rows
    except Exception as e:
        logger.warning("[BOOT CACHE] Failed to load %s cache: %s", interval, e)
        return None


def _save_historical_cache(interval: str, days: int, rows: list[dict]) -> None:
    try:
        os.makedirs(HISTORICAL_CACHE_DIR, exist_ok=True)
        with open(_historical_cache_path(interval, days), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "cache_version": HISTORICAL_CACHE_VERSION,
                    "saved_at": time.time(),
                    "interval": interval,
                    "days": days,
                    "klines": rows,
                },
                f,
                default=_json_serialize,
            )
    except Exception as e:
        logger.warning("[BOOT CACHE] Failed to save %s cache: %s", interval, e)


async def _fetch_historical_cached(interval: str, days: int) -> list[dict]:
    cached = _load_historical_cache(interval, days)
    now_ms = int(time.time() * 1000)
    interval_ms = _interval_seconds(interval) * 1000

    if cached:
        cached = _merge_klines(cached)
        last_time = int(cached[-1].get("time", 0))
        gap_seconds = max(0, int(time.time()) - last_time)
        if gap_seconds <= _interval_seconds(interval) * 2:
            logger.info(
                "[BOOT CACHE] Loaded fresh %s cache: rows=%s gap=%ss",
                interval,
                len(cached),
                gap_seconds,
            )
            return cached

        if gap_seconds <= HISTORICAL_CACHE_REFRESH_MAX_GAP_SECONDS:
            logger.info(
                "[BOOT CACHE] Refreshing %s cache gap only: rows=%s gap=%ss",
                interval,
                len(cached),
                gap_seconds,
            )
            missing = await rest_client.fetch_historical_klines(
                interval,
                days=days,
                start_time_ms=last_time * 1000 + interval_ms,
                end_time_ms=now_ms,
            )
            merged = _trim_klines_window(_merge_klines(cached + (missing or [])), days)
            _save_historical_cache(interval, days, merged)
            return merged

        logger.info(
            "[BOOT CACHE] %s cache is too stale for gap refresh: gap=%ss",
            interval,
            gap_seconds,
        )

    rows = await rest_client.fetch_historical_klines(interval, days=days)
    rows = _trim_klines_window(_merge_klines(rows or []), days)
    if rows:
        _save_historical_cache(interval, days, rows)
    return rows


async def _best_effort(label: str, coro, timeout: float):
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("%s timed out after %.1fs; continuing with defaults", label, timeout)
    except Exception as e:
        logger.warning("%s failed: %s; continuing with defaults", label, e)
    return None


def _set_status(key: str, **updates) -> dict:
    status = backend_state.setdefault(key, _fresh_status())
    status.update(updates)
    if status.get("started_at") and status.get("running"):
        status["elapsed_seconds"] = round(time.time() - status["started_at"], 1)
    elif status.get("started_at") and status.get("completed_at"):
        status["elapsed_seconds"] = round(status["completed_at"] - status["started_at"], 1)
    return status


def _safe_public_status(status: dict) -> dict:
    out = dict(status or _fresh_status())
    started = out.get("started_at")
    completed = out.get("completed_at")
    if started and out.get("running"):
        out["elapsed_seconds"] = round(time.time() - started, 1)
    elif started and completed:
        out["elapsed_seconds"] = round(completed - started, 1)
    return out


def _save_backtest_cache(results: dict) -> None:
    try:
        os.makedirs(os.path.dirname(BACKTEST_CACHE_PATH), exist_ok=True)
        with open(BACKTEST_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "cache_version": BACKTEST_CACHE_VERSION,
                    "saved_at": time.time(),
                    "historical_days": HISTORICAL_DAYS,
                    # Cache is only valid for the model that produced it: without this key,
                    # a retrain's startup path loaded the PREVIOUS model's backtest and
                    # skipped running a fresh one — the UI showed stale stats as current.
                    "model_arch": MODEL_ARCH_VERSION,
                    "results": results,
                },
                f,
                default=_json_serialize,
            )
    except Exception as e:
        logger.warning(f"[BACKTEST] Failed to save cache: {e}")


def _load_backtest_cache() -> bool:
    if not os.path.exists(BACKTEST_CACHE_PATH):
        return False
    try:
        with open(BACKTEST_CACHE_PATH, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if payload.get("cache_version") != BACKTEST_CACHE_VERSION:
            return False
        if payload.get("historical_days") != HISTORICAL_DAYS:
            return False
        if payload.get("model_arch") != MODEL_ARCH_VERSION:
            logger.info("[BACKTEST] Cache is for a different model arch (%s) — discarding; "
                        "a fresh backtest will run for the current model.",
                        payload.get("model_arch"))
            return False
        backend_state["last_backtest"] = payload.get("results")
        backend_state["last_backtest_time"] = payload.get("saved_at", time.time())
        _set_status(
            "backtest_status",
            running=False,
            phase="cached",
            message="Loaded cached backtest results.",
            progress=1.0,
            started_at=payload.get("saved_at"),
            completed_at=payload.get("saved_at"),
            error=None,
        )
        logger.info("[BACKTEST] Loaded cached results from %s", BACKTEST_CACHE_PATH)
        return True
    except Exception as e:
        logger.warning(f"[BACKTEST] Failed to load cache: {e}")
        return False


def handle_trade(trade):
    trade["freshness_ms"] = (
        int(time.time() * 1000) - trade.get("receive_time", 0)
        if trade.get("receive_time")
        else 0
    )
    # Capture the real-time last trade price (Binance aggTrade, ~sub-100ms) so the live
    # price display is driven by actual trades — NOT the 1m kline close, which only refreshes
    # every ~1-2s and lags noticeably (worse while the loop is CPU-busy). This is the
    # freshest BTC price the app sees.
    _tp = trade.get("price")
    if _tp:
        data_state["live_price"] = float(_tp)
        data_state["live_price_ts"] = int(time.time() * 1000)
    order_flow.process_trade(trade)
    data_state["order_flow"] = order_flow.get_summary()
    data_state["order_flow"]["freshness_ms"] = trade["freshness_ms"]
    
    # Fire and forget parquet log
    import database
    database.log_raw_trade_parquet(trade)


def handle_depth(depth: dict) -> None:
    depth["freshness_ms"] = (
        int(time.time() * 1000) - depth.get("receive_time", 0)
        if depth.get("receive_time")
        else 0
    )
    order_flow.process_depth(depth)
    data_state["order_flow"] = order_flow.get_summary()
    data_state["order_flow"]["freshness_ms"] = depth["freshness_ms"]
    
    # Log orderbook to Parquet
    import database
    database.log_depth_parquet(depth)


def handle_kline(kline: dict) -> None:
    if not data_state["klines"]:
        return

    last_kline = data_state["klines"][-1]
    if kline["time"] == last_kline["time"]:
        data_state["klines"][-1] = kline
    elif kline["time"] > last_kline["time"]:
        # The previous candle just closed — snapshot the live signals for it so
        # they can be replayed per-bar during training (fixes the dead-feature bug).
        signal_buffer.record(last_kline["time"], data_state)
        data_state["klines"].append(kline)
        # Persist OFF the event loop. The old synchronous save() pickled the whole
        # multi-MB buffer right here on the WS path — and since it only wrote every 5th
        # candle, the freeze landed EXACTLY on every 5m window boundary (the operator's
        # "Polymarket window freezes + wrong reference price" report). Snapshot is ~ms;
        # the pickle+rename runs in a worker thread.
        if signal_buffer._dirty_count >= 5:
            _sb_payload = signal_buffer.snapshot_payload()
            asyncio.get_event_loop().run_in_executor(
                None, signal_buffer.write_payload, _sb_payload, SIGNAL_HISTORY_PATH)
        # Bound memory while preserving the configured training window.
        if len(data_state["klines"]) > MAX_KLINES:
            data_state["klines"].pop(0)


def handle_cross_asset_trade(trade: dict) -> None:
    asset = trade.get("asset")
    if asset == "ETH":
        data_state["eth_price"] = trade["price"]
    elif asset == "SOL":
        data_state["sol_price"] = trade["price"]

def handle_cross_asset_depth(depth: dict) -> None:
    asset = depth.get("asset")
    bids = depth.get("bids", [])
    asks = depth.get("asks", [])
    bid_vol = sum(q for p, q in bids[:10])
    ask_vol = sum(q for p, q in asks[:10])
    imb = (bid_vol - ask_vol) / max(1e-9, bid_vol + ask_vol)
    if asset == "ETH":
        data_state["eth_imbalance"] = imb
    elif asset == "SOL":
        data_state["sol_imbalance"] = imb

def handle_cross_asset_kline(kline: dict) -> None:
    asset = kline.get("asset")
    if asset == "ETH":
        data_state["eth_price"] = kline["close"]
        data_state["eth_volume"] = kline["volume"]
    elif asset == "SOL":
        data_state["sol_price"] = kline["close"]
        data_state["sol_volume"] = kline["volume"]


def handle_coinbase_ticker(ticker: dict) -> None:
    if data_state["klines"]:
        binance_price = data_state["klines"][-1]["close"]
        coinbase_price = ticker["price"]
        data_state["coinbase_premium"] = coinbase_price - binance_price


def handle_perp_bar(bar: dict) -> None:
    """A4 parity recorder: persist one finalized live perp-CVD 1m bar. Crash-safe — a logging
    failure must never affect the feed. Spot leg already lives in trade_features; this fills the
    perp leg so a future retrain can add the spot-vs-perp divergence feature with train/serve parity."""
    try:
        database.log_perp_cvd_bar(bar["ts"], bar["cvd_perp"], bar["vol_perp"], bar["perp_price"])
    except Exception as e:
        logger.debug(f"perp-cvd bar log skipped: {e}")


def handle_liquidation(liq: dict) -> None:
    # Maintain a rolling window of liquidations (last 60 seconds)
    now = time.time() * 1000
    # Guard: a slow-data poll may have replaced `liquidations` with the REST client's
    # vestigial empty list. Reset to a dict so the aggregation keeps working.
    der = data_state["derivatives"]
    if not isinstance(der.get("liquidations"), dict):
        der["liquidations"] = {}
    recent = der["liquidations"].setdefault("recent", [])

    # Add new
    recent.append(liq)

    # Remove older than 60s
    recent = [liq_item for liq_item in recent if now - liq_item["time"] <= 60000]

    # Re-calculate volumes
    long_vol = sum(
        (liq_item["qty"] * liq_item["price"])
        for liq_item in recent
        if liq_item["side"] == "SELL"
    )
    short_vol = sum(
        (liq_item["qty"] * liq_item["price"])
        for liq_item in recent
        if liq_item["side"] == "BUY"
    )

    data_state["derivatives"]["liquidations"] = {
        "recent": recent,
        "long_vol": long_vol,
        "short_vol": short_vol,
        "imbalance": long_vol - short_vol,
    }


def refresh_derivatives_from_rest() -> None:
    """Replace derivatives with the latest REST snapshot WITHOUT clobbering the
    WebSocket-maintained liquidation aggregation (which the REST payload lacks)."""
    liq = data_state.get("derivatives", {}).get("liquidations")
    data_state["derivatives"] = _safe_dict(rest_client.data)
    if isinstance(liq, dict) and ("long_vol" in liq or "recent" in liq):
        data_state["derivatives"]["liquidations"] = liq


def current_venue_prices(data_state: dict) -> dict:
    """Latest BTC spot price per venue from whatever feeds are live."""
    klines = data_state.get("klines") or []
    binance = float(klines[-1]["close"]) if klines else None
    prem = data_state.get("coinbase_premium", 0.0) or 0.0
    mx = _safe_dict(data_state.get("multi_exchange"))
    chainlink = data_state.get("chainlink_price") or None
    return {
        "binance": binance,
        "coinbase": (binance + prem) if binance is not None else None,
        "bybit": mx.get("bybit"),
        "kucoin": mx.get("kucoin"),
        "chainlink": float(chainlink) if chainlink else None,
    }


def build_exchanges_block(data_state: dict) -> dict:
    """Multi-venue BTC spot consensus + per-exchange deviation (lead/lag signal)."""
    venues = current_venue_prices(data_state)
    valid = [v for v in venues.values() if v and v > 0]
    consensus = sorted(valid)[len(valid) // 2] if valid else None  # median
    out = {"consensus": round(consensus, 2) if consensus else None, "venues": {}}
    best_dev = None
    best_venue = None
    for name, price in venues.items():
        if price and consensus:
            dev_bps = round((price - consensus) / consensus * 10000, 2)
            out["venues"][name] = {"price": round(price, 2), "deviation_bps": dev_bps}
            if best_dev is None or dev_bps > best_dev:
                best_dev, best_venue = dev_bps, name
        elif price:
            out["venues"][name] = {"price": round(price, 2), "deviation_bps": 0.0}
        else:
            out["venues"][name] = {"price": None, "deviation_bps": None}
    # Lead venue = highest above consensus (aggressive demand leading); fragmentation =
    # max-min spread across venues (high = stress / cross-venue dislocation / arb signal).
    if valid and consensus:
        frag_bps = round((max(valid) - min(valid)) / consensus * 10000, 2)
    else:
        frag_bps = None
    out["lead_venue"] = best_venue
    out["lead_bps"] = best_dev
    out["fragmentation_bps"] = frag_bps
    return out


def build_scoreboard(predictions: list, verification: dict) -> dict:
    """Compact 5m/15m decision board: our call + conviction (kronos removed in v6)."""
    by_h = {p.get("horizon"): p for p in (predictions or [])}
    acc = _safe_dict(verification.get("accuracy")) if verification else {}
    board = {}
    for h in (5, 15):
        p = by_h.get(h) or {}
        a = _safe_dict(acc.get(h) or acc.get(str(h)))
        board[h] = {
            "direction": p.get("direction", "NEUTRAL"),
            "rawDirection": p.get("rawDirection", p.get("direction", "NEUTRAL")),
            "signal": p.get("signal", "ABSTAIN"),
            "confidence": p.get("confidence", 0.0),
            "conviction": p.get("conviction", 0.0),
            "convictionGrade": p.get("convictionGrade", "WATCH"),
            "actionable": p.get("actionable", False),
            "confluence": p.get("confluence", 0.0),
            "confluenceDetail": p.get("confluenceDetail", {}),
            "expectedMove": p.get("expectedMove", 0.0),
            "targetPrice": p.get("targetPrice"),
            # our live record
            "ourAccuracy": a.get("directional_accuracy", a.get("accuracy", 0.0)),
            "ourSamples": a.get("directional_total", a.get("total", 0)),
        }
    return board


def build_meta_context(p: dict, seq, regime_name: str, now_ms: int) -> dict:
    """Assemble the prediction-time context the trained meta-model uses/learns from."""
    last = seq[-1] if seq is not None and len(seq) else None

    def g(i):
        try:
            return float(last[i])
        except Exception:
            return 0.0

    horizon = p.get("horizon", 5)
    last_backtest = _safe_dict(backend_state.get("last_backtest"))
    wf_map = _safe_dict(last_backtest.get("walk_forward"))
    wf_res = _safe_dict(wf_map.get(horizon) or wf_map.get(str(horizon)))
    wf_age_minutes = 0.0
    if backend_state.get("last_backtest_time"):
        wf_age_minutes = (time.time() - backend_state["last_backtest_time"]) / 60.0

    return {
        "confidence": p.get("confidence", 0.0),
        "agreement": p.get("agreement", 0.0),
        "regime": regime_name,
        "ewma_vol": g(50),  # feature 50
        "spread_norm": g(15),  # feature 15
        "wall_imbalance": g(52),  # feature 52
        "sr_compression": g(59),  # feature 59
        "liq_imbalance": g(44),  # feature 44
        "quantile_width_pct": p.get("quantile_width_pct", 0.0),
        "quantile_asymmetry": p.get("quantile_asymmetry", 0.0),
        "quantile_spread": p.get("quantileSpread", 0.0),
        "wf_accuracy": wf_res.get("mean_directional_accuracy", 0.5),
        "wf_accuracy_minus_0_5": wf_res.get("mean_directional_accuracy", 0.5) - 0.5,
        "wf_fold_std": wf_res.get("std_accuracy", 0.0),
        "wf_sample_count": wf_res.get("sample_count", 0),
        "wf_age_minutes": round(wf_age_minutes, 1),
        "hour_utc": (now_ms // 3600000) % 24,
        "tradeability": p.get("tradeability", 0.0),
        "regime_score": p.get("regimeScore", 0.0),
        "liquidity_score": p.get("liquidityScore", 0.0),
        "expected_edge": p.get("expectedEdge", 0.0),
        "conviction": p.get("conviction", 0.0),
        "actionable": 1.0 if p.get("actionable") else 0.0,
        "confluence": p.get("confluence", 0.0),
    }


def _pct_change(history: list) -> float:
    """Percent change between first and last entry of an OI history list."""
    if len(history) >= 2:
        prev = history[0]["value"]
        last = history[-1]["value"]
        return ((last - prev) / prev * 100) if prev > 0 else 0.0
    return 0.0


def prepare_derivatives_data():
    """Inject dynamic cross-exchange metrics into derivatives dict."""
    der = _safe_dict(data_state.get("derivatives"))
    data_state["derivatives"] = der
    bybit = _safe_dict(data_state.get("bybit_data"))
    der["coinbase_premium"] = data_state.get("coinbase_premium", 0.0)
    # Feed the live Chainlink/CoinGecko oracle price to the ML pipeline. features.py
    # converts it to a BOUNDED normalized deviation vs Binance (col 51) and a fair-value
    # mean-reversion signal (col 60); 0.0 means "no oracle yet" (neutral), so saved
    # models stay compatible while the column goes live once the oracle polls. (P4.2)
    der["chainlink_price"] = float(data_state.get("chainlink_price") or 0.0)
    der["bybit_funding_rate"] = bybit.get("funding_rate")

    # Coinbase premium velocity (USD/sec over the tracked window)
    prem_vel = 0.0
    if len(coinbase_premium_history) >= 2:
        old = coinbase_premium_history[0]
        recent = coinbase_premium_history[-1]
        dt = recent["t"] - old["t"]
        if dt > 0:
            prem_vel = (recent["value"] - old["value"]) / dt
    der["coinbase_premium_velocity"] = prem_vel

    # Calculate global OI change from history
    der["global_oi_change"] = _pct_change(global_oi_history)

    # Per-exchange OI change + divergence (Binance %chg - Bybit %chg)
    binance_oi_ch = _pct_change(binance_oi_history)
    bybit_oi_ch = _pct_change(bybit_oi_history)
    der["binance_oi_change"] = binance_oi_ch
    der["bybit_oi_change"] = bybit_oi_ch
    der["oi_divergence"] = binance_oi_ch - bybit_oi_ch

    # Calculate current combined Open Interest in USD
    binance_oi_usd = 0.0
    oi_hist = _safe_list(der.get("oi_history"))
    if oi_hist:
        binance_oi_usd = _safe_float(_safe_dict(oi_hist[-1]).get("sum_oi_value"))
    else:
        binance_oi_btc = _safe_float(
            _safe_dict(der.get("open_interest")).get("open_interest")
        )
        last_price = (
            data_state["klines"][-1]["close"] if data_state["klines"] else 60000.0
        )
        binance_oi_usd = binance_oi_btc * last_price

    bybit_oi_btc = _safe_float(bybit.get("open_interest"))
    last_price = data_state["klines"][-1]["close"] if data_state["klines"] else 60000.0
    bybit_oi_usd = bybit_oi_btc * last_price

    der["global_oi"] = binance_oi_usd + bybit_oi_usd

    # Pass through regime and institutional data for feature extraction
    der["regime_data"] = data_state.get("regime_info", {})
    der["institutional"] = {
        "options": deribit_client.data,
        "basis": cme_basis_client.data,
        "stablecoin": stablecoin_client.data,
        "exchange_flow": exchange_flow_client.data,
    }

    # Fair-value engine: synthetic Binance/Coinbase true price + Binance deviation.
    last_price = data_state["klines"][-1]["close"] if data_state["klines"] else 0.0
    cb_prem = data_state.get("coinbase_premium", 0.0)
    if last_price > 0:
        P_b = last_price
        P_c = last_price + cb_prem
        fair = 0.55 * P_b + 0.45 * P_c
        der["fair_value"] = round(fair, 2)
        der["fv_deviation"] = round((P_b - fair) / fair * 100, 4) if fair > 0 else 0.0

    return der


def update_global_oi_history():
    der = _safe_dict(data_state.get("derivatives"))
    data_state["derivatives"] = der
    bybit = _safe_dict(data_state.get("bybit_data"))
    binance_oi_usd = 0.0
    oi_hist = _safe_list(der.get("oi_history"))
    if oi_hist:
        binance_oi_usd = _safe_float(_safe_dict(oi_hist[-1]).get("sum_oi_value"))
    else:
        binance_oi_btc = _safe_float(
            _safe_dict(der.get("open_interest")).get("open_interest")
        )
        last_price = (
            data_state["klines"][-1]["close"] if data_state["klines"] else 60000.0
        )
        binance_oi_usd = binance_oi_btc * last_price

    bybit_oi_btc = _safe_float(bybit.get("open_interest"))
    last_price = data_state["klines"][-1]["close"] if data_state["klines"] else 60000.0
    bybit_oi_usd = bybit_oi_btc * last_price

    global_oi_usd = binance_oi_usd + bybit_oi_usd
    now = time.time()
    global_oi_history.append({"value": global_oi_usd, "timestamp": now})
    binance_oi_history.append({"value": binance_oi_usd, "timestamp": now})
    bybit_oi_history.append({"value": bybit_oi_usd, "timestamp": now})
    for hist in (global_oi_history, binance_oi_history, bybit_oi_history):
        if len(hist) > 30:
            hist.pop(0)


async def train_model(target_model=None):
    """Train or retrain the multi-model ensemble."""
    target_model = target_model or model
    if len(data_state["klines"]) < LOOKBACK + 20:
        logger.info(
            "[TRAIN] Skipping training: only %s candles available, need at least %s",
            len(data_state["klines"]),
            LOOKBACK + 20,
        )
        return

    train_started = time.time()
    logger.info("[TRAIN] Preparing training data from %s candles", len(data_state["klines"]))
    prepare_derivatives_data()
    # Snapshot klines ONCE so the timestamp list, the threaded feature build, and the
    # closes/highs/lows arrays below all see the IDENTICAL bars. data_state["klines"] is
    # appended in place by the feed coroutine; with the build now off the event loop, a
    # candle arriving mid-build would otherwise desync features (N rows) from labels
    # (N+1 rows) and risk 'list changed size during iteration' in the worker. (deep-scan)
    kl_snapshot = list(data_state["klines"])
    train_ts = [k["time"] for k in kl_snapshot]
    sig_hist = signal_buffer.get_aligned_series(train_ts)
    # N4 merge: overlay historical trade-derived features (CVD) from the offline backfill
    # (data.binance.vision SPOT aggTrades → trade_features_backfill.parquet) onto candles
    # with no live snapshot. Defensive no-op if the parquet is absent, so it never blocks a
    # retrain; logs how many cells it filled so the effect is visible, not silent.
    try:
        _bf_path = os.path.join(DATA_DIR, "trade_features_backfill.parquet")
        _bf_filled = signal_buffer.overlay_backfill(sig_hist, train_ts, _bf_path)
        if _bf_filled:
            logger.info(f"[TRAIN] Backfill overlay filled {_bf_filled} CVD cells from {_bf_path}")
    except Exception as _bf_e:
        logger.debug(f"Backfill overlay skipped: {_bf_e}")
    logger.info(
        f"Signal-history coverage for training: {signal_buffer.coverage(train_ts) * 100:.1f}% of {len(train_ts)} candles"
    )
    feature_t0 = time.time()
    logger.info("[TRAIN] Building %s-feature matrix...", NUM_FEATURES)
    # Off-load the heavy feature build so it can't stall WebSocket pings. (#6)
    features = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: build_features_from_klines(
            kl_snapshot,
            data_state["order_flow"],
            data_state["derivatives"],
            data_state["sentiment"],
            signal_history=sig_hist,
        ),
    )
    logger.info(
        "[TRAIN] Feature matrix complete: rows=%s cols=%s elapsed=%.1fs",
        features.shape[0] if hasattr(features, "shape") else len(features),
        features.shape[1] if hasattr(features, "shape") and len(features.shape) > 1 else "?",
        time.time() - feature_t0,
    )

    closes = np.array([k["close"] for k in kl_snapshot])
    highs = np.array([k["high"] for k in kl_snapshot])
    lows = np.array([k["low"] for k in kl_snapshot])
    volumes = np.array([k["volume"] for k in kl_snapshot])
    atr_arr = compute_atr(highs, lows, closes)

    # Fit data-driven temporal (HMM) regimes on the full history — replaces fixed thresholds.
    try:
        logger.info("[TRAIN] Fitting market regime engine...")
        regime_engine.fit_hmm(closes, volumes)
    except Exception as e:
        logger.warning(f"HMM regime fit skipped: {e}")

    # Train every horizon the model serves (was defaulting to [1,5,10,15],
    # leaving 3m and 7m with no trained model). Pass true highs/lows so the
    # triple-barrier labels use real intrabar extremes.
    seq_t0 = time.time()
    logger.info("[TRAIN] Building lookback sequences for horizons=%s...", target_model.horizons)
    X, Y, Ymag = build_sequences(
        features,
        closes,
        lookback=LOOKBACK,
        horizons=target_model.horizons,
        atr_arr=atr_arr,
        highs=highs,
        lows=lows,
        return_magnitude=True,
    )
    # P4.3 regime alignment: label every training row with the SAME HMM that routes at
    # serving time, so the regime experts train on the partition they answer for. X rows
    # correspond to build_sequences' loop range(LOOKBACK, len(features)-max_h) with decision
    # close index == i, so the label list aligns 1:1 with X. Defensive: any failure → None
    # → train() falls back to the legacy threshold clustering (no crash, no behaviour change).
    regime_labels = None
    try:
        _max_h = max(target_model.horizons)
        _reg_by_close = regime_engine.classify_series(closes, volumes)
        regime_labels = [
            _reg_by_close[i] if i < len(_reg_by_close) else "RANGE"
            for i in range(LOOKBACK, len(features) - _max_h)
        ]
        if hasattr(X, "shape") and len(regime_labels) != X.shape[0]:
            logger.warning("[TRAIN] P4.3 label/X length mismatch (%s vs %s) — disabling alignment.",
                           len(regime_labels), X.shape[0])
            regime_labels = None
        else:
            from collections import Counter as _Counter
            logger.info("[TRAIN] P4.3 regime label distribution: %s",
                        dict(_Counter(regime_labels)))
    except Exception as _re:
        logger.warning(f"[TRAIN] P4.3 regime labelling skipped: {_re}")
        regime_labels = None

    horizon_counts = {h: int(len(Y.get(h, []))) for h in target_model.horizons}
    label_counts = {}
    for h in target_model.horizons:
        if h in Y and len(Y[h]) > 0:
            labels = np.argmax(Y[h], axis=1)
            unique, counts = np.unique(labels, return_counts=True)
            label_counts[h] = {int(k): int(v) for k, v in zip(unique, counts)}
    logger.info(
        "[TRAIN] Sequence build complete: X=%s horizon_samples=%s label_counts=%s elapsed=%.1fs",
        getattr(X, "shape", None),
        horizon_counts,
        label_counts,
        time.time() - seq_t0,
    )

    if len(X) > 0:
        backend_state["is_training"] = True
        loop = asyncio.get_event_loop()
        logger.info("[TRAIN] Dispatching ensemble training to worker thread...")
        try:
            await loop.run_in_executor(
                None, functools.partial(target_model.train, X, Y, Ymag,
                                        regime_labels=regime_labels))
        finally:
            backend_state["is_training"] = False
        backend_state["last_train_time"] = time.time()
        # Record the TRAIN-SPLIT BOUNDARY so the backtest can evaluate strictly
        # held-out candles. train() fits on the first 80% of samples; sample k's
        # decision candle is kl_snapshot[LOOKBACK + k], so the last in-sample
        # decision candle is LOOKBACK + 0.8*n - 1. Without this, a latest-12000
        # backtest right after training overlaps ~28% TRAINING rows — silently
        # inflating every reported accuracy. Persisted so a restart that loads
        # models from disk (no retrain) keeps the same honest boundary.
        try:
            _n_samp = int(X.shape[0])
            _b_idx = min(LOOKBACK + int(_n_samp * 0.8) - 1, len(kl_snapshot) - 1)
            _b_ts = int(kl_snapshot[_b_idx]["time"])
            backend_state["train_boundary_ts"] = _b_ts
            with open(os.path.join(DATA_DIR, "saved_models", "train_boundary.json"),
                      "w", encoding="utf-8") as _bf:
                json.dump({"train_boundary_ts": _b_ts}, _bf)
            logger.info("[TRAIN] Out-of-sample boundary recorded at candle ts=%s", _b_ts)
        except Exception as _be:
            logger.warning(f"[TRAIN] Could not record train boundary: {_be}")
        logger.info("[TRAIN] Model training complete in %.1fs", time.time() - train_started)
        # Release training intermediates back to the OS promptly. The sequence tensor alone
        # is ~1.3GB (43k × 60 × 126 float32) and the OOF/stacker arrays add more; they are
        # locals (freed at return) but CPython's allocator holds the pages without an
        # explicit collect, leaving the process bloated for hours after training. The
        # MODELS themselves stay resident on purpose — live inference needs them; the
        # pickles in saved_models/ are the restart-time copy, not a swap-out.
        try:
            del X, Y, Ymag, features
            import gc
            gc.collect()
            logger.info("[TRAIN] Training intermediates released (gc.collect).")
        except Exception:
            pass
    else:
        logger.warning("[TRAIN] No sequences built; model training skipped")


async def run_backtest_legacy_unused():
    """Run walk-forward backtest."""
    if len(data_state["klines"]) < LOOKBACK + 20:
        logger.info("[BACKTEST] Skipping: only %s candles available", len(data_state["klines"]))
        return

    backtest_started = time.time()
    logger.info("[BACKTEST] Building validation feature matrix from %s candles", len(data_state["klines"]))
    prepare_derivatives_data()
    # Snapshot once so the threaded build and closes below align on identical bars. (deep-scan)
    kl_snapshot = list(data_state["klines"])
    bt_ts = [k["time"] for k in kl_snapshot]
    # Off-load the heavy feature build so it can't stall WebSocket pings. (#6)
    features = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: build_features_from_klines(
            kl_snapshot,
            data_state["order_flow"],
            data_state["derivatives"],
            data_state["sentiment"],
            signal_history=signal_buffer.get_aligned_series(bt_ts),
        ),
    )
    closes = np.array([k["close"] for k in kl_snapshot])
    logger.info(
        "[BACKTEST] Feature matrix ready: rows=%s cols=%s",
        features.shape[0] if hasattr(features, "shape") else len(features),
        features.shape[1] if hasattr(features, "shape") and len(features.shape) > 1 else "?",
    )

    if len(features) > LOOKBACK + 20:
        loop = asyncio.get_event_loop()
        logger.info("[BACKTEST] Running main backtest for horizons=%s", model.horizons)
        bt_res = await loop.run_in_executor(
            None,
            backtester.run,
            features,
            closes,
            model.horizons,
            model.predict_base,
            LOOKBACK,
        )

        # Honest out-of-sample check: strict temporal walk-forward on every horizon.
        # The in-sample backtest above is optimistic; this tells us whether the
        # numbers hold up on held-out future periods.
        try:
            from backtester import walk_forward_validate
            from features import build_sequences as _bs

            wf_features = features[-8000:] if len(features) > 8000 else features
            wf_closes = (
                closes[-(len(wf_features) + 1) :]
                if len(closes) > len(wf_features)
                else closes
            )
            Xv, Yv = _bs(
                wf_features, wf_closes, lookback=LOOKBACK, horizons=model.horizons
            )
            if len(Xv) > 300:
                Xflat = Xv.reshape((Xv.shape[0], -1))
                bt_res["walk_forward"] = {}
                for wf_h in model.horizons:
                    if wf_h not in Yv:
                        continue
                    logger.info("[BACKTEST] Running purged walk-forward validation for %sm", wf_h)
                    yv_all = np.argmax(Yv[wf_h], axis=1)
                    wf_embargo = LOOKBACK + wf_h
                    wf_all = await loop.run_in_executor(
                        None, walk_forward_validate, Xflat, yv_all, 5, None, wf_embargo
                    )
                    bt_res["walk_forward"][wf_h] = wf_all
                    bt_res[f"walk_forward_{wf_h}m"] = wf_all
                yv = np.argmax(Yv[5], axis=1)
                # Embargo = lookback + horizon so train/val windows can't overlap (purged WF).
                embargo = LOOKBACK + 5
                wf = await loop.run_in_executor(
                    None, walk_forward_validate, Xflat, yv, 5, None, embargo
                )
                bt_res["walk_forward_5m"] = wf
                if wf.get("is_below_chance"):
                    logger.warning(
                        f"⚠️ Walk-forward 5m is BELOW CHANCE (directional precision {wf.get('mean_directional_precision')} over {wf.get('directional_calls')} calls). Backtest numbers are optimistic / overfit."
                    )
                elif wf.get("is_overfit_warning"):
                    logger.warning(
                        f"⚠️ Walk-forward 5m is INCONSISTENT across folds (std {wf['std_accuracy']}). Regime-sensitive model."
                    )
                else:
                    logger.info(
                        f"Walk-forward 5m: mean dir acc {wf['mean_directional_accuracy']}, std {wf['std_accuracy']}"
                    )
        except Exception as e:
            logger.warning(f"Walk-forward validation skipped: {e}")

        backend_state["last_backtest"] = bt_res
        backend_state["last_backtest_time"] = time.time()
        logger.info("[BACKTEST] Backtest complete in %.1fs", time.time() - backtest_started)


async def run_backtest(reason: str = "manual"):
    """Run historical validation with progress, logging, and cached results."""
    global backtest_task
    if len(data_state["klines"]) < LOOKBACK + 20:
        logger.info("[BACKTEST] Skipping: only %s candles available", len(data_state["klines"]))
        _set_status(
            "backtest_status",
            running=False,
            phase="skipped",
            message="Not enough candles for backtest.",
            progress=0.0,
            completed_at=time.time(),
            error=None,
        )
        return

    backtest_started = time.time()
    _set_status(
        "backtest_status",
        running=True,
        phase="features",
        message=f"Building validation features ({reason})...",
        progress=0.02,
        started_at=backtest_started,
        completed_at=None,
        error=None,
    )
    try:
        bt_klines = list(data_state["klines"])  # copy: threaded build must not iterate the live list (deep-scan)
        if BACKTEST_MAX_ROWS and len(bt_klines) > BACKTEST_MAX_ROWS:
            logger.info(
                "[BACKTEST] Using latest %s of %s candles for validation. Set BTC_BACKTEST_MAX_ROWS=0 for full replay.",
                BACKTEST_MAX_ROWS,
                len(bt_klines),
            )
            bt_klines = bt_klines[-BACKTEST_MAX_ROWS:]
        # OUT-OF-SAMPLE GUARD: score only candles AFTER the training split boundary
        # (+ a max-horizon embargo so the last train labels' look-ahead windows can't
        # touch scored rows). Right after a fresh train, "latest 12000" overlapped
        # ~28% training rows — every backtest number was silently optimistic.
        _btb = backend_state.get("train_boundary_ts")
        if _btb:
            _emb = max(model.horizons)
            _first = next((j for j, k in enumerate(bt_klines) if k["time"] > _btb), None)
            if _first is None:
                logger.warning("[BACKTEST] ALL candles precede the train boundary — "
                               "metrics would be fully IN-SAMPLE; proceeding unfiltered (flagged).")
            else:
                _first = min(_first + _emb, len(bt_klines) - 1)
                _start = max(0, _first - LOOKBACK)  # keep lookback warm-up; scoring begins post-boundary
                _held = len(bt_klines) - _first
                if _held >= 500:
                    if _start > 0:
                        bt_klines = bt_klines[_start:]
                    logger.info("[BACKTEST] Out-of-sample only: %s held-out candles scored "
                                "(boundary ts=%s, embargo=%s candles).", _held, _btb, _emb)
                else:
                    logger.warning("[BACKTEST] Only %s held-out candles (<500) — keeping full "
                                   "window; treat metrics as partially in-sample.", _held)

        logger.info("[BACKTEST] Building validation feature matrix from %s candles", len(bt_klines))
        prepare_derivatives_data()
        bt_ts = [k["time"] for k in bt_klines]
        # Backtest must see the SAME feature distribution the model trained on: apply the
        # backfill overlay here exactly like the train path does, otherwise the backtest
        # evaluates a model on emptier features than it learned from (skewed metrics).
        bt_sig = signal_buffer.get_aligned_series(bt_ts)
        try:
            _bt_path = os.path.join(DATA_DIR, "trade_features_backfill.parquet")
            _bt_filled = signal_buffer.overlay_backfill(bt_sig, bt_ts, _bt_path)
            if _bt_filled:
                logger.info(f"[BACKTEST] Backfill overlay filled {_bt_filled} cells")
        except Exception as _bt_e:
            logger.debug(f"Backtest backfill overlay skipped: {_bt_e}")
        # Off-load the heavy feature build so it can't stall WebSocket pings. (#6)
        features = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: build_features_from_klines(
                bt_klines,
                data_state["order_flow"],
                data_state["derivatives"],
                data_state["sentiment"],
                signal_history=bt_sig,
            ),
        )
        closes = np.array([k["close"] for k in bt_klines])
        logger.info(
            "[BACKTEST] Feature matrix ready: rows=%s cols=%s",
            features.shape[0] if hasattr(features, "shape") else len(features),
            features.shape[1] if hasattr(features, "shape") and len(features.shape) > 1 else "?",
        )

        if len(features) <= LOOKBACK + 20:
            _set_status(
                "backtest_status",
                running=False,
                phase="skipped",
                message="Feature matrix too short for backtest.",
                progress=0.0,
                completed_at=time.time(),
                error=None,
            )
            return

        loop = asyncio.get_event_loop()
        total_horizons = max(1, len(model.horizons))

        def main_progress(info: dict):
            h = info.get("horizon", "?")
            processed = int(info.get("processed", 0) or 0)
            total = max(1, int(info.get("total", 1) or 1))
            horizon_idx = model.horizons.index(h) if h in model.horizons else 0
            horizon_frac = processed / total
            progress = 0.05 + 0.45 * ((horizon_idx + horizon_frac) / total_horizons)
            _set_status(
                "backtest_status",
                running=True,
                phase="main",
                message=f"Main backtest {h}m: {processed}/{total} samples",
                progress=round(min(progress, 0.50), 3),
            )
            if info.get("status") in ("started", "done") or processed % 3000 == 0:
                logger.info(
                    "[BACKTEST MAIN] h=%sm status=%s processed=%s/%s",
                    h,
                    info.get("status"),
                    processed,
                    total,
                )

        logger.info("[BACKTEST] Running main backtest for horizons=%s", model.horizons)
        bt_res = await loop.run_in_executor(
            None,
            lambda: backtester.run(
                features,
                closes,
                model.horizons,
                model.predict_base,
                LOOKBACK,
                progress_cb=main_progress,
            ),
        )

        try:
            from backtester import walk_forward_validate
            from features import build_sequences as _bs

            _set_status(
                "backtest_status",
                running=True,
                phase="walk_forward_prepare",
                message="Preparing purged walk-forward validation...",
                progress=0.52,
            )
            wf_features = features[-8000:] if len(features) > 8000 else features
            wf_closes = (
                closes[-(len(wf_features) + 1) :]
                if len(closes) > len(wf_features)
                else closes
            )
            Xv, Yv = _bs(
                wf_features, wf_closes, lookback=LOOKBACK, horizons=model.horizons
            )
            if len(Xv) > 300:
                Xflat = Xv.reshape((Xv.shape[0], -1))
                bt_res["walk_forward"] = {}
                for wf_h in model.horizons:
                    if wf_h not in Yv:
                        continue
                    wf_idx = model.horizons.index(wf_h)
                    _set_status(
                        "backtest_status",
                        running=True,
                        phase="walk_forward",
                        message=f"Walk-forward validation {wf_h}m...",
                        progress=round(0.55 + 0.40 * (wf_idx / total_horizons), 3),
                    )
                    logger.info("[BACKTEST] Running purged walk-forward validation for %sm", wf_h)
                    yv_all = np.argmax(Yv[wf_h], axis=1)
                    wf_embargo = LOOKBACK + wf_h

                    def wf_progress(info: dict, horizon=wf_h, idx=wf_idx):
                        fold = info.get("fold", "?")
                        n_folds = info.get("n_folds", "?")
                        try:
                            fold_frac = (float(fold) - 1.0) / max(1.0, float(n_folds))
                        except Exception:
                            fold_frac = 0.0
                        progress = 0.55 + 0.40 * ((idx + fold_frac) / total_horizons)
                        _set_status(
                            "backtest_status",
                            running=True,
                            phase="walk_forward",
                            message=f"Walk-forward {horizon}m fold {fold}/{n_folds}: {info.get('status')}",
                            progress=round(min(progress, 0.95), 3),
                        )
                        logger.info(
                            "[WALKFORWARD] h=%sm fold=%s/%s status=%s train=%s val=%s",
                            horizon,
                            fold,
                            n_folds,
                            info.get("status"),
                            info.get("train_bars"),
                            info.get("val_bars"),
                        )

                    wf_all = await loop.run_in_executor(
                        None,
                        lambda yv=yv_all, embargo=wf_embargo, cb=wf_progress: walk_forward_validate(
                            Xflat,
                            yv,
                            5,
                            None,
                            embargo,
                            "expanding",
                            cb,
                        ),
                    )
                    bt_res["walk_forward"][wf_h] = wf_all
                    bt_res[f"walk_forward_{wf_h}m"] = wf_all
                    logger.info(
                        "[WALKFORWARD] h=%sm precision=%s (calls=%s) recall=%s std=%s below_chance=%s",
                        wf_h,
                        wf_all.get("mean_directional_precision"),
                        wf_all.get("directional_calls"),
                        wf_all.get("mean_directional_accuracy"),
                        wf_all.get("std_accuracy"),
                        wf_all.get("is_below_chance"),
                    )
        except Exception as e:
            logger.warning(f"Walk-forward validation skipped: {e}")

        backend_state["last_backtest"] = bt_res
        backend_state["last_backtest_time"] = time.time()
        _save_backtest_cache(bt_res)
        _set_status(
            "backtest_status",
            running=False,
            phase="complete",
            message="Backtest complete.",
            progress=1.0,
            completed_at=time.time(),
            error=None,
        )
        logger.info("[BACKTEST] Backtest complete in %.1fs", time.time() - backtest_started)
    except Exception as e:
        _set_status(
            "backtest_status",
            running=False,
            phase="error",
            message="Backtest failed.",
            completed_at=time.time(),
            error=str(e),
        )
        logger.error(f"[BACKTEST] Failed: {e}")
    finally:
        backtest_task = None


def schedule_backtest(reason: str = "background") -> bool:
    """Start validation in the background if not already running."""
    global backtest_task
    if backtest_task and not backtest_task.done():
        logger.info("[BACKTEST] Already running; skip schedule request: %s", reason)
        return False
    backtest_task = asyncio.create_task(run_backtest(reason))
    logger.info("[BACKTEST] Scheduled background validation: %s", reason)
    return True


async def relearn_models_background(reason: str = "manual"):
    """Train a candidate ensemble in the background, then swap it in atomically."""
    global model, relearn_task
    started = time.time()
    if backend_state.get("is_training"):
        _set_status(
            "relearn_status",
            running=False,
            phase="blocked",
            message="Training is already running.",
            completed_at=time.time(),
            error="training_already_running",
        )
        relearn_task = None
        return

    _set_status(
        "relearn_status",
        running=True,
        phase="prepare",
        message=f"Preparing candidate model ({reason})...",
        progress=0.02,
        started_at=started,
        completed_at=None,
        error=None,
    )
    try:
        candidate = MultiModelEnsemble(
            horizons=model.horizons,
            config=getattr(model, "config", {}) or {},
        )
        candidate.cascade_monitor = cascade_monitor
        _set_status(
            "relearn_status",
            running=True,
            phase="training",
            message="Training candidate model in background...",
            progress=0.20,
        )
        await train_model(candidate)
        if not candidate.is_trained:
            raise RuntimeError("Candidate model did not finish training")

        _set_status(
            "relearn_status",
            running=True,
            phase="swap",
            message="Candidate trained. Swapping active model...",
            progress=0.90,
        )
        model = candidate
        model.cascade_monitor = cascade_monitor
        ab_runner.primary = ModelVariant(f"baseline_{int(time.time())}", model)
        _set_status(
            "relearn_status",
            running=False,
            phase="complete",
            message="Relearn complete. New model is active.",
            progress=1.0,
            completed_at=time.time(),
            error=None,
        )
        logger.info("[RELEARN] Candidate model swapped active in %.1fs", time.time() - started)
        schedule_backtest("post-relearn")
    except Exception as e:
        _set_status(
            "relearn_status",
            running=False,
            phase="error",
            message="Relearn failed. Existing model remains active.",
            completed_at=time.time(),
            error=str(e),
        )
        logger.error(f"[RELEARN] Failed: {e}")
    finally:
        relearn_task = None


def schedule_relearn(reason: str = "manual") -> bool:
    """Queue a full model relearn if one is not already running."""
    global relearn_task
    if relearn_task and not relearn_task.done():
        logger.info("[RELEARN] Already running; skip schedule request: %s", reason)
        return False
    if backend_state.get("is_training"):
        logger.info("[RELEARN] Training is already running; skip schedule request: %s", reason)
        return False
    relearn_task = asyncio.create_task(relearn_models_background(reason))
    logger.info("[RELEARN] Scheduled background relearn: %s", reason)
    return True


def format_klines_for_chart(klines: list[dict], limit: int = 300) -> list[dict]:
    """Format klines for lightweight-charts candlestick rendering."""
    recent = klines[-limit:]
    return [
        {
            "time": k["time"],
            "open": round(k["open"], 2),
            "high": round(k["high"], 2),
            "low": round(k["low"], 2),
            "close": round(k["close"], 2),
        }
        for k in recent
    ]


def format_volume_for_chart(klines: list[dict], limit: int = 300) -> list[dict]:
    """Format volume data for chart."""
    recent = klines[-limit:]
    return [
        {
            "time": k["time"],
            "value": round(k["volume"], 2),
            "color": "rgba(0, 230, 118, 0.3)"
            if k["close"] >= k["open"]
            else "rgba(255, 23, 68, 0.3)",
        }
        for k in recent
    ]


def compute_support_resistance_payload(
    klines: list[dict], order_flow_summary: dict | None = None, lookback: int = 120
) -> dict:
    """Nearest plain-English support/resistance levels for the UI and snapshots."""
    if len(klines) < 20:
        return {}

    price = float(klines[-1]["close"])
    recent = klines[-lookback:]
    support_candidates = []
    resistance_candidates = []

    lows = [float(k["low"]) for k in recent]
    highs = [float(k["high"]) for k in recent]
    for i in range(2, len(recent) - 2):
        low = lows[i]
        high = highs[i]
        if low < lows[i - 1] and low < lows[i + 1] and low < lows[i - 2] and low < lows[i + 2]:
            support_candidates.append(low)
        if high > highs[i - 1] and high > highs[i + 1] and high > highs[i - 2] and high > highs[i + 2]:
            resistance_candidates.append(high)

    support_candidates.extend(v for v in lows if v < price)
    resistance_candidates.extend(v for v in highs if v > price)

    walls = ((order_flow_summary or {}).get("liquidity_walls") or {})
    bid_wall = float(walls.get("bid_wall_price") or 0.0)
    ask_wall = float(walls.get("ask_wall_price") or 0.0)
    if 0 < bid_wall < price:
        support_candidates.append(bid_wall)
    if ask_wall > price:
        resistance_candidates.append(ask_wall)

    support = max((v for v in support_candidates if v < price), default=price * 0.99)
    resistance = min((v for v in resistance_candidates if v > price), default=price * 1.01)
    support_dist = max(price - support, 0.0)
    resistance_dist = max(resistance - price, 0.0)

    return {
        "support": round(support, 2),
        "resistance": round(resistance, 2),
        "support_distance_usd": round(support_dist, 2),
        "resistance_distance_usd": round(resistance_dist, 2),
        "support_distance_pct": round((support_dist / price) * 100, 4) if price else 0.0,
        "resistance_distance_pct": round((resistance_dist / price) * 100, 4) if price else 0.0,
        "compression_pct": round(((resistance - support) / price) * 100, 4) if price else 0.0,
        "source": "candles+liquidity_walls" if walls else "candles",
    }


from collections import deque as _deque
# Rolling RAW confidence per horizon so the safety bar can adapt to the model's
# *actual* confidence scale. A 3-class direction model rarely exceeds ~0.55, so a
# fixed 0.64 bar makes everything NEUTRAL. The bar is clamped between recent
# percentiles so a controlled fraction of the most-confident signals always pass.
_recent_conf = {hh: _deque(maxlen=400) for hh in [1, 3, 5, 7, 10, 15]}

# Stage 1+2 precision engine: isotonic calibration (auto-activates at >=150 resolved
# leans/horizon) + shrunk empirical precision bins. Fitted off the event loop in the
# maintenance section; applied per-prediction below.
from calibration import PrecisionEngine
precision_engine = PrecisionEngine()


def _confluence(p: dict, of: dict) -> dict:
    """Stage 3: per-prediction setup grade. Counts independent confirmations of the lean:
    committed model lean (not the two-way fallback), regime quality (LOW_VOLATILITY was the
    weakest cell in live evidence), and three order-flow agreements (CVD, large-trade flow,
    book imbalance). Grade A ≈ the high-precision subset worth betting; C ≈ skip."""
    lean = p.get("rawDirection")
    if lean not in ("UP", "DOWN"):
        return {"score": 0, "grade": "C", "checks": {"model_lean": False}}
    sgn = 1.0 if lean == "UP" else -1.0
    checks = {
        "model_lean": True,
        "regime_ok": p.get("regime") not in ("LOW_VOLATILITY", "UNKNOWN"),
        "cvd_agrees": sgn * float(of.get("cvd_1m", 0.0) or 0.0) > 0,
        "large_trades_agree": sgn * float(of.get("large_trade_delta", 0.0) or 0.0) > 0,
        "book_agrees": sgn * float(of.get("obi_5", 0.0) or 0.0) > 0,
    }
    score = int(sum(checks.values()))
    grade = "A" if score >= 4 else ("B" if score >= 3 else "C")
    return {"score": score, "grade": grade, "checks": checks}


def _conf_percentile(h: int, q: float):
    vals = sorted(c for c in _recent_conf.get(h, []) if c > 0.1)
    if len(vals) < 20:
        return None
    idx = int(q / 100.0 * (len(vals) - 1))
    return vals[idx]


def _neutralize_prediction(prediction: dict, code: str, message: str, status: str = "blocked") -> dict:
    """Mark a raw directional lean as final WAIT/NEUTRAL with an auditable reason."""
    prediction["direction"] = "NEUTRAL"
    prediction["signal"] = "NEUTRAL"
    prediction["quality_filtered"] = True
    prediction["qualityStatus"] = status
    prediction["qualityMessage"] = message
    prediction["skipReason"] = message
    prediction["neutralReasonCode"] = code
    prediction["neutralReason"] = message
    return prediction


def apply_live_quality_filters(
    prediction: dict, verifier_state: PredictionVerifier, data_state: dict
) -> dict:
    """
    Phase 3 Institutional Quality Filter:
    - Block on negative Expected Value (EV) instead of raw win-rate.
    - Block stale data feeds (>5000ms latency).
    - Block high Shannon entropy (model confusion).
    - Apply transition risk penalties.
    - Enforce fast vs slow horizon liquidity rules.
    """
    import math

    h = prediction.get("horizon")
    state = _safe_dict(data_state)
    regime_info = _safe_dict(state.get("regime_info"))
    regime_name = regime_info.get("regime", "UNKNOWN")
    acc = verifier_state.get_accuracy_summary().get(h, {})
    total = int(acc.get("total", 0) or 0)

    # 1. Freshness Blocker
    order_flow_state = _safe_dict(state.get("order_flow"))
    freshness = order_flow_state.get("freshness_ms", 0)
    if freshness > 5000:
        if prediction.get("direction") != "NEUTRAL":
            _neutralize_prediction(
                prediction,
                "stale_feed",
                f"Feed is lagging ({freshness}ms stale). Safety block.",
            )
        return prediction

    # 2. Shannon Entropy / Model Dispersion
    probs = [
        prediction.get("probDown", 0.0),
        prediction.get("probNeutral", 1.0),
        prediction.get("probUp", 0.0),
    ]
    entropy = -sum(p * math.log(p + 1e-9) for p in probs if p > 0)
    # Max 3-class entropy is ln(3)=1.0986. The old 1.05 cut killed clearly-leaning outputs
    # (e.g. [0.40,0.35,0.25] has entropy 1.079) — only distributions sharper than the model
    # can structurally produce passed, which contributed to the "always NEUTRAL" state.
    # 1.085 still blocks true confusion (1.09+ = near-uniform) without eating real leans.
    if entropy > 1.085 and prediction.get("direction") != "NEUTRAL":
        _neutralize_prediction(
            prediction,
            "model_confusion",
            f"High ensemble confusion (Entropy: {entropy:.2f}). Skipping.",
        )
        return prediction

    # 3. Transition Risk
    cv = regime_info.get("confidence_vector", {})
    transition_risk = False
    if cv and len(cv) >= 2:
        top_two = sorted(cv.values(), reverse=True)[:2]
        if (top_two[0] - top_two[1]) < 0.05:
            transition_risk = True

    # Calibrated to the model's real confidence scale (3-class direction tops out
    # near ~0.55). Old bars (0.60–0.70) sat above the max achievable confidence, so
    # every signal became NEUTRAL. The adaptive clamp below keeps these honest.
    base_thresholds = {1: 0.50, 3: 0.48, 5: 0.47, 7: 0.46, 10: 0.45, 15: 0.45}
    threshold = base_thresholds.get(h, 0.47)
    # Gate on CALIBRATED confidence once the calibrator is active (Stage 1). Raw confidence
    # was proven anti-correlated with success at 5m+ (gate-passed 50% vs raw-lean 64%);
    # the calibrated value is an honest P(correct), so the bar selects the RIGHT subset.
    # The percentile window tracks the same quantity used for gating.
    raw_conf = float(prediction.get("confidence", 0.0) or 0.0)
    _calv = prediction.get("calibratedConfidence")
    eff_conf = float(_calv) if _calv is not None else raw_conf
    if h in _recent_conf and eff_conf > 0.1:
        _recent_conf[h].append(eff_conf)
    if transition_risk:
        threshold += 0.02

    signal_policy = _safe_dict(state.get("signal_policy"))
    policy = _safe_dict(signal_policy.get("by_regime", {}).get(h))
    if not policy.get("ready"):
        policy = _safe_dict(signal_policy.get("by_horizon", {}).get(h))
    if policy.get("ready") and policy.get("threshold") is not None:
        learned_threshold = float(policy.get("threshold") or threshold)
        # Use learned live precision policy as the base bar. It can ease the gate
        # where raw leans have been accurate, and tighten it where they have not.
        threshold = max(0.40, min(0.76, learned_threshold))
        prediction["thresholdPolicy"] = policy

    prediction["qualityStatus"] = "not_enough_data" if total < 100 else "usable"
    prediction["qualityMessage"] = (
        f"Only {total}/100 verified {h}m predictions. Early read."
        if total < 100
        else "Verified data is active."
    )
    prediction["requiredConfidence"] = round(threshold, 3)

    # Move-range check
    move_range = prediction.get("expectedMoveRange") or {}
    q_spread = prediction.get("quantileSpread")
    if q_spread is None and move_range:
        median = float(
            move_range.get("median") or prediction.get("expectedMove") or 0.0
        )
        width = float(move_range.get("high") or 0.0) - float(
            move_range.get("low") or 0.0
        )
        q_spread = (width / median) if median > 0 else 0.0
    q_spread = float(q_spread or 0.0)
    prediction["quantileSpread"] = round(q_spread, 4)

    # 4. Horizon-Specific Policies
    if h in [1, 3]:
        # Fast horizons: strict liquidity
        spread_norm = float(order_flow_state.get("spread_expansion_ratio", 1.0))
        if spread_norm > 2.0 and prediction.get("direction") != "NEUTRAL":
            threshold += 0.03
            prediction["qualityMessage"] = (
                "Wide bid-ask spread requires more confidence to overcome slippage."
            )
    elif h in [10, 15]:
        # Slow horizons: strict volatility/spread
        if q_spread >= 2.5 and prediction.get("direction") != "NEUTRAL":
            threshold += 0.02
            prediction["qualityMessage"] = (
                "Wide target-size uncertainty. Raising confidence bar."
            )

    # 5. Expectancy over Accuracy
    if total >= 100:
        expectancy = float(acc.get("expectancy_usd", 0.0) or 0.0)
        if expectancy <= 0:
            threshold += 0.03
            prediction["qualityMessage"] = (
                f"This horizon has negative historical EV (${expectancy:.2f}). Safety bar raised."
            )
        elif expectancy > 5.0:
            threshold = max(0.43, threshold - 0.02)
            prediction["qualityMessage"] = (
                f"This horizon has strong positive EV (${expectancy:.2f}). Easing safety bar."
            )

    regime_horizon = _safe_dict(verifier_state.get_regime_horizon_quality().get(h))
    regime_quality = regime_horizon.get(regime_name)
    poor_regimes = _safe_dict(state.get("poor_regimes")).get(h, [])

    if (
        regime_quality
        and regime_quality.get("ready")
        and regime_quality.get("accuracy", 0.0) < 0.50
    ) or regime_name in poor_regimes:
        if prediction.get("direction") != "NEUTRAL":
            _neutralize_prediction(
                prediction,
                "poor_regime",
                f"{h}m has under-50% accuracy in {regime_name}; skipped.",
            )
            prediction["regime_filtered"] = True
        return prediction

    # ── B2: conviction-gate (operator 2026-06-13) ───────────────────────────────
    # Cells that CLEARED the <50% poor-regime gate but are still only coin-flip
    # (measured 50–54%, e.g. 5m LOW_VOLATILITY ~51.7%) must not be surfaced as
    # CONFIDENT bets. Keep the directional READ (direction unchanged, so 5m stays
    # visible) but strip "actionable" — conviction is reserved for PROVEN-edge cells
    # (>= PROVEN_EDGE). Only fires when the cell's accuracy is statistically READY
    # (enough samples); marginal-but-unproven cells keep the benefit of the doubt.
    # Serving-side only: no model change, no retrain, no effect on raw_direction or
    # the sign-truth tables. Option A of MEASUREMENT_WINDOW §5 (visible, not silent).
    PROVEN_EDGE = 0.54
    if (
        prediction.get("direction") != "NEUTRAL"
        and prediction.get("actionable")
        and regime_quality
        and regime_quality.get("ready")
        and regime_quality.get("accuracy", 0.0) < PROVEN_EDGE
    ):
        _ce = regime_quality.get("accuracy", 0.0)
        prediction["actionable"] = False
        prediction["convictionCapped"] = True
        prediction["convictionCapReason"] = (
            f"{regime_name} {h}m is {_ce:.0%} (coin-flip) — shown as a read, not a "
            f"confident bet (needs ≥{PROVEN_EDGE:.0%})."
        )

    # Adaptive clamp: the bar can never sit above the recent 72nd percentile of
    # confidence (so the most-confident ~28% of signals always pass) nor below a
    # sensible floor (so we don't spam near-random calls). This self-corrects to the
    # model's live confidence distribution and is what stops the "always NEUTRAL" state.
    cap = _conf_percentile(h, 72)
    floor = _conf_percentile(h, 30)
    if cap is not None:
        threshold = min(threshold, cap)
    if floor is not None:
        threshold = max(threshold, floor, 0.40)
    else:
        threshold = max(threshold, 0.40)
    # HARD ceiling at the model's structural confidence cap. A 3-class head with class
    # priors tops out ~0.50-0.55, but right after a restart (<20 samples) the percentile
    # cap above is None while the learned policy is allowed up to 0.76 — the DB showed
    # bars of 0.61-0.63, which are mathematically unpassable → guaranteed 100% NEUTRAL
    # until the rolling window refills. The bar must never exceed what the model can emit.
    threshold = min(threshold, 0.50)

    if prediction.get("direction") != "NEUTRAL" and eff_conf < threshold:
        _src = "calibrated " if _calv is not None else ""
        _neutralize_prediction(
            prediction,
            "low_confidence",
            f"{_src.capitalize()}Confidence {eff_conf:.2f} is below safety bar {threshold:.2f}.",
            status="filtered",
        )

    prediction["requiredConfidence"] = round(threshold, 3)
    
    # Map outputs to TRADE BUY, TRADE SELL, ABSTAIN
    if prediction.get("direction") == "UP":
        prediction["signal"] = "TRADE BUY"
    elif prediction.get("direction") == "DOWN":
        prediction["signal"] = "TRADE SELL"
    else:
        prediction["signal"] = "ABSTAIN"
        
    return prediction


async def main_loop():
    """Background task: fetch data, train models, generate predictions."""

    # 1. Warm-up
    boot_started = time.time()
    logger.info("[BOOT] Startup warm-up begins. historical_days=%s", HISTORICAL_DAYS)
    logger.info(
        "[BOOT] Model is %s. %s",
        "FROZEN (no auto/scheduled retraining)" if MODEL_FROZEN else "AUTO-IMPROVE (will retrain)",
        "Set BTC_FREEZE_MODEL=0 to allow auto-retrain." if MODEL_FROZEN
        else "WARNING: a background retrain will saturate the CPU and freeze the live feed.",
    )
    await broadcast(
        {
            "type": "status",
            "step": "step-data",
            "msg": f"Fetching {HISTORICAL_DAYS}-day historical data...",
        }
    )
    step_t0 = time.time()
    logger.info("[BOOT 1/7] Loading %s days of 1m Binance candles...", HISTORICAL_DAYS)
    klines = await _fetch_historical_cached("1m", HISTORICAL_DAYS)
    data_state["klines"] = klines
    logger.info("[BOOT 1/7] Loaded %s 1m candles in %.1fs", len(klines), time.time() - step_t0)

    # Also fetch multi-timeframe klines
    step_t0 = time.time()
    logger.info("[BOOT 2/7] Loading %s days of 5m candles...", HISTORICAL_DAYS)
    klines_5m = await _fetch_historical_cached("5m", HISTORICAL_DAYS)
    data_state["klines_5m"] = klines_5m
    logger.info("[BOOT 2/7] Loaded %s 5m candles in %.1fs", len(klines_5m), time.time() - step_t0)
    step_t0 = time.time()
    logger.info("[BOOT 3/7] Loading %s days of 15m candles...", HISTORICAL_DAYS)
    klines_15m = await _fetch_historical_cached("15m", HISTORICAL_DAYS)
    data_state["klines_15m"] = klines_15m
    logger.info("[BOOT 3/7] Loaded %s 15m candles in %.1fs", len(klines_15m), time.time() - step_t0)

    step_t0 = time.time()
    logger.info("[BOOT 4/7] Fetching derivatives, Bybit and sentiment snapshots...")
    await _best_effort("Binance derivatives snapshot", rest_client.fetch_all_derivatives(), 8.0)
    refresh_derivatives_from_rest()
    await _best_effort("Bybit snapshot", bybit_client.fetch_all(), 5.0)
    data_state["bybit_data"] = _safe_dict(bybit_client.data)
    update_global_oi_history()

    await _best_effort("sentiment snapshot", sentiment_client.fetch_all(), 5.0)
    data_state["sentiment"] = sentiment_client.data
    await _best_effort("chainlink snapshot", chainlink_client.fetch_price(), 5.0)
    if chainlink_client.data.get("btc_usd"):
        data_state["chainlink_price"] = chainlink_client.data["btc_usd"]
    await _best_effort("macro snapshot", macro_client.fetch_all(), 8.0)
    data_state["macro"] = {"dxy": macro_client.data.get("dxy"),
                           "us10y": macro_client.data.get("us10y")}
    logger.info("[BOOT 4/7] Slow snapshots ready in %.1fs", time.time() - step_t0)

    # 2. Try loading saved models first
    await broadcast(
        {"type": "status", "step": "step-model", "msg": "Loading models..."}
    )
    step_t0 = time.time()
    logger.info("[BOOT 5/7] Loading saved models from disk...")
    loaded = model.load_models()
    if loaded:
        # Restore the saved model's out-of-sample boundary so backtests stay honest
        # across restarts (models loaded from disk, no retrain → boundary from json).
        try:
            with open(os.path.join(DATA_DIR, "saved_models", "train_boundary.json"),
                      "r", encoding="utf-8") as _bf:
                backend_state["train_boundary_ts"] = int(json.load(_bf)["train_boundary_ts"])
                logger.info("[BOOT] Restored out-of-sample boundary ts=%s",
                            backend_state["train_boundary_ts"])
        except Exception:
            pass  # legacy bundle without a boundary file — backtest falls back to old behavior
    startup_train_bg = None  # set to the bg training task when a fresh train is needed

    if not loaded:
        logger.warning("[BOOT 5/7] No compatible saved models found. Startup training is required.")
        # 3. Train fresh
        await broadcast(
            {"type": "status", "step": "step-features", "msg": "Computing features..."}
        )
        await broadcast(
            {
                "type": "status",
                "step": "step-model",
                "msg": "Training multi-model ensemble...",
            }
        )
        # Train in the BACKGROUND so the dashboard (chart, live feeds, price) becomes
        # usable immediately. Blocking the boot on the multi-minute first train — which
        # happens whenever MODEL_ARCH_VERSION changes and the saved bundle is stale —
        # leaves the UI with only the price tick and an empty chart until training ends,
        # because the main update loop only starts after boot completes. The main loop
        # already guards predictions on model.is_trained, so it streams market data now
        # and begins predicting once training lands. The startup backtest is deferred to
        # inside this task so it never runs against an untrained model.
        async def _startup_train_then_backtest():
            # Report progress through relearn_status: the startup train is the LONGEST
            # operation the app ever does (hours), yet it previously showed "Idle" in the
            # UI — the operator watched a wall of WAITs with no clue training was running
            # (the relearn path reports status; this path didn't).
            _set_status(
                "relearn_status",
                running=True,
                phase="startup-training",
                message="Training the full ensemble (first run on this model version — hours).",
                progress=0.05,
                started_at=time.time(),
                error=None,
            )
            try:
                await train_model()
                await broadcast({"type": "status", "step": "step-model", "msg": "Model trained"})
                logger.info("[BOOT] Background startup training complete.")
                _set_status(
                    "relearn_status",
                    running=False,
                    phase="complete",
                    message="Startup training complete.",
                    progress=1.0,
                    completed_at=time.time(),
                    error=None,
                )
                if MODEL_BOOT_BACKTEST and not _load_backtest_cache():
                    schedule_backtest("startup")
            except Exception as e:
                logger.error("[BOOT] Background startup training failed: %s", e, exc_info=True)
                _set_status(
                    "relearn_status",
                    running=False,
                    phase="failed",
                    message=f"Startup training failed: {e}",
                    progress=0.0,
                    completed_at=time.time(),
                    error=str(e),
                )
        startup_train_bg = asyncio.create_task(_startup_train_then_backtest())
    else:
        logger.info("[BOOT 5/7] Saved models loaded in %.1fs. Startup training required: no", time.time() - step_t0)
        await broadcast(
            {"type": "status", "step": "step-features", "msg": "Features ready"}
        )
        await broadcast(
            {"type": "status", "step": "step-model", "msg": "Models loaded from disk"}
        )

    # 4. Backtest is intentionally backgrounded. The app should become usable as
    # soon as models are available; validation keeps running with visible progress.
    if _load_backtest_cache():
        await broadcast(
            {
                "type": "status",
                "step": "step-backtest",
                "msg": "Loaded cached backtest results.",
            }
        )
    elif MODEL_BOOT_BACKTEST and startup_train_bg is None:
        await broadcast(
            {
                "type": "status",
                "step": "step-backtest",
                "msg": "Backtest scheduled in background...",
            }
        )
        logger.info("[BOOT 6/7] Scheduling startup backtest in background...")
        schedule_backtest("startup")
    elif MODEL_BOOT_BACKTEST:
        # Fresh model is still training in the background; the training task will
        # schedule the backtest once the model exists (avoids backtesting nothing).
        await broadcast(
            {
                "type": "status",
                "step": "step-backtest",
                "msg": "Backtest will run after startup training completes...",
            }
        )
    else:
        _set_status(
            "backtest_status",
            running=False,
            phase="disabled",
            message="Startup backtest disabled by BTC_RUN_STARTUP_BACKTEST=0.",
            progress=0.0,
            completed_at=time.time(),
            error=None,
        )

    # 5. Ready
    backend_state["ready_time"] = time.time()
    backend_state["boot_seconds"] = round(
        backend_state["ready_time"] - backend_state["startup_start_time"],
        2,
    )
    await broadcast({"type": "status", "step": "step-connect", "msg": "Ready"})
    logger.info("[BOOT 7/7] Ready in %.1fs", time.time() - boot_started)

    # Periodic polling loop
    tick_count = 0
    while True:
        try:
            await asyncio.sleep(MAIN_LOOP_SEC)  # configurable; live feed is on separate fast tickers
            tick_count += 1

            # Fetch slow data every 30s (derivatives, Bybit, options, basis)
            if tick_count % 15 == 0:
                spot_price = (
                    data_state["klines"][-1]["close"] if data_state["klines"] else 0
                )
                await asyncio.gather(
                    _best_effort("Binance derivatives poll", rest_client.fetch_all_derivatives(), 8.0),
                    _best_effort("Bybit poll", bybit_client.fetch_all(), 5.0),
                    _best_effort("Deribit options poll", deribit_client.fetch_options_summary(), 5.0),
                    _best_effort("CME basis poll", cme_basis_client.fetch_basis(spot_price), 5.0),
                )
                refresh_derivatives_from_rest()
                data_state["bybit_data"] = _safe_dict(bybit_client.data)
                update_global_oi_history()
                await _best_effort("multi-exchange price poll", multi_exchange_client.fetch_all(), 5.0)
                data_state["multi_exchange"] = _safe_dict(multi_exchange_client.data)
                # Chainlink (CoinGecko proxy) BTC/USD for the multi-exchange consensus strip.
                # CoinGecko "chainlink" price — kept at 30s (display-only for the consensus
                # strip). It is NOT the price-to-beat reference (that uses fresh Binance);
                # polling it faster just hits CoinGecko's rate limit without getting fresher.
                await _best_effort("chainlink price poll", chainlink_client.fetch_price(), 5.0)
                if chainlink_client.data.get("btc_usd"):
                    data_state["chainlink_price"] = chainlink_client.data["btc_usd"]

            # Fetch very slow data every 5 min (sentiment, stablecoin flows, exchange flows)
            if tick_count % 150 == 0:
                await asyncio.gather(
                    _best_effort("sentiment poll", sentiment_client.fetch_all(), 5.0),
                    _best_effort("stablecoin flow poll", stablecoin_client.fetch_stablecoin_data(), 5.0),
                    _best_effort("exchange flow poll", exchange_flow_client.fetch_exchange_flow(), 5.0),
                    _best_effort("macro poll", macro_client.fetch_all(), 8.0),
                )
                data_state["sentiment"] = sentiment_client.data
                data_state["macro"] = {"dxy": macro_client.data.get("dxy"),
                                       "us10y": macro_client.data.get("us10y")}

            # (Kronos removed in v6 — the module was never installed; its fallback
            # emitted noise that we maintained a verifier + UI for. See §5ar / R1.)

            # Precision-engine refresh (cheap aggregate query, off the event loop). Runs
            # shortly after boot (tick 5) then re-checks every ~5 min; the engine itself
            # no-ops unless its 6h staleness window has elapsed. NOT gated by MODEL_FROZEN:
            # calibration is post-processing fitted on live outcomes, not model weights —
            # and it is the fix for the proven anti-selecting gate at 5m+.
            if tick_count % 100 == 5:
                asyncio.get_event_loop().run_in_executor(
                    None, precision_engine.refresh_if_stale)

            # Periodic maintenance (~every 30 min). The heavy ENSEMBLE relearn is
            # gated behind a real cooldown so the box isn't perpetually retraining —
            # a full retrain takes tens of minutes, and firing one every 30 min starves
            # live predictions and never lets signal-history coverage accumulate. The
            # cheap meta-model + poor-regime refreshes still run every cycle.
            if tick_count % 900 == 0 and not backend_state["is_training"]:
                # Heavy ensemble relearn — only when the cooldown has elapsed since the
                # last relearn (auto OR scheduled). Default 6h; tune via env.
                sched_cooldown = float(os.environ.get("BTC_SCHEDULED_RELEARN_SEC", "21600"))
                since_last = time.time() - backend_state.get("last_auto_relearn_time", 0.0)
                if not MODEL_FROZEN and since_last >= sched_cooldown:
                    logger.info("Scheduled model relearn check (cooldown elapsed)...")
                    if schedule_relearn("scheduled"):
                        backend_state["last_auto_relearn_time"] = time.time()
                else:
                    logger.info(
                        "Scheduled relearn skipped — in cooldown (%.0fs/%.0fs); "
                        "letting the model stabilize and coverage accumulate.",
                        since_last, sched_cooldown,
                    )
                # Attempt to (re)train the meta-model trust filters from accumulated
                # DuckDB outcomes. Pass-through until each horizon has >=200 verified.
                for h in model.horizons:
                    msg = meta_models[h].train(database.DB_PATH, h)
                    logger.info(f"Meta-model {h}m: {msg}")

                # Update poor regimes
                try:
                    from analytics import validate_regime_thresholds

                    loop = asyncio.get_event_loop()
                    regime_res = await loop.run_in_executor(
                        None, validate_regime_thresholds, model.horizons
                    )
                    poor_regimes = {}
                    for h_reg, rows in regime_res.items():
                        poor_regimes[h_reg] = [
                            r["regime"]
                            for r in rows
                            if r["n"] >= 30 and r["accuracy"] < 50
                        ]
                    data_state["poor_regimes"] = poor_regimes
                except Exception as e:
                    logger.warning(f"Error updating poor regimes: {e}")

            # Track Coinbase premium each tick (~2s) so velocity has a fresh window
            coinbase_premium_history.append(
                {"value": data_state.get("coinbase_premium", 0.0), "t": time.time()}
            )
            if len(coinbase_premium_history) > 15:  # ~30s window
                coinbase_premium_history.pop(0)

            # Prepare dynamic cross-exchange and OI indicators before feature building and payload dispatch
            prepare_derivatives_data()

            # If we don't have enough data, skip
            if len(data_state["klines"]) < LOOKBACK:
                continue

            # PERF: live inference only needs a recent window. Building features over
            # the full historical window every 2s is wasteful and blocks the event loop.
            # blocks the event loop. We only consume the last LOOKBACK rows for the
            # sequence, so slice to a recent window. Training still uses full history.
            recent_klines = data_state["klines"][-1500:]

            # Current Features (per-bar signal history keeps the sequence window
            # consistent with how the model was trained).
            live_sig_hist = signal_buffer.get_aligned_series(
                [k["time"] for k in recent_klines]
            )
            # Build features off the event loop. This is a heavy synchronous numpy job
            # (~0.3s/tick on the live window) and running it inline stalls WebSocket
            # pings — the stale-feed/ping-timeout disconnects seen in the UI. (#6)
            live_features = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: build_features_from_klines(
                    recent_klines,
                    data_state["order_flow"],
                    data_state["derivatives"],
                    data_state["sentiment"],
                    signal_history=live_sig_hist,
                ),
            )

            if len(live_features) < LOOKBACK:
                continue

            seq = live_features[-LOOKBACK:]

            # Compute Regime before predictions
            regime = {"regime": "RANGE", "confidence": 0.0}
            if len(recent_klines) > 100:
                closes = np.array([k["close"] for k in recent_klines], dtype=np.float64)
                highs = np.array([k["high"] for k in recent_klines], dtype=np.float64)
                lows = np.array([k["low"] for k in recent_klines], dtype=np.float64)
                volumes = np.array(
                    [k["volume"] for k in recent_klines], dtype=np.float64
                )

                from features import adx as compute_adx, atr as compute_atr

                adx_arr = compute_adx(highs, lows, closes)
                atr_arr = compute_atr(highs, lows, closes)
                regime = regime_engine.detect_regime(closes, adx_arr, atr_arr, volumes)
            data_state["regime_info"] = regime

            predictions = []
            meta_contexts = {}
            if model.is_trained:
                acc_cache = verifier.get_accuracy_summary()
                cascade_data = {}
                now_ms_pred = int(time.time() * 1000)
                # Learned regime-specific model weights (from per-model live accuracy
                # in the current regime); empty until enough outcomes accumulate.
                data_state["regime_model_weights"] = verifier.get_regime_model_weights(
                    regime.get("regime", "UNKNOWN")
                )
                # Per-regime confidence calibration (honest confidence per regime).
                data_state["regime_calibration"] = verifier.get_regime_calibration()
                # (kronos_accuracy no longer populated — model.py's Kronos hooks are
                # self-gating on it and go inert with it absent.)
                # Live isotonic confidence calibrators (raw conf -> realized hit rate).
                data_state["confidence_calibrators"] = verifier.get_confidence_calibrators()
                # Adaptive precision policy from resolved raw UP/DOWN leans. This lets
                # the gate permit more BUY/SELL calls where live evidence supports it
                # and tighten where a horizon/regime has been wrong.
                data_state["signal_policy"] = verifier.get_signal_policy(
                    regime.get("regime", "UNKNOWN")
                )
                for h in model.horizons:
                    # Run the (CPU-heavy, ~0.3-0.7s) ensemble inference in a worker thread.
                    # On the 30-day model this loop was ~2s of synchronous work per cycle,
                    # blocking the event loop and freezing the live price/chart feed ($50-80
                    # lag). Offloading + awaiting per horizon lets the price broadcaster and
                    # WebSocket handlers run in between, keeping the feed live. (price-lag fix)
                    p = await asyncio.get_event_loop().run_in_executor(
                        None, ab_runner.predict, h, seq, data_state, acc_cache, cascade_data
                    )
                    p["regime"] = regime.get("regime", "UNKNOWN")
                    p["rawDirection"] = p.get("direction", "NEUTRAL")
                    p["rawSignal"] = p.get("signal", "NEUTRAL")
                    current_price = data_state["klines"][-1]["close"]
                    order_flow_state = _safe_dict(data_state.get("order_flow"))
                    spread_exp = order_flow_state.get("spread_expansion_ratio", 1.0)
                    pre_exp_calc = simulator.calculate_signal_expectancy(
                        p, current_price, order_flow_state, spread_exp
                    )
                    p["expectancy_usd"] = pre_exp_calc.get("expectancy_usd", 0.0)
                    p["expected_slippage_usd"] = pre_exp_calc.get(
                        "expected_slippage_usd", 0.0
                    )
                    # Meta-model trust filter (pass-through until trained on enough outcomes).
                    ctx = build_meta_context(
                        p, seq, regime.get("regime", "RANGE"), now_ms_pred
                    )
                    meta_contexts[h] = ctx
                    execute, trust = meta_models[h].should_execute(ctx)
                    p["metaTrust"] = round(trust, 3)
                    if not execute and p["direction"] != "NEUTRAL":
                        _neutralize_prediction(
                            p,
                            "meta_reject",
                            "Meta-model trust filter blocked the raw signal.",
                        )
                        p["meta_filtered"] = True
                    # Stage 1-3 precision instrumentation (attached BEFORE the gate so the
                    # gate can use calibrated confidence once the calibrator activates).
                    try:
                        _of_now = _safe_dict(data_state.get("order_flow"))
                        p["confluence"] = _confluence(p, _of_now)
                        _cal = precision_engine.calibrated(h, float(p.get("confidence", 0.0) or 0.0))
                        if _cal is not None:
                            p["calibratedConfidence"] = round(_cal, 4)
                        _ep = precision_engine.expected_precision(
                            h, p.get("regime", "UNKNOWN"), float(p.get("conviction", 0.0) or 0.0))
                        if _ep is not None:
                            p["expectedPrecision"] = _ep
                    except Exception as _pe:
                        logger.debug(f"precision instrumentation skipped: {_pe}")
                    p = apply_live_quality_filters(p, verifier, data_state)

                    # Expectancy Filter
                    current_price = data_state["klines"][-1]["close"]
                    order_flow_state = _safe_dict(data_state.get("order_flow"))
                    spread_exp = order_flow_state.get("spread_expansion_ratio", 1.0)
                    exp_calc = simulator.calculate_signal_expectancy(
                        p, current_price, order_flow_state, spread_exp
                    )
                    p["expectancy_usd"] = exp_calc.get("expectancy_usd", 0.0)
                    p["expected_slippage_usd"] = exp_calc.get(
                        "expected_slippage_usd", 0.0
                    )

                    if p["direction"] != "NEUTRAL" and p["expectancy_usd"] <= 0:
                        _neutralize_prediction(
                            p,
                            "negative_expectancy",
                            "Negative Expectancy after costs",
                        )
                        p["meta_filtered"] = True

                    predictions.append(p)
                    cascade_data[h] = p

            # FSR-PPO mothballed in v6 (R3): a challenger strategy layer is premature
            # until the core model proves its edge. Re-enable with BTC_FSR_PPO=1 —
            # code + DB tables intact, just not invoked (saves a compute pass/loop).
            if FSR_PPO_ENABLED:
                fsr_ppo_block = fsr_ppo_strategy.recommend(
                    data_state,
                    predictions,
                    verifier.get_accuracy_summary(),
                ) if predictions else {
                    "status": fsr_ppo_strategy.status(),
                    "fsr": fsr_ppo_strategy.signal_representation(data_state.get("klines") or []),
                    "by_horizon": {},
                    "best": None,
                    "summary": "PPO challenger is waiting for ensemble predictions.",
                }
                data_state["fsr_ppo"] = fsr_ppo_block
            else:
                data_state["fsr_ppo"] = {"status": {"enabled": False},
                                         "summary": "FSR-PPO mothballed (v6 roster surgery). Set BTC_FSR_PPO=1 to revive."}

            # Record predictions per-horizon cadence
            now_ms = int(time.time() * 1000)
            if predictions:
                current_price = data_state["klines"][-1]["close"]
                # Use the real Chainlink/CoinGecko oracle price as the reference when it
                # has reported; fall back to the Binance close only while the oracle is
                # cold. Previously reference_price was hardcoded to current_price, so the
                # DuckDB `chainlink_price` column silently stored Binance prices —
                # corrupting any Chainlink-vs-Binance reconciliation and disagreeing with
                # price-to-beat (which already uses the oracle/fallback).
                _cl = data_state.get("chainlink_price")
                reference_price = float(_cl) if _cl else current_price
                _feature_logged = False  # B1: log the vector once per recording cycle
                for p in predictions:
                    h = p["horizon"]
                    if verifier.should_record(h, now_ms):
                        pred_id = f"{h}m_{now_ms}"
                        p["id"] = pred_id  # attach to pass to verifier
                        verifier.record_prediction(p, current_price, now_ms)

                        # B1 (2026-06-13): persist the live per-bar feature vector
                        # (seq[-1]) keyed by now_ms == predictions_{h}m.timestamp, ONCE per
                        # recording cycle. A future retrain joins this on ts to learn the
                        # microstructure features that are constant in the historical matrix.
                        # Outcome already persists in predictions_*; no resolution hook.
                        # Crash-guarded — a logging failure must never break serving.
                        if not _feature_logged:
                            try:
                                database.log_feature_vector(
                                    now_ms,
                                    __import__("features").get_feature_schema()["schema_hash"],
                                    regime.get("regime", "UNKNOWN"),
                                    [float(x) for x in seq[-1]],
                                )
                                _feature_logged = True
                            except Exception as _fe:
                                logger.debug(f"B1 feature-vector log skipped: {_fe}")
                            # GEX recorder (start the clock on options positioning) —
                            # once per cycle, same guard. Crash-safe; never affects serving.
                            try:
                                _od = deribit_client.data
                                database.log_gex(
                                    now_ms,
                                    float(_od.get("gex", 0.0) or 0.0),
                                    float(_od.get("total_gamma", 0.0) or 0.0),
                                    float(current_price),
                                    float(_od.get("put_call_ratio", 0.0) or 0.0),
                                    float(_od.get("atm_iv", 0.0) or 0.0),
                                )
                            except Exception as _gle:
                                logger.debug(f"GEX log skipped: {_gle}")

                        # Durably log A/B variant predictions for this recorded id.
                        ab_runner.persist(pred_id, h, now_ms)
                        # Per-venue: snapshot exchange prices for this directional call.
                        if h in (5, 15) and p.get("direction") in ("UP", "DOWN"):
                            exchange_verifier.record(p["direction"], h, current_venue_prices(data_state), now_ms)

                        # Per-model live accuracy: record every base model's vote.
                        model_verifier.record(p.get("modelDirs", {}), h, current_price, now_ms)

                        # A10 setup-fingerprint recorder — the per-prediction DECISION context
                        # (regime/conviction/agreement/grade/CVD/GEX), keyed by (now_ms,h) →
                        # joins predictions_{h}m for the outcome. Feeds the kNN voter / T3
                        # "similar setups" gate AND measures which signals have edge before any
                        # becomes a gate. Crash-guarded — never affects serving.
                        try:
                            _ofp = _safe_dict(data_state.get("order_flow"))
                            _cfl = p.get("confluence")
                            database.log_setup_fingerprint(
                                now_ms, h, p.get("regime", "UNKNOWN"),
                                p.get("rawDirection", p.get("direction", "NEUTRAL")),
                                float(p.get("conviction", 0.0) or 0.0),
                                float(p.get("agreement", 0.0) or 0.0),
                                float(p.get("confidence", 0.0) or 0.0),
                                (_cfl.get("grade", "") if isinstance(_cfl, dict) else ""),
                                float(_ofp.get("cvd_1m", 0.0) or 0.0),
                                float((deribit_client.data or {}).get("gex", 0.0) or 0.0),
                                float(p.get("expectedMove", 0.0) or 0.0),
                            )
                        except Exception as _fpe:
                            logger.debug(f"A10 setup-fingerprint log skipped: {_fpe}")

                        # Process live signals into the execution engine simulator
                        order_flow_state = _safe_dict(data_state.get("order_flow"))
                        spread_exp = order_flow_state.get("spread_expansion_ratio", 1.0)
                        simulator.process_signal(
                            p,
                            current_price,
                            now_ms,
                            order_flow_state,
                            spread_exp,
                        )

                        # Log to DuckDB
                        expected_move = p.get(
                            "expectedMove", p["targetPrice"] - current_price
                        )
                        signed_move = (
                            p.get("targetPrice", current_price) - current_price
                        )
                        reference_target = reference_price + signed_move
                        database.log_prediction(
                            pred_id=pred_id,
                            timestamp=now_ms,
                            horizon=h,
                            binance_price=current_price,
                            target_price=p["targetPrice"],
                            expected_move=expected_move,
                            confidence=p["confidence"],
                            signal=p["direction"],
                            chainlink_price=reference_price,
                            chainlink_target=reference_target,
                            cascade_active=p.get("cascade_active", False),
                            regime=regime.get("regime", "UNKNOWN"),
                            context=meta_contexts.get(h),
                            raw_direction=p.get(
                                "rawDirection", p.get("direction", "NEUTRAL")
                            ),
                            skip_reason=p.get("skipReason", ""),
                            avoid_success=False,
                            prob_up=p.get("probUp", 0.0),
                            prob_down=p.get("probDown", 0.0),
                            agreement=p.get("agreement", 0.0),
                            model_dirs=p.get("modelDirs", {}),
                            verify_at=now_ms + h * 60 * 1000,
                            expected_move_range=p.get("expectedMoveRange"),
                            expectancy_usd=p.get("expectancy_usd", 0.0),
                            expected_slippage_usd=p.get("expected_slippage_usd", 0.0),
                            model_bundle_id=p.get("model_bundle_id", "baseline_v9"),
                            feature_schema_hash=__import__(
                                "features"
                            ).get_feature_schema()["schema_hash"],
                            confluence_grade=(p.get("confluence") or {}).get("grade", "")
                                if isinstance(p.get("confluence"), dict) else "",
                            expected_precision=p.get("expectedPrecision"),
                            calibrated_confidence=p.get("calibratedConfidence"),
                        )
                        # FSR-PPO logging only when the challenger is enabled (v6 R3
                        # mothballs it by default → fsr_ppo_block is never assigned;
                        # this guard prevents the UnboundLocalError that crashed the
                        # serving loop every cycle). (operator-caught, 2026-06-13)
                        ppo_block = data_state.get("fsr_ppo") or {}
                        ppo_h = (
                            (ppo_block.get("by_horizon") or {}).get(str(h))
                            or (ppo_block.get("by_horizon") or {}).get(h)
                            or {}
                        ) if FSR_PPO_ENABLED else {}
                        if ppo_h:
                            database.log_fsr_ppo_decision(
                                {
                                    "id": f"fsrppo_{pred_id}",
                                    "prediction_id": pred_id,
                                    "timestamp": now_ms,
                                    "horizon": h,
                                    "price": current_price,
                                    "action": ppo_h.get("action", "AVOID"),
                                    "side": ppo_h.get("side", "AVOID"),
                                    "size_fraction": ppo_h.get("size_fraction", 0.0),
                                    "confidence": ppo_h.get("confidence", 0.0),
                                    "expected_reward_usd": ppo_h.get(
                                        "expected_reward_usd", 0.0
                                    ),
                                    "reason": ppo_h.get("reason", ""),
                                    "risk_note": ppo_h.get("risk_note", ""),
                                    "fsr": ppo_block.get("fsr", {}),
                                    "state": ppo_h.get("state", {}),
                                    "verify_at": now_ms + h * 60 * 1000,
                                }
                            )
                backend_state["last_prediction_record_time"] = now_ms

            # Check pending verifications
            current_price = data_state["klines"][-1]["close"]
            newly_verified = verifier.check_and_verify(current_price, now_ms)
            model_verifier.check(current_price, now_ms)  # resolve per-model votes
            exchange_verifier.check(current_venue_prices(data_state), now_ms)  # per-venue confirmation

            # Price-to-beat rounds are owned by the FAST 1s ticker (price_to_beat_ticker),
            # which anchors AND resolves on Pyth (§5u) with an offset-corrected Binance
            # fallback — this loop's only job is handing it the latest predictions below.
            preds_by_h = {p.get("horizon"): p for p in predictions}
            # Hand the latest predictions to the FAST price-to-beat ticker (1s cadence,
            # decoupled from this heavy loop) so the windows resolve + open and the
            # HOLD/EXIT advice refresh within ~1s of the boundary — regardless of how slow
            # a prediction cycle gets on a loaded/throttled machine. (instant-window fix)
            data_state["_ptb_preds"] = preds_by_h

            # Update simulator (closes expired trades, applies slippage, logs PnL)
            simulator.update(current_price, now_ms)

            for v in newly_verified:
                h = v["horizon"]
                pred_id = v.get("id", "")
                cascade_active = v.get("cascade_active", False)
                hit = v["hit"]

                # A/B tracking (durable: resolves each variant in DuckDB by its stored
                # direction vs the actual outcome, and updates in-memory stats).
                ab_runner.resolve(pred_id, v.get("actual_direction", "NEUTRAL"))
                database.resolve_fsr_ppo_decision(
                    pred_id,
                    v.get("actual_price", current_price),
                    v.get("actual_direction", "NEUTRAL"),
                )

                # Check for A/B promotion
                if ab_runner.enabled:
                    comp = ab_runner.get_comparison()
                    if comp.get("promotion_recommendation") == "promote_challenger":
                        logger.critical(
                            f"A/B TEST WIN: Challenger model beat primary! Triggering promotion. Delta: {comp.get('accuracy_delta', 0):.2f}"
                        )
                        ab_runner.primary = ab_runner.challenger
                        ab_runner.challenger = None
                        ab_runner.enabled = False
                        ab_runner.comparison_log.clear()

                # Feed the CascadeMonitor — grade the RAW lean vs the realized sign,
                # NOT the `hit` column: on gated rows (the majority) hit=avoid_success,
                # TRUE when the lean was WRONG, which would invert the cascade-on vs
                # cascade-off accuracy comparison that auto-enables/disables the cascade.
                if h in [3, 5]:
                    raw_dir = v.get("raw_direction", v.get("direction"))
                    move = float(v.get("actual_move_usd") or 0.0)
                    if raw_dir in ("UP", "DOWN") and move != 0.0:
                        lean_correct = (raw_dir == "UP") == (move > 0)
                        cascade_monitor.record_outcome(f"{h}m", cascade_active, lean_correct)

                database.update_outcome(
                    pred_id=pred_id,
                    horizon=h,
                    actual_price=v["actual_price"],
                    actual_move=v["actual_move_usd"],
                    hit=hit,
                    price_match=v["price_match"],
                    move_error=v["move_error_usd"],
                    avoid_success=v.get("avoid_success", False),
                    lean_hit=v.get("lean_hit"),
                )

            # Auto-learning feedback loop (every 10 seconds approx).
            # Skip entirely while a (re)train is in progress — otherwise it thrashes:
            # it reads the in-flight/half-baked model's poor accuracy and keeps raising
            # smoothing + re-flagging a retrain every 10s, which degrades live output.
            # ALSO skip when MODEL_FROZEN: although this path never retrains while frozen,
            # it still mutates two INFERENCE knobs (smoothing_alpha + confidence_threshold)
            # from live accuracy. For an evidence run "frozen" must mean fully inert — the
            # weights AND the post-processing — so the baseline is reproducible. (This is
            # why the log showed "Auto-learning: decreased smoothing" on a frozen model.)
            if (
                tick_count % 5 == 0
                and model.is_trained
                and not backend_state["is_training"]
                and not MODEL_FROZEN
            ):
                feedback = verifier.get_learning_feedback()
                retrain_horizons = model.apply_learning_feedback(feedback)

                # Cooldown so the model can actually STABILIZE: don't auto-relearn again
                # until enough time has passed (a fresh relearn needs time to accumulate
                # its own verified samples before we judge it). Default 1h; tune via env.
                cooldown = float(os.environ.get("BTC_AUTO_RELEARN_COOLDOWN_SEC", "3600"))
                since_last = time.time() - backend_state.get("last_auto_relearn_time", 0.0)
                if retrain_horizons and not MODEL_FROZEN and since_last >= cooldown:
                    logger.info(
                        f"Auto-learning triggered retraining for horizons: {retrain_horizons}"
                    )
                    if schedule_relearn("auto-learning"):
                        backend_state["last_auto_relearn_time"] = time.time()
                elif retrain_horizons:
                    logger.debug(
                        "Auto-learning wants a retrain but is in cooldown "
                        f"({since_last:.0f}s/{cooldown:.0f}s) — letting the model stabilize."
                    )

            # Regime already computed above

            # Compute indicator snapshot for frontend (recent window only — uses latest values)
            indicators = compute_indicator_snapshot(recent_klines)
            indicator_series = compute_indicator_series(recent_klines)
            support_resistance = compute_support_resistance_payload(
                recent_klines, data_state.get("order_flow")
            )
            drift_state = model.compute_psi(live_features)
            accuracy_summary = verifier.get_accuracy_summary()
            error_summary = verifier.get_error_summary()
            action_summary = verifier.get_action_accuracy_summary()
            data_quality = verifier.get_data_quality_summary()
            signal_policy = data_state.get("signal_policy") or verifier.get_signal_policy(
                regime.get("regime", "UNKNOWN")
            )
            neutral_summary = verifier.get_neutral_reason_summary()
            _candle_ts = [k["time"] for k in data_state["klines"]]
            signal_history_state = {
                "snapshots": len(signal_buffer),
                "coverage_pct": round(signal_buffer.coverage(_candle_ts) * 100, 1),
            }
            # READ-ONLY feature-coverage / feed-health audit (freeze-safe measurement).
            feed_health = signal_buffer.coverage_report(_candle_ts)
            # LIVE training-signal values: the EXACT snapshot the recorder writes at each
            # candle close (what the model trains on), evaluated on current live state so
            # the operator can watch the features in real time. Cheap scalar reads.
            try:
                training_signals = signal_buffer._snapshot(data_state)
            except Exception:
                training_signals = {}
            boot_status = {
                "boot_seconds": backend_state.get("boot_seconds", 0.0),
                "ready": backend_state.get("ready_time", 0) > 0,
                "uptime_seconds": round(
                    time.time() - backend_state["startup_start_time"], 1
                ),
                "restored_pending_predictions": backend_state.get(
                    "restored_pending_predictions", 0
                ),
                "historical_days": HISTORICAL_DAYS,
            }
            if (
                time.time() - backend_state.get("last_fsr_ppo_summary_time", 0.0)
                >= 30
            ):
                backend_state["last_fsr_ppo_summary"] = database.fetch_fsr_ppo_summary(
                    20
                )
                backend_state["last_fsr_ppo_summary_time"] = time.time()
            fsr_ppo_summary = backend_state.get("last_fsr_ppo_summary") or {}
            
            # NOTE: The Polymarket "Value Engine" was removed — Polymarket only lists
            # long-dated BTC markets (e.g. "$150k by Dec 31"), which the fair-value model
            # could not price, producing misleading ~99% edges. The self-contained
            # PriceToBeatTracker (5m/15m) replaces it for the BTC up/down use case.

            # Construct dashboard payload
            payload = {
                "type": "update",
                # Real-time aggTrade price (sub-100ms) so the slow update's render doesn't
                # snap the displayed price back to the stale 1m-kline close between fast ticks.
                "price": data_state.get("live_price") or data_state["klines"][-1]["close"],
                # Pyth BTC/USD (the Polymarket settlement-oracle proxy) for the price-to-beat
                # panel — so the UI can show BOTH the Binance live price (model feed) and the
                # Pyth price (the side the bet actually resolves on).
                "pyth_price": data_state.get("pyth_price"),
                "pyth_price_age_s": (round(time.time() - data_state.get("pyth_price_ts", 0), 1)
                                     if data_state.get("pyth_price_ts") else None),
                "ticker_24h": rest_client.data.get("ticker_24h"),
                "order_flow": data_state["order_flow"],
                "derivatives": data_state["derivatives"],
                "sentiment": data_state["sentiment"],
                "chainlink_price": data_state.get("chainlink_price"),
                "tape": order_flow.get_last_trades_for_tape(15),
                "predictions": predictions,
                "regime": regime,
                "cascade_status": cascade_monitor.evaluate_cascade(),
                "meta_model": {h: meta_models[h].status() for h in model.horizons},
                "model_inventory": model.get_model_inventory(),
                "regime_model_accuracy": verifier.get_regime_model_accuracy(),
                "drift": drift_state,
                "boot_status": boot_status,
                "backtest_status": _safe_public_status(
                    backend_state.get("backtest_status")
                ),
                "relearn_status": _safe_public_status(
                    backend_state.get("relearn_status")
                ),
                "health": model.compute_health_score(
                    backend_state["last_backtest"], ws_client.running, model.is_trained
                ),
                "backtest": backend_state["last_backtest"],
                "indicators": indicators,
                "indicator_series": indicator_series,
                "support_resistance": support_resistance,
                "execution_simulator": simulator.get_metrics(),
                "ab_test": ab_runner.get_comparison(),
                "verification": {
                    "accuracy": accuracy_summary,
                    "recent": verifier.get_recent_verifications(15),
                    "pending": verifier.get_pending_count(),
                    "pending_by_horizon": verifier.get_pending_by_horizon(),
                    "histories": verifier.get_all_horizon_histories(30),
                    "error_summary": error_summary,
                    "action_summary": action_summary,
                    "neutral_summary": neutral_summary,
                    "signal_policy": signal_policy,
                    "data_quality": data_quality,
                    "learning_state": model.learning_adjustments,
                },
                "signal_policy": signal_policy,
                "signal_history": signal_history_state,
                "feed_health": feed_health,
                "training_signals": training_signals,
                "klines": format_klines_for_chart(data_state["klines"]),
                "volume_data": format_volume_for_chart(data_state["klines"]),
                "exchange_accuracy": exchange_verifier.accuracy(),
                "exchanges": build_exchanges_block(data_state),
                "scoreboard": build_scoreboard(
                    predictions,
                    {"accuracy": accuracy_summary},
                ),
                "model_accuracy": model_verifier.accuracy(),
                "fsr_ppo": data_state.get("fsr_ppo", {}),
                "fsr_ppo_summary": fsr_ppo_summary,
                "price_to_beat": {
                    "latest": price_to_beat_tracker.latest(),
                    "accuracy": price_to_beat_tracker.accuracy(),
                    # 60: six mirror horizons now resolve rounds; keep slow ones visible
                    "recent": price_to_beat_tracker.recent(200),
                },
                # Binance-priced mirror of the same game (in-memory; rebuilds live).
                "price_to_beat_binance": {
                    "latest": price_to_beat_binance_tracker.latest(),
                    "accuracy": price_to_beat_binance_tracker.accuracy(),
                    "recent": price_to_beat_binance_tracker.recent(200),
                },
            }

            if time.time() - backend_state.get("last_analysis_snapshot_time", 0) >= 60:
                database.log_analysis_snapshot(
                    {
                        "timestamp": int(time.time() * 1000),
                        "price": payload["price"],
                        "regime": regime.get("regime", "UNKNOWN"),
                        "boot_seconds": boot_status["boot_seconds"],
                        "signal_history_snapshots": signal_history_state["snapshots"],
                        "signal_history_coverage_pct": signal_history_state[
                            "coverage_pct"
                        ],
                        "resolved_total": sum(
                            int(v.get("total", 0) or 0)
                            for v in accuracy_summary.values()
                        ),
                        "pending_total": verifier.get_pending_count(),
                        "action_summary": action_summary,
                        "neutral_summary": neutral_summary,
                        "signal_policy": signal_policy,
                        "horizon_accuracy": accuracy_summary,
                        "error_summary": error_summary,
                        "drift": drift_state,
                        "support_resistance": support_resistance,
                        "indicator_snapshot": indicators,
                        "exchange_accuracy": exchange_verifier.accuracy(),
                        "fsr_ppo": {
                            "current": data_state.get("fsr_ppo", {}),
                            "summary": fsr_ppo_summary,
                        },
                        "notes": "periodic live analysis snapshot",
                    }
                )
                backend_state["last_analysis_snapshot_time"] = time.time()

            await broadcast(payload)

        except Exception as e:
            logger.error(f"Loop error: {e}", exc_info=True)


@app.get("/api/runtime-status")
async def runtime_status():
    return {
        "model_trained": model.is_trained,
        "boot_status": {
            "boot_seconds": backend_state.get("boot_seconds", 0.0),
            "ready": backend_state.get("ready_time", 0) > 0,
            "uptime_seconds": round(time.time() - backend_state["startup_start_time"], 1),
            "historical_days": HISTORICAL_DAYS,
        },
        "backtest_status": _safe_public_status(backend_state.get("backtest_status")),
        "relearn_status": _safe_public_status(backend_state.get("relearn_status")),
        "model_inventory": model.get_model_inventory(),
    }


@app.post("/api/relearn")
async def api_relearn():
    scheduled = schedule_relearn("manual-ui")
    return {
        "scheduled": scheduled,
        "status": _safe_public_status(backend_state.get("relearn_status")),
    }


@app.post("/api/backtest")
async def api_backtest():
    scheduled = schedule_backtest("manual-ui")
    return {
        "scheduled": scheduled,
        "status": _safe_public_status(backend_state.get("backtest_status")),
    }


@app.get("/api/action-log")
async def api_action_log(limit: int = 50):
    """Timestamped feed of recorded predictions across all horizons (latest first):
    what the tool advised, what it expected, and how it resolved."""
    try:
        limit = max(1, min(int(limit), 500))
    except Exception:
        limit = 50
    # Degrade gracefully: the live backend holds the DuckDB write connection, so a
    # read here can lose a lock race. Return an empty feed instead of a 500 so the
    # Models & Signals tab renders (it retries on the next poll) rather than erroring.
    try:
        return {"items": database.fetch_action_log(limit)}
    except Exception as e:
        logger.warning("action-log fetch failed (returning empty): %s", e)
        return {"items": [], "error": "temporarily_unavailable"}


@app.get("/api/scorecard")
async def get_scorecard():
    """Sign-truth scorecard via the live process — Windows DuckDB locks are exclusive
    (outside processes can't even COPY the file), so the app itself serves the
    measurement. Same era-filtered queries as backend/sign_truth_scorecard.py."""
    def _run():
        era = 0
        try:
            _vp = os.path.join(DATA_DIR, "saved_models", "architecture_version.pkl")
            if os.path.exists(_vp):
                era = int(os.path.getmtime(_vp) * 1000)
        except Exception:
            pass
        out = {"era_ts": era, "generated_at": int(time.time() * 1000),
               "horizons": {}, "mirror": {}, "partial_candle_buckets": []}
        conn = database._connect()
        try:
            for h in [1, 3, 5, 7, 10, 15]:
                try:
                    r = conn.execute(f"""
                        SELECT COUNT(*),
                               SUM(CASE WHEN (raw_direction='UP' AND actual_move>0)
                                          OR (raw_direction='DOWN' AND actual_move<0) THEN 1 ELSE 0 END),
                               SUM(CASE WHEN raw_direction='UP' THEN 1 ELSE 0 END),
                               SUM(CASE WHEN raw_direction='DOWN' THEN 1 ELSE 0 END),
                               SUM(CASE WHEN raw_direction='UP' AND actual_move>0 THEN 1 ELSE 0 END),
                               SUM(CASE WHEN raw_direction='DOWN' AND actual_move<0 THEN 1 ELSE 0 END)
                        FROM predictions_{h}m
                        WHERE resolved AND raw_direction IN ('UP','DOWN') AND actual_move IS NOT NULL
                          AND timestamp >= {era}
                    """).fetchone()
                    n, w, u, d, uw, dw = (int(x or 0) for x in r)
                    out["horizons"][h] = {
                        "n": n, "wins": w, "acc": round(w / n, 4) if n else None,
                        "up_n": u, "up_acc": round(uw / u, 4) if u else None,
                        "down_n": d, "down_acc": round(dw / d, 4) if d else None,
                    }
                except Exception as e:
                    out["horizons"][h] = {"error": str(e)}
            for h in (1, 3, 5, 7, 10, 15):
                try:
                    rows = conn.execute(f"""
                        SELECT COALESCE(lean_source,'model'), COUNT(*),
                               SUM(CASE WHEN hit THEN 1 ELSE 0 END)
                        FROM price_to_beat
                        WHERE horizon={h} AND resolved AND our_direction IN ('UP','DOWN')
                          AND timestamp >= {era}
                        GROUP BY 1
                    """).fetchall()
                    out["mirror"][h] = {s: {"n": int(n), "wins": int(w or 0),
                                            "acc": round((w or 0) / n, 4) if n else None}
                                        for s, n, w in rows}
                except Exception as e:
                    out["mirror"][h] = {"error": str(e)}
            # Per-BASE-MODEL directional accuracy (which model earns its seat) —
            # from the per-model vote verifier's resolved rows, era-filtered.
            out["models"] = {}
            try:
                rows = conn.execute(f"""
                    SELECT model, horizon, COUNT(*),
                           SUM(CASE WHEN hit THEN 1 ELSE 0 END)
                    FROM model_predictions
                    WHERE resolved AND direction IN ('UP','DOWN')
                      AND timestamp >= {era}
                    GROUP BY 1, 2 ORDER BY 1, 2
                """).fetchall()
                for m, h, n, w in rows:
                    out["models"].setdefault(str(m), {})[int(h)] = {
                        "n": int(n), "acc": round((w or 0) / n, 4) if n else None}
            except Exception as e:
                out["models"] = {"error": str(e)}
            try:
                rows = conn.execute(f"""
                    SELECT CAST(FLOOR((timestamp % 60000) / 15000.0) AS INT), COUNT(*),
                           ROUND(AVG(CASE WHEN (raw_direction='UP' AND actual_move>0)
                                            OR (raw_direction='DOWN' AND actual_move<0)
                                          THEN 1.0 ELSE 0.0 END), 4)
                    FROM predictions_5m
                    WHERE resolved AND raw_direction IN ('UP','DOWN') AND actual_move IS NOT NULL
                      AND timestamp >= {era}
                    GROUP BY 1 ORDER BY 1
                """).fetchall()
                out["partial_candle_buckets"] = [
                    {"bucket": int(b), "n": int(n), "acc": float(a)} for b, n, a in rows]
            except Exception:
                pass
        finally:
            conn.close()
        return out
    return await asyncio.get_event_loop().run_in_executor(None, _run)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    clients.append(websocket)
    try:
        await websocket.send_text(json.dumps({"type": "connected"}))
        while True:
            # Broadcast-only socket: inbound text is read to keep the connection
            # alive / detect disconnects, and intentionally discarded.
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in clients:
            clients.remove(websocket)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "server:app",
        host="127.0.0.1",
        port=8000,
        reload=os.getenv("BTC_DEV_RELOAD", "0") == "1",
    )
