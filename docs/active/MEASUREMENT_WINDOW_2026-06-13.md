# Measurement-Window Findings — 2026-06-13 (v6-classbal frozen run)

**Model:** `2026-06-12-v6-classbal-roster-130-tcn` (class-balanced loss live, 150 components).
**State:** FROZEN (`BTC_FREEZE_MODEL`), scheduled relearn in 24h cooldown. New training era began
**2026-06-13 03:33**. App rebooted clean on this code at 04:08 (all v4–v6 fixes + Pyth anchor +
dual views + sign-truth grading now running).

This doc is the honest snapshot of what the first interpretable data says, the decisions taken,
and the one number that decides what's next. It supersedes the retracted "83–90% winrate" reports
(those used the poisoned `hit` column — see [V3_CHANGES_AND_AUDIT.md](V3_CHANGES_AND_AUDIT.md) §5z).

---

## 1. The objective (operator) — precision in THREE dimensions

Precision in **prediction**, not in betting. Of the calls the model *commits* to, what fraction
are right (by sign-truth). Staying silent is fine; when it speaks it should be right. Three
dimensions, in strict dependency order (see [V5.md](V5.md) "THREE precisions"):

1. **Signal (direction)** — the committed UP/DOWN is right. FOUNDATION. Must clear ~56–60% at 5m
   before anything below matters.
2. **Price (how far)** — conditional-quantile magnitude regressor. Built onto a proven direction.
3. **Path (how it travels up/down)** — path classifier. Built last.

#2 and #3 refine a *correct* call; sequencing them before #1 is proven polishes noise. All three
are next-retrain changes → bundled AFTER v6's direction precision is measured.

---

## 2. First interpretable data: the regime × horizon EDGE MAP (sign-truth)

Source: `validate_regime_thresholds` (FIXED this session to sign-truth grading; it feeds the live
`poor_regimes` blocker). These carry real samples (n up to 184) and are the first honest accuracy
we have. Captured from the running server 05:11–06:14 on 2026-06-13.

| horizon | regime | n | sign-acc | read |
|---|---|---|---|---|
| 3m | LOW_VOL | 184 | **56.5%** | real edge ✓ |
| 3m | TRENDING_DOWN | 57 | 42.1% | negative edge ✗ (silenced) |
| 5m | LOW_VOL | 143 | 51.7% | coin-flip |
| 5m | TRENDING_DOWN | 45 | 42.2% | negative edge ✗ (silenced) |
| 7m | TRENDING_DOWN | 36 | 52.8% | mild edge |
| 7m | LOW_VOL | 103 | 45.6% | negative edge ✗ (silenced) |
| 10m | LOW_VOL | 79 | **54.4%** | real edge ✓ |
| 10m | TRENDING_DOWN | 22 | 50.0% | coin-flip (n<30, not gated) |
| 15m | LOW_VOL | 53 | 43.4% | negative edge ✗ (silenced) |

**What this means:** the model is NOT a uniform coin-flip. It has genuine positive edge in specific
cells (**3m LOW_VOL, 10m LOW_VOL, 7m TRENDING_DOWN**) and genuine *negative* edge in others
(TRENDING_DOWN 3–5m, LOW_VOL 7m/15m). The negative-edge cells are the DOWN-bias signature: in a
downtrend at 3–5m the move mean-reverts (bounces), the model commits DOWN, and it's wrong ~58% of
the time — but by 7m the trend reasserts and it recovers to ~53%.

---

## 3. The live gate — working correctly now (precision win banked)

`server.py` ~L2030: a prediction is forced to NEUTRAL when its regime is in `poor_regimes`
(or the regime-horizon quality is <50%). `poor_regimes` is built (~L2372) from
`validate_regime_thresholds` rows where **`n ≥ 30 and accuracy < 50`**, refreshed ~hourly.

Because the grading is now sign-truth (was poisoned/inverted before), the blocker now correctly
silences exactly the negative-edge cells: **3m & 5m TRENDING_DOWN, 7m & 15m LOW_VOL.** Before the
fix these read as ~83% (inverted) and the model was actively committing its *worst* calls. This is
a real precision gain with no retrain. See [V3_CHANGES_AND_AUDIT.md](V3_CHANGES_AND_AUDIT.md) §5z.

