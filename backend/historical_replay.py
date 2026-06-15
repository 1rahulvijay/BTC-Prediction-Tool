"""
Offline historical replay for saved BTC models.

This is intentionally NOT part of app startup. It replays historical candles through
the saved model bundle and stores rows in `historical_replay_predictions`, separate
from live accuracy tables.

Rules:
- only uses candles available up to the prediction timestamp
- resolves after the row is created
- does not apply live-only order-flow/feed gates unless those snapshots exist
- does not contaminate `predictions_*m`
"""

import argparse
import asyncio
import time
import uuid

DEFAULT_LOOKBACK = 60


def _emit_progress(progress_cb, **payload):
    if not progress_cb:
        return
    try:
        progress_cb(payload)
    except Exception:
        pass


def _actual_direction(entry_price: float, exit_price: float, neutral_band: float) -> str:
    if entry_price <= 0:
        return "NEUTRAL"
    ret = (exit_price - entry_price) / entry_price
    if ret > neutral_band:
        return "UP"
    if ret < -neutral_band:
        return "DOWN"
    return "NEUTRAL"


def _hit(final_direction: str, actual_direction: str, raw_direction: str) -> bool:
    if final_direction == actual_direction:
        return True
    if final_direction == "NEUTRAL":
        return actual_direction == "NEUTRAL" or (
            raw_direction in ("UP", "DOWN") and raw_direction != actual_direction
        )
    return False


def _empty_state(klines_slice: list[dict]) -> dict:
    return {
        "klines": klines_slice,
        "order_flow": {},
        "derivatives": {},
        "sentiment": {},
        "regime_info": {"regime": "RANGE", "confidence": 0.5},
        "kronos_accuracy": {},
        "confidence_calibrators": {},
        "regime_calibration": {},
        "regime_model_weights": {},
    }


def _finalize_replay_prediction(pred: dict) -> dict:
    """Apply the lightweight final-decision layer used by the live UI.

    Historical replay intentionally does not import server.py because that would
    construct live clients/background state. This keeps replay fast and isolated
    while still persisting the same final-action vocabulary the dashboard uses.
    """
    pred["modelRawDirection"] = pred.get(
        "modelRawDirection",
        pred.get("rawDirection", pred.get("direction", "NEUTRAL")),
    )
    pred["rawDirection"] = pred.get("rawDirection", pred["modelRawDirection"])
    pred["preServerDirection"] = pred.get("preServerDirection", pred.get("direction", "NEUTRAL"))
    pred.setdefault("finalDirection", pred.get("direction", "NEUTRAL"))
    pred.setdefault("finalSignal", pred.get("signal", "NEUTRAL"))
    try:
        from decision_gate import compute_no_trade_reasons
        pred = compute_no_trade_reasons(pred)
    except Exception:
        pred.setdefault("trade_verdict", "NO_TRADE")
        pred.setdefault("no_trade_reasons", ["replay_gate_error"])
        pred.setdefault("no_trade_reason_text", ["replay decision gate failed"])
    pred["finalDirection"] = pred.get("direction", "NEUTRAL")
    pred["finalSignal"] = pred.get("signal", "NEUTRAL")
    pred["finalAction"] = pred.get("trade_verdict", "NO_TRADE")
    return pred


