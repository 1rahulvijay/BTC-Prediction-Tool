# Paper Strategy Lab — Frozen Specs & Live Ledger (2026-07-04)

**What this is.** The single reference for every paper strategy the app now trades automatically on **real
executable Polymarket quotes** (recorder bridge, ≤5s fresh, exact-round anchor, fail-closed). Strategies
log supported actions to `rule_paper_trades` with entry ask, exit value,
fees on every leg, per-trade P/L, and (new) the **BTC price at entry and at exit**. All visible in the
**📒 Trades tab** (per-strategy filter chips) and the **💀 Strategy lab** panel (live EV next to the
historical verdict). **PAPER ONLY — no real money, no order routing exists.**

**Specs are FROZEN at wiring time.** No threshold may be re-tuned after seeing live results — a strategy
passes or dies as written. Variants are new names, never edits.

---

## Tier 1 — the frozen rule (promotion candidate)

| | LATE_LEADER_30S_V1 |
|---|---|
| Spec | 5m only · evaluate once at 20–32s left · buy market leader at ask · skip ask <0.60 or ≥0.97 · hold to settle · 1 share |
| Evidence | 14,226 Kaggle rounds at executable asks: EV +2.5c, 95%-LB +1.6c, PF 1.22, 8/8 weeks positive; all BTC-side gates failed shuffled-gate nulls |
| Promotion gate | n≥500 settled AND EV≥2c AND LB>0 AND PF≥1.2 — tracked live on the 📜 RULE STATUS tile |
| Live so far | first day: 32 settled, 87.5%, +92.9c, avg +2.9c/share — consistent with offline |

## Tier 2 — late-leader variants (edge candidates, wired 2026-07-04)

| Rule | Spec (frozen) | Why / evidence basis |
|---|---|---|
| `LATE_LEADER_15M_SHADOW_V1` | identical mechanics, **15m rounds**, eval 20–32s left | No archive had 15m quotes; the 15m question gets answered live. NOT the frozen rule. |
| `LATE_LEADER_60S_V1` | identical gates, eval **50–65s** left (5m) | EV-vs-expiry ladder: offline 60s ≈ +0.5c LB. |
| `LATE_LEADER_15S_V1` | identical gates, eval **10–17s** left (5m) | The unmeasured end of the ladder (offline data lost late quotes); gradient suggests it may beat 30s. |
| `LATE_LEADER_MAKER_V1` | at 20–32s **rest at the leader's bid** (bid 0.55–0.97); conservative fill = a later ask trades DOWN to our price; maker fee 0; unfilled by 3s left → **NO_FILL** row | The biggest cost lever: the spread (~1–2c) is ≈half the whole taker edge. The open question is the **fill rate** — NO_FILL rows keep the denominator honest. |

## Tier 3 — dead-strategy replications (falsifiability guards)

| Rule | Spec | Offline verdict being re-tested |
|---|---|---|
| `MID_SCALP_LIVE_V1` | buy leader 0.50–0.70 mid-window, TP +5c / SL −3c / 30s stop | −4.1c/share, PF 0.28, 0/9 weeks |
| `TP_OR_SETTLE_LIVE_V1` | buy early 0.50–0.70, TP +20% else settle | −1.8…−4.4c/share (optional stopping) |
| `STRADDLE_LIVE_V1` | both sides near 50/50 (bids ≤0.55, spreads ≤2c), sell legs at +20%, rest settles | −10.7c/straddle, PF 0.48 |

## Tier 4 — model-gated variants (do the heads add value?)

| Rule | Trigger (models decide) |
|---|---|
| `MODEL_FADE_LIVE_V1` | path head FADE-SETUP + touch + fade grade ≥55% → buy cheap side, TP +20% or settle |
| `MODEL_STRADDLE_LIVE_V1` | straddle ONLY when path head predicts two_sided (round-trip ≥35%) — the gap vs the blind straddle IS the model's value |
| `MODEL_RIDE_LIVE_V1` | path RIDE + big-move elevated → buy leader 0.55–0.80 mid-window, hold |
| `CHEAP_SAFE_EARLY_V1` | leader ask 0.42–0.58 + dist/vol ratio ≥1.5 (the HF SAFE gate), early-mid window, hold. **Expectation LOW** — the shuffled-gate nulls say BTC state is priced in; this closes the question on live asks. |
| `SHOCK_SNIPER_LIVE_V1` | BTC moved ≥$20 within ~3–8s AND the target side's ask did **not** move (±0.5c) → buy the stale ask ≤0.90, hold. ⚠️ **1s approximation** — the bridge cadence understates the sub-second opportunity: a positive result is strong, a zero is NOT conclusive. The exact test is the **offline L2 replay** on `polymarket_l2.duckdb` (queued; run while recorders are stopped). |