---

## 4. The hard truth about 5m (the Polymarket target)

After silencing 5m TRENDING_DOWN (42%), the model's *best* 5m cell is **5m LOW_VOL at 51.7%** —
barely above a coin-flip. **No gating or refinement can manufacture 5m edge that isn't there; this
is an information problem.** It is the clearest evidence this session that the real 5m lever is the
**§2 new-information bundle** (L2 depth, multi-depth OBI, cross-venue lead-lag, funding×momentum),
NOT magnitude/path refinement and NOT the betting layer.

Honest implication: **the tool should not be committing precise 5m Polymarket bets yet** — the edge
is not there. The model's real edge today lives at **3m and 10m in calm markets.**

---

## 5. Decision taken (2026-06-13): gate left AS-IS, re-check in 24h

We considered tightening the gate so coin-flip cells (50–54%, incl. 5m LOW_VOL 51.7%) stop
committing with full conviction. Three options weighed:
- **A. Conviction-gate** — show the read but never mark it actionable below ~54% proven edge.
- **B. Full silence <54%** — force NEUTRAL; tool speaks only at 3m & 10m LOW_VOL.
- **C. Leave as-is** — block only <50%; re-check after 24h. ← **CHOSEN (operator)**

Rationale for C: the model is only hours into its frozen window; changing the gate now would change
behavior before there's enough resolved data to judge it. Re-evaluate A vs B after the 24h scorecard.

---

## 6. Why the meta-model says "insufficient data" (expected, self-healing)

The meta-model is the **trust supervisor** — a second layer that learns *when the base ensemble can
be trusted* and filters out shaky calls (a precision tool, §4). It learns from resolved calls
(prediction → was it right) and needs ~**100 resolved examples per horizon**.