async def run_replay(args, progress_cb=None) -> dict:
    import numpy as np
    import database
    from data_ingestion import BinanceRESTClient
    from features import LOOKBACK, build_features_from_klines, calculate_schema_hash, FEATURE_NAMES
    from model import MultiModelEnsemble

    database.init_db()
    _emit_progress(progress_cb, phase="load_models", message="Loading saved model bundle...", progress=0.03)
    model = MultiModelEnsemble(horizons=args.horizons)
    if not model.load_models():
        raise RuntimeError("No compatible saved model bundle found. Train/load models before replay.")

    _emit_progress(progress_cb, phase="fetch", message=f"Fetching {args.days} days of 1m candles...", progress=0.08)
    rest = BinanceRESTClient()
    try:
        klines = await rest.fetch_historical_klines(
            interval="1m",
            days=args.days,
            start_time_ms=args.start_ms,
            end_time_ms=args.end_ms,
        )
    finally:
        await rest.close()

    if len(klines) < LOOKBACK + max(args.horizons) + 5:
        raise RuntimeError(f"Not enough candles for replay: {len(klines)}")

    _emit_progress(progress_cb, phase="features", message=f"Building features from {len(klines)} candles...", progress=0.18)
    features = build_features_from_klines(klines)
    closes = np.array([k["close"] for k in klines], dtype=np.float64)
    schema_hash = calculate_schema_hash(FEATURE_NAMES)
    run_id = args.run_id or f"replay_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    max_h = max(args.horizons)
    start_i = max(LOOKBACK, args.offset)
    end_i = min(len(features) - max_h - 1, len(closes) - max_h - 1)
    indices = list(range(start_i, end_i, max(1, args.step)))
    if args.max_samples > 0:
        indices = indices[-args.max_samples:]

    _emit_progress(
        progress_cb,
        phase="replay",
        message=f"Replaying {len(indices)} windows for horizons {args.horizons}...",
        progress=0.25,
        windows=len(indices),
    )
    counts = {
        h: {
            "rows": 0,
            "final_quality_hits": 0,
            "directional": 0,
            "directional_hits": 0,
            "price_matches": 0,
        }
        for h in args.horizons
    }

    for n, i in enumerate(indices, start=1):
        seq = features[i - LOOKBACK:i]
        if seq.shape[0] != LOOKBACK:
            continue
        state = _empty_state(klines[: i + 1])
        ts_ms = int(klines[i].get("time", 0)) * 1000
        entry_price = float(closes[i])

        for h in args.horizons:
            exit_idx = i + h
            if exit_idx >= len(closes):
                continue
            if not args.stateful:
                # Avoid wall-clock hysteresis contaminating a fast replay loop.
                model.locked_direction.pop(h, None)
                model.direction_lock_time.pop(h, None)
                model.opposing_ticks.pop(h, None)
                model.smoothed_probs.pop(h, None)

            pred = _finalize_replay_prediction(model.generate_ensemble_prediction(h, seq, state, {}, {}))
            raw_dir = pred.get("modelRawDirection") or pred.get("rawDirection") or pred.get("direction", "NEUTRAL")
            final_dir = pred.get("finalDirection") or pred.get("direction", "NEUTRAL")
            exit_price = float(closes[exit_idx])
            actual_move = exit_price - entry_price
            neutral_band = float(pred.get("neutralBand", 0.0008) or 0.0008)
            actual_dir = _actual_direction(entry_price, exit_price, neutral_band)
            hit = _hit(final_dir, actual_dir, raw_dir)
            expected_signed = float(pred.get("targetPrice", entry_price)) - entry_price
            move_error = abs(actual_move - expected_signed)
            direction_hit = None
            if raw_dir in ("UP", "DOWN") and actual_move != 0:
                direction_hit = bool((raw_dir == "UP") == (actual_move > 0))
            price_match = (
                final_dir in ("UP", "DOWN")
                and final_dir == actual_dir
                and move_error <= max(10.0, abs(expected_signed) * 0.2)
            )
            setup_quality = pred.get("setupQuality")
            if not isinstance(setup_quality, dict):
                setup_quality = {
                    "model_confluence": pred.get("modelConfluenceScore", pred.get("confluence", 0.0)),
                    "model_detail": pred.get("modelConfluenceDetail", pred.get("confluenceDetail", {})),
                }

            row_id = f"{run_id}_{h}m_{ts_ms}"
            database.log_historical_replay_prediction({
                "id": row_id,
                "replay_run_id": run_id,
                "timestamp": ts_ms,
                "horizon": h,
                "model_bundle_id": pred.get("model_bundle_id", model.model_bundle_id),
                "feature_schema_hash": schema_hash,
                "model_raw_direction": raw_dir,
                "final_direction": final_dir,
                "trade_verdict": pred.get("trade_verdict", "NO_TRADE"),
                "confidence": pred.get("confidence", 0.0),
                "expected_move": pred.get("expectedMove", 0.0),
                "target_price": pred.get("targetPrice", entry_price),
                "actual_price": exit_price,
                "actual_move": actual_move,
                "direction_hit": direction_hit,
                "price_match": price_match,
                "move_error": move_error,
                "no_trade_reasons": pred.get("no_trade_reasons", []),
                "setup_quality": setup_quality,
                "created_at": int(time.time() * 1000),
            })
            counts[h]["rows"] += 1
            counts[h]["final_quality_hits"] += int(bool(hit))
            counts[h]["directional"] += int(raw_dir in ("UP", "DOWN"))
            counts[h]["directional_hits"] += int(bool(direction_hit))
            counts[h]["price_matches"] += int(bool(price_match))

        if args.log_every and n % args.log_every == 0:
            print(f"[REPLAY] {n}/{len(indices)} windows processed")
            _emit_progress(
                progress_cb,
                phase="replay",
                message=f"Replay processed {n}/{len(indices)} windows...",
                progress=0.25 + 0.70 * (n / max(1, len(indices))),
                processed=n,
                windows=len(indices),
            )
        if n % 25 == 0:
            await asyncio.sleep(0)

    _emit_progress(progress_cb, phase="store", message="Replay complete. Summarizing results...", progress=0.98)
    return {"run_id": run_id, "windows": len(indices), "counts": counts}


def parse_args():
    p = argparse.ArgumentParser(description="Replay saved BTC model on historical unseen candles.")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--start-ms", type=int, default=None)
    p.add_argument("--end-ms", type=int, default=None)
    p.add_argument("--horizons", type=int, nargs="+", default=[5, 15])
    p.add_argument("--max-samples", type=int, default=1000)
    p.add_argument("--step", type=int, default=1)
    p.add_argument("--offset", type=int, default=DEFAULT_LOOKBACK)
    p.add_argument("--run-id", default="")
    p.add_argument("--stateful", action="store_true", help="Keep model smoothing/locks across replay rows.")
    p.add_argument("--log-every", type=int, default=250)
    return p.parse_args()


if __name__ == "__main__":
    result = asyncio.run(run_replay(parse_args()))
    print(result)
