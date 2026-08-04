"""
FastAPI Backend Server + WebSocket Handler.
Orchestrates connections to Binance, maintains model state,
streams real-time predictions, indicators, and verification data to frontend.
"""

import asyncio
import copy
import functools
import requests  # used only by the lightweight Pyth price-to-beat anchor poller
import json
import time
import logging
import os
import uuid
import hashlib
import hmac
from logging.handlers import RotatingFileHandler
from types import SimpleNamespace
from fastapi import (
    FastAPI,
    WebSocket,
    WebSocketDisconnect,
    Header,
    HTTPException,
    Query,
    Request,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
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
from decision_gate import compute_no_trade_reasons
from features import (
    build_features_from_klines,
    build_sequences,
    compute_indicator_snapshot,
    compute_indicator_series,
    LOOKBACK,
    NUM_FEATURES,
    FEATURE_NAMES,
    atr as compute_atr,
)
from model_verifier import PerModelVerifier
from price_to_beat import PriceToBeatTracker, persistence_model_status
from open_position_action_recorder import recorder as open_position_action_recorder
import round_state_panel
from trade_forecast import live_forecaster as complete_trade_forecaster
import model_metrics_logger        # separate DuckDB; logs every model's live output (crash-safe)
from exchange_verifier import PerVenueVerifier
from model import MultiModelEnsemble, CascadeMonitor, MODEL_ARCH_VERSION, MODEL_DIR
import model_promotion
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
from polymarket_verifier import PolymarketVerifier
from fsr_ppo_strategy import FSRPPOStrategy
from binance_paper import BinancePaperService
from binance_paper.routes import (
    configure_service as configure_binance_paper_service,
    router as binance_paper_router,
)
from historical_replay import run_replay as run_historical_replay
from control_auth import (
    allowed_origins as _allowed_origins,
    origin_is_allowed as _origin_is_allowed,
    token_is_usable as _token_is_usable,
)
from task_supervisor import (
    BEST_EFFORT as TASK_BEST_EFFORT,
    CRITICAL as TASK_CRITICAL,
    IMPORTANT as TASK_IMPORTANT,
    SUPERVISOR,
)
from feed_writer import FEED_WRITER
from model_revision_ledger import ModelRevisionLedger, forecast_identity
from bc_forward_readiness_report import (
    SourceUnreadable as ForwardReadinessUnavailable,
    build_report as build_forward_readiness_report,
)
from evidence_health_report import build_report as build_evidence_health_report
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
MODEL_REVISION_DB = os.path.join(DATA_DIR, "model_revision_ledger.duckdb")
_MODEL_REVISION_LEDGER = None
_FORWARD_READINESS_CACHE = {"generated_at_s": 0.0, "payload": None}
_EVIDENCE_HEALTH_CACHE = {"generated_at_s": 0.0, "payload": None}


def _model_revision_ledger() -> ModelRevisionLedger:
    """Create the research-only revision ledger lazily, never as an import side effect."""
    global _MODEL_REVISION_LEDGER
    if _MODEL_REVISION_LEDGER is None:
        _MODEL_REVISION_LEDGER = ModelRevisionLedger(MODEL_REVISION_DB)
    return _MODEL_REVISION_LEDGER


def _revision_market_quote(current_price: float, order_flow_state: dict, observed_ts: int) -> dict:
    """Exact observable Binance quote proxy used by the direction model at this cycle."""
    mid = float(order_flow_state.get("mid_price") or current_price)
    spread_bps = float(order_flow_state.get("spread_bps") or 0.0)
    half_spread = mid * max(0.0, spread_bps) / 20_000.0
    return {
        "venue": "BINANCE_SPOT",
        "symbol": "BTCUSDT",
        "observed_ts": int(observed_ts),
        "last_price": float(current_price),
        "mid": mid,
        "bid": mid - half_spread,
        "ask": mid + half_spread,
        "spread_bps": spread_bps,
        "quote_method": "mid_plus_observed_spread",
    }


def _revision_rows(predictions: list[dict], current_price: float,
                   order_flow_state: dict, prediction_ts: int) -> list[dict]:
    quote = _revision_market_quote(current_price, order_flow_state, prediction_ts)
    rows = []
    for prediction in predictions:
        # A model revision is the forecast BEFORE server-side trade vetoes. FinalDirection may be
        # NEUTRAL because costs/feed/risk blocked a directional forecast; recording that as a
        # model NEUTRAL would corrupt stability and calibration research. Both states remain in
        # model_outputs so forecast quality and decision quality can be evaluated separately.
        model_prediction, calibrated, calibration_source = forecast_identity(prediction)
        rows.append({
            "release_id": str(
                prediction.get("model_bundle_id") or f"unversioned:{MODEL_ARCH_VERSION}"
            ),
            "model_id": "main_ensemble",
            "horizon_min": int(prediction.get("horizon") or 0),
            "prediction_ts": int(prediction_ts),
            "prediction": model_prediction,
            "calibrated_probability": float(calibrated or 0.0),
            "probability_up": float(prediction.get("probUp", 0.0) or 0.0),
            "probability_down": float(prediction.get("probDown", 0.0) or 0.0),
            "probability_neutral": float(prediction.get("probNeutral", 0.0) or 0.0),
            "reference_price": float(current_price),
            "market_quote": quote,
            "model_outputs": {
                **copy.deepcopy(prediction),
                "calibration_source": calibration_source,
            },
        })
    return rows


def _write_revision_cycle(*, predictions: list[dict], feature_values: np.ndarray,
                          feature_names: list[str], snapshot_ts: int,
                          current_price: float, order_flow_state: dict,
                          prediction_ts: int) -> tuple[list[str], int]:
    """Blocking DuckDB work executed by main_loop in a worker thread."""
    ledger = _model_revision_ledger()
    revision_ids = ledger.record_batch(
        _revision_rows(predictions, current_price, order_flow_state, prediction_ts),
        feature_values=feature_values,
        feature_names=feature_names,
        snapshot_ts=int(snapshot_ts),
        # The sequence includes live order-flow values captured at snapshot_ts, so its causal
        # cutoff is the snapshot instant rather than merely the newest closed candle.
        feature_cutoff_ts=int(snapshot_ts),
        now_ms=int(time.time() * 1000),
    )
    outcomes = ledger.resolve_due(
        observed_price=float(current_price),
        observed_ts=int(prediction_ts),
    )
    return revision_ids, outcomes
LOG_DIR = (os.getenv("BTC_LOG_DIR") or "").strip()
if LOG_DIR:
    os.makedirs(LOG_DIR, exist_ok=True)
    file_handler = RotatingFileHandler(
        os.path.join(LOG_DIR, "backend.log"),
        maxBytes=max(1, _env_int("BTC_LOG_MAX_MB", 20)) * 1024 * 1024,
        backupCount=max(1, _env_int("BTC_LOG_BACKUP_COUNT", 10)),
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s:%(name)s:%(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    logging.getLogger().addHandler(file_handler)

_BACKEND_CODE_FILES = (
    "server.py",
    "price_to_beat.py",
    "database.py",
    "decision_champion.py",
    "round_state_panel.py",
)


def _backend_code_hash() -> str:
    """Hash the core decision/ledger source currently present on disk."""
    digest = hashlib.sha256()
    backend_dir = os.path.dirname(os.path.abspath(__file__))
    for name in _BACKEND_CODE_FILES:
        path = os.path.join(backend_dir, name)
        digest.update(name.encode("utf-8"))
        try:
            with open(path, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            digest.update(b"<missing>")
    return digest.hexdigest()[:12]


BACKEND_BOOT_CODE_HASH = _backend_code_hash()
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

# Admin passcode gate for expensive/mutating actions (relearn, backtest, replay).
# Local development may deliberately run without a token. A production deployment may not:
# leaving the token unset used to disable the gate entirely, so any dashboard viewer could launch
# a multi-hour retrain or replay.
ADMIN_TOKEN = (os.getenv("BTC_ADMIN_TOKEN") or "").strip()
DEPLOYMENT_ENV = (os.getenv("BTC_DEPLOYMENT_ENV") or "development").strip().lower()
REQUIRE_ADMIN_TOKEN = (
    os.getenv("BTC_REQUIRE_ADMIN_TOKEN", "1" if DEPLOYMENT_ENV == "production" else "0") == "1"
)
ADMIN_TOKEN_USABLE, ADMIN_TOKEN_ISSUE = _token_is_usable(
    ADMIN_TOKEN or None,
    env_name="BTC_ADMIN_TOKEN",
)
if REQUIRE_ADMIN_TOKEN and not ADMIN_TOKEN_USABLE:
    raise RuntimeError(
        f"{ADMIN_TOKEN_ISSUE} when BTC_REQUIRE_ADMIN_TOKEN=1 "
        f"(BTC_DEPLOYMENT_ENV={DEPLOYMENT_ENV!r})."
    )


def _require_admin(token: str | None) -> None:
    if ADMIN_TOKEN and not ADMIN_TOKEN_USABLE:
        raise HTTPException(
            status_code=503,
            detail="Admin actions are disabled: configured token is not usable.",
        )
    if REQUIRE_ADMIN_TOKEN and not ADMIN_TOKEN_USABLE:
        raise HTTPException(
            status_code=503,
            detail="Admin actions are disabled: token not configured.",
        )
    if ADMIN_TOKEN_USABLE and not hmac.compare_digest(
        (token or "").strip(), ADMIN_TOKEN
    ):
        raise HTTPException(status_code=403, detail="Admin passcode required for this action.")
FORCE_MAIN_RETRAIN = (
    os.getenv("BTC_FORCE_MAIN_RETRAIN", "0") == "1"
    or os.getenv("BTC_OVERNIGHT_TRAIN_ALL", "0") == "1"
)
RETRAIN_COMPLETION_MARKER = os.getenv("BTC_RETRAIN_COMPLETION_MARKER", "").strip()
HEAD_RETRAIN_COMPLETE = os.getenv("BTC_HEAD_RETRAIN_COMPLETE", "0") == "1"
FULL_REFIT_AFTER_GATE = os.getenv("BTC_FULL_REFIT_AFTER_GATE", "0") == "1"
MODEL_CHALLENGER_DIR = os.path.join(DATA_DIR, "saved_models", "challengers")
MODEL_PROMOTION_REPORT_DIR = os.path.join(DATA_DIR, "saved_models", "promotion_reports")
FULL_REFIT_SHADOW_MANIFEST = os.path.join(DATA_DIR, "saved_models", "full_refit_shadow.json")
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


def _write_retrain_completion_marker(trained_model, deployment_state: str = "active",
                                     gate_report_path: str | None = None) -> bool:
    """Atomically mark a requested full retrain complete only after heads and main saved."""
    if not RETRAIN_COMPLETION_MARKER:
        return False
    if not HEAD_RETRAIN_COMPLETE:
        logger.warning("[TRAIN] Full-retrain marker not written: one or more standalone heads failed.")
        return False
    try:
        payload = {
            "completed_at": time.time(),
            "historical_days": HISTORICAL_DAYS,
            "model_arch": MODEL_ARCH_VERSION,
            "horizons": list(trained_model.horizons),
            "model_features": int(trained_model.model_num_features),
            "train_split_frac": float(trained_model.train_split_frac),
            "model_bundle_id": str(getattr(trained_model, "model_bundle_id", "")),
            "model_dir": str(getattr(trained_model, "model_dir", "")),
            "full_refit": bool(getattr(trained_model, "full_refit", False)),
            "deployment_state": deployment_state,
            "gate_report_path": gate_report_path,
            "heads_complete": True,
            "model_trained": bool(trained_model.is_trained),
        }
        os.makedirs(os.path.dirname(RETRAIN_COMPLETION_MARKER), exist_ok=True)
        tmp = RETRAIN_COMPLETION_MARKER + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, RETRAIN_COMPLETION_MARKER)
        logger.info("[TRAIN] Full retrain completion marker written: %s", RETRAIN_COMPLETION_MARKER)
        return True
    except Exception as exc:
        logger.error("[TRAIN] Could not write full-retrain marker: %s", exc)
        return False


def _write_active_train_boundary(train_boundary_ts=None, *, full_refit: bool = False,
                                 gate_report_path: str | None = None) -> None:
    """Persist the active bundle's honest historical-validation boundary.

    A 100% refit has no untouched historical tail. Writing a null boundary prevents the
    backtest path from inheriting an incumbent boundary and reporting fitted rows as OOS.
    """
    payload = {
        "train_boundary_ts": int(train_boundary_ts) if train_boundary_ts else None,
        "full_refit": bool(full_refit),
        "gate_report_path": gate_report_path,
        "updated_at": time.time(),
    }
    model_promotion.atomic_json(
        os.path.join(DATA_DIR, "saved_models", "train_boundary.json"), payload
    )
    backend_state["train_boundary_ts"] = payload["train_boundary_ts"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing Database...")
    database.init_db()
    try:
        open_position_action_recorder().record_positions(
            [],
            market_snapshot=None,
            recorded_ts=int(time.time() * 1000),
            context={"round_id": "startup", "mode": "PAPER_RESEARCH_ONLY"},
        )
    except Exception as exc:
        logger.error("Open-position evidence recorder startup failed: %s", exc)
    # The feed writer is owned HERE, not started at import. An import-time thread starts during
    # test collection, hot reloads, pre-fork workers and any module that imports server.py merely
    # to inspect it - and it was never stopped, so queued writes were abandoned at shutdown.
    FEED_WRITER.start()
    logger.info("Feed writer started (bounded, non-blocking persistence queue)")
    binance_paper_service.initialize()
    loaded_signals = signal_buffer.load(SIGNAL_HISTORY_PATH)
    logger.info(f"Loaded {loaded_signals} persisted signal-history snapshots")
    _pending_predictions = database.fetch_unresolved_predictions()
    restored = verifier.restore_from_database(
        _pending_predictions,
        database.get_last_prediction_timestamps(),
    )
    backend_state["restored_pending_predictions"] = restored
    logger.info(f"Restored {restored} pending predictions from DuckDB")
    _resolved_restored = verifier.restore_verified_from_database(
        database.fetch_prediction_verifier_history(500)
    )
    logger.info("Restored %s current-model verified predictions", _resolved_restored)
    _model_restore = model_verifier.restore_from_database(
        _pending_predictions,
        database.fetch_model_verifier_history(500),
    )
    logger.info(
        "Restored per-model verifier: %s committed outcomes, %s pending votes",
        _model_restore["resolved"], _model_restore["pending"],
    )
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
        _open_ptb = price_to_beat_tracker.restore_pending(
            database.fetch_open_price_to_beat("pyth"))
        _open_binance = price_to_beat_binance_tracker.restore_pending(
            database.fetch_open_price_to_beat("binance"))
        if _open_ptb or _open_binance:
            logger.info("Restored %s Pyth and %s Binance open price-to-beat rounds",
                        _open_ptb, _open_binance)
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
    futures_ws_client.on("book", binance_paper_service.on_book)
    cross_asset_client.on("cross_asset_trade", handle_cross_asset_trade)
    cross_asset_client.on("cross_asset_depth", handle_cross_asset_depth)
    cross_asset_client.on("cross_asset_kline", handle_cross_asset_kline)

    logger.info("Starting background tasks...")
    logger.info("Discovering Polymarket Markets...")
    # Gamma discovery uses requests. Keep that blocking HTTP call off the event
    # loop so startup WebSocket/price tasks are not delayed by a slow response.
    await asyncio.to_thread(polymarket_client.discover_markets)
    # OWNED, NOT DETACHED. These were bare create_task calls with no retained handle: the loop
    # keeps only a WEAK reference, so a task could be garbage-collected mid-flight, an exception
    # inside one was never observed, and shutdown never awaited any of them. A dead feed then
    # coexisted with a server answering 200.
    #
    # Criticality is declared here. A dead or flapping CRITICAL task becomes a trust blocker.
    for _name, _factory, _crit in (
        ("main_loop", main_loop, TASK_CRITICAL),
        ("fast_price_broadcaster", fast_price_broadcaster, TASK_IMPORTANT),
        ("pyth_price_poller", pyth_price_poller, TASK_CRITICAL),
        ("price_to_beat_ticker", price_to_beat_ticker, TASK_IMPORTANT),
        ("binance_spot_ws", ws_client.connect, TASK_CRITICAL),
        ("coinbase_ws", coinbase_client.connect, TASK_BEST_EFFORT),
        ("binance_futures_ws", futures_ws_client.connect, TASK_CRITICAL),
        ("binance_paper_service", binance_paper_service.run, TASK_IMPORTANT),
        ("polymarket_ws", polymarket_client.connect_ws, TASK_IMPORTANT),
        ("cross_asset_ws", cross_asset_client.connect, TASK_BEST_EFFORT),
    ):
        SUPERVISOR.spawn(_name, _factory, criticality=_crit)
    logger.info("Task supervisor owns %s long-running tasks", SUPERVISOR.status()["total"])

    yield

    # Stop supervised tasks FIRST and await them, so no feed callback is still running while
    # the clients below are torn down.
    _task_shutdown = await SUPERVISOR.shutdown(timeout=5.0)
    if _task_shutdown["clean"]:
        logger.info("Task supervisor shutdown clean: %s", _task_shutdown)
    else:
        logger.error("Task supervisor shutdown INCOMPLETE: %s", _task_shutdown)
    ws_client.stop()
    coinbase_client.stop()
    futures_ws_client.stop()
    binance_paper_service.shutdown()
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
    # Stop LAST: the clients above may still hand off writes while they close. The result is
    # logged rather than discarded, so an incomplete drain is a visible line in the log instead
    # of a silent gap in the parquet files.
    _feed_shutdown = FEED_WRITER.stop(timeout=5.0)
    if _feed_shutdown.clean:
        logger.info("Feed writer shutdown clean: %s", dict(_feed_shutdown))
    else:
        logger.error("Feed writer shutdown INCOMPLETE - queued writes lost: %s",
                     dict(_feed_shutdown))
    model_metrics_logger.close()
    database.close_db()


app = FastAPI(
    lifespan=lifespan,
    docs_url=None if DEPLOYMENT_ENV == "production" else "/docs",
    redoc_url=None if DEPLOYMENT_ENV == "production" else "/redoc",
    openapi_url=None if DEPLOYMENT_ENV == "production" else "/openapi.json",
)
# EXPLICIT ORIGINS. `allow_origins=["*"]` let any site the operator had open in the same browser
# issue cross-origin requests to this API - and until now the paper-trading control endpoints
# required no authentication, so that was a drive-by control plane. Override with
# BTC_ALLOWED_ORIGINS (comma-separated); the default is loopback only.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), payment=()"
    )
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self' ws: wss:; "
        "font-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'"
    )
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
    if request.url.scheme == "https" or forwarded_proto == "https":
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
    return response


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
model_verifier = PerModelVerifier(horizons=(5, 15))   # pruned 2026-06-21: dropped 3/7/10/30
# 1m/3m/7m/10m are PRACTICE mirrors (Polymarket's real BTC windows are 5m/15m):
# same rule, same grading — they accrue evidence fast and map every horizon's
# betting behavior; only 5m/15m are real markets.
price_to_beat_tracker = PriceToBeatTracker(horizons=(5, 15))   # pruned 2026-06-21: tradeable markets only
# Binance-priced MIRROR of the same up/down game — anchored on the live Binance feed
# (the model's native data) instead of Pyth. Now PERSISTS with source="binance" (source-prefixed
# ids, so it never collides with the Pyth rows in the shared `price_to_beat` table) — this lets the
# timeframe / time-of-day analysis cover the Binance anchor too. Pyth-only auxiliary recorders
# (persistence/champion snapshots) and the boot rehydration stay source-gated. The in-memory UI strip
# still rebuilds live after a restart (we don't rehydrate the mirror); the DB accrues for analysis.
price_to_beat_binance_tracker = PriceToBeatTracker(horizons=(5, 15), persist=True, source="binance")
exchange_verifier = PerVenueVerifier(horizons=(5, 15))   # pruned 2026-06-21: dropped 30m
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


def restore_full_refit_shadow() -> bool:
    """Reload a completed 100%-data challenger without changing the decision primary."""
    try:
        if not os.path.exists(FULL_REFIT_SHADOW_MANIFEST):
            return False
        with open(FULL_REFIT_SHADOW_MANIFEST, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if payload.get("status") != "shadow" or not payload.get("model_dir"):
            return False
        config = dict(getattr(model, "config", {}) or {})
        config["model_bundle_id"] = payload.get("bundle_id") or "full_refit_shadow"
        shadow = MultiModelEnsemble(
            horizons=model.horizons,
            config=config,
            model_dir=payload["model_dir"],
        )
        shadow.cascade_monitor = cascade_monitor
        if not shadow.load_models():
            logger.warning("[PROMOTION] Persisted full-refit shadow failed model validation.")
            return False
        ab_runner.primary = ModelVariant(f"incumbent_{model.model_bundle_id}", model)
        ab_runner.challenger = ModelVariant(
            f"full_refit_shadow_{shadow.model_bundle_id}",
            shadow,
            started_at=float(payload.get("created_at") or time.time()),
        )
        ab_runner.enabled = True
        restored = ab_runner.restore_from_db()
        logger.info(
            "[PROMOTION] Restored full-refit live shadow %s (%s resolved outcomes).",
            shadow.model_bundle_id,
            restored,
        )
        return True
    except Exception as exc:
        logger.warning("[PROMOTION] Could not restore full-refit shadow: %s", exc)
        return False

# Polymarket Value Engine
# pm_model / pm_simulator REMOVED 2026-07-28: both were instantiated here and
# never called. polymarket_simulator invents a 1% notional fee that does not
# exist and synthesises the NO ask as (1 - YES bid); polymarket_model returns a
# placeholder residual of 0.0. Neither may sit on the live import surface. They
# now raise on construction - see the guards in those modules.
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
    "replay_status": _fresh_status(),
    "last_historical_replay": {"summary": {}, "recent": []},
    "last_historical_replay_time": 0.0,
    "last_threshold_recommendations": {"recommendations": [], "summary": ""},
}

backtest_task = None
relearn_task = None
replay_task = None

data_state = {
    "klines": [],
    "klines_5m": [],
    "klines_15m": [],
    "order_flow": {},
    "derivatives": {},
    "sentiment": {},
    "coinbase_premium": 0.0,
    "bybit_data": {},
    "feed_timestamps_ms": {},
    "poor_regimes": {},
    "macro": {"dxy": 104.5, "us10y": 4.25},
    "eth_price": 0.0,
    "eth_volume": 0.0,
    "eth_imbalance": 0.0,
    "sol_price": 0.0,
    "sol_volume": 0.0,
    "sol_imbalance": 0.0,
    "_ptb_preds": {},
    "_model_context_updated_ms": 0,
    "_binance_paper_context": {},
}


def _binance_paper_model_context() -> dict:
    """Pre-published compact context; no copying or model work on futures book ticks."""
    value = data_state.get("_binance_paper_context") or {}
    return value if isinstance(value, dict) else {}


binance_paper_service = BinancePaperService(
    futures_ws_client,
    lambda: data_state.get("derivatives") or {},
    _binance_paper_model_context,
)
configure_binance_paper_service(binance_paper_service)
app.include_router(binance_paper_router)


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


async def _supervised(coro_fn, name: str):
    """Run a forever-task under a supervisor: if it EXITS or CRASHES for any reason
    (including BaseException escapes that `except Exception` inside the task cannot see),
    log loudly and restart it after 5s. Cancellation still propagates (clean shutdown).
    Added 2026-07-03 after the Pyth poller died silently mid-session and froze the
    price-to-beat panel for 15+ minutes with no log line."""
    while True:
        try:
            await coro_fn()
            logger.error("[supervisor] %s returned unexpectedly; restarting in 5s", name)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:                # noqa: BLE001 -- supervisor must survive anything
            logger.error("[supervisor] %s crashed (%r); restarting in 5s", name, exc)
        await asyncio.sleep(5.0)


# Dedicated single thread for the Pyth fetch: run_in_executor(None, ...) uses the SHARED default
# pool, so any other blocking work clogging it silently parks the poller forever (observed
# 2026-07-03: pyth_price stopped updating while Hermes itself was healthy). One private worker
# means the anchor poll can never be starved by unrelated blocking calls.
from concurrent.futures import ThreadPoolExecutor as _TPE
_PYTH_FETCH_POOL = _TPE(max_workers=1, thread_name_prefix="pyth-poll")


async def pyth_price_poller():
    """Poll Pyth BTC/USD ~every 1.5s for the price-to-beat anchor. Runs the blocking HTTP
    call in a PRIVATE worker thread so it can neither stall the event loop nor be starved
    by the shared pool. Stores price + timestamp; the ticker falls back to Binance if this
    goes stale, so the panel never freezes. Runs under _supervised() -- auto-restarts."""
    loop = asyncio.get_event_loop()

    # PERSISTENT session (2026-07-03): a bare requests.get opens DNS+TCP+TLS EVERY poll; from this
    # network that costs 6-11s per call, so every 5m boundary anchor arrived seconds late and the
    # fail-closed tracker skipped ~half the rounds. Keep-alive makes each poll a single ~RTT hop.
    session = requests.Session()
    session.headers["Connection"] = "keep-alive"

    def _fetch():
        t0 = time.time()
        r = session.get("https://hermes.pyth.network/v2/updates/price/latest",
                        params={"ids[]": PYTH_BTC_ID}, timeout=(4, 6))
        p = r.json()["parsed"][0]["price"]
        dt = time.time() - t0
        if dt > 3.0:
            logger.warning("Pyth poll slow: %.1fs (connection reuse may have been dropped)", dt)
        return float(p["price"]) * (10 ** int(p["expo"]))

    last_error_log = 0.0
    last_stale_log = 0.0
    while True:
        try:
            # wait_for guards the await itself: even if the worker thread wedged, the poller
            # keeps cycling and logging instead of parking forever on a dead future.
            price = await asyncio.wait_for(
                loop.run_in_executor(_PYTH_FETCH_POOL, _fetch), timeout=15.0)
            if price and price > 0:
                data_state["pyth_price"] = price
                data_state["pyth_price_ts"] = time.time()
                data_state["feed_timestamps_ms"]["pyth_price"] = int(time.time() * 1000)
        except Exception as exc:
            now = time.time()
            if now - last_error_log >= 30.0:
                logger.warning("Pyth price poll failed; retrying: %s", exc)
                last_error_log = now
        # loud staleness watchdog: if the anchor is >60s old, say so every 60s -- a silent
        # freeze must never be silent again.
        now = time.time()
        age = now - float(data_state.get("pyth_price_ts") or 0)
        if age > 60.0 and now - last_stale_log >= 60.0:
            logger.error("Pyth anchor STALE for %.0fs (poller alive, feed not updating)", age)
            last_stale_log = now
        await asyncio.sleep(0.75)   # fresher anchor for the price-to-beat panel (was 1.5s)


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
    _last_error_log = 0.0
    _last_no_pyth_log = 0.0
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
                    # Without one Pyth observation there is no measured venue offset.
                    # Raw Binance must not open a round that may later resolve on Pyth.
                    ref = None
                    kl = None
                    now = time.time()
                    if now - _last_no_pyth_log >= 30.0:
                        logger.warning(
                            "Pyth is unavailable and no Pyth/Binance offset is known; "
                            "settlement-feed rounds remain paused."
                        )
                        _last_no_pyth_log = now
            _keepers = None   # live vol keepers for BOTH trackers; set in the `if ref` block
            if ref:
                # FEED-FRESHNESS guard: a ref unchanged for >10s means the anchor feed is
                # frozen; still resolve/refresh, but DO NOT open new rounds at a stale price.
                if ref != _last_ref:
                    _last_ref = ref
                    _last_change_t = time.time()
                feed_fresh = (time.time() - _last_change_t) < 10.0
                # Live volatility keepers (rv_15m/30m/60m, compression, shock + vpin) computed
                # from the recent klines via edge_probe builders (parity-proven, live_keepers).
                # Feeds the keeper P(hold) model + the signed-quantile band; None => fallback.
                _keepers = None
                try:
                    _kl = data_state.get("klines") or []
                    if len(_kl) >= 70:
                        import live_keepers
                        _rk = _kl[-130:]
                        _keepers = live_keepers.compute_keepers(
                            [int(k.get("time", 0)) // 60000 for k in _rk],
                            [k["close"] for k in _rk], [k["high"] for k in _rk],
                            [k["low"] for k in _rk],
                            vpin=(_safe_dict(data_state.get("order_flow")) or {}).get("vpin"))
                except Exception as _ke:
                    logger.debug(f"live keepers compute skipped: {_ke}")
                price_to_beat_tracker.update(
                    int(time.time() * 1000),
                    float(ref),
                    data_state.get("_ptb_preds") or {},
                    {},  # kronos removed in v6 — tracker records "NONE"
                    klines=kl,
                    feed_fresh=feed_fresh,
                    keepers=_keepers,
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
                    keepers=_keepers,
                )
            # Log the price-to-beat model outputs (P(Hold), tier, band, projection) to the
            # separate metrics DuckDB — crash-safe, both venues.
            try:
                model_metrics_logger.log_ptb(
                    list(price_to_beat_tracker.latest_round.values()), venue="pyth")
                model_metrics_logger.log_ptb(
                    list(price_to_beat_binance_tracker.latest_round.values()), venue="binance")
            except Exception as exc:
                logger.debug("Price-to-beat metrics logging skipped: %s", exc)
        except Exception as exc:
            now = time.time()
            if now - _last_error_log >= 30.0:
                logger.warning("Price-to-beat ticker error; loop will continue: %s", exc)
                _last_error_log = now
        # Fast price-to-beat push (1s) so the Pyth-anchored panel doesn't wait for the heavy ~3s
        # main payload. Lightweight: just the live latest rounds for both trackers (no accuracy/
        # recent — those change slowly and ride the main payload). Crash-safe.
        try:
            await broadcast({
                "type": "ptb_tick",
                "pyth": price_to_beat_tracker.latest(),
                "binance": price_to_beat_binance_tracker.latest(),
            })
        except Exception as exc:
            logger.debug("Price-to-beat tick broadcast skipped: %s", exc)
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


_PAPER_RULE_CACHE = {"ts": 0.0, "val": None}


_PTB_ALLTIME_CACHE = {"ts": 0.0, "val": {}}


def _ptb_alltime_accuracy():
    """All-time price-to-beat accuracy straight from DuckDB, cached 60s."""
    now = time.time()
    if now - _PTB_ALLTIME_CACHE["ts"] < 60.0:
        return _PTB_ALLTIME_CACHE["val"]
    _PTB_ALLTIME_CACHE["ts"] = now
    try:
        import database
        _PTB_ALLTIME_CACHE["val"] = database.fetch_price_to_beat_accuracy() or {}
    except Exception:
        pass  # keep last good value; never break serving
    return _PTB_ALLTIME_CACHE["val"]


def _accuracy_alltime(tracker) -> dict:
    """tracker.accuracy() with total/hits/accuracy overridden by the ALL-TIME DB counts.

    Ported from the Oracle deployment 2026-07-25. The tracker keeps a capped in-memory ring
    buffer, so on a long-running box the headline win rate silently becomes "accuracy over the
    last N rounds" while presenting itself as the overall figure. The DB has every resolved
    round; use it for the headline and keep the tracker's richer per-horizon structure.
    """
    acc = tracker.accuracy()
    alltime = _ptb_alltime_accuracy()
    for _h, _a in acc.items():
        at = alltime.get(int(_h)) if isinstance(alltime, dict) else None
        if at and at.get("total"):
            _a["total"] = int(at["total"])
            _a["hits"] = int(at["hits"])
            _a["accuracy"] = at["accuracy"]
    return acc


def _paper_rule_status_cached():
    """RULE STATUS for the UI tile: forward paper-ledger summary of the frozen LATE_LEADER_30S_V1
    rule + recorder liveness (quote-bridge file age). Cached 30s; crash-safe (None on failure)."""
    now = time.time()
    if now - _PAPER_RULE_CACHE["ts"] < 30.0:
        return _PAPER_RULE_CACHE["val"]
    _PAPER_RULE_CACHE["ts"] = now
    try:
        import database
        data_dir = os.environ.get("BTC_DATA_DIR") or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
        settlement_reconcile = database.reconcile_official_polymarket_settlements(
            os.path.join(data_dir, "pm_export_settlements.parquet"))
        if int(settlement_reconcile.get("rounds") or 0) > 0:
            # Official outcomes supersede the Pyth proxy. Refresh the in-memory
            # accuracy and recent-round views immediately so UI and DuckDB agree.
            for _h in price_to_beat_tracker.horizons:
                price_to_beat_tracker.history[_h].clear()
                price_to_beat_tracker.history[_h].extend(
                    database.fetch_price_to_beat_history(_h, 500))
            price_to_beat_tracker.recent_rounds.clear()
            for _r in database.fetch_price_to_beat_recent(200):
                price_to_beat_tracker.recent_rounds.append(_r)
        s = database.rule_paper_summary("LATE_LEADER_30S_V1")
        qpath = os.path.join(data_dir, "pm_live_quotes.json")
        quote_age = round(now - os.path.getmtime(qpath), 1) if os.path.exists(qpath) else None
        # Boot/code stamp (2026-07-04): compare core source content with the boot hash. Two
        # restart collisions served stale code invisibly; touching a file without changing
        # content no longer creates a false warning.
        _bdir = os.path.dirname(os.path.abspath(__file__))
        _code_mtime = max((os.path.getmtime(os.path.join(_bdir, f))
                           for f in _BACKEND_CODE_FILES
                           if os.path.exists(os.path.join(_bdir, f))), default=0.0)
        _started = float(backend_state.get("startup_start_time") or 0.0)
        _disk_code_hash = _backend_code_hash()
        _hash_stale = _disk_code_hash != BACKEND_BOOT_CODE_HASH
        val = {"summary": s, "quote_bridge_age_s": quote_age,
               "official_settlement_reconcile": settlement_reconcile,
               "backend": {"started_ts": _started, "code_mtime": _code_mtime,
                           "boot_code_hash": BACKEND_BOOT_CODE_HASH,
                           "disk_code_hash": _disk_code_hash,
                           "stale_code": bool(_hash_stale)},
               # pre-declared promotion thresholds (frozen 2026-07-02 -- no re-tuning)
               "targets": {"n": 500, "ev_c": 2.0, "lb_c": 0.0, "pf": 1.2},
               # live SHADOW replications of the three measured-dead strategies (paper-only):
               # running EV shown next to the historical verdict in the dead-strategies panel.
               "shadows": {r: database.rule_paper_summary(r) for r in
                           ("MID_SCALP_LIVE_V1", "TP_OR_SETTLE_LIVE_V1", "STRADDLE_LIVE_V1",
                            "MODEL_FADE_LIVE_V1", "MODEL_STRADDLE_LIVE_V1", "MODEL_RIDE_LIVE_V1",
                            "MODEL_SEQUENTIAL_REVERSAL_V1",
                            "LATE_LEADER_15M_SHADOW_V1", "LATE_LEADER_15S_V1",
                            "LATE_LEADER_60S_V1", "LATE_LEADER_MAKER_V1",
                            "CHEAP_SAFE_EARLY_V1", "SHOCK_SNIPER_LIVE_V1",
                            "MODEL_CROSSFLIP_L1_V1", "MODEL_CROSSFLIP_L2_V1",
                            "CHAMPION_DYNAMIC_PAPER_V1",
                            "PM_CALIBRATED_FAIR_VALUE_FORWARD_BENCHMARK_V1")},
               # live action feed: every shadow/rule entry+exit, newest first (UI table)
               "recent": database.rule_paper_recent(14)}
        _PAPER_RULE_CACHE["val"] = val
    except Exception as e:
        logging.getLogger(__name__).debug(f"paper rule status skipped: {e}")
        _PAPER_RULE_CACHE["val"] = None
    return _PAPER_RULE_CACHE["val"]


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
    _ms_now = int(time.time() * 1000)
    data_state["order_flow_updated_ms"] = _ms_now  # wall-clock: DETECTS disconnects (§5bw)
    # TRADE-specific freshness (2026-06-28): depth alone refreshes order_flow_updated_ms, which left
    # cvd/vpin/large_trade dead-zero through a silent multi-day trade-feed outage. The feature-log +
    # feed-health warning key off THIS, so a trade outage can't hide behind a live depth stream again.
    data_state["last_trade_ms"] = _ms_now
    data_state["feed_timestamps_ms"]["binance_trade"] = _ms_now

    # Fire and forget parquet log
    import database
    # Hand off, do not block the feed. This was a plain blocking call under a
    # "fire and forget" comment; a slow disk or a parquet flush stalled the
    # callback and therefore the feed itself.
    FEED_WRITER.submit(database.log_raw_trade_parquet, trade)


def handle_depth(depth: dict) -> None:
    depth["freshness_ms"] = (
        int(time.time() * 1000) - depth.get("receive_time", 0)
        if depth.get("receive_time")
        else 0
    )
    order_flow.process_depth(depth)
    data_state["order_flow"] = order_flow.get_summary()
    data_state["order_flow"]["freshness_ms"] = depth["freshness_ms"]
    data_state["order_flow_updated_ms"] = int(time.time() * 1000)  # wall-clock: DETECTS disconnects (§5bw)
    data_state["last_depth_ms"] = data_state["order_flow_updated_ms"]
    data_state["feed_timestamps_ms"]["binance_depth"] = data_state["last_depth_ms"]

    # Log orderbook to Parquet on the DEPTH lane, which coalesces per symbol: a newer snapshot
    # supersedes an older unwritten one instead of queueing behind it. Depth arrives far faster
    # than trades, and on one shared queue a depth burst filled the queue and dropped TRADES -
    # the stream whose loss is unrecoverable.
    import database
    FEED_WRITER.submit_depth(
        database.log_depth_parquet, depth, key=depth.get("symbol") or "BTCUSDT"
    )


def handle_kline(kline: dict) -> None:
    if not data_state["klines"]:
        return
    data_state["feed_timestamps_ms"]["binance_kline"] = int(time.time() * 1000)

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
    data_state["feed_timestamps_ms"]["coinbase_ticker"] = int(time.time() * 1000)
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
    data_state["feed_timestamps_ms"]["binance_liquidation"] = int(now)
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
    liq = _safe_dict(data_state.get("derivatives")).get("liquidations")
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
    """Compact 5m/15m/30m decision board: our call + conviction (kronos removed in v6)."""
    by_h = {p.get("horizon"): p for p in (predictions or [])}
    acc = _safe_dict(verification.get("accuracy")) if verification else {}
    board = {}
    for h in (5, 15):
        p = by_h.get(h) or {}
        a = _safe_dict(acc.get(h) or acc.get(str(h)))
        board[h] = {
            "direction": p.get("direction", "NEUTRAL"),
            "modelRawDirection": p.get("modelRawDirection", p.get("rawDirection", p.get("direction", "NEUTRAL"))),
            "rawDirection": p.get("rawDirection", p.get("direction", "NEUTRAL")),
            "preServerDirection": p.get("preServerDirection", p.get("direction", "NEUTRAL")),
            "finalDirection": p.get("finalDirection", p.get("direction", "NEUTRAL")),
            "finalAction": p.get("finalAction", p.get("trade_verdict", "NO_TRADE")),
            "tradeVerdict": p.get("trade_verdict", "NO_TRADE"),
            "noTradeReasons": p.get("no_trade_reasons", []),
            "noTradeReasonText": p.get("no_trade_reason_text", []),
            "signal": p.get("signal", "ABSTAIN"),
            "confidence": p.get("confidence", 0.0),
            "conviction": p.get("conviction", 0.0),
            "convictionGrade": p.get("convictionGrade", "WATCH"),
            "actionable": p.get("actionable", False),
            "modelConfluenceScore": p.get("modelConfluenceScore", 0.0),
            "modelConfluenceDetail": p.get("modelConfluenceDetail", {}),
            "setupQuality": p.get("setupQuality", p.get("confluence", {})),
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


async def train_model(target_model=None, promotion_pipeline: bool = False, incumbent_model=None):
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
    _model_feature_count = getattr(target_model, "model_num_features", NUM_FEATURES)
    _model_pruning = getattr(target_model, "model_feature_pruning", "unknown")
    logger.info(
        "[TRAIN] Building raw %s-feature app matrix; main ensemble will train on %s pruned model features (pruning=%s).",
        NUM_FEATURES,
        _model_feature_count,
        _model_pruning,
    )
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
        "[TRAIN] Feature matrix complete: rows=%s raw_cols=%s model_cols=%s elapsed=%.1fs",
        features.shape[0] if hasattr(features, "shape") else len(features),
        features.shape[1] if hasattr(features, "shape") and len(features.shape) > 1 else "?",
        _model_feature_count,
        time.time() - feature_t0,
    )

    closes = np.array([k["close"] for k in kl_snapshot])
    highs = np.array([k["high"] for k in kl_snapshot])
    lows = np.array([k["low"] for k in kl_snapshot])
    volumes = np.array([k["volume"] for k in kl_snapshot])
    atr_arr = compute_atr(highs, lows, closes)

    # Fit the HMM on the TRAINING PORTION ONLY, then freeze it and filter the whole series.
    #
    # Fitting on the full history leaked the future. The forward filter is causal GIVEN the
    # parameters, but the parameters themselves - means, covariances, state labels and the
    # transition matrix - were estimated using validation and test observations. Future regime
    # distributions therefore helped define the past regime partition, which makes historical
    # validation optimistic. It is unsupervised leakage, weaker than target leakage but real.
    #
    # Fit on train -> freeze -> filter forward through validation and test is the same
    # discipline every other artifact here follows.
    _hmm_split = float(os.getenv("BTC_TRAIN_SPLIT_FRAC", "0.8"))
    _hmm_cut = max(200, int(len(closes) * min(max(_hmm_split, 0.5), 0.98)))
    _hmm_cut = min(_hmm_cut, len(closes))
    try:
        logger.info("[TRAIN] Fitting market regime engine on the training slice "
                    "(%s of %s bars; validation and test are filtered with FROZEN parameters)...",
                    _hmm_cut, len(closes))
        regime_engine.fit_hmm(closes[:_hmm_cut], volumes[:_hmm_cut])
        regime_engine.hmm_fit_rows = _hmm_cut
    except Exception as e:
        logger.warning(f"HMM regime fit skipped: {e}")

    # Train every horizon the model serves (was defaulting to [1,5,10,15],
    # leaving 3m and 7m with no trained model). Pass true highs/lows so the
    # triple-barrier labels use real intrabar extremes.
    seq_t0 = time.time()
    feature_row_count = len(features)
    sequence_features = target_model._select_model_features(features)
    sequence_rows = max(0, feature_row_count - max(target_model.horizons) - LOOKBACK)
    sequence_bytes = sequence_rows * LOOKBACK * sequence_features.shape[1] * 4
    memmap_threshold_mb = max(0, _env_int("BTC_SEQUENCE_MEMMAP_THRESHOLD_MB", 2048))
    sequence_memmap_path = None
    if memmap_threshold_mb and sequence_bytes >= memmap_threshold_mb * 1024 * 1024:
        os.makedirs(HISTORICAL_CACHE_DIR, exist_ok=True)
        for _old_name in os.listdir(HISTORICAL_CACHE_DIR):
            if not (_old_name.startswith("training_sequences_") and _old_name.endswith(".mmap")):
                continue
            _old_path = os.path.join(HISTORICAL_CACHE_DIR, _old_name)
            try:
                if time.time() - os.path.getmtime(_old_path) > 3600:
                    os.remove(_old_path)
            except OSError:
                pass
        sequence_memmap_path = os.path.join(
            HISTORICAL_CACHE_DIR, f"training_sequences_{os.getpid()}.mmap")
    logger.info(
        "[TRAIN] Building lookback sequences for horizons=%s using %s model features (raw=%s, storage=%s, estimated=%.2f GiB)...",
        target_model.horizons,
        sequence_features.shape[1],
        features.shape[1],
        "disk-memmap" if sequence_memmap_path else "RAM",
        sequence_bytes / (1024 ** 3),
    )
    # The learners never consume retired raw columns. Prune before sequence expansion so a
    # 150-day run allocates ~3.6 GB (69 columns), not ~7.0 GB (136 columns), with identical inputs.
    del features
    X, Y, Ymag, Yvalid = build_sequences(
        sequence_features,
        closes,
        lookback=LOOKBACK,
        horizons=target_model.horizons,
        atr_arr=atr_arr,
        highs=highs,
        lows=lows,
        return_magnitude=True,
        memmap_path=sequence_memmap_path,
        return_valid_mask=True,
    )
    for _h, _mask in Yvalid.items():
        _amb = int((~_mask).sum())
        if _amb:
            logger.info(
                "[TRAIN] h=%sm: %s of %s rows are AMBIGUOUS (one bar touched both barriers); "
                "they are excluded from directional training, not relabelled NEUTRAL.",
                _h, _amb, len(_mask))
    del sequence_features
    # P4.3 regime alignment: label every training row with the SAME HMM that routes at
    # serving time, so the regime experts train on the partition they answer for. X rows
    # correspond to build_sequences' loop range(LOOKBACK, feature_row_count-max_h) with decision
    # close index == i, so the label list aligns 1:1 with X. Defensive: any failure → None
    # → train() falls back to the legacy threshold clustering (no crash, no behaviour change).
    regime_labels = None
    try:
        _max_h = max(target_model.horizons)
        _reg_by_close = regime_engine.classify_series(closes, volumes)
        regime_labels = [
            _reg_by_close[i] if i < len(_reg_by_close) else "RANGE"
            for i in range(LOOKBACK, feature_row_count - _max_h)
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
        pipeline_result = {
            "promotion_pipeline": bool(promotion_pipeline),
            "evaluated_model": target_model,
            "gate_report": None,
            "shadow_model": None,
            "smoke_test": None,
        }
        try:
            await loop.run_in_executor(
                None, functools.partial(target_model.train, X, Y, Ymag,
                                        valid_mask=Yvalid,
                                        regime_labels=regime_labels))

            if promotion_pipeline:
                _set_status(
                    "relearn_status", running=True, phase="holdout-gate",
                    message="Scoring untouched 2% holdout against frozen gates...", progress=0.48,
                )
                decision_timestamps = np.asarray(
                    train_ts[LOOKBACK:LOOKBACK + len(X)], dtype=np.int64
                )
                gate_report = await loop.run_in_executor(
                    None,
                    functools.partial(
                        model_promotion.evaluate_candidate,
                        target_model,
                        incumbent_model,
                        X,
                        Y,
                        int(target_model.train_split_idx),
                        decision_timestamps,
                        int(backend_state.get("train_boundary_ts") or 0),
                    ),
                )
                gate_report.update({
                    "historical_days": HISTORICAL_DAYS,
                    "candidate_bundle_id": target_model.model_bundle_id,
                    "candidate_model_dir": target_model.model_dir,
                    "candidate_train_boundary_ts": int(
                        decision_timestamps[max(0, int(target_model.train_split_idx) - 1)]
                    ),
                })
                report_path = os.path.join(
                    MODEL_PROMOTION_REPORT_DIR,
                    f"{target_model.model_bundle_id}.json",
                )
                model_promotion.atomic_json(report_path, gate_report)
                pipeline_result["gate_report"] = gate_report
                pipeline_result["gate_report_path"] = report_path
                logger.info("[PROMOTION] Holdout gate passed=%s report=%s",
                            gate_report.get("passed"), report_path)

                if gate_report.get("passed"):
                    _set_status(
                        "relearn_status", running=True, phase="full-refit",
                        message="Holdout passed. Refitting production challenger on 100% of rows...",
                        progress=0.55,
                    )
                    full_dir = os.path.join(os.path.dirname(target_model.model_dir), "full_refit")
                    full_config = dict(getattr(target_model, "config", {}) or {})
                    full_config["model_bundle_id"] = (
                        f"full{HISTORICAL_DAYS}_{int(time.time())}_{uuid.uuid4().hex[:8]}"
                    )
                    full_model = MultiModelEnsemble(
                        horizons=target_model.horizons,
                        config=full_config,
                        model_dir=full_dir,
                    )
                    full_model.cascade_monitor = cascade_monitor
                    await loop.run_in_executor(
                        None,
                        functools.partial(
                            full_model.train,
                            X,
                            Y,
                            Ymag,
                            valid_mask=Yvalid,
                            regime_labels=regime_labels,
                            full_refit=True,
                            calibration_source=target_model,
                        ),
                    )
                    _set_status(
                        "relearn_status", running=True, phase="staged-smoke-test",
                        message="Loading and smoke-testing the staged 100% bundle...", progress=0.88,
                    )
                    staged = MultiModelEnsemble(
                        horizons=full_model.horizons,
                        config=full_config,
                        model_dir=full_dir,
                    )
                    staged.cascade_monitor = cascade_monitor
                    if not staged.load_models():
                        raise RuntimeError("full-refit staging bundle failed reload validation")
                    smoke = await loop.run_in_executor(
                        None,
                        functools.partial(
                            model_promotion.smoke_test_model,
                            staged,
                            X,
                            staged.horizons,
                            3,
                        ),
                    )
                    shadow_payload = {
                        "status": "shadow",
                        "created_at": time.time(),
                        "historical_days": HISTORICAL_DAYS,
                        "bundle_id": staged.model_bundle_id,
                        "model_dir": full_dir,
                        "gate_report_path": report_path,
                        "smoke_test": smoke,
                        "training_fraction": 1.0,
                        "decision_primary": False,
                    }
                    model_promotion.atomic_json(FULL_REFIT_SHADOW_MANIFEST, shadow_payload)
                    pipeline_result["shadow_model"] = staged
                    pipeline_result["shadow_manifest"] = shadow_payload
                    pipeline_result["smoke_test"] = smoke
        except Exception:
            if isinstance(X, np.memmap):
                try:
                    X.flush()
                    X._mmap.close()
                except Exception:
                    pass
            if sequence_memmap_path:
                try:
                    os.remove(sequence_memmap_path)
                except OSError:
                    pass
            raise
        finally:
            backend_state["is_training"] = False
        backend_state["last_train_time"] = time.time()
        # Record the TRAIN-SPLIT BOUNDARY so the backtest can evaluate strictly
        # held-out candles. train() exposes the exact configured split; sample k's
        # decision candle is kl_snapshot[LOOKBACK + k], so the last in-sample
        # decision candle is LOOKBACK + split_idx - 1. Persisting the exact split
        # prevents post-training validation from overlapping fitted rows. A restart that loads
        # models from disk (no retrain) keeps the same honest boundary.
        try:
            _n_samp = int(X.shape[0])
            _split_idx = int(getattr(target_model, "train_split_idx", 0))
            if not (0 < _split_idx <= _n_samp):
                _split_frac = float(getattr(target_model, "train_split_frac", 0.8))
                _split_idx = int(_n_samp * min(max(_split_frac, 0.5), 0.98))
            _b_idx = min(LOOKBACK + _split_idx - 1, len(kl_snapshot) - 1)
            _b_ts = int(kl_snapshot[_b_idx]["time"])
            if promotion_pipeline:
                model_promotion.atomic_json(
                    os.path.join(target_model.model_dir, "train_boundary.json"),
                    {"train_boundary_ts": _b_ts},
                )
                logger.info("[TRAIN] Candidate holdout boundary recorded at candle ts=%s", _b_ts)
            else:
                backend_state["train_boundary_ts"] = _b_ts
                with open(os.path.join(DATA_DIR, "saved_models", "train_boundary.json"),
                          "w", encoding="utf-8") as _bf:
                    json.dump({"train_boundary_ts": _b_ts}, _bf)
                logger.info("[TRAIN] Out-of-sample boundary recorded at candle ts=%s", _b_ts)
        except Exception as _be:
            logger.warning(f"[TRAIN] Could not record train boundary: {_be}")
        logger.info("[TRAIN] Model training complete in %.1fs", time.time() - train_started)
        # Release training intermediates and remove the long-window disk-backed tensor.
        try:
            if isinstance(X, np.memmap):
                X.flush()
                X._mmap.close()
            del X, Y, Ymag
            import gc
            gc.collect()
            if sequence_memmap_path:
                try:
                    os.remove(sequence_memmap_path)
                except OSError as _rm_error:
                    logger.warning("[TRAIN] Could not remove sequence memmap: %s", _rm_error)
            logger.info("[TRAIN] Training intermediates released (gc.collect, memmap removed).")
        except Exception:
            pass
        return pipeline_result
    else:
        logger.warning("[TRAIN] No sequences built; model training skipped")
        return None


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
    if bool(getattr(model, "full_refit", False)) or float(
        getattr(model, "train_split_frac", 0.0) or 0.0
    ) >= 0.999:
        message = (
            "Historical backtest skipped: the active model was refit on 100% of history. "
            "Use its saved candidate holdout report and live shadow results."
        )
        backend_state["last_backtest"] = {
            "validation_mode": "candidate_holdout_plus_live_shadow",
            "skipped": True,
            "reason": "active_bundle_is_full_refit",
            "model_bundle_id": getattr(model, "model_bundle_id", ""),
            "calibration_provenance": getattr(model, "calibration_provenance", {}),
        }
        backend_state["last_backtest_time"] = time.time()
        _set_status(
            "backtest_status", running=False, phase="skipped-full-refit",
            message=message, progress=1.0, completed_at=time.time(), error=None,
        )
        logger.warning("[BACKTEST] %s", message)
        backtest_task = None
        return
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
    """Train an evaluated candidate; optionally refit 100% into the live shadow lane."""
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
        had_trained_incumbent = bool(getattr(model, "is_trained", False))
        # Every relearn follows the same evaluate -> refit -> shadow contract. Manual,
        # scheduled and auto-learning requests must not bypass holdout gates and replace
        # the incumbent merely because they were not startup-triggered.
        promotion_pipeline = model_promotion.promotion_required(
            FULL_REFIT_AFTER_GATE,
            reason,
        )
        run_id = f"eval{HISTORICAL_DAYS}_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        candidate_dir = (
            os.path.join(MODEL_CHALLENGER_DIR, run_id, "evaluation")
            if promotion_pipeline else None
        )
        candidate_config = dict(getattr(model, "config", {}) or {})
        candidate_config["model_bundle_id"] = run_id
        candidate = MultiModelEnsemble(
            horizons=model.horizons,
            config=candidate_config,
            model_dir=candidate_dir,
        )
        candidate.cascade_monitor = cascade_monitor
        _set_status(
            "relearn_status",
            running=True,
            phase="training",
            message="Training candidate model in background...",
            progress=0.20,
        )
        pipeline_result = await train_model(
            candidate,
            promotion_pipeline=promotion_pipeline,
            incumbent_model=model,
        )
        if not candidate.is_trained:
            raise RuntimeError("Candidate model did not finish training")

        if promotion_pipeline:
            gate_report = (pipeline_result or {}).get("gate_report") or {}
            if not gate_report.get("passed"):
                if had_trained_incumbent:
                    _write_retrain_completion_marker(
                        candidate,
                        deployment_state="gate_rejected",
                        gate_report_path=(pipeline_result or {}).get("gate_report_path"),
                    )
                _set_status(
                    "relearn_status",
                    running=False,
                    phase="gate-rejected",
                    message="98/2 candidate failed promotion gates. Incumbent remains active.",
                    progress=1.0,
                    completed_at=time.time(),
                    error=None,
                )
                logger.warning(
                    "[PROMOTION] Candidate rejected by untouched holdout; no full refit or swap. report=%s",
                    (pipeline_result or {}).get("gate_report_path"),
                )
                return
            shadow_model = (pipeline_result or {}).get("shadow_model")
            if not shadow_model or not shadow_model.is_trained:
                raise RuntimeError("promotion gate passed but full-refit shadow bundle is unavailable")

            if not had_trained_incumbent:
                # First-ever boot has no incumbent to keep serving. The evaluated 98% candidate
                # becomes the temporary primary; the 100% refit remains the silent challenger.
                promotion = model_promotion.promote_model_bundle(
                    candidate.model_dir,
                    MODEL_DIR,
                    os.path.join(DATA_DIR, "model_bundle_backups"),
                )
                logger.info("[PROMOTION] Bootstrap candidate committed with rollback: %s", promotion)
                candidate.model_dir = MODEL_DIR
                model = candidate
                model.cascade_monitor = cascade_monitor
                boundary_path = os.path.join(candidate_dir, "train_boundary.json")
                boundary_ts = None
                try:
                    with open(boundary_path, "r", encoding="utf-8") as handle:
                        boundary_ts = json.load(handle).get("train_boundary_ts")
                except Exception:
                    pass
                _write_active_train_boundary(boundary_ts, full_refit=False,
                                             gate_report_path=(pipeline_result or {}).get("gate_report_path"))

            shadow_label = f"full_refit_shadow_{shadow_model.model_bundle_id}"
            ab_runner.primary = ModelVariant(f"incumbent_{model.model_bundle_id}", model)
            ab_runner.challenger = ModelVariant(
                shadow_label,
                shadow_model,
                started_at=float(((pipeline_result or {}).get("shadow_manifest") or {}).get("created_at") or time.time()),
            )
            ab_runner.enabled = True
            ab_runner.comparison_log.clear()
            _write_retrain_completion_marker(
                shadow_model,
                deployment_state="shadow",
                gate_report_path=(pipeline_result or {}).get("gate_report_path"),
            )
            _set_status(
                "relearn_status",
                running=False,
                phase="shadow-verification",
                message="100% refit passed smoke tests and is now a silent live challenger.",
                progress=1.0,
                completed_at=time.time(),
                error=None,
            )
            logger.info(
                "[PROMOTION] Full-data refit installed in live shadow: %s. Incumbent remains primary.",
                shadow_label,
            )
            return

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
        _write_retrain_completion_marker(model)
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
        if MODEL_BOOT_BACKTEST:
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


def build_threshold_recommendations(replay_block: dict = None, forward_ev_block: dict = None) -> dict:
    """Read-only threshold advice from replay + live paper-EV evidence.

    This deliberately does not mutate model.confidence_threshold. Threshold changes
    should be reviewed/applied explicitly after enough samples exist.
    """
    replay_block = replay_block or backend_state.get("last_historical_replay") or {}
    forward_ev_block = forward_ev_block or backend_state.get("last_forward_ev") or {}
    summary = replay_block.get("summary") or {}
    latest_run_id = next(iter(summary.keys()), None)
    recommendations = []

    if latest_run_id:
        for h_raw, stats in (summary.get(latest_run_id) or {}).items():
            try:
                h = int(h_raw)
            except Exception:
                h = h_raw
            directional_n = int(stats.get("directional_n") or 0)
            acc = stats.get("directional_accuracy")
            price_match = stats.get("price_match_rate")
            avg_err = float(stats.get("avg_move_error_usd") or 0.0)
            action = "collect_more_data"
            severity = "watch"
            reason = f"{h}m replay has only {directional_n} directional calls."
            if directional_n >= 100:
                if acc is not None and acc < 0.50:
                    action = "raise_or_skip"
                    severity = "high"
                    reason = f"{h}m replay is below coin-flip direction accuracy ({acc:.0%}). Keep it mostly AVOID until retrained."
                elif acc is not None and acc < 0.53:
                    action = "raise_threshold"
                    severity = "medium"
                    reason = f"{h}m replay edge is thin ({acc:.0%}). Require higher confidence/agreement before BUY/SELL."
                elif price_match is not None and price_match < 0.30:
                    action = "widen_target_tolerance"
                    severity = "medium"
                    reason = f"{h}m direction is usable, but price targets miss often ({price_match:.0%} close-match). Treat targets as zones."
                else:
                    action = "keep_current_gate"
                    severity = "low"
                    reason = f"{h}m replay is acceptable for monitoring ({acc:.0%} direction, {price_match:.0%} price-match)."
            recommendations.append({
                "source": "historical_replay",
                "horizon": h,
                "action": action,
                "severity": severity,
                "directional_n": directional_n,
                "directional_accuracy": acc,
                "price_match_rate": price_match,
                "avg_move_error_usd": round(avg_err, 2),
                "reason": reason,
            })
    else:
        recommendations.append({
            "source": "historical_replay",
            "horizon": None,
            "action": "run_replay",
            "severity": "watch",
            "reason": "No historical replay run found yet. Run 7-day replay before changing thresholds.",
        })

    ev_totals = (forward_ev_block or {}).get("totals") or {}
    resolved = int(ev_totals.get("resolved") or 0)
    net = float(ev_totals.get("net_pnl_usd") or 0.0)
    avoided = float(ev_totals.get("avoided_loss_usd") or 0.0)
    if resolved >= 20:
        if net < 0:
            recommendations.append({
                "source": "forward_ev",
                "horizon": "all",
                "action": "tighten_trade_gate",
                "severity": "high",
                "reason": f"Forward paper EV is negative (${net:.2f}) after {resolved} resolved events. Fewer TRADE calls until expectancy improves.",
            })
        elif avoided > 0:
            recommendations.append({
                "source": "forward_ev",
                "horizon": "all",
                "action": "keep_avoid_gate",
                "severity": "low",
                "reason": f"Risk gate has avoided about ${avoided:.2f} of paper loss. Keep AVOID strict.",
            })
    else:
        recommendations.append({
            "source": "forward_ev",
            "horizon": "all",
            "action": "collect_more_live_outcomes",
            "severity": "watch",
            "reason": f"Only {resolved} forward-EV events resolved. Wait for 20+ before trusting live paper-PnL.",
        })

    high = sum(1 for r in recommendations if r.get("severity") == "high")
    medium = sum(1 for r in recommendations if r.get("severity") == "medium")
    if high:
        label = "Tighten gates before trusting more BUY/SELL calls."
    elif medium:
        label = "Signals are usable only with stricter confidence/target caution."
    else:
        label = "Current gates can stay, but keep collecting live evidence."
    return {
        "latest_replay_run_id": latest_run_id,
        "recommendations": recommendations,
        "summary": label,
        "high_count": high,
        "medium_count": medium,
    }


async def run_historical_replay_background(days: int = 7, horizons: list[int] = None,
                                           max_samples: int = 1000, step: int = 1,
                                           stateful: bool = False):
    """Run saved-model replay inside the backend process to avoid DuckDB writer conflicts."""
    global replay_task
    horizons = horizons or [5, 15]
    started = time.time()

    def _progress(evt: dict):
        _set_status(
            "replay_status",
            running=True,
            phase=evt.get("phase", "replay"),
            message=evt.get("message", "Running historical replay..."),
            progress=float(evt.get("progress", 0.0) or 0.0),
            processed=evt.get("processed"),
            windows=evt.get("windows"),
        )

    _set_status(
        "replay_status",
        running=True,
        phase="queued",
        message=f"Starting {days}d replay for {horizons}...",
        progress=0.01,
        started_at=started,
        completed_at=None,
        error=None,
    )
    try:
        valid_horizons = []
        for h in horizons:
            try:
                hi = int(h)
                if hi in model.horizons:
                    valid_horizons.append(hi)
            except Exception:
                pass
        args = SimpleNamespace(
            days=max(1, min(int(days), 30)),
            start_ms=None,
            end_ms=None,
            horizons=sorted(set(valid_horizons)) or [5, 15],
            max_samples=max(1, min(int(max_samples), 5000)),
            step=max(1, int(step)),
            offset=LOOKBACK,
            run_id="",
            stateful=bool(stateful),
            log_every=250,
        )
        logger.info("[REPLAY] Scheduled in-process historical replay: days=%s horizons=%s max_samples=%s",
                    args.days, args.horizons, args.max_samples)
        result = await run_historical_replay(args, progress_cb=_progress)
        replay_block = database.fetch_historical_replay_summary(75)
        forward_ev = backend_state.get("last_forward_ev") or database.fetch_forward_ev_summary(30)
        recs = build_threshold_recommendations(replay_block, forward_ev)
        backend_state["last_historical_replay"] = replay_block
        backend_state["last_historical_replay_time"] = time.time()
        backend_state["last_threshold_recommendations"] = recs
        _set_status(
            "replay_status",
            running=False,
            phase="complete",
            message=f"Replay complete: {result.get('windows', 0)} windows.",
            progress=1.0,
            completed_at=time.time(),
            result=result,
            error=None,
        )
        logger.info("[REPLAY] Complete in %.1fs: %s", time.time() - started, result)
    except Exception as e:
        _set_status(
            "replay_status",
            running=False,
            phase="error",
            message="Historical replay failed.",
            completed_at=time.time(),
            error=str(e),
        )
        logger.error("[REPLAY] Failed: %s", e, exc_info=True)
    finally:
        replay_task = None


def schedule_historical_replay(days: int = 7, horizons: list[int] = None,
                               max_samples: int = 1000, step: int = 1,
                               stateful: bool = False) -> bool:
    global replay_task
    if replay_task and not replay_task.done():
        logger.info("[REPLAY] Already running; skip schedule request")
        return False
    replay_task = asyncio.create_task(
        run_historical_replay_background(days, horizons, max_samples, step, stateful)
    )
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
_recent_conf = {hh: _deque(maxlen=400) for hh in [5, 15]}   # pruned 2026-06-21

# Stage 1+2 precision engine: isotonic calibration (auto-activates at >=150 resolved
# leans/horizon) + shrunk empirical precision bins. Fitted off the event loop in the
# maintenance section; applied per-prediction below.
from calibration import PrecisionEngine
precision_engine = PrecisionEngine()


def _confluence(p: dict, of: dict) -> dict:
    """Stage 3 setup-quality LABEL (logged/displayed only — NOT a live gate; no bet/abstain/champion
    decision reads this). Regime is the primary discriminator per live evidence
    (DUCKDB_METRICS_ANALYSIS_2026-06-21 §F): RANGE/LOW_VOLATILITY are the only regimes above coin-flip
    (RANGE 59%, Wilson-LB 52%); TRENDING_UP/HIGH_VOLATILITY are the weakest (~46%). Order-flow agreement
    (CVD, large-trade flow, book imbalance) is a weak secondary confirmation. Grade A ≈ favorable regime
    with majority flow confirmation; C ≈ adverse/unconfirmed.

    NOTE: the regime edge is NOT yet confirmed forward (shadow recent-window LB < 50% — see
    regime_gate_shadow.py), so this remains a measurement label that lets the setup_fingerprint recorder
    validate it going forward. Promoting regime to a real decision gate needs the shadow LB to hold > 50%
    AND explicit sign-off."""
    lean = p.get("rawDirection")
    if lean not in ("UP", "DOWN"):
        return {"score": 0, "grade": "C", "checks": {"model_lean": False}}
    sgn = 1.0 if lean == "UP" else -1.0
    regime = p.get("regime") or "UNKNOWN"
    flow = {
        "cvd_agrees": sgn * float(of.get("cvd_1m", 0.0) or 0.0) > 0,
        "large_trades_agree": sgn * float(of.get("large_trade_delta", 0.0) or 0.0) > 0,
        "book_agrees": sgn * float(of.get("obi_5", 0.0) or 0.0) > 0,
    }
    flow_score = int(sum(flow.values()))                      # 0..3
    regime_tier = (2 if regime in ("RANGE", "LOW_VOLATILITY")  # favorable
                   else 0 if regime in ("TRENDING_UP", "HIGH_VOLATILITY")  # adverse
                   else 1)                                    # neutral: TRENDING_DOWN / UNKNOWN
    score = regime_tier + flow_score                          # 0..5
    grade = "A" if (regime_tier == 2 and flow_score >= 2) else ("B" if score >= 3 else "C")
    # `regime_favorable` / `flow_agree` are the keys the scoreboard chips read (main.js); emit them so
    # the UI reflects the new regime tier instead of always showing ✗.
    checks = {"model_lean": True, "regime": regime, "regime_tier": regime_tier,
              "regime_favorable": regime_tier == 2, "flow_agree": flow_score >= 2, **flow}
    return {"score": score, "grade": grade, "checks": checks}


def _conf_percentile(h: int, q: float):
    vals = sorted(c for c in _recent_conf.get(h, []) if c > 0.1)
    if len(vals) < 20:
        return None
    idx = int(q / 100.0 * (len(vals) - 1))
    return vals[idx]


def _neutralize_prediction(prediction: dict, code: str, message: str, status: str = "blocked") -> dict:
    """Mark a raw directional lean as final WAIT/NEUTRAL with an auditable reason."""
    prediction.setdefault("preNeutralDirection", prediction.get("direction", "NEUTRAL"))
    prediction.setdefault("preNeutralSignal", prediction.get("signal", "NEUTRAL"))
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

    # 1. Freshness Blocker — DISCONNECT-AWARE (§5bw, 2026-06-14). The old check used
    # `freshness_ms` = per-trade LATENCY, which is only set WHEN a trade arrives, so it FREEZES
    # at ~5ms when the WS drops (no trades) — it silently missed the overnight outage that left
    # the model predicting on a DEAD/zero order-flow feed for hours. Gate on WALL-CLOCK time since
    # the last order_flow update, which actually grows during a disconnect (never-updated = dead).
    order_flow_state = _safe_dict(state.get("order_flow"))
    _upd = state.get("order_flow_updated_ms", 0)
    stale_ms = (int(time.time() * 1000) - _upd) if _upd else 10_000_000
    if stale_ms > 5000:
        if prediction.get("direction") != "NEUTRAL":
            _neutralize_prediction(
                prediction,
                "stale_feed",
                f"Order-flow feed stale/disconnected ({stale_ms}ms since last update). Safety block.",
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
    policy = _safe_dict(_safe_dict(signal_policy.get("by_regime")).get(h))
    if not policy.get("ready"):
        policy = _safe_dict(_safe_dict(signal_policy.get("by_horizon")).get(h))
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
    elif h in [10, 15, 30]:
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
    global model
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
    if FORCE_MAIN_RETRAIN:
        logger.warning(
            "[BOOT] BTC_FORCE_MAIN_RETRAIN=1: a fresh candidate will train in the background; "
            "a compatible saved ensemble remains active until the candidate swaps in."
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
    # A forced long-window retrain should not blank the dashboard for hours or days. Load any
    # compatible incumbent first, then train a separate candidate through the same atomic-swap
    # path as a manual relearn. If no compatible incumbent exists, fall back to fresh startup
    # training as before.
    loaded = model.load_models()
    if loaded:
        # Restore the saved model's out-of-sample boundary so backtests stay honest
        # across restarts (models loaded from disk, no retrain → boundary from json).
        try:
            with open(os.path.join(DATA_DIR, "saved_models", "train_boundary.json"),
                      "r", encoding="utf-8") as _bf:
                boundary_payload = json.load(_bf)
                if boundary_payload.get("full_refit"):
                    backend_state["train_boundary_ts"] = None
                    logger.info(
                        "[BOOT] Active bundle is a 100%% refit; historical backtest is disabled."
                    )
                else:
                    raw_boundary = boundary_payload.get("train_boundary_ts")
                    backend_state["train_boundary_ts"] = (
                        int(raw_boundary) if raw_boundary else None
                    )
                    logger.info("[BOOT] Restored out-of-sample boundary ts=%s",
                                backend_state["train_boundary_ts"])
        except Exception:
            pass  # legacy bundle without a boundary file — backtest falls back to old behavior
        restore_full_refit_shadow()
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
                # No compatible incumbent is a promotion event even when start.bat
                # found an older completion marker and therefore cleared FORCE_MAIN_RETRAIN.
                # Never install a freshly trained architecture without the holdout gate.
                if model_promotion.promotion_required(
                    FULL_REFIT_AFTER_GATE,
                    "forced-startup",
                ):
                    await relearn_models_background("forced-startup")
                    if not getattr(model, "is_trained", False):
                        raise RuntimeError(
                            "evaluated startup candidate did not pass gates; no active model available"
                        )
                else:
                    await train_model()
                    _write_retrain_completion_marker(model, deployment_state="active")
                await broadcast({"type": "status", "step": "step-model", "msg": "Model trained"})
                logger.info("[BOOT] Background startup training complete.")
                if not FULL_REFIT_AFTER_GATE:
                    _set_status(
                        "relearn_status",
                        running=False,
                        phase="complete",
                        message="Startup training complete.",
                        progress=1.0,
                        completed_at=time.time(),
                        error=None,
                    )
                if MODEL_BOOT_BACKTEST:
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
        if FORCE_MAIN_RETRAIN:
            if schedule_relearn("forced-startup"):
                startup_train_bg = relearn_task
                logger.info(
                    "[BOOT] Forced candidate retrain scheduled; incumbent model remains active."
                )
            else:
                logger.warning("[BOOT] Forced candidate retrain could not be scheduled.")

    # 4. Backtest is intentionally backgrounded. The app should become usable as
    # soon as models are available; validation keeps running with visible progress.
    if not FORCE_MAIN_RETRAIN and _load_backtest_cache():
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
            recent_klines = copy.deepcopy(data_state["klines"][-1500:])
            # P0-2 CLOSED-BAR INFERENCE. handle_kline() overwrites klines[-1] on every tick,
            # so the last row is the FORMING candle: partial high, low, volume, trade count and
            # every indicator derived from them. Training is built from completed REST candles,
            # so serving on a partial bar feeds the model a row that cannot occur in its
            # training distribution - a half-finished minute reads as an unusually quiet one.
            # The HMM path already excluded it; the ensemble sequence did not.
            # The live price is kept separately below as the DECISION price; only the model's
            # feature window is trimmed.
            # A SEPARATE list, deliberately. `recent_klines` keeps the forming bar because the
            # HMM block below already does its own `recent_klines[:-1]` on the assumption that
            # the last row is unfinished - trimming in place would make that drop the newest
            # CLOSED bar instead, breaking a path this fix was not meant to touch.
            if recent_klines and recent_klines[-1].get("is_closed") is False:
                model_klines = recent_klines[:-1]
            else:
                model_klines = recent_klines
            if len(model_klines) < LOOKBACK:
                continue
            order_flow_snapshot = copy.deepcopy(data_state["order_flow"])
            derivatives_snapshot = copy.deepcopy(data_state["derivatives"])
            sentiment_snapshot = copy.deepcopy(data_state["sentiment"])

            # Current Features (per-bar signal history keeps the sequence window
            # consistent with how the model was trained).
            live_sig_hist = signal_buffer.get_aligned_series(
                [k["time"] for k in model_klines]
            )
            # LIVE PARITY FIX (2026-06-28): get_aligned_series fills every UNCOVERED candle with a 0.0
            # default, and build_features_from_klines.series() then uses that all-zero array INSTEAD of the
            # live order_flow snapshot -> cvd_1m/cvd_5m/cvd_change/large_trade_* went DEAD-ZERO live (proven
            # by [feat-diag]: of.cvd_1m=-1.63 but sighist[-1]=0 -> feature=0). Training masks this with the
            # backfill overlay; live serving has none. Drop the keys the live order_flow already provides so
            # series() broadcasts the LIVE value (the live equivalent of the train-time overlay). VPIN is
            # included too (2026-06-28): features.py:1146 now passes of.get("vpin"), so popping it lets the
            # live analyzer vpin land. NOTE vpin is a slow warmup -- 50 buckets x 15 BTC = 750 BTC of spot
            # volume (~1h continuous) and the deque resets on restart -- so it reads 0 until warm (cold-start,
            # not a bug).
            # P0-3 CURRENT-ROW OVERLAY, NOT HISTORY BROADCAST.
            # Popping the key made features.series() fall back to np.full(n, snapshot) - the
            # CURRENT value painted across every historical row. The model was trained on a
            # time series and served a constant. That is not "the live equivalent of the
            # train-time overlay": the backfill overlay fills each historical candle with ITS
            # OWN historical value; this filled all of them with now.
            # Correct behaviour: keep the history, replace only the last row.
            _overlay_src = {
                "cvd_change": order_flow_snapshot.get("cvd_change"),
                "cvd_1m": order_flow_snapshot.get("cvd_1m"),
                "cvd_5m": order_flow_snapshot.get("cvd_5m"),
                "large_trade_delta": order_flow_snapshot.get("large_trade_delta"),
                "large_trade_imbalance": order_flow_snapshot.get("large_trade_imbalance"),
                "vpin": order_flow_snapshot.get("vpin"),
            }
            _overlay_n = len(model_klines)
            _overlaid, _broadcast = [], []
            for _key, _live_val in _overlay_src.items():
                if _live_val is None:
                    continue
                _arr = live_sig_hist.get(_key)
                if _arr is not None and len(_arr) == _overlay_n:
                    _arr = np.asarray(_arr, dtype=np.float64).copy()
                    _arr[-1] = float(_live_val)      # only the CURRENT bar is live
                    live_sig_hist[_key] = _arr
                    _overlaid.append(_key)
                else:
                    # No usable history for this key. Falling back to a broadcast would invent
                    # a past, so the key is dropped and the degradation is COUNTED and
                    # surfaced rather than silently papered over.
                    live_sig_hist.pop(_key, None)
                    _broadcast.append(_key)
            data_state.setdefault("feature_parity", {})
            data_state["feature_parity"] = {
                "overlaid_current_row": _overlaid,
                "no_history_fell_back": _broadcast,
                "degraded": bool(_broadcast),
                "ts_ms": int(time.time() * 1000),
            }
            # Build features off the event loop. This is a heavy synchronous numpy job
            # (~0.3s/tick on the live window) and running it inline stalls WebSocket
            # pings — the stale-feed/ping-timeout disconnects seen in the UI. (#6)
            live_features = await asyncio.get_event_loop().run_in_executor(
                None,
                functools.partial(
                    build_features_from_klines,
                    model_klines,
                    order_flow_snapshot,
                    derivatives_snapshot,
                    sentiment_snapshot,
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
                # The HMM advances ONCE PER CLOSED BAR, never once per main-loop tick. The
                # transition matrix counts one transition per one-minute kline, while this loop
                # runs every BTC_MAIN_LOOP_SEC (default 2.0s) - so an unguarded call applied
                # ~30 transitions per minute and re-filtered the still-forming candle each time.
                # recent_klines[-1] is that unfinished bar, so the id and the observation both
                # come from [-2], the newest CLOSED bar.
                _closed = recent_klines[:-1] if len(recent_klines) > 1 else recent_klines
                _obs_id = _closed[-1].get("time") if _closed else None
                regime = regime_engine.detect_regime(
                    closes[:len(_closed)], adx_arr, atr_arr, volumes[:len(_closed)],
                    observation_id=_obs_id,
                )
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
                    p["modelRawDirection"] = p.get(
                        "modelRawDirection",
                        p.get("rawDirection", p.get("direction", "NEUTRAL")),
                    )
                    p["rawDirection"] = p.get(
                        "rawDirection",
                        p.get("modelRawDirection", p.get("direction", "NEUTRAL")),
                    )
                    p["preServerDirection"] = p.get("direction", "NEUTRAL")
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
                        if "modelConfluenceScore" not in p and isinstance(p.get("confluence"), (int, float)):
                            p["modelConfluenceScore"] = p.get("confluence")
                        if "modelConfluenceDetail" not in p:
                            p["modelConfluenceDetail"] = p.get("confluenceDetail", {})
                        p["setupQuality"] = _confluence(p, _of_now)
                        p["confluence"] = p["setupQuality"]  # legacy UI/DB field
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

                    # Do-not-trade reason engine — runs LAST, after EVERY filter (incl. the
                    # expectancy neutralizer above) so `no_trade_reasons` + `trade_verdict` reflect
                    # the FINAL state, not a pre-filter snapshot (external-review fix 2026-06-14).
                    # Pure + crash-safe (decision_gate never raises).
                    try:
                        p = compute_no_trade_reasons(p)
                    except Exception as _dge:
                        logger.debug(f"decision_gate skipped: {_dge}")
                    p["finalDirection"] = p.get("direction", "NEUTRAL")
                    p["finalSignal"] = p.get("signal", "NEUTRAL")
                    p["finalAction"] = p.get("trade_verdict", "NO_TRADE")

                    predictions.append(p)
                    cascade_data[h] = p

                # Unlike the cadence-based prediction tables below, this records EVERY completed
                # ensemble revision. Exact model input is compressed once for the cycle; horizon
                # rows reference it and link to their preceding revision. DuckDB work runs off the
                # event loop so evidence persistence cannot freeze WebSocket heartbeats.
                if predictions:
                    try:
                        revision_ts = int(time.time() * 1000)
                        revision_ids, _revision_outcomes = await asyncio.get_event_loop().run_in_executor(
                            None,
                            functools.partial(
                                _write_revision_cycle,
                                predictions=predictions,
                                feature_values=np.asarray(seq, dtype=np.float32).copy(),
                                # `seq` is the exact full feature array passed to predict(); the
                                # ensemble applies the current model-contract mask internally.
                                feature_names=list(FEATURE_NAMES),
                                snapshot_ts=now_ms_pred,
                                current_price=float(data_state["klines"][-1]["close"]),
                                order_flow_state=copy.deepcopy(
                                    _safe_dict(data_state.get("order_flow"))
                                ),
                                prediction_ts=revision_ts,
                            ),
                        )
                        for prediction, revision_id in zip(predictions, revision_ids):
                            prediction["revisionId"] = revision_id
                    except Exception as revision_error:
                        # Research evidence must fail visibly but cannot take down live serving.
                        logger.error("[REVISION LEDGER] cycle rejected: %s", revision_error)

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
                            _feature_logged = True   # once-per-cycle gate (B1 + GEX below)
                            # §5bw: only log when the order-flow feed is ALIVE. A disconnect makes
                            # the microstructure half of the vector dead-zero; logging those rows
                            # POISONS the retrain set (the overnight run logged 881 all-zero rows).
                            # TRADE freshness, not just "order-flow updated": depth alone refreshes
                            # order_flow_updated_ms, so cvd/vpin/large_trade can be dead-zero while the
                            # §5bw guard stays green — a silent multi-day outage hid exactly this
                            # (2026-06-28). Log ONLY on fresh trades; warn LOUDLY (rate-limited) otherwise.
                            _tr_upd = data_state.get("last_trade_ms", 0)
                            if _tr_upd and (now_ms - _tr_upd) <= 5000:
                                try:
                                    database.log_feature_vector(
                                        now_ms,
                                        __import__("features").get_feature_schema()["schema_hash"],
                                        regime.get("regime", "UNKNOWN"),
                                        [float(x) for x in seq[-1]],
                                    )
                                except Exception as _fe:
                                    logger.debug(f"B1 feature-vector log skipped: {_fe}")
                            else:
                                _lw = data_state.get("_trade_stale_warn_ms", 0)
                                if now_ms - _lw > 60000:
                                    data_state["_trade_stale_warn_ms"] = now_ms
                                    _age = (now_ms - _tr_upd) / 1000.0 if _tr_upd else -1.0
                                    logger.warning("[feed] TRADE stream stale (%.0fs) — cvd/vpin/large_trade "
                                                   "dead-zero; predictions degraded + feature-log paused. "
                                                   "Check the spot aggTrade feed.", _age)
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
                        model_verifier.record(
                            p.get("modelDirs", {}), h, current_price, now_ms,
                            prediction_id=pred_id,
                        )

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
                        setup_quality = p.get("setupQuality") if isinstance(p.get("setupQuality"), dict) else {}
                        decision_state = {
                            "modelRawDirection": p.get("modelRawDirection", p.get("rawDirection", "")),
                            "rawDirection": p.get("rawDirection", ""),
                            "lockedDirection": p.get("lockedDirection", ""),
                            "modelFilteredDirection": p.get("modelFilteredDirection", ""),
                            "preServerDirection": p.get("preServerDirection", ""),
                            "preNeutralDirection": p.get("preNeutralDirection", ""),
                            "finalDirection": p.get("finalDirection", p.get("direction", "")),
                            "finalSignal": p.get("finalSignal", p.get("signal", "")),
                            "finalAction": p.get("finalAction", p.get("trade_verdict", "")),
                            "tradeVerdict": p.get("trade_verdict", ""),
                            "neutralReasonCode": p.get("neutralReasonCode", ""),
                            "neutralReason": p.get("neutralReason", ""),
                            "noTradeReasons": p.get("no_trade_reasons", []),
                            "noTradeReasonText": p.get("no_trade_reason_text", []),
                            "modelConfluenceScore": p.get("modelConfluenceScore", 0.0),
                            "modelConfluenceDetail": p.get("modelConfluenceDetail", {}),
                            "setupQuality": setup_quality,
                        }
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
                            model_raw_direction=p.get("modelRawDirection", p.get("rawDirection", "")),
                            pre_server_direction=p.get("preServerDirection", ""),
                            final_direction=p.get("finalDirection", p.get("direction", "")),
                            trade_verdict=p.get("trade_verdict", ""),
                            no_trade_reasons=p.get("no_trade_reasons", []),
                            decision_state=decision_state,
                            model_confluence=float(p.get("modelConfluenceScore", 0.0) or 0.0),
                            setup_score=float(setup_quality.get("score", 0.0) or 0.0),
                            setup_quality=setup_quality,
                            neutral_band=float(p.get("neutralBand", 0.0008) or 0.0008),
                        )
                        try:
                            database.log_forward_ev_event({
                                "id": f"ev_{pred_id}",
                                "prediction_id": pred_id,
                                "source": "ensemble",
                                "timestamp": now_ms,
                                "horizon": h,
                                "entry_price": current_price,
                                "target_price": p.get("targetPrice", current_price),
                                "expected_move": expected_move,
                                "confidence": p.get("confidence", 0.0),
                                "raw_direction": p.get("modelRawDirection", p.get("rawDirection", "")),
                                "final_direction": p.get("finalDirection", p.get("direction", "")),
                                "trade_verdict": p.get("trade_verdict", ""),
                                "action": p.get("trade_verdict", "NO_TRADE"),
                                "notional_usd": float(os.environ.get("BTC_PAPER_NOTIONAL_USD", "1000")),
                                "fee_bps": float(os.environ.get("BTC_TAKER_FEE_BPS", "4.0")),
                                "slippage_bps": float(os.environ.get("BTC_PAPER_SLIPPAGE_BPS", "2.0")),
                                "no_trade_reasons": p.get("no_trade_reasons", []),
                                "setup_quality": setup_quality,
                            })
                        except Exception as _ev:
                            logger.debug(f"Forward-EV ledger log skipped: {_ev}")
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
            # The 1m bars are the intrabar path the FIRST_TOUCH contract is graded on.
            # Without them every row would return GRADE_UNAVAILABLE and verification
            # would silently stop, so this argument is load-bearing, not optional.
            newly_verified = verifier.check_and_verify(
                current_price, now_ms, klines=data_state["klines"][-2000:])
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
            data_state["_model_context_updated_ms"] = int(now_ms if predictions else 0)
            _paper_prediction = preds_by_h.get(5)
            _paper_fields = (
                "horizon", "direction", "finalDirection", "trade_verdict", "finalAction",
                "actionable", "no_trade_reasons", "calibratedConfidence", "agreement",
                "metaTrust", "expectedMove", "expectedMoveRange", "stopLoss",
                "model_bundle_id", "regime",
            )
            data_state["_binance_paper_context"] = {
                "updated_at_ms": int(now_ms if _paper_prediction else 0),
                "model_trained": bool(model.is_trained),
                "model_arch_version": MODEL_ARCH_VERSION,
                "predictions": {
                    5: {key: copy.deepcopy(_paper_prediction.get(key)) for key in _paper_fields}
                } if _paper_prediction else {},
                "regime_info": copy.deepcopy(data_state.get("regime_info") or {}),
            }

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
                        promoted_variant = ab_runner.challenger
                        if (promoted_variant
                                and promoted_variant.label.startswith("full_refit_shadow_")):
                            try:
                                promotion = model_promotion.promote_model_bundle(
                                    promoted_variant.model.model_dir,
                                    MODEL_DIR,
                                    os.path.join(DATA_DIR, "model_bundle_backups"),
                                )
                                logger.info(
                                    "[PROMOTION] Full-refit bundle committed with rollback: %s",
                                    promotion,
                                )
                            except Exception as promotion_exc:
                                logger.error(
                                    "[PROMOTION] Live gates passed but transactional active-bundle "
                                    "commit failed; keeping incumbent primary: %s",
                                    promotion_exc,
                                )
                                continue
                            promoted_variant.model.model_dir = MODEL_DIR
                            model = promoted_variant.model
                            model.cascade_monitor = cascade_monitor
                            previous_manifest = {}
                            try:
                                with open(FULL_REFIT_SHADOW_MANIFEST, "r", encoding="utf-8") as handle:
                                    previous_manifest = json.load(handle)
                            except Exception:
                                pass
                            gate_report_path = previous_manifest.get("gate_report_path")
                            _write_active_train_boundary(
                                None, full_refit=True, gate_report_path=gate_report_path
                            )
                            model_promotion.atomic_json(
                                FULL_REFIT_SHADOW_MANIFEST,
                                {
                                    "status": "promoted",
                                    "promoted_at": time.time(),
                                    "bundle_id": model.model_bundle_id,
                                    "model_dir": MODEL_DIR,
                                    "decision_primary": True,
                                    "gate_report_path": gate_report_path,
                                    "live_comparison": comp,
                                },
                            )
                        ab_runner.primary = promoted_variant
                        ab_runner.challenger = None
                        ab_runner.enabled = False
                        ab_runner.comparison_log.clear()

                # Feed the CascadeMonitor — grade the RAW lean vs the realized sign,
                # NOT the `hit` column: on gated rows (the majority) hit=avoid_success,
                # TRUE when the lean was WRONG, which would invert the cascade-on vs
                # cascade-off accuracy comparison that auto-enables/disables the cascade.
                if h == 15:   # cascade TARGET (15m<-5m, cascade_map={15:5}); pruned 2026-06-21
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
                try:
                    database.resolve_forward_ev_event(
                        pred_id,
                        v.get("actual_price", current_price),
                        v.get("actual_move_usd", 0.0),
                        v.get("actual_direction", "NEUTRAL"),
                        v.get("hit", False),
                        int(v.get("verified_at") or now_ms),
                    )
                except Exception as _evr:
                    logger.debug(f"Forward-EV ledger resolve skipped: {_evr}")

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
            if time.time() - backend_state.get("last_forward_ev_time", 0.0) >= 15:
                try:
                    backend_state["last_forward_ev"] = database.fetch_forward_ev_summary(30)
                except Exception:
                    backend_state["last_forward_ev"] = {
                        "summary": {},
                        "recent": [],
                        "error": "temporarily_unavailable",
                    }
                backend_state["last_forward_ev_time"] = time.time()
            forward_ev = backend_state.get("last_forward_ev") or {"summary": {}, "recent": []}
            if time.time() - backend_state.get("last_historical_replay_time", 0.0) >= 30:
                try:
                    replay_block = database.fetch_historical_replay_summary(75)
                    backend_state["last_historical_replay"] = replay_block
                    backend_state["last_threshold_recommendations"] = build_threshold_recommendations(
                        replay_block, forward_ev
                    )
                except Exception:
                    replay_block = backend_state.get("last_historical_replay") or {
                        "summary": {},
                        "recent": [],
                        "error": "temporarily_unavailable",
                    }
                backend_state["last_historical_replay_time"] = time.time()
            historical_replay = backend_state.get("last_historical_replay") or {"summary": {}, "recent": []}
            threshold_recommendations = backend_state.get("last_threshold_recommendations") or {
                "recommendations": [],
                "summary": "",
            }
            
            # NOTE: The Polymarket "Value Engine" was removed — Polymarket only lists
            # long-dated BTC markets (e.g. "$150k by Dec 31"), which the fair-value model
            # could not price, producing misleading ~99% edges. The self-contained
            # PriceToBeatTracker (5m/15m) replaces it for the BTC up/down use case.

            # Log every model's live per-horizon output to the SEPARATE metrics DuckDB
            # (crash-safe; never touches the live DB). Read offline for model metrics.
            try:
                model_metrics_logger.log_direction(predictions, regime=regime)
            except Exception:
                pass

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
                "replay_status": _safe_public_status(
                    backend_state.get("replay_status")
                ),
                "health": model.compute_health_score(
                    backend_state["last_backtest"], ws_client.running, model.is_trained
                ),
                "backtest": backend_state["last_backtest"],
                "indicators": indicators,
                "indicator_series": indicator_series,
                "support_resistance": support_resistance,
                "execution_simulator": simulator.get_metrics(),
                "forward_ev": forward_ev,
                "historical_replay": historical_replay,
                "threshold_recommendations": threshold_recommendations,
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
                    "accuracy": _accuracy_alltime(price_to_beat_tracker),
                    "p_hold_status": persistence_model_status(),
                    "round_state_status": round_state_panel.status(),
                    "complete_trade_status": complete_trade_forecaster.status(),
                    # RULE STATUS tile: forward paper ledger of the frozen LATE_LEADER_30S_V1 rule
                    # + recorder liveness (quote-bridge age). Cheap DB aggregate, cached 30s.
                    "paper_rule_status": _paper_rule_status_cached(),
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


def _source_age_status(
    timestamp_ms: int | float | None, stale_after_ms: float
) -> dict:
    if not timestamp_ms:
        return {"status": "MISSING", "age_ms": None}
    age_ms = max(0.0, time.time() * 1000.0 - float(timestamp_ms))
    return {
        "status": "HEALTHY" if age_ms <= stale_after_ms else "STALE",
        "age_ms": round(age_ms, 1),
    }


def _recorder_file_status(path: str, stale_after_s: float = 30.0) -> dict:
    candidates = [path, f"{path}.wal"]
    mtimes = [
        os.path.getmtime(candidate)
        for candidate in candidates
        if os.path.exists(candidate)
    ]
    if not mtimes:
        return {"status": "MISSING", "age_s": None, "path": path}
    age_s = max(0.0, time.time() - max(mtimes))
    return {
        "status": "HEALTHY" if age_s <= stale_after_s else "STALE",
        "age_s": round(age_s, 1),
        "path": path,
    }


def _forward_readiness_snapshot(max_age_s: float = 15.0) -> dict:
    now = time.time()
    cached = _FORWARD_READINESS_CACHE.get("payload")
    if cached is not None and now - float(_FORWARD_READINESS_CACHE["generated_at_s"]) <= max_age_s:
        return copy.deepcopy(cached)
    try:
        payload = build_forward_readiness_report()
        payload["available"] = True
        payload["error"] = None
    except ForwardReadinessUnavailable as exc:
        payload = {"available": False, "error": str(exc), "B": None, "C": None}
    _FORWARD_READINESS_CACHE["generated_at_s"] = now
    _FORWARD_READINESS_CACHE["payload"] = payload
    return copy.deepcopy(payload)


def _evidence_health_snapshot(max_age_s: float = 60.0) -> dict:
    """Performance-blind revision/opportunity recorder health from the writer process."""
    now = time.time()
    cached = _EVIDENCE_HEALTH_CACHE.get("payload")
    if cached is not None and now - float(_EVIDENCE_HEALTH_CACHE["generated_at_s"]) <= max_age_s:
        return copy.deepcopy(cached)
    try:
        payload = build_evidence_health_report(
            os.path.join(DATA_DIR, "model_revision_ledger.duckdb"),
            os.path.join(DATA_DIR, "opportunity_ledger.duckdb"),
            expect_live=True,
            state_scan_limit=25,
        )
        payload["available"] = True
        payload["error"] = None
    except Exception as exc:
        payload = {"available": False, "status": "FAIL", "error": str(exc)}
    _EVIDENCE_HEALTH_CACHE["generated_at_s"] = now
    _EVIDENCE_HEALTH_CACHE["payload"] = payload
    return copy.deepcopy(payload)


def _system_health_snapshot() -> dict:
    timestamps = _safe_dict(data_state.get("feed_timestamps_ms"))
    pyth_ts = data_state.get("pyth_price_ts")
    feeds = {
        "binance_trade": _source_age_status(
            timestamps.get("binance_trade"), 5_000.0
        ),
        "binance_depth": _source_age_status(
            timestamps.get("binance_depth"), 5_000.0
        ),
        "binance_kline": _source_age_status(
            timestamps.get("binance_kline"), 10_000.0
        ),
        "coinbase_ticker": _source_age_status(
            timestamps.get("coinbase_ticker"), 15_000.0
        ),
        "pyth_price": _source_age_status(
            float(pyth_ts) * 1000.0 if pyth_ts else None, 10_000.0
        ),
    }
    recorders = {
        "polymarket_quotes_settlements": _recorder_file_status(
            os.path.join(DATA_DIR, "execution_layer.duckdb"), 120.0
        ),
        "polymarket_l2": _recorder_file_status(
            os.path.join(DATA_DIR, "polymarket_l2.duckdb"), 120.0
        ),
        "microstructure": _recorder_file_status(
            os.path.join(DATA_DIR, "microstructure.duckdb"), 120.0
        ),
        "multi_venue": _recorder_file_status(
            os.path.join(DATA_DIR, "multi_venue.duckdb"), 120.0
        ),
        "binance_l2": _recorder_file_status(
            os.path.join(DATA_DIR, "binance_l2.duckdb"), 120.0
        ),
        "deribit_options_optional": _recorder_file_status(
            os.path.join(DATA_DIR, "deribit_options.duckdb"), 180.0
        ),
        "model_revisions": _recorder_file_status(
            MODEL_REVISION_DB, 120.0
        ),
        "opportunity_ledger": _recorder_file_status(
            os.path.join(DATA_DIR, "opportunity_ledger.duckdb"), 120.0
        ),
    }
    for recorder_name, recorder_status in recorders.items():
        recorder_status["required"] = recorder_name != "deribit_options_optional"
    forward_readiness = _forward_readiness_snapshot()
    evidence_health = _evidence_health_snapshot()
    action_recorder = forward_readiness.get("recorder") or {}
    heartbeat_ms = int(action_recorder.get("last_heartbeat_ms") or 0)
    recorders["open_position_actions"] = _source_age_status(
        heartbeat_ms if heartbeat_ms > 0 else None,
        120_000.0,
    )
    recorders["open_position_actions"]["required"] = True
    recorders["model_metrics"] = model_metrics_logger.status(stale_after_s=120.0)
    recorders["model_metrics"]["required"] = True
    polymarket_feed = polymarket_client.status()
    feed_protocols = {
        "binance_spot": ws_client.health_snapshot(),
        "binance_futures": futures_ws_client.health_snapshot(),
        "coinbase": coinbase_client.health_snapshot(),
    }
    disk_hash = _backend_code_hash()
    required_feed_names = ("binance_trade", "binance_depth", "pyth_price")
    blockers = [
        f"feed:{name}:{feeds[name]['status'].lower()}"
        for name in required_feed_names
        if feeds[name]["status"] != "HEALTHY"
    ]
    if disk_hash != BACKEND_BOOT_CODE_HASH:
        blockers.append("backend_code_changed_after_boot")
    if os.getenv("BTC_EVIDENCE_MODE", "0") == "1":
        blockers.extend(
            f"recorder:{name}:{item['status'].lower()}"
            for name, item in recorders.items()
            if item.get("required") and item.get("status") != "HEALTHY"
        )
        if not forward_readiness.get("available"):
            blockers.append("forward_readiness_unavailable")
        if evidence_health.get("status") in ("FAIL", "DEGRADED"):
            blockers.append(f"evidence_health:{str(evidence_health.get('status')).lower()}")
    p_hold_status = persistence_model_status()
    round_state_status = round_state_panel.status()
    if not getattr(model, "is_trained", False):
        blockers.append("model:main_ensemble_unavailable")
    if not p_hold_status.get("loaded"):
        blockers.append("model:p_hold_unavailable")
    if os.getenv(
        "BTC_REQUIRE_POLYMARKET_FEED",
        "1" if DEPLOYMENT_ENV == "production" else "0",
    ) == "1":
        blockers.extend(
            f"polymarket:{reason}"
            for reason in polymarket_feed.get("blockers", ())
        )
    if os.getenv(
        "BTC_REQUIRE_PROTOCOL_HEALTH",
        "1" if DEPLOYMENT_ENV == "production" else "0",
    ) == "1":
        for source in ("binance_spot", "binance_futures"):
            blockers.extend(
                f"protocol:{source}:{reason}"
                for reason in feed_protocols[source].get("blockers", ())
            )
    # A dead or dropping feed writer means the parquet archive has gaps. That must appear in
    # trust state, not only in a stats dict nothing reads: an archive with silent holes is worse
    # than a missing one, because it still looks like complete evidence.
    supervisor_status = SUPERVISOR.status()
    blockers.extend(supervisor_status["blockers"])
    feed_writer_stats = FEED_WRITER.stats()
    if not feed_writer_stats["worker_alive"]:
        blockers.append("feed_writer_not_running")
    if feed_writer_stats["dropped"]:
        blockers.append(f"feed_writer_dropped:{feed_writer_stats['dropped']}")
    if feed_writer_stats["failed"]:
        blockers.append(f"feed_writer_failed:{feed_writer_stats['failed']}")
    complete_trade = complete_trade_forecaster.status()
    if os.getenv(
        "BTC_REQUIRE_COMPLETE_TRADE",
        "1" if DEPLOYMENT_ENV == "production" else "0",
    ) == "1":
        for name in ("share_model", "btc_model", "execution_model"):
            status = complete_trade.get(name) or {}
            if not status.get("loaded") or status.get("bundle_verified") is not True:
                blockers.append(f"complete_trade_{name}_unavailable")
    return {
        "trust_state": "DATA_OK" if not blockers else "DO_NOT_TRUST",
        "blockers": blockers,
        "feed_writer": feed_writer_stats,
        "tasks": supervisor_status,
        "feeds": feeds,
        "feed_protocols": feed_protocols,
        "polymarket_feed": polymarket_feed,
        "recorders": recorders,
        "forward_protocols": forward_readiness,
        "evidence_collection": evidence_health,
        "model_readiness": {
            "main_ensemble": "READY" if getattr(model, "is_trained", False) else "UNAVAILABLE",
            "p_hold": p_hold_status,
            "round_state": round_state_status,
        },
        "database_writer": {
            "status": "HEALTHY" if os.access(DATA_DIR, os.W_OK) else "BLOCKED",
            "data_directory": os.path.relpath(DATA_DIR, PROJECT_ROOT),
            "analytics_db": os.path.relpath(database.DB_PATH, PROJECT_ROOT),
        },
        "backend": {
            "started_at_s": backend_state.get("startup_start_time"),
            "boot_code_hash": BACKEND_BOOT_CODE_HASH,
            "disk_code_hash": disk_hash,
            "code_current": disk_hash == BACKEND_BOOT_CODE_HASH,
            "websocket_clients": len(clients),
        },
        "complete_trade": complete_trade,
        "live_execution": {
            "available": False,
            "reason": "real-order adapters are not implemented or loaded",
        },
        "paper_engines": {
            "polymarket": {"status": "ENABLED", "paper_only": True},
            "binance": {
                "status": binance_paper_service.status().get("runtime_state", "UNKNOWN"),
                "enabled": bool(binance_paper_service.config.hard_enabled),
                "paper_only": True,
            },
        },
        "control_plane": {
            "browser_admin_enabled": DEPLOYMENT_ENV != "production",
            "admin_header": "X-Admin-Token",
            "paper_header": "X-Control-Token",
        },
        "generated_at_s": time.time(),
    }


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
        "replay_status": _safe_public_status(backend_state.get("replay_status")),
        "model_inventory": model.get_model_inventory(),
        "system_health": _system_health_snapshot(),
        "binance_paper": binance_paper_service.status(),
    }


@app.get("/api/system-health")
async def api_system_health():
    return _system_health_snapshot()


@app.get("/api/evidence-readiness")
async def api_evidence_readiness():
    """Performance-blind Protocol B/C counts from the live writer process."""
    payload = _forward_readiness_snapshot(max_age_s=0.0)
    if not payload.get("available"):
        return JSONResponse(status_code=503, content=payload)
    return payload


@app.get("/api/evidence-health")
async def api_evidence_health():
    """Performance-blind model-revision and opportunity recorder diagnostics."""
    payload = _evidence_health_snapshot(max_age_s=0.0)
    return JSONResponse(
        status_code=200 if payload.get("available") else 503,
        content=payload,
    )


@app.get("/healthz", include_in_schema=False)
async def healthz():
    """Process liveness only. Readiness is deliberately stricter."""
    return {
        "status": "alive",
        "started_at_s": backend_state.get("startup_start_time"),
        "code_hash": BACKEND_BOOT_CODE_HASH,
    }


@app.get("/readyz", include_in_schema=False)
async def readyz():
    """Fail-closed readiness for a service manager or reverse proxy."""
    snapshot = _system_health_snapshot()
    blockers = list(snapshot.get("blockers") or [])
    if backend_state.get("ready_time", 0) <= 0:
        blockers.append("startup_not_ready")
    if not getattr(model, "is_trained", False):
        blockers.append("main_model_not_trained")
    blockers = sorted(set(blockers))
    ready = not blockers and snapshot.get("trust_state") == "DATA_OK"
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "not_ready",
            "blockers": blockers,
            "trust_state": snapshot.get("trust_state"),
            "model_trained": bool(getattr(model, "is_trained", False)),
        },
    )


@app.get("/api/paper-ledger")
async def api_paper_ledger():
    """FULL paper-trade blotter for the Trades tab: every rule/shadow entry with buy price, net
    exit, per-trade P/L, plus per-rule + overall win rates and totals. Read-only aggregate."""
    import database as _db
    return _db.rule_paper_ledger(300)


@app.get("/api/round-state")
async def api_round_state():
    """Current information-only 5m/15m state; never an order endpoint."""
    latest = price_to_beat_tracker.latest()
    return {
        "mode": "SHADOW_INFO_ONLY",
        "model_status": round_state_panel.status(),
        "latest": {
            str(horizon): (latest.get(horizon) or {}).get("round_state")
            for horizon in price_to_beat_tracker.horizons
        },
    }


@app.post("/api/relearn")
async def api_relearn(x_admin_token: str | None = Header(default=None)):
    _require_admin(x_admin_token)
    scheduled = schedule_relearn("manual-ui")
    return {
        "scheduled": scheduled,
        "status": _safe_public_status(backend_state.get("relearn_status")),
    }


@app.post("/api/backtest")
async def api_backtest(x_admin_token: str | None = Header(default=None)):
    _require_admin(x_admin_token)
    scheduled = schedule_backtest("manual-ui")
    return {
        "scheduled": scheduled,
        "status": _safe_public_status(backend_state.get("backtest_status")),
    }


@app.post("/api/historical-replay/run")
async def api_historical_replay_run(
                                    days: int = Query(default=7, ge=1, le=30),
                                    max_samples: int = Query(default=1000, ge=1, le=10_000),
                                    step: int = Query(default=1, ge=1, le=60),
                                    stateful: bool = False,
                                    horizons: str = "5,15",
                                    x_admin_token: str | None = Header(default=None)):
    _require_admin(x_admin_token)
    try:
        parsed_horizons = [
            int(x.strip()) for x in str(horizons).replace(";", ",").split(",")
            if x.strip()
        ]
    except Exception:
        parsed_horizons = [5, 15]
    scheduled = schedule_historical_replay(days, parsed_horizons, max_samples, step, stateful)
    return {
        "scheduled": scheduled,
        "status": _safe_public_status(backend_state.get("replay_status")),
    }


@app.get("/api/historical-replay/status")
async def api_historical_replay_status(limit: int = 75):
    try:
        replay_block = database.fetch_historical_replay_summary(limit)
        forward_ev = backend_state.get("last_forward_ev") or database.fetch_forward_ev_summary(30)
        recs = build_threshold_recommendations(replay_block, forward_ev)
        backend_state["last_historical_replay"] = replay_block
        backend_state["last_threshold_recommendations"] = recs
        backend_state["last_historical_replay_time"] = time.time()
        return {
            "status": _safe_public_status(backend_state.get("replay_status")),
            "historical_replay": replay_block,
            "threshold_recommendations": recs,
        }
    except Exception as e:
        logger.warning("historical replay status failed: %s", e)
        return {
            "status": _safe_public_status(backend_state.get("replay_status")),
            "historical_replay": {"summary": {}, "recent": [], "error": "temporarily_unavailable"},
            "threshold_recommendations": {"recommendations": [], "summary": "Replay status temporarily unavailable."},
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


@app.get("/api/forward-ev")
async def api_forward_ev(limit: int = 50):
    try:
        return database.fetch_forward_ev_summary(limit)
    except Exception as e:
        logger.warning("forward-ev fetch failed: %s", e)
        return {"summary": {}, "recent": [], "error": "temporarily_unavailable"}


@app.get("/api/historical-replay")
async def api_historical_replay(limit: int = 50):
    try:
        replay_block = database.fetch_historical_replay_summary(limit)
        forward_ev = backend_state.get("last_forward_ev") or database.fetch_forward_ev_summary(30)
        return {
            **replay_block,
            "status": _safe_public_status(backend_state.get("replay_status")),
            "threshold_recommendations": build_threshold_recommendations(replay_block, forward_ev),
        }
    except Exception as e:
        logger.warning("historical replay fetch failed: %s", e)
        return {"summary": {}, "recent": [], "error": "temporarily_unavailable"}


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
            for h in [5, 15]:   # pruned 2026-06-21: dropped 3/7/10/30
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
            for h in (5, 15):   # pruned 2026-06-21: dropped 3/7/10/30
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
    origin = websocket.headers.get("origin")
    if not _origin_is_allowed(origin):
        logger.warning("Rejected WebSocket origin: %s", origin)
        await websocket.close(code=1008, reason="origin not allowed")
        return
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


if os.getenv("BTC_SERVE_FRONTEND", "0") == "1":
    frontend_dist = os.path.join(PROJECT_ROOT, "dist")
    frontend_index = os.path.join(frontend_dist, "index.html")
    if not os.path.isfile(frontend_index):
        raise RuntimeError(
            f"BTC_SERVE_FRONTEND=1 but the production frontend is absent at "
            f"{frontend_index}. Run `npm run build` first."
        )
    # Registered last so /api/*, /healthz, /readyz and /ws retain authority.
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "server:app",
        host="127.0.0.1",
        port=8000,
        reload=os.getenv("BTC_DEV_RELOAD", "0") == "1",
    )