---

## Accounting (identical for every strategy)

- **Entry** = real executable ask (or resting bid for MAKER) · **1 paper share** · entry taker fee `0.07·p·(1−p)` (maker = 0).
- **Exit** = bid − exit fee (early exits) or settlement $1/$0 (holds). Straddles settle both legs (one always pays $1; DB-level floor guards restart orphans).
- **BTC @ entry / BTC @ exit** recorded on every trade (from 2026-07-04 rows onward).
- SKIP / NO_QUOTE / NO_FILL rows are kept for rules with a fixed evaluation checkpoint. Several
  conditional model/dead-strategy shadows log entries only, so entered-trade P/L is valid but their
  opportunity-coverage denominator is incomplete.
- Blotter: 📒 Trades tab → header totals, per-strategy scoreboard, filter chips, full trade table. API: `GET /api/paper-ledger`.

## ⚠️ Activation & the "where is 15m?" answer

**Everything wired on 2026-07-04 activates on the next backend restart** — the running process predates it.
That includes: the 15m shadow, the 15s/60s/maker/cheap-safe/sniper shadows, BTC entry/exit recording, and the
`btc_entry/btc_exit` schema migration. Until that restart the Trades tab shows only the strategies the old
process knows (hence "no 15m rows yet"). The planned reboot for the long retrain switches everything on at once.

## Reading discipline

1. **n < 100 per strategy = noise.** Judge nothing before that; the frozen rule alone has a pre-declared n≥500 gate.
2. Cents ≈ % of stake (1 share of a $1 market).
3. A dead replication going positive live means the historical kill needs re-examination — that is the point of running them.
4. No strategy here is real-money approved. Promotion is a separate, explicit operator decision after its gate passes.

## Accounting integrity upgrade (Codex audit, 2026-07-04)

New rows store raw gross exit proceeds, exit fee, exit reason and crash-safe mutable state. The API
independently recomputes P/L from those components and the Trades tab marks each row `exact`, `mismatch`
or `legacy`. Old rows remain legacy because their raw exit components were never stored.

Two-leg exits are persisted per leg. A restart after one straddle leg takes profit no longer causes
settlement to forget that exit. State is restored when the round resumes, and settlement can reconstruct
the result even if only the database state survives.

Path move probabilities display their live dollar-equivalent thresholds. These classifiers use
price-normalized basis-point events, so a permanently fixed `$50/$100` label would become inaccurate as
BTC moves away from the training reference price.

---

# Addendum — Trades-tab evolution, accounting validation & first live day (2026-07-04)

## Trades-tab features (final state)

- **Horizon tabs `ALL / 5m / 15m`** split BOTH the per-strategy scoreboard and the blotter. The backend
  aggregates per (rule, horizon); when the running backend predates that split, the frontend computes the
  per-horizon scoreboard **client-side from the trade rows** (header shows *"5m — from the last N trades"*),
  so the split works without waiting for a restart. After restart it switches to the exact server aggregate.
- **Strategy filter chips** (ALL + one per strategy present in the current horizon view) — every trade each
  strategy takes, in its own view; state survives the 30s auto-refresh.
- **`BTC @ buy (Pyth)` / `BTC @ exit (Pyth)` columns** with the $ move colored; footer explains the feed.

## Trade-accounting validation (operator challenged it; audited row-by-row)

- **P/L formula reconciles to the cent** on every audited row: `exit_value − ask − fee_in (− fee_out on
  early exits)`. Settlement winners show `100.0c` net (no exit fee); TP exits show `bid − exit fee`.
- **"Bought UP, BTC fell, still won"** is CORRECT: shares price distance from the round's ANCHOR, not from
  the entry — plus binary time-decay pushes the leading side toward $1 as the clock runs out.
- **"Stopped out worse than the −3c stop"** is CORRECT: stops/TPs sell into the **visible bid**, which gaps
  (e.g. a −20.3c SL when the bid collapsed as BTC crossed the anchor). That worst-visible-fill accounting is
  deliberate — it is the mechanism that killed scalping offline, reproduced honestly.
- **BTC prices are PYTH** (the settlement-oracle proxy these markets resolve on), NOT Binance — the two
  routinely differ by tens of dollars (observed Δ ≈ $72), so blotter prices will not match TradingView.

