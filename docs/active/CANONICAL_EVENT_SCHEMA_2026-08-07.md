# Canonical event schema, and a worth-fixing triage — `2026-08-07`

Two things: the schema that seven tracked defects trace to, and an honest answer to *"are the
remaining open issues worth fixing?"* — because several are not.

---

## Part 1 — the canonical kline schema

### What was wrong

There was no canonical candle. Three producers built three different dicts:

```text
REST  data_ingestion ~:532   time, OHLCV, close_time    ... and no is_closed
WS    data_ingestion ~:226   time, OHLCV, is_closed     ... and no close_time
WS    data_ingestion ~:971   cross-asset, close/volume only
```

Neither carried the source, the exchange event time, or the local receive time. Consumers then
disagreed about what a **missing** field meant — in the same process, on the path that decides
whether a bar may settle a prediction:

```text
target_contract.py:538      k.get("is_closed") is not False    -> MISSING reads as CLOSED
target_contract.py:591      same
prediction_verifier.py:215  same
server.py:712               k.get("is_closed") is True         -> MISSING reads as FORMING
```

### The rule now enforced

> **Closure must be PROVEN, never inferred, and never assumed from absence.**

`is_closed_at()` answers "was this bar complete at this instant?" from **recorded** timestamps
only, and an unknown bar counts as OPEN — the fail-closed direction. Refusing to grade is
recoverable; grading against a forming bar is not.

`canonical_kline()` **raises** on a close before an open, a non-positive timestamp, a
non-integer time, or a missing source. A candle that cannot be attributed can never be aged
against its own feed.

### P0-4 is fixed, and this is why it could not be fixed before

Two earlier attempts were reverted. The reasons are worth keeping:

1. **Changing selection** broke the P0-11 fixture — and *that fixture is right*. `at_ms` NAMES
   the horizon-end bar; requiring `open + interval <= at_ms` would silently redefine every
   horizon by one bar.
2. **Correcting only the returned timestamp** was right in principle but needed the bar's
   duration, and the only available signal was the spacing of neighbouring rows. That is unsafe:
   the P0-11 fixture's bars run +60s/+300s/+540s, so `min(diffs)` yields 240s — not the cadence.

The schema removes the guess. Binance supplies the close time on **both** transports — REST
field `[6]` and the websocket payload's `T` — and both producers simply dropped it. Now recorded:

```text
selection            UNCHANGED  (at_ms still names the horizon-end bar)
resolution event     179,999    the RECORDED close
previously           120,000    the bar's OPEN - one interval early, on every consumer
legacy rows          unchanged  open time returned, so nothing regresses mid-migration
```

All three contract suites pass, **including the P0-11 fixture that forced both reverts**.

### Deliberately not normalized

The second websocket site (`~:971`) is a **cross-asset** kline carrying `asset`, `close` and
`volume` — no OHLC, never used for grading. Forcing the full shape there is churn with no
safety value.

---

## Part 2 — worth-fixing triage

Asked directly: *are the remaining issues worth fixing?* For several, **no**. Recorded so effort
goes where it changes an outcome.

### Now cheap, because the schema landed

| # | issue | why it is now worth doing |
|---|---|---|
| 2.3 | cache never re-fetches its last candle | a REST row now carries explicit `is_closed`, so an incomplete tail bar is *rejectable*. Before, there was nothing to reject it by. |
| 4.1 | delayed book becomes the executable quote | `source_event_ts_ms` and `received_ts_ms` are now distinct, so source age and transport delay are separable — the rejection condition can finally be written |
| 2.5 | signal-history rollover race | event-time bucketing needs an event time on the event; it now exists |

### NOT worth fixing as stated

| # | issue | why not |
|---|---|---|
| **5.1** | "TRADE is unreachable" | **Already refused** (`74e12db`). The block is deliberate and measured — the setup grade is *inverted*. The proposed fix deletes a safety control. |
| 4.21 | learned regime weights bypassed when a stacker exists | This is a **design decision**, not a defect. The stacker *should* dominate; the honest change is a one-line relabel to "diagnostics-only", not a redesign. |
| 3.6 | Binance paper is a multi-strategy portfolio | A **labelling** issue with no code defect. Worth one doc sentence, not an engine change. |
| 2.16 | revision-ledger timestamps | **NOT ESTABLISHED** — the three columns exist and are distinct. Needs a runtime trace before any code moves. Fixing it now would be guessing. |
| 2.30 | CoinGecko under Chainlink names | Real, but the rename touches persisted column names and every consumer. Worth doing **once, deliberately** — not as a drive-by. Pinned by test meanwhile. |
| 4.11–4.13 | smoothing/hysteresis measured in calls | Real, and the fix is event-time plumbing through the whole loop. Worth it **only after** the schema propagates — otherwise it is the same guesswork in a new place. |

### Still worth it, and still open

| # | issue | value |
|---|---|---|
| **2.1** | the artifact hashes a dataset the model did not train on | **highest remaining.** Nothing a manifest says is trustworthy until this lands. |
| 1.7 | `predictions_{5,15}m` lack `target_contract` / `release_id` | unblocks 1.6, 1.4, 3.7 and calibration restoration — one migration, four defects |
| 2.9 | full-refit shadow saves `hmm_state = None` | **one assignment**, and it invalidates the live A/B comparison. Best value-per-line on the list. |
| 2.19 | ambiguous-mask mismatch fails open | a few lines, in the label path |
| 2.20 | settlement head: `lookback` and `groups` unwired | **two arguments at one call site** |
| 2.23 | GPU models fitted at import | precedes every safety check; a native CUDA fault is not catchable by `except` |

**The three at the bottom — 2.9, 2.19, 2.20 — are a handful of lines between them and remain
the best value-per-risk in the entire backlog.**

---

## Status

```text
local CI                  163 steps
the one failure           check_feature_contract - documented; clears on a clean-tree retrain
canonical schema          landed, producers wired, P0-4 closed
real-money authority      NONE
```
