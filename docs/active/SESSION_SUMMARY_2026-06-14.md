# Session Summary — 2026-06-13 evening → 2026-06-14 (the "prove the ceiling" session)

The definitive single-read record of this session. Full forensic detail is in the change-audit
(`V3_CHANGES_AND_AUDIT.md` §5bj–§5by); the roadmaps (V8/V9/V10) hold the forward plan; this doc ties
the whole arc together. **Read the TL;DR first — it's the conclusion the whole session earned.**

---

## TL;DR — what we now KNOW (proven, not assumed)

1. **5m BTC direction is near-efficient / information-poor with retail-available data.** Proven FOUR
   independent ways this session: the model bakeoff (5 families converge to coin-flip), the
   trading-edge backtest (~0 expectancy, negative after costs), the live shadow lane (coin-flip on
   real-time data), and the depth-edge probe (order-book depth predicts at AUC ~0.53). There is **no
   model, feature, or feed** that changes this. Stop chasing 5m direction.
2. **The ONE validated edge is P(hold) / late-entry** — when price is already ahead late in a window,
   it holds 84–99% (Wilson-LB 81–98%) on 19,298 live snapshots. It needs **no direction call**; it's
   a conditional/structural play. This is the product.
3. **The app's correct identity = a disciplined ABSTENTION machine anchored on P(hold)** — mostly
   silent, speaks only on the proven tier. Everything built this session serves that.
4. **The "L2/depth is the missing edge" thesis is DISPROVEN** (for free/coarse depth). So getting
   live futures via a proxy/vendor is NOT justified for direction. Infra spend saved.

---

## PART A — Diagnostic findings (what we discovered)

| Finding | Detail | Audit |
|---|---|---|
| **Grade is INVERTED** | Confluence grade A 44% < C 50% < B 57% — "highest agreement" is the WORST (exhaustion). Don't trust grade letters until A≥B≥C, n≥100. | §5br |
| **At-open heads LEAKED** | beat/magnitude/path/fingerprints read the outcome bar's close (h=1 AUC 1.000). Fixed (features→t+1); leaked `.pkl`s deleted + rebuilt leak-free. v7 ensemble + P(hold) unaffected. | §5bs |
| **Bakeoff: 5-way coin-flip** | logistic/RF/histgb/lightgbm/mlp all AUC 0.50–0.54 at every horizon. LightGBM did NOT win. Information ceiling confirmed from a fresh angle. | §5bt |
| **No tradable edge** | Cost-aware BUY/SELL/AVOID: ~+0.5 bps frictionless tilt, but −4–5 bps after realistic 5 bps cost. Reliably negative. | §5bv |
| **P(hold) VALIDATED** | 84–99% hold in the late-entry structural zone (19k live snapshots); $50+/<60s = 97–100%. The real edge. | §5bw |
| **78/136 features dead-ZERO** | The entire microstructure half (CVD, OBI, walls, liq, funding) was zero even live — the model ran half-blind. Root cause = a feed disconnect (below). | §5bw |
| **Feed-disconnect bug** | The staleness guard used per-trade *latency* (freezes when the WS drops), so the app predicted on a DEAD feed all night + logged 881 all-zero rows. **Fixed.** | §5bw |
| **Geo-block split** | Spot aggTrade/depth = WORK here (CVD/OBI recoverable). Futures + Coinbase = geo-blocked (perp CVD, liquidations, coinbase premium genuinely unavailable). | §5bw |
| **Depth has NO edge** | Free futures `bookDepth` archive IS reachable (HTTP, unlike the live WS). Tested leak-free: AUC 0.51–0.54. Order-book depth doesn't predict 5m. Reframes the ceiling. | §5by |

---

## PART B — Everything BUILT (all no-train, validated, activate on restart or runnable app-stopped)

