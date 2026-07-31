"""
dead_feature_classifier.py -- classify every one of the 136 features by DATA SOURCE and ACTION.
================================================================================================
Turns "81 features are dead in backfill" into a concrete per-feature plan. Each feature is mapped to
its data source (the curated map below, derived from features.py's own section comments) and an action:

  KEEP        -- kline- or aggTrade-derived; computable in backfill AND live with parity. (live-active)
  PARITY-FIX  -- aggTrade-derived; rides the backfill+live path but appears dead in any KLINES-ONLY
                build -> verify the training matrix actually fills it (no schema change, just wiring).
  RETIRE      -- needs an external feed NOT in the backfill (L2 depth / derivatives / options /
                liquidations / cross-asset / macro / sentiment). Proven no direction edge (depth 0.53,
                cross-venue 0.52) -> drop from the training schema (the neutral hygiene trim).
  RECORD-LIVE -- Polymarket features: can't be backfilled, ARE the real edge -> record live (the frontier).

The curated source map is authoritative (deterministic). `--empirical` cross-checks it against a
klines-only build (which non-kline feature is actually constant), to validate the mapping.

Usage:  python backend/dead_feature_classifier.py            # the per-feature action plan
        python backend/dead_feature_classifier.py --empirical  # + validate vs a klines-only build
        python backend/dead_feature_classifier.py --selftest
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

# source -> action
ACTION = {
    "klines": "KEEP", "aggtrades": "PARITY-FIX", "orderbook": "RETIRE", "derivatives": "RETIRE",
    "options": "RETIRE", "liquidations": "RETIRE", "crossasset": "RETIRE", "external": "RETIRE",
    "regime_live": "RETIRE", "duplicate": "RETIRE", "polymarket": "RECORD-LIVE",
}
NOTE = {
    "klines": "kline-derived; perfect train/serve parity",
    "aggtrades": "trade-derived; backfillable (backfill_trade_features) + live recorder -- verify it's filled",
    "orderbook": "L2 depth; NO historical book -> proven no direction edge (depth probe 0.53)",
    "derivatives": "funding/OI; geo-blocked live + not in spot backfill (basis: keep for the side-rule only)",
    "options": "options chain; no historical source",
    "liquidations": "forceOrder; geo-blocked + not archived",
    "crossasset": "eth/sol/dxy/us10y; external, low 5m prior",
    "external": "coinbase premium / fear-greed / oracle / netflow; external feed, geo/low-info",
    "regime_live": "post-fit live regime snapshot; no causal historical backfill for the direction model",
    "duplicate": "exact duplicate of an existing selected feature; adds cost without information",
    "polymarket": "Polymarket book/odds; CANNOT backfill -- the real edge -> record live",
}

SOURCE = {
    "klines": [
        "price_return", "volume_norm", "rsi", "macd_hist", "bb_position", "atr_norm", "vwap_deviation",
        "stoch_rsi", "adx_norm", "obv_change", "williams_r_norm", "cci_norm", "mfi_norm",
        "price_vs_ema9", "price_vs_ema21", "price_vs_sma50", "volume_ma_ratio", "roc_5", "roc_10",
        "heikin_ashi_trend", "rsi_x_adx", "vol_x_trend", "rv_1m", "rv_5m", "rv_15m", "vol_acceleration",
        "ewma_vol", "dist_to_resistance", "dist_to_support", "sr_compression", "mtf_trend_alignment",
        "mtf_volatility_ratio", "volume_profile_poc_distance",
        "volume_profile_lvn_distance", "twap_deviation", "exhaustion", "volume_profile_value_area_pos",
        "orb_position", "orb_breakout", "rv_upside", "rv_downside", "trend_efficiency", "signed_streak",
        "momentum_fast_slow", "return_acceleration", "variance_ratio", "rv_term_structure",
        "session_asia", "session_eu", "session_us", "is_weekend",
    ],
    "aggtrades": [
        "cvd_change", "cvd_1m", "cvd_5m", "trade_intensity", "vpin", "cvd_delta_divergence",
        "delta_ratio", "delta_acceleration", "flow_efficiency", "cvd_slope_divergence",
        "large_trade_delta", "large_trade_imbalance",
    ],
    "orderbook": [
        "book_imbalance", "obi_5", "obi_10", "obi_20", "spread_norm", "obi_x_atr", "wall_imbalance",
        "distance_to_bid_wall_norm", "distance_to_ask_wall_norm", "spread_expansion_ratio",
        "vacuum_detected", "bid_wall_persistence", "ask_wall_persistence", "bid_wall_growth",
        "ask_wall_growth", "queue_depletion_rate", "liquidity_sweep_bullish", "liquidity_sweep_bearish",
        "spoof_score", "absorption_ratio", "bid_consume_rate", "ask_consume_rate", "queue_pressure",
        "order_add_cancel_imbalance", "absorption_persistence", "book_replenishment_rate",
    ],
    "derivatives": [
        "funding_rate", "funding_velocity", "oi_change", "long_short_ratio", "funding_x_oi",
        "global_oi_change", "oi_divergence_norm", "basis_spread", "basis_velocity",
        "funding_oi_interaction", "time_to_funding", "oi_momentum", "price_oi_interaction",
    ],
    "options": ["put_call_ratio", "options_skew_25d", "max_pain_distance", "atm_iv_norm"],
    "liquidations": ["long_liq_volume", "short_liq_volume", "liq_imbalance", "liq_acceleration"],
    "crossasset": [
        "eth_btc_price_ratio", "sol_btc_price_ratio", "eth_volume_norm", "sol_volume_norm",
        "eth_imbalance", "sol_imbalance", "macro_dxy_norm", "macro_us10y_norm",
    ],
    "external": [
        "coinbase_premium_norm", "coinbase_premium_velocity_norm", "fear_greed_norm",
        "chainlink_price_norm", "fv_deviation", "stablecoin_flow", "exchange_netflow",
        "cross_exchange_lead_lag",
    ],
    "regime_live": [
        "regime_transition_prob", "regime_entropy", "vol_forecast_1m",
        "vol_forecast_5m", "vol_forecast_15m",
    ],
    "duplicate": ["mtf_support_distance"],
    "polymarket": [
        "polymarket_relevant_event", "polymarket_probability_change", "polymarket_liquidity",
        "polymarket_event_shock",
    ],
}


def classify():
    """Return {feature: (source, action, note)} for every mapped feature."""
    out = {}
    for src, feats in SOURCE.items():
        for f in feats:
            out[f] = (src, ACTION[src], NOTE[src])
    return out


def _report(empirical=False):
    from features import FEATURE_NAMES
    cls = classify()
    mapped, unmapped = set(cls), [f for f in FEATURE_NAMES if f not in cls]
    extra = [f for f in mapped if f not in FEATURE_NAMES]
    print(f"FEATURE_NAMES: {len(FEATURE_NAMES)} | mapped: {len(mapped)} | "
          f"unmapped: {len(unmapped)} | stale-in-map: {len(extra)}")
    if unmapped:
        print("  ⚠️ UNMAPPED (fix the map):", unmapped)
    if extra:
        print("  ⚠️ in map but not in FEATURE_NAMES:", extra)

    by_action = {}
    for f in FEATURE_NAMES:
        src, act, _ = cls.get(f, ("?", "UNMAPPED", ""))
        by_action.setdefault(act, []).append((f, src))
    print("\n=== ACTION SUMMARY ===")
    for act in ("KEEP", "PARITY-FIX", "RETIRE", "RECORD-LIVE", "UNMAPPED"):
        rows = by_action.get(act, [])
        if rows:
            print(f"  {act:<12} {len(rows):>3}")
    print("\n=== BY SOURCE ===")
    for src in SOURCE:
        n = sum(1 for f in FEATURE_NAMES if cls.get(f, ("",))[0] == src)
        print(f"  {src:<13} {n:>3}  [{ACTION[src]}]  -- {NOTE[src]}")

    print("\n=== THE PLAN ===")
    keep = by_action.get("KEEP", [])
    pfix = by_action.get("PARITY-FIX", [])
    ret = by_action.get("RETIRE", [])
    rec = by_action.get("RECORD-LIVE", [])
    print(f"  KEEP ({len(keep)}): kline/derived live-active -- no action.")
    print(f"  PARITY-FIX ({len(pfix)}): verify the TRAINING matrix fills these from aggTrades "
          f"(they're real signal, must not be zero-in-train): {', '.join(f for f, _ in pfix)}")
    print(f"  RETIRE ({len(ret)}): drop from the training schema (neutral hygiene; proven no edge). "
          f"NOTE: keep basis_spread/basis_velocity for the SIDE rule even if dropped from direction.")
    print(f"  RECORD-LIVE ({len(rec)}): the frontier -- record live, can't backfill: "
          f"{', '.join(f for f, _ in rec)}")

    if empirical:
        try:
            from features import build_features_from_klines
            import database
            kl = database.get_klines(1500) if hasattr(database, "get_klines") else None
            if not kl:
                print("\n[empirical] no klines available (app DB needed) -- skipped")
                return
            X = build_features_from_klines(kl)
            var = X.var(axis=0)
            dead = var < 1e-10
            print(f"\n=== EMPIRICAL (klines-only build): {int(dead.sum())}/{len(FEATURE_NAMES)} constant ===")
            # validate: every constant feature should be non-klines (klines features must vary)
            wrong = [FEATURE_NAMES[i] for i in range(len(FEATURE_NAMES))
                     if dead[i] and cls.get(FEATURE_NAMES[i], ("",))[0] == "klines"]
            if wrong:
                print("  ⚠️ klines-mapped but constant (warmup or mis-map):", wrong[:10])
            else:
                print("  ✓ all constant features are non-kline sources (map consistent)")
        except Exception as e:
            print(f"\n[empirical] skipped: {str(e)[:80]}")


def selftest():
    from features import FEATURE_NAMES, NUM_FEATURES
    cls = classify()
    unmapped = [f for f in FEATURE_NAMES if f not in cls]
    extra = [f for f in cls if f not in FEATURE_NAMES]
    assert not unmapped, f"UNMAPPED features (extend SOURCE): {unmapped}"
    assert not extra, f"map has features not in FEATURE_NAMES: {extra}"
    # no feature in two categories
    seen = {}
    for src, feats in SOURCE.items():
        for f in feats:
            assert f not in seen, f"{f} in both {seen[f]} and {src}"
            seen[f] = src
    assert len(cls) == NUM_FEATURES == len(FEATURE_NAMES) == 136, f"count mismatch: {len(cls)}"
    keep = sum(1 for f in FEATURE_NAMES if ACTION[cls[f][0]] == "KEEP")
    retire = sum(1 for f in FEATURE_NAMES if ACTION[cls[f][0]] == "RETIRE")
    pfix = sum(1 for f in FEATURE_NAMES if ACTION[cls[f][0]] == "PARITY-FIX")
    rec = sum(1 for f in FEATURE_NAMES if ACTION[cls[f][0]] == "RECORD-LIVE")
    assert keep + retire + pfix + rec == 136
    print(f"dead_feature_classifier self-test: ALL PASS "
          f"(KEEP {keep}, PARITY-FIX {pfix}, RETIRE {retire}, RECORD-LIVE {rec}; all 136 mapped)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--empirical", action="store_true", help="cross-check vs a klines-only build")
    a = ap.parse_args()
    if a.selftest:
        selftest()
    else:
        _report(empirical=a.empirical)
