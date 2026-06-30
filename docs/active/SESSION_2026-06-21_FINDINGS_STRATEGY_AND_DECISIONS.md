# Session 2026-06-21 — Findings, Strategy & Decisions (consolidated)

**Purpose:** single source of truth for everything analyzed, built, decided, and left open in this
session. Companion docs: [POLYMARKET_BOT_REQUIREMENTS_2026-06-21.md](POLYMARKET_BOT_REQUIREMENTS_2026-06-21.md),
[TIMEFRAME_PERFORMANCE_pyth_2026-06-21.md](TIMEFRAME_PERFORMANCE_pyth_2026-06-21.md).

---

## 0. The one-paragraph headline

The 150-day retrain (06-18) already hit the **information ceiling**: direction is a coin-flip across
*every* cut we tested this session — every CEST hour, every 4-hour block, every calendar day, every
weekday, every timeframe (all ~45–53%). "More/longer training" is **not** a lever. The only ways forward
are **(A)** act less and only in the best cells (policy), **(B)** feed the model genuinely new
information it can't currently see (L2 microstructure / cross-exchange — now recording), and **(C)** the
make-or-break: prove the **Polymarket mispricing edge** (`P(Hold) − ask − buffer`), which is still at
Settlement recovery now has **364 official outcomes but only 4 joined quote rounds**. No model gain is
profit until (C) is positive after costs on enough quote+outcome pairs.

---

## 1. Time-of-day / calendar analysis (the question: "when is the model best?")

**Tooling:** `backend/analyze_timeframe_performance.py` — now source-aware (`--source pyth|binance`),
timezone-aware (`--tz Europe/Berlin`), with hourly, N-hour-block (`--block N`), per-day, day-of-week, and
per-day repeatability sections. Report written to `TIMEFRAME_PERFORMANCE_<source>_<date>.md`.

### 1a. Hour-of-day (CEST, Europe/Berlin)
Everything sits in a 45–53% band — coin-flip. Best 5-hour windows:

| CEST window | n | win % | Wilson-LB | model-lean win % |
|---|---|---|---|---|
| 18:00–23:00 | 3,496 | 51.0 | 49.4 | 50.5 |
| 20:00–01:00 | 2,945 | 50.8 | 49.0 | 51.9 |
| 15:00–20:00 (user's window) | 3,131 | 50.8 | 49.0 | **47.9** |

- Best single hour for the model: ~**20:00 CEST** (model lean 54.0%, n=200); 23:00 (54.2%); 12:00 (59% but n=59).
- **The user's 15:00–20:00 CEST window is NOT actually better** — its 50.8% is fallback noise; the
  model's own leans there are 47.9% (below coin-flip). The evening (18:00–23:00, ~20:00) is the
  least-bad stretch, and even it has Wilson-LB < 50%.

### 1b. 4-hour blocks (CEST) — "which 4 hours performs better"
| group | best block | win % | Wilson-LB | model-lean win % |
|---|---|---|---|---|
| All horizons | **20:00–24:00** | 50.9 | 49.0 | 52.5 |
| 5m (shortest) | 00:00–04:00 | 52.6 | 45.6 | 48.3 |
| 15m | **20:00–24:00** | **61.8** | **51.4 ✅** | 59.1 |

- **Winner: the evening block 20:00–24:00 CEST**, strongest on **15m** — the *only* cell in the 18-cell
  grid whose Wilson-LB clears 50% (51.4%). Worst block consistently the morning 08:00–12:00 CEST.

### 1c. Repeatability of the evening-15m tilt
- All-horizon block beat rest-of-day on **6 of 9 days**; >50% on 5/9.
- **15m: >50% on 7 of 8 days** (61/30/73/60/60/56/83/69%) — more consistent than a pure coin-flip
  (~3–4% chance under the null).

### 1d. Per-day / day-of-week
Flat. Every calendar day 47.5–51.5%, every weekday 47.5–50.4%, all Wilson-LBs < 50%. No standout day or
weekday (and only ~10 days of data → weekday buckets are 1–2 samples, very thin).

### 1e. Verdict (honest)
**Evenings 20:00–24:00 CEST, best on 15m, are a genuine recurring tendency but NOT yet a provable edge.**
Two killers: (1) tiny n — the 15m evening block is 6–16 rounds/day, LB barely over 50; (2) **selection
bias** — the block was *chosen* for looking best across 18 cells, then tested on the same data (circular).
**Status: WATCH, do not trade.** Decisive test = keep logging, re-run in 1–2 weeks; if 15m-evening holds
>50% on *new* days, it graduates to a usable filter.

---

## 2. The strategy — how to improve from the 150d baseline

Direction ceiling confirmed again (150d OOS backtest net-negative every horizon; 27 retrained bundles,
none reliably better; coin-flip across every cut above). Three levers, in ROI order:

### Lever A — Squeeze existing signal with policy (cheap, no retrain, do now)
- **Regime abstention** — the one faint directional lever (RANGE/LOW_VOL ~59% vs TRENDING/HIGH_VOL ~46%).
  In the grade label now; promote to a real gate only after the shadow monitor holds >50% forward.
- **Evening-15m watch** — accrue days; promote if it survives §1c on out-of-sample days.
- **P(Hold) calibration** — already STABLE (ECE 0.01); only a per-horizon tweak left, heads-only (seconds).
- These don't make the model smarter — they make it **act less, only on its best cells.**

### Lever B — Feed it genuinely new information (the only thing that moves the ceiling)
A retrain only helps with signal it hasn't seen:
- **L2 order book** (microprice, OFI, depth, book pull/refill) — record-forward only. ✅ **now recording**.
- **Cross-exchange lead-lag** (who reprices first) — ✅ **now recording**.
- Liquidations / OI (aligns with big-drop, the strongest head).
- Polymarket ask history (the bot's actual edge source).
- Binance persistence is now on → a **second anchor** to compare against Pyth.

### Lever C — The make-or-break: the Polymarket edge gate
The recorder has **364 official outcomes but only 4 joined quote rounds**. Until `analyze_pm_recorder.py`
prints positive `P(Hold) − ask − buffer` ROI after costs over ~500–1000 joined rounds, **no model gain is profit** —
it's just a nicer probability. This gates everything downstream. See the bot requirements doc.

**Bottom line:** gains come from **policy + new data + the edge gate**, NOT a bigger/longer model. If new
data + the gate don't yield a positive recorder table, the honest outcome is shipping the world-class
probability/risk **dashboard** — still the best tool achievable on this data.

---

## 3. What was built / changed this session

### 3a. Binance second anchor (persistence)
- `price_to_beat.py`, `database.py`, `server.py`: new **`source` column** (`'pyth'`/`'binance'`); the
  Binance mirror writes **source-prefixed ids** (no collision with Pyth rows).
- **Pyth-only recorders gated:** persistence + champion snapshots and boot rehydration filter
  `source='pyth'`, so the Binance mirror can't poison the Pyth model's training data.
- Migration additive/idempotent; existing rows default `'pyth'`. Activates on backend restart.

### 3b. `analyze_timeframe_performance.py` (source- & tz-aware)
Timeframe ranking (Wilson-LB, model-vs-fallback, 5m-vs-15m), hour-of-day, N-hour blocks, per-day,
day-of-week, per-day repeatability, last-100 + CSVs. Degrades gracefully if the `source` migration
hasn't run yet.

### 3c. `microstructure_recorder.py` + `start_microstructure_recorder.bat` (Lever B)
- Standalone, **own `data/microstructure.duckdb`** (zero lock conflict with the app), 1s cadence.
- `l2_snapshots`: mid, microprice, spread_bps, OBI (top-20 & near-mid), bid/ask depth USD, depth-slope,
  **OFI** (Cont formula vs prior book).
- `crossvenue_snapshots`: Binance/Coinbase/Bybit/OKX mids, max cross-venue spread, per-venue 1s returns,
  **lead venue**.
- Validated: 4/4 venues live, OFI range −6.9→+11.8, 12.8 bps cross-venue dispersion, lead venue rotates.
- Modes: `--once` (smoke), `--report` (coverage), continuous (default).
- **Usage:** run the `.bat`, leave going next to the app + Polymarket recorder; `--report` to check;
  accrue ~2–4 weeks, then a join-probe (`microstructure.duckdb` → price_to_beat outcomes) tests whether
  L2 + cross-venue lifts the top buckets over candle-only. If yes → the first retrain that could truly
  move direction.

### 3d. Polymarket bot requirements
Full spec in [POLYMARKET_BOT_REQUIREMENTS_2026-06-21.md](POLYMARKET_BOT_REQUIREMENTS_2026-06-21.md).
Headline: a bot doesn't create profit — it executes an edge. **R0 = prove the edge before automating.**
Do NOT build a direction bot; the only thesis is buying P(Hold) when Polymarket underprices it. Phased:
prove (offline) → paper → micro-live → scale, each with a hard go/no-go gate. Current blocker:
`pm_round_settlements` is populated (364 official CLOB outcomes); Phase 0 is now limited by quote accrual.

### 3e. Other shipped changes (UI + grade + guides)
- **`_confluence()` regime fix** (`server.py:2156`) — the setup grade is now **regime-first** (RANGE/LOW_VOL
  favorable, TRENDING_UP/HIGH_VOL adverse; order-flow demoted from 3/5 of the score). Fixes the stale
  "LOW_VOLATILITY is weak" assumption that made the grade non-discriminating. **Display/log label only — NOT a
  gate.** Activates on restart; the `setup_fingerprint` recorder + `grade_ok` check will now measure it forward.
- **UI consolidation** — 6 tabs → **5** (🧭 Decision · 📈 Markets · ₿ Bitcoin · 🎯 Polymarket · 🔬 Diagnostics);
  Decision is the default hero (champion cockpit), heavy diagnostics collapsed. (`index.html`, `src/main.js`.)
- **Betting guides** (`public/polymarket-betting.html`, `public/polymarket-specialist-guide.html`) — added a
  **"How to take action"** section (the edge gate front-and-center + champion-action lookup) and **corrected the
  backwards regime advice** (RANGE/LOW_VOL are the *best* cells, not the worst — matching §3a's fix).

### 3f. Earlier-session analyses & negative results (the discipline working)
| Analysis | Verdict | Report |
|---|---|---|
| DuckDB metrics (by day / model_version / horizon) | coin-flip everywhere; confluence grade was inverted, root-caused | `DUCKDB_METRICS_ANALYSIS_2026-06-21.md` |
| Regime-gate shadow | "act only RANGE/LOW_VOL" = 59.8% (LB **53.5%**) full window, but recent-250 LB **46.3%** → NOT confirmed forward | `REGIME_GATE_SHADOW_2026-06-21.md` |
| Calibration monitor | P(hold) **STABLE**: ECE 0.0108, Brier 0.1685 on 67,529 rounds (~1.2pt top-tier optimism) | `CALIBRATION_MONITOR_2026-06-21.md` |
| Ceiling-break experiments | triple-barrier **net-negative after 2bps** every horizon; flow-proxy no top-bucket lift; meta-skip re-expresses P(hold) | `CEILING_BREAK_EXPERIMENTS_2026-06-20.md` |
| Fallback-abstain-in-TRENDING shadow | **do NOT wire** — cut set is coin-flip (50.5%), retained LB <50%, and at 15m it's *harmful* (cuts a 65% set) | `FALLBACK_ABSTAIN_SHADOW_2026-06-21.md` |

These negatives are the point: several plausible-looking ideas (and a vivid "fallback 0/4" screenshot) were
tested in *shadow* and **killed before any live behavior changed**. Champion edge gate independently validated
as mathematically exact + conservative (bets only when `fair − ask − costs − buffer > required_edge`).

---

## 4. Timeframe pruning — analysis & PENDING decision

**Question:** which timeframe is garbage / can be removed to speed training? And the user's idea — use
small timeframes to time the 15m bet — which decision is better?

**Answer on the idea:** the *instinct* (finer scale informs the 15m) is right; the *mechanism* (small-TF
**direction** stacking) is wrong — direction is a coin-flip at every TF, so stacking 1m/3m/5m leans =
stacking coin-flips → noise, not edge. The validated "fine scale → 15m" channels are **P(Hold)
late-entry** (already built) and **L2 microstructure** (now recording), not a small-TF direction model.

**Garbage timeframes (recommend remove):**

| TF | Tradeable market? | Verdict |
|---|---|---|
| 5m, 15m | ✅ real Polymarket markets | **KEEP** — the whole point |
| 1m | ❌ | optional keep — fastest feedback + most intra-window P(Hold) snapshots |
| 3m | ❌ | remove — 1m already gives faster feedback |
| 7m, 10m | ❌ | remove — non-standard, no market, pure training cost |
| 30m | ❌ | remove — no market, worst accuracy (34% in 24h cut) |

**Optimization win:** heads train per-horizon, so cost ~linear in count. 7 → 3 (or 2) horizons cuts head
training + matrix labeling **~55–70%**, faster retrains, cleaner UI — **no accuracy lost** (removed TFs
were coin-flips with no market).

**⛔ PENDING DECISION (not yet executed):** the exact keep-set, because horizons are wired in ~10 spots
(tracker, head training, UI tabs, bucket config). Options:
- **Keep {5m, 15m}** — leanest, only the tradeable markets.
- **Keep {1m, 5m, 15m}** (recommended) — adds fastest feedback + densest P(Hold) snapshots, still ~57% cheaper.
- Remove {3m, 7m, 10m, 30m} in all cases.

Nothing has been ripped out yet — awaiting the keep-set choice before touching the ~10 wiring sites.

---

## 5. Decisions log

| # | Decision | Status |
|---|---|---|
| D1 | Binance second anchor persists (source-gated, Pyth model protected) | ✅ DONE (active on restart) |
| D2 | Don't retrain longer (300d won't help; 150d is at the ceiling) | ✅ AGREED |
| D3 | Stand up L2 + cross-exchange record-forward recorder (Lever B) | ✅ BUILT, recording |
| D4 | Treat evening-15m (20:00–24:00 CEST) as WATCH, not a tradeable edge | ✅ AGREED |
| D5 | Build Polymarket bot only behind the edge-proof gate (R0); no direction bot | ✅ AGREED (spec written) |
| D6 | Prune garbage timeframes (remove 3m/7m/10m/30m) | ⛔ PENDING keep-set choice |
| D7 | `_confluence()` grade made regime-first (RANGE/LOW_VOL favorable) — label only, not a gate | ✅ DONE (active on restart) |
| D8 | UI consolidated 6→5 tabs (Decision hero); both betting guides corrected (regime + how-to-act) | ✅ DONE |
| D9 | Fallback-abstain-in-TRENDING → do NOT wire (shadow showed it's harmful at 15m) | ✅ DECIDED (shadow) |

---

## 6. What to do next (in order)

1. **Keep the recorders running** — app + Polymarket recorder + microstructure recorder + Binance anchor.
   The whole strategy now waits on data accrual.
2. **Keep official settlement + quote recording running** → grow the 4 joined rounds toward the Phase-0 gate.
3. **Re-run** `analyze_timeframe_performance.py --source binance --tz Europe/Berlin` once Binance has hours;
   re-run the Pyth evening-15m check in 1–2 weeks (out-of-sample test of §1c).
4. **Decide the keep-set** (D6) → then prune timeframes across the ~10 wiring sites.
5. After ~2–4 weeks of microstructure data: build the join-probe (Lever B verdict).
6. Re-run `analyze_pm_recorder.py` once ≥30–500 Polymarket rounds have both quotes and outcomes.