The **03:33 retrain wiped its memory** (lessons from the old model don't transfer), so it restarts
near-zero and is currently **dormant**:

```
1m:  only one outcome class present  (every resolved 1m call so far had the same result)
3m:  5/100      5m:  1/100      7m:  31/100      10m: 0/100      15m: 0/100
```

This is normal fresh-retrain emptiness; counts climb toward 100 over ~24h and the supervisor
switches on automatically. Until then calls pass through without its extra filter (the regime gate
still applies).

**Meaningful detail:** 7m has 31 but 5m has only 1 — because the supervisor only learns from
*committed* (non-NEUTRAL) calls, and **5m is gated to NEUTRAL frequently** (downtrends silenced, calm
marginal). Same "5m is weak" story from a second angle. One more reason the 24h wait matters: a key
piece of the precision machinery isn't even online yet.

---

## 7. The `sign_truth_scorecard.py` snapshot — NOT yet interpretable (and no bug)

The first scorecard after the retrain showed ugly per-model numbers (e.g. `lr` 0/21). Investigated:
**no grading bug** — `model_verifier._CLASS_DIR = {0:DOWN,1:NEUTRAL,2:UP}` matches the model's
`[down,neutral,up]` prob order exactly. The numbers are three real things stacked: tiny sample
(n=5–29, hours old), a one-directional down-market window, and genuine base-model disagreement
(`lr` leans UP, the trees lean DOWN — in a falling market `lr` looks terrible). Over a balanced 24h
they regress toward their ~0.51 OOF reality. **Do not act on n=24.**

---

## 8. Betting mirror (Polymarket panel) — third independent confirmation

Snapshot of "RECENT RESOLVED ROUNDS & WIN RATE" (2026-06-13, BTC chopping ~$63,500). **Only 5m
and 15m are real Polymarket markets; 1m/3m/7m/10m are practice mirrors (not bettable).**

**The tool's signal stratification is VALIDATED — it ranks its own setups correctly:**
- Model leans **60%** (3/5) vs Fallback leans **36%** (8/22) → committed leans beat the two-way-prob
  fallback, and fallback is *below* coin-flip. Confirms the discipline rule "bet model leans only".
- Grade A **67%** (2/3) vs Grade C **39%** (9/23) → grade stratification ranks setups correctly;
  Grade C is junk and loses. The headline "all rounds 49%" is dragged down by junk the tool already
  says to skip.

**But the encouraging numbers are all noise-tier samples** (model leans n=5, Grade A n=3, 15m 71%
n=7) — they could swing 20 points on the next handful. The only statistically meaningful figures are
the *bad* ones (Grade C 39% n=23, fallback 36% n=22), and they reliably confirm the junk loses.

**The number that matters most — 5m committed model leans — is 48% over 23 rounds.** Best-sampled
real-market committed-lean figure, still a coin-flip.

**Three independent views now agree there is no reliable 5m edge yet:**

| view | 5m signal | n |
|---|---|---|
| Regime edge map (§2) | 51.7% (best cell, LOW_VOL) | 143 |
| sign_truth_scorecard (§7) | 45.8% | 24 |
| Betting mirror committed leans (this §) | 48% | 23 |

This is an **information** limitation, not gating/refinement — it strengthens the "§2 feature bundle"
branch of the §10 plan. The thin Grade-A/committed-lean slice *may* be a real edge but at n=3–7 is a
hypothesis, not a result.

---

## 9. Logic audit (2026-06-13) — clean; one doc defect fixed

Deep correctness pass over the highest-risk / recently-changed paths. **No functional bugs found.**

| path | concern tested | result |
|---|---|---|
| Poor-regime gate (`server.py` ~2032) | double-pass / wrong early-return | ✅ clean |
| SOL/ETH null→0 (`features.py`) | 0 injected as a false ratio value | ✅ `_ffill_zeros` carries last price fwd before any diff/ratio |
| classbal de-bias (`model.py` ~1736) | double prior-division on balanced model | ✅ `"classbal" not in ARCH` skips it |
| Pyth same-feed rule (`server.py` ~416) | anchor Pyth but recover boundary from Binance klines | ✅ `kl=None` in all non-raw-Binance branches |
| Pyth freshness keys (poller vs consumer) | key-name mismatch → anchor never engages | ✅ `pyth_price_ts` set & read consistently |
| `raw_direction` grading integrity (`model.py` ~1831) | graded direction ≠ displayed | ✅ from post-nudge **smoothed** probs; matches shown |
| Smoothing EWMA (`model.py` ~1818) | double-advance from separate ptb predict pass | ✅ `_ptb_preds` reuses main pass; advances once/cycle |
| Price-to-beat resolve (`price_to_beat.py`) | late-tick feed mixing, lean lock, model/fallback split | ✅ clean |

**One defect fixed:** `price_to_beat.py` docstrings said the bet anchored/resolved on *Chainlink*
(module header) and *Binance aggTrade* (`update()`), but the live anchor is **Pyth** with an
offset-corrected Binance fallback. Corrected both to the real Pyth-with-fallback + same-feed rule.
Scope note: backend logic/correctness only; `main.js` (display-only) not re-audited — a render bug
cannot poison the measured precision.

**Takeaway:** the app's logic is trustworthy enough that the precision data accruing now is real.

---

## 10. The 24h re-check plan — the single deciding number

Let v6 run **untouched for 24h** (spans an up move and a down move), then re-run
`sign_truth_scorecard.py` and re-read the regime edge map. The one number that decides everything:
**5m committed-lean sign-truth, and whether UP-acc and DOWN-acc are within ~8 points.**

- **If ≥56% and balanced** → direction precision is real. Proceed: revisit gate option A/B, then
  bundle magnitude (#2) + path (#3) into the next retrain.
- **If still ~50%** → direction edge is information-limited. Next retrain = **§2 new-information
  bundle**, NOT refinement. This matches what §4 above already predicts for 5m.

Either way the betting layer (`p_up`/fair value) stays deferred until 5m committed-lean clears
~56–60%.

---

## 11. HOW to actually raise accuracy — see the dedicated spec

The "why it's at 0.51 and what fixes it" analysis lives in
[SPEC_ACCURACY_NEXT_RETRAIN.md](SPEC_ACCURACY_NEXT_RETRAIN.md). Headline: the bottleneck is a
**train/serve gap** — the highest-edge features (L2 depth, walls, queue, liquidations; slots ~40–72)
are live-only and **constant across the training matrix** (`server.py:1160` broadcasts one live
snapshot over 50 days of history), so the trees can't learn them. The fix is to close that gap
(Track B1: log full feature-vector+outcome live, then train on it), add backfillable multi-venue
**flow** features (Track C), and harvest the cells that already have edge (Track A) — NOT more TA or
bigger models.

---

## 12. DuckDB analysis — ~8.5h of v6 era (via /api/scorecard, DB file is locked by the live app)

The live process holds an exclusive lock (Windows won't even copy the file), so analysis is via the
app's `/api/scorecard` + `/api/runtime-status`. Era `2026-06-13 03:33` → +8.5h. Inventory healthy:
6 models × {TREND,RANGE,VOLATILE,GLOBAL} × 6 horizons all trained; lgb on GPU; TCN deep model.

**A. Ensemble sign-truth (committed leans) — coin-flip at bettable horizons, confirms info-ceiling:**

| h | n | sign-acc | UP n/acc | DOWN n/acc |
|---|---|---|---|---|
| 5 | 42 | 50.0% | 9 / 44% | 33 / 52% |
| 7 | 41 | 46.3% | 9 / 33% | 32 / 50% |
| 10 | 31 | 54.8% | 0 / — | 31 / 55% |
| 15 | 11 | 63.6% | 10 / 60% | 1 / — |
| 3 | 10 | 20.0% | 4 / 25% | 6 / 17% |

**B. Committed model leans vs fallback (mirror) — committing buys ~zero edge:**
5m model **51.1%** (45) vs fallback 50.9% (53); 7m model 47.5% (40); 10m model 50.0% (24) vs
fallback 56.5%; only 15m model 58.3% (n=12) shows a gap. The earlier "model leans 60%" was n=5
noise — at n=24–45 committed leans ≈ coin-flip at 5–10m. **This is the larger-sample confirmation
that there is no demonstrable 5m directional edge today.**

**C. Persistent strong DOWN bias — class-balancing did NOT balance live serving:**
5m 33 DOWN / 9 UP, 7m 32 / 9, **10m 31 / 0** (zero UP commits). And UP leans underperform DOWN
(5m UP 44% vs DOWN 52%; 7m UP 33% vs DOWN 50%) — the DOWN-machine signature. DOWN accuracy is only
~51–55% (not 60%+), so the model is leaning DOWN more by *habit* than by reward. Either an 8.5h
down/chop window or residual bias; the balanced UP/DOWN-accuracy test is still the 24h decider.

**D. Partial-candle skew (watch, don't act — small n):** 5m sign-acc by second-of-minute:
0–14s 62.5% (8), 15–29s 62.5% (8), 30–44s 46.7% (15), 45–59s 36.4% (11). Hints that predictions made
early in the bar beat late-bar ones — possible stale partial-candle feature effect. Too small to act.

**E. 🐛 BUG FOUND + FIXED — per-base-model accuracy was neutral-poisoned.**
`/api/scorecard.models` showed every base model at ~0–20% across all horizons (e.g. `lr` 0/48 at 1m,
`cat` 6% at 5m). Root cause (`model_verifier.check`): a base model's argmax is NEUTRAL on most ticks
(abstention), but the grader compared that NEUTRAL against an almost-always-moved market → every
abstention scored as a directional miss, all in the denominator. The panel was measuring "how often
does an abstention equal a moved market" ≈ 0 — **not** model skill. This is the SAME neutral/`hit`
poisoning fixed earlier in calibration / regime-quality / analytics. **Fix:** grade only committed
UP/DOWN votes by strict sign-truth, exclude NEUTRAL from the denominator (NEUTRAL still resolved as
`hit=NULL`). After restart the per-model panel will read ~40–55% (real), not ~5%. Measurement-only —
does not touch the frozen model or the `predictions_*` sign-truth tables.

**Net:** the honest model quality is "coin-flip at 5–10m, committing adds no edge, still DOWN-biased"
— exactly what §11 / the SPEC predict (information ceiling). The only *broken* thing found was the
per-model display metric, now fixed.
