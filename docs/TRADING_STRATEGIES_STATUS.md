# Trading strategies — what exists on each venue, and what each one's evidence is

`2026-08-02`. Every claim here is labelled with the evidence behind it. "Implemented" never
means "profitable."

**Capital authority: NONE. Promotable economic strategies: 0. Paper/shadow only.**

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

## 2. Polymarket — calibrated fair-value forward benchmark

`backend/polymarket_paper/calibrated_fair_value.py` — 13 selftest checks, gated in CI.

This is a frozen forward benchmark, not a candidate. Its earlier positive historical result is
**RETRACTED** because 93.5% of the quote/state joins used model state observed after the decision
(median look-ahead 8.1 seconds). The causal reconstruction is 0 of 3 with a negative day-block
lower bound in every tested window. See `docs/RESEARCH_LEDGER.md`.

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
| **ENTRY** | **UNVALIDATED_FORWARD.** Historical economic claim retracted; causal rerun 0/3. |
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

**No leakage guarantee is claimed for old studies.** They are retracted. The new atomic ledger
refuses post-decision state/quote inputs and requires exact artifact, calibrator, policy and input
identity for evaluated ENTER and WAIT rows.

**No overfitting guarantee is possible.** The margins are frozen constants, but the only economic
evidence previously cited for them is retracted. They must earn new forward evidence unchanged.

## 4. Validation of everything in this change

| check | result |
|---|---|
| `backend/tests/run_ci_locally.py` | **79/79 passed** |
| `python -m pytest -q` | **100 passed** |
| `research/run_all_sequence.py --selftest` | frontier=7, uncovered=0 |
| Polymarket module selftest | 13 checks |
| Binance paper: engine / api / selftest / economics | all pass |

## 5. Not done, and why

**Forward benchmark wiring.** It is wired into `price_to_beat.py`, but remains unavailable until
the source model/calibrator identity is serviceable. It records `UNAVAILABLE`, `NO_QUOTE`,
`BLOCKED`, `WAIT`, and `ENTER` distinctly in the causal ledger. Its probability is paired with
the executable quote for the same BTC-ahead contract, never whichever token the market happens
to price higher.

**`start.bat`** — left to you as instructed. Recorders remain down (last write
`2026-07-29 19:18` UTC). Nothing above can accrue forward evidence until they run.

## 6. Standing constraints

Real orders **DISABLED**. **PAPER / SHADOW ONLY**. No artifact promoted, no calibrator applied
to serving (`BTC_APPLY_PHOLD_CALIBRATION` unset).

```bash
python -m backend.polymarket_paper.calibrated_fair_value --selftest
python -m backend.polymarket_paper.calibrated_fair_value --measured-only
python -m backend.binance_paper.test_strategy_economics
python backend/tests/run_ci_locally.py
```
