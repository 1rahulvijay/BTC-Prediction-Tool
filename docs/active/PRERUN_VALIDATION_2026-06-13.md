# Pre-Run Validation + Tomorrow's Analysis Plan (2026-06-13)

Final clean-bill before the 30-day overnight run, and the exact checklist to analyze it in the morning.

## Validation results — ALL PASS
- **Syntax:** all **40** `backend/*.py` parse, 0 errors.
- **Imports:** all 11 session tools import clean (beat/path/magnitude/fingerprints/persistence/
  crossvenue builders, diagnose_model, data_quality_audit, model_verifier, price_to_beat, database).
- **DB schema:** `init_db()` creates every table on a throwaway DB — none missing (feature_outcome_log,
  persistence_snapshot, perp_cvd_live, setup_fingerprint, gex_live, feature_importance, predictions_*).
- **Consolidated logic test (one pass, all PASS):** ticks→OHLC, beat labels/features, classify_path,
  abs_move_pct, fingerprint aggregation, data-quality clean (removes bad bars), Wilson LB monotonic
  in n, perp-CVD live==offline parity, shared `resolve_dates(--days)`.
- **Noise gates proven:** beat (signal AUC 0.967 kept / random 0.517 rejected); data-quality detects
  all planted defects and cleans to zero.
- **start.bat:** `if not exist` head-training pattern, no caret-parens (caret-paren count = 0 — §5bh
  safe). 30-day window set (`BTC_HISTORICAL_DAYS=30`, BACKFILL follows).
- **Housekeeping:** stray temp files removed (`tmp_preflight.py`, `tmp_ptbcheck.py`).
- **Reconciliation:** both Claude sessions' edits coexist (verified earlier); no clobbering.

## What runs tonight (start.bat, in order)
1. Data builders (incremental, --auto): trade-features, persistence, cross-venue.
2. Specialized heads (train-if-missing): beat, magnitude, path, fingerprints — each prints SIGNAL or
   NOISE per horizon (only saves what clears its gate).
3. Data-quality health check (last 3 days, report only).
4. App boots → stale v7 arch → **136-feature ensemble retrain** (~2–3h on 30d). Recorders (B1, A1,
   perp-CVD, GEX, setup-fingerprint) start accruing live immediately.

## Tomorrow morning — analysis checklist
Run these with the app **stopped** (DuckDB single-writer) unless noted:

1. **Did the heads clear their gates?** Check the start.bat console for each head's per-horizon
   SIGNAL/NOISE line, and that the `.pkl`s exist:
   `dir data\saved_models\*.pkl`  (beat_model, magnitude_model, path_model, persistence_model).
2. **Model + feature noise:** `python backend\diagnose_model.py` →
   - §1 horizon health (the real sign-truth) — did 5m move off ~50%? gate ≥56%.
   - §2 per-model — now reliable on post-restart rows (the §5ba fix is live) — any model trailing >3pts?
   - §3 dead features — B1 should have >200 rows now → which slots are ~0-variance (cut list).
   - §4 low-signal (SHAP) + §5 **grade validation** (did A/B/C start stratifying, or still inverted?).
3. **Direction scorecard:** `python backend\sign_truth_scorecard.py` → 5m committed-lean sign-truth,
   UP/DOWN balance (the gate number).
4. **Data quality of the trained window:** `python backend\data_quality_audit.py --days 30` →
   especially up-bar fraction (was the 30d one-directional? = inherited bias).
5. **Recorders accruing?** quick counts (app stopped): `feature_outcome_log`, `persistence_snapshot`,
   `perp_cvd_live`, `setup_fingerprint`, `gex_live` row counts climbing.

## The decision tomorrow
- **If 5m ≥ ~56% and balanced** → v7's features helped → proceed to wire P(beat)/path/magnitude into
  the card (next build) + the V8 information features.
- **If still ~50%** (expected) → v7 was incremental → the edge needs INFORMATION (options/L2/
  cross-venue), and the usable edge today remains **P(hold)**. Either way, bump to 60d for the keeper.
- Grade letters: only trust again once §5 shows A≥B≥C with each n≥100 (currently INVERTED — A worst).
