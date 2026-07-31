# Trading strategies — what exists on each venue, and what each one's evidence is

`2026-07-31`. Every claim here is labelled with the evidence behind it. "Implemented" never
means "profitable."

---

## 1. Binance paper lane — 5 strategies

| strategy | species | evidence |
|---|---|---|
| `trend_following` | continuation | none. Cost defect fixed; capable of profit, not shown to be |
| `breakout` | continuation | **negative** — `BREAKOUT_BRACKET_V1` lost all 9 configs, control lost equally |
| `mean_reversion` | fade | hypothesis, untested species in this lane |
| `model_consensus` | model/heads | calibrated, cost-aware (parallel session) |
| `random_control` | **zero-information benchmark** | by construction |

`random_control` is the one that makes the others readable. Before it, paper P&L was reported
against nothing — a gain was indistinguishable from BTC drift, a loss from the cost of trading.
**A strategy that does not beat it has established nothing.**

All five now pass the economics gate: take-profit must clear the round trip. That gate exists
because both original strategies shipped with a **6.0 bps target against a 12.0 bps round trip** —
every perfect winner lost 6 bps.

## 2. Polymarket — `calibrated_fair_value` (new)

`backend/polymarket_paper/calibrated_fair_value.py` — 13 selftest checks, gated in CI.

Built on the one result in this repository that survived a strictly temporal split.

### Entry and exit are one rule, not two heuristics

```
ENTER  market UNDERprices the leader :  p_cal  >  ask + fee + 0.02
EXIT   market OVERprices it          :  bid    >  p_cal + 0.02
STOP   the thesis itself breaks      :  p_cal  <  entry_p_cal - 0.10
else   HOLD to settlement
```

Taking profit is not a price target — it's the moment someone will pay more than the position is
worth. That's the same test that justified entry, so it adds no free parameter beyond its margin.
`STOP` is evaluated **before** `TAKE_PROFIT`, so a position can never be booked as a winner while
its premise is gone.

### Evidence status — be precise about which half is measured

| half | status |
|---|---|
| **ENTRY** | **measured.** 2 of 3 strictly temporal splits: +0.0430/$1, day-block LCB +0.0164, vs a trade-everything baseline whose bound is negative. Hold-to-settlement. |
| **EXIT** | **NOT measured, and not measurable from data on disk.** |

A dynamic exit needs the bid observed repeatedly across a round's life. `rule_paper_trades` holds
**exactly 1.00 rows per round** for every rule — one snapshot, no trajectory. `polymarket_quotes`
does carry 1,174 quotes per market, but for **two markets**. Two markets is not a sample.

So the exit is a stated hypothesis with a symmetric justification, not a result. Running it in
paper is how it earns evidence. `--measured-only` prints the reduced policy that stays inside the
envelope the evidence actually covers: enter on the rule, then **hold to settlement**.

### The leakage guard is structural, not advisory

A calibrator fitted on rounds it later scores produces a confident number that means nothing — and
the shipped calibrator in `data/research/phold_challenger/` was fitted on the whole live sample.

`Calibration` carries `fitted_through_ms`, and `decide()` **raises** `CalibrationRefused` on any
round at or before it. There is no flag to disable this, so a look-ahead evaluation cannot be
written by accident. The selftest proves the guard fires, and that a 5m calibrator cannot score a
15m quote.

A `Calibration` also rejects non-monotone knots at construction — a non-monotone map would reorder
rounds, which is not calibration.

## 3. On the three things you asked me to guarantee

**No logic gaps.** One real one was found and fixed this session: the 6 bps target against a
12 bps round trip, now blocked by a `StrategyBase` guard that raises, negative-tested by
reintroducing the original expression verbatim.

**No leakage.** Enforced structurally in the Polymarket module (above) and in the research script,
which refits the calibrator inside each split on strictly earlier days only, per horizon.

**No overfitting.** Three margins are module constants; nothing selects them at runtime, and the
selftest asserts that. But the honest statement is stronger than a guarantee: the entry rule's
own evidence is **2 of 3 splits, with the most recent one failing**. That is the split closest to
tomorrow. It has the fewest days and fewest trades, so decay and small-sample noise are not
separable — and 21 days of live rounds cannot separate them. Anyone reading this as settled is
reading it wrong.

## 4. Validation of everything in this change

| check | result |
|---|---|
| `backend/run_ci_locally.py` | **74/74** |
| `python -m pytest -q` | **98 passed** |
| `research/run_all_sequence.py --selftest` | frontier=7, uncovered=0 |
| Polymarket module selftest | 13 checks |
| Binance paper: engine / api / selftest / economics | all pass |

## 5. Not done, and why

**Wiring into `price_to_beat.py`.** The 16 live Polymarket rules are inline in a 2,630-line
serving file that the parallel session is actively editing. The new module is a pure decision
function with no I/O so it can be called in one line when you choose to wire it — that is your
call, not something to do quietly inside a shared file.

**`start.bat`** — left to you as instructed. Recorders remain down (last write
`2026-07-29 19:18` UTC). Nothing above can accrue forward evidence until they run.

## 6. Standing constraints

Real orders **DISABLED**. **PAPER / SHADOW ONLY**. No artifact promoted, no calibrator applied
to serving (`BTC_APPLY_PHOLD_CALIBRATION` unset).

```bash
python -m backend.polymarket_paper.calibrated_fair_value --selftest
python -m backend.polymarket_paper.calibrated_fair_value --measured-only
python -m backend.binance_paper.test_strategy_economics
python backend/run_ci_locally.py
```