## First live-day snapshot (n tiny — recorded for the drift trail, NOT conclusions)

| Strategy | n settled | win | total | Read |
|---|---:|---:|---:|---|
| 📜 LATE_LEADER_30S | 35 | 82.9% | **−60.8c** | The losers arrived: wins earn ~+7–15c, the ~6 high-ask losses cost ~75–96c each. Was +92.9c at n=32 hours earlier — exactly why the n≥500 gate exists. Within noise of the offline +2.5c; judged only at the gate. |
| Early profit-take | 9 | 100% | +123c | The seductive offline pattern (78% TPs hit, still lost) — losers not yet arrived. |
| Straddle (blind) | 7 | 57% | −21.5c | Tracking the offline kill. |
| Mid-round scalp | 7 | 14% | −65.2c | Confirming the offline kill fast (PF 0.08). |

## Defects found & fixed this cycle

1. **Blank UI after card redesign** — a template variable was read before declaration (TDZ crash on every
   render). Vite builds cannot catch this class of bug; fixed + declaration order re-audited.
2. **Trades tab "SyntaxError: Unexpected token '<'"** — the blotter fetch used a relative `/api/paper-ledger`,
   which the Vite dev server answered with index.html. All API calls now use the absolute `HTTP_API_BASE`
   (the same latent bug fixed in the action-log fetch).
3. **Straddle orphan mispricing** — a mid-round restart loses the in-memory leg state; the generic settler
   would have booked `side='BOTH'` rows as total losses. DB-level fix: straddles floor at $1.00 settle value
   (one leg always pays exactly $1). Functionally tested: orphan −7.6c real vs −107.6c fake.
4. **False "No 5m entries yet"** — the horizon filter emptied the scoreboard on backends whose aggregate
   predates the split; now falls back to the client-side aggregation (fix #1 in this addendum).
5. **Missing lab row** — `LATE_LEADER_15M_SHADOW_V1` was in the name maps but not the strategy-lab table.

## Operational: restarts & how to verify which code is live

Two mid-wiring restarts landed BETWEEN code saves (backend booted 23:57; strategies saved 00:05) — the
process was serving stale code while the files were current. **Verification recipe:** check the port owner's
start time vs file mtimes (`Get-NetTCPConnection -LocalPort 8000` → process StartTime vs
`backend/price_to_beat.py` LastWriteTime), or simply look at the Trades tab scoreboard header — *"(5m —
from the last N trades)"* means the backend predates the per-horizon split. If a new backend won't start,
the old process is still holding port 8000 — kill it first, then `start.bat` (instant start; the completion
marker prevents a retrain).

**Both failure modes are now automated away (2026-07-04):**
1. **start.bat app-port guard** — stops existing listeners on 3000/8000 before any data/model work, so
   "run start.bat" means one fresh frontend/backend pair. Set `BTC_AUTO_STOP_EXISTING_APP=0` to abort
   instead of stopping them.
2. **Boot/code stamp on the 📜 RULE STATUS tile** — the backend compares a SHA-256 content hash captured
   at boot with the current core-module hash every 30s. The tile shows the 12-character build hash plus
   either `up since HH:MM · code current ✓` or
   `⚠ CODE CHANGED AFTER BOOT — restart to load it`.
3. **Ask-bucket diagnostic on the tile** — the live rule EV split by entry ask (`60-70c / 70-80c / 80-90c /
   90c+`, each with EV × n), so a drawdown is attributable to its bucket at a glance. Offline, the EV lived
   in mid-priced asks and 90c+ added ≈0 — if the live losses concentrate in 90c+, that is the offline result
   repeating, not a new failure. Diagnostic only; the frozen rule is not re-tuned on it.

## Live two-sided Polymarket share prices (2026-07-04)

Each open 5m/15m card now exposes the fresh executable quote for **both** binary contracts:

- `UP share`: best ask labelled `Buy`, best bid labelled `Sell now`, and best-ask size.
- `DOWN share`: best ask labelled `Buy`, best bid labelled `Sell now`, and best-ask size.
- Quote age is displayed so the operator can distinguish a live price from an old snapshot.

The backend emits `share_prices` only when the quote belongs to the card's exact anchor timestamp and is
no more than five seconds old. Missing, stale, malformed, or wrong-round quotes fail closed and the strip
is hidden. These are not model-estimated fair values: `Buy` is the executable best ask and `Sell now` is
the executable best bid from the live Polymarket quote bridge. Strategy fees and edge calculations remain
separate, so the display does not imply that the spread or fee has already been recovered.