**Measurement / research tools (read-only):**
- `composed_decision_scorecard.py` — the end-to-end gated-decision metric (gate ladder + Wilson-LB + break-even).
- `model_bakeoff.py` — 6 model families × 6 horizons, full metrics + `--dump-predictions` (310k per-window real-data calls).
- `seq_model_feasibility.py` — TCN/LSTM/Transformer fit + decorrelation gate (the transformer decision).
- `trading_edge_backtest.py` — expectancy / profit-factor / Sharpe / max-DD yardstick.
- `shadow_live_predictor.py` — live shadow test on its own Binance feed (zero app interference).
- `feed_health.py` — feed-reachability + live order_flow probe (run it to confirm feeds are alive).
- `phold_tier_scorecard.py` — the T2/T3 precision-tier PROOF panel + half-life + signal-stability → `data/phold_tier.json`.
- `anti_signal_scan.py` — fade-candidate detector (cells the model is reliably WRONG on; log, don't auto-invert).
- `depth_edge_probe.py` — does order-book depth predict? (the decisive L2 test; verdict: no).

**Serving changes (activate on next restart, crash-guarded):**
- **Feed-disconnect fix** — wall-clock staleness guard (`stale_feed`) + B1 skips dead-feed rows.
- **`decision_gate.py` — do-not-trade reason engine** — every prediction gets `no_trade_reasons[]` + 3-state `trade_verdict` (NO_TRADE/WEAK_LEAN/TRADE), surfaced in the card.
- **Live T2/T3 precision card** — `price_to_beat.py` classifies late-entry rounds T2/T3 (T3 needs proof n≥100/hold≥90%/LB≥80%); `main.js` renders the proof panel; hot-reloads `phold_tier.json`.
- **Recorder fix** — `persistence_snapshot` persists `vol_60s_pct` + `p_hold` (exact P(hold) grading).
- **Noise-gate tightened** — `model_bakeoff.py` + `train_beat_classifier.py`: AUC≥0.55 + a usable confident subset (closes the false-SIGNAL loophole).
- **`diagnose_model §3` fixed** — schema-filtered dead-feature read (was crashing on mixed 130/136 widths).

**Doc reconciliations:** SPEC slot table (130–135 = kline/time bundle, not cross-venue), RUNBOOK A4
feature names (spot-vs-perp), V8/V9 (leak + grade findings woven in), INTEGRATION_AND_METRICS,
DATA_COLLECTORS (L2 sourcing + bookDepth lead).

---

## PART C — Decisions taken (and why)

- **Do NOT retrain to chase 5m** (60d or otherwise) — proven coin-flip on backfillable features; a
  retrain reshuffles noise. Let B1 accrue *clean* data for measurement, not for a direction retrain.
- **Do NOT buy futures-feed infra** (proxy/VPN/vendor) for direction — depth has no edge (§5by).
- **Anchor the product on P(hold)** — the one validated edge; everything else is the abstention layer.
- **persistence features won't enhance the ensemble** (window-relative / redundant) — kept as separate heads.
- **QIOS material was a mis-paste** (a stock-investing product) — removed from all docs.
- **vpin is now train/serve parity-safe** and backfilled; stale docs corrected.

---

## PART D — What's next (the honest, short list)

1. **Restart** the app → banks every fix above (feed guard, decision gate, T2/T3 card, recorders).
2. **Forward-EV paper tracker** (recommended next build) — log every T3 setup forward, grade actual
   P&L (expectancy/PF/drawdown). Turns "P(hold) validated retrospectively" into "made +X over N live
   days" — the honest path to a *proven* edge. NOT YET BUILT.
3. **Abstention-cockpit UI** — home screen defaults to NO TRADE; surface `no_trade_reasons` everywhere;
   clean real-vs-practice separation (Codex Phase 7).
4. **Signal-lifecycle state machine** (created→weakened→invalidated in real time) — the last Codex item.
5. **Let B1 accrue clean microstructure** for a few weeks (measurement), run the scorecards weekly.

**What NOT to do:** more models, more indicators, a transformer, blind retrains, futures-feed infra.
We proved none of it helps. The winning version is a ruthless filter, not a louder predictor.

---

## One-line conclusion
We came in chasing 5m accuracy and leave having **proven it isn't there** — and having built the one
thing that IS (P(hold)) into a disciplined, honest, abstention-first tool. That clarity, earned with
measurement instead of hope, is the session's real product.

---

## Codex Addendum - Decision Sync And Replay

See `docs/active/DECISION_SYNC_AND_REPLAY_PLAN_2026-06-14.md`.

This pass adds no-training decision-state cleanup:

- preserved model raw direction, locked direction, pre-server direction, final direction, and final action
- separated model confluence from setup quality while keeping legacy fields compatible
- added DuckDB audit columns for trade verdict, no-trade reasons, decision-state JSON, model confluence, and setup score
- tightened UI action behavior so weak UP/DOWN leans do not become BUY/SELL unless the gate says TRADE/actionable
- documented the correct use of historical unseen data as offline replay, not fake live truth

Historical replay is useful for calibration and challenger evaluation, but replay rows must stay separate from live accuracy tables and must never use future information at prediction time.

### Replay Safety Fix

- `backend/historical_replay.py` now initializes the DuckDB schema before writing replay rows.
- Default replay mode resets model locks and smoothed probabilities per row so “independent” replay is not contaminated by wall-clock hysteresis.
- Replay summary now separates total rows from raw UP/DOWN directional accuracy, so NEUTRAL/avoid rows do not inflate model direction results.
- Do not run the replay writer beside the live backend against the same DuckDB file. Stop the app first, or set `BTC_DB_PATH` to a separate replay DB.

### In-App Replay Proof Lab

- Added backend replay job status (`replay_status`) and in-process scheduler (`POST /api/historical-replay/run`).
- Added `GET /api/historical-replay/status` with replay results plus threshold recommendations.
- Added websocket payload fields `historical_replay` and `threshold_recommendations`.
- Added Decision Center UI panel: run replay, watch progress, read 5m/15m replay scorecards, review recent replay examples, and see whether thresholds should be tightened.
- Threshold recommendations are read-only; they do not silently change live model behavior.
- `historical_replay.py` now lazy-loads heavy ML/native imports only when a replay actually runs, so CLI help and backend import stay lightweight and do not trigger native-library shutdown crashes.
