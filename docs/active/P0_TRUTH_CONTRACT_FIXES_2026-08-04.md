# P0 #1–#4 — the truth-contract fixes

Response to the external review of 2026-08-04. **All four defects were verified in current source
before any code was changed**, and all four are fixed, tested and wired.

The review's framing is right and worth restating: this was not a modelling ceiling, it was a
**truth-contract ceiling** — the label, the timing, the feature history and the grader did not
agree with each other.

---

## P0-1 — the model was trained and graded against different targets

**Verified.** `features.build_sequences` labelled a triple barrier — UP if the upper barrier is
touched *first*. `PredictionVerifier.check_and_verify` (line 186) graded
`(current_price - predicted_price) / predicted_price` against a threshold — the *endpoint*.

Those are different random variables. Measured, on random walks, **they disagree 24.9% of the
time** (`target_contract.selftest`). A first-touch model could be right and recorded wrong on a
quarter of paths, and those wrong grades fed confidence recalibration, regime weights,
auto-learning, A/B comparison and the accuracy panels.

### Fix

`backend/target_contract.py` — one definition per contract, called by **both** sides:

```
FIRST_TOUCH_TRIPLE_BARRIER_V1     label_first_touch(entry, highs, lows, threshold)
ENDPOINT_SETTLEMENT_V1            label_endpoint(entry, final, threshold)
```

- `build_sequences` now calls `label_first_touch`. There is no second implementation to drift.
- Every prediction is stamped with `target_contract` at `record_prediction`.
- `check_and_verify` dispatches on that stamp and grades under the **matching** rule. First-touch
  grading needs the intrabar path, so the 1m bars are passed in at the call site.
- An unknown contract, or first-touch without a path, returns **no grade** — the row stays
  pending with a `grade_status`, rather than being graded by a rule the model never saw.

> Two implementations of "direction" cannot be kept in agreement by discipline. They are now one
> function, and disagreement is a test failure rather than a silent 25% error rate.

**Not done:** the review's full head separation (`settlement_direction`, `first_touch_direction`,
`path_excursion`, `tradeability`, `execution`) is *not* built. What exists is the contract that
makes those heads separable and makes the current mismatch impossible. For Polymarket the primary
head must be `ENDPOINT_SETTLEMENT_V1`; the machinery to declare and enforce that now exists, but
no settlement-targeted model has been trained.

---

## P0-2 — live inference included the unfinished candle

**Verified.** `handle_kline` (line 1481) overwrites `klines[-1]` on every tick regardless of
`is_closed`, and the live inference block sliced `data_state["klines"][-1500:]` straight into the
model sequence. Training is built from completed REST candles, so the model was served a partial
high, low, volume, trade count and every indicator derived from them — a half-finished minute
reads as an unusually quiet one.

### Fix, and a regression it nearly caused

The model's feature window is now built from `model_klines`, which drops the forming bar.

**`recent_klines` is deliberately left intact.** The HMM block at line 3856 already does its own
`recent_klines[:-1]` on the assumption that the last row is unfinished. Trimming in place — the
obvious fix, and the one the review suggested — would have made that path drop the newest
**closed** bar instead. Caught before commit; the fix is scoped to the model window only.

---

## P0-3 — the "live parity fix" painted the present over the past

**Verified, and the review's reading was exact.** `features.series()` falls back to
`np.full(n, snapshot)` when a history array is missing, and the live path *deliberately popped six
keys* (`cvd_change`, `cvd_1m`, `cvd_5m`, `large_trade_delta`, `large_trade_imbalance`, `vpin`) so
that fallback would trigger. The comment called it "the live equivalent of the train-time
overlay". It is not: the backfill overlay fills each historical candle with **its own** historical
value; popping filled every row with **now**.

The model was trained on a time series and served a constant.

### Fix

Preserve the history, replace only the current row:

```python
arr = np.asarray(live_sig_hist[key]).copy()
arr[-1] = float(live_value)          # only the CURRENT bar is live
```

When a key has no usable history, it is dropped and the degradation is **counted and surfaced**
in `data_state["feature_parity"]` (`overlaid_current_row`, `no_history_fell_back`, `degraded`)
rather than silently invented. The review's stronger option — refuse to score, or score a reduced
model — is **not** implemented; the degradation is currently reported, not enforced.

---

## P0-4 — ambiguous labels were called NEUTRAL

**Verified.** When one bar touched both barriers, the loop broke with both flags false and the row
fell through to `NEUTRAL` — asserting "price went nowhere" about the most violent bars in the
sample.

### Fix

`AMBIGUOUS` is now its own outcome. `build_sequences(..., return_valid_mask=True)` returns
`Yvalid`, and `train()` **zeroes the sample weight** of those rows.

Zeroing rather than dropping is deliberate: X rows are index-aligned with `regime_labels` and the
train/test split, so reindexing would silently desynchronise all three. The row also keeps a
NEUTRAL one-hot so `argmax` stays safe for any caller that ignores the mask — an all-zero row
would `argmax` to **DOWN**, which is worse than the bug being fixed.

`test_training_integrity_20260731` previously asserted only `argmax == 1` — **it pinned the
defect**. It now asserts both halves.

---

## Testing

```
target_contract          --selftest             19 checks
test_target_contract_parity                     11 checks   END-TO-END through the real
                                                            build_sequences and the real _grade
test_training_integrity_20260731                PASS        both halves of the ambiguity contract
mutation suite                                  8/8 CAUGHT
```

Mutations, all caught:

```
AMBIGUOUS collapses back to NEUTRAL                          CAUGHT
first-touch silently accepts an endpoint grade               CAUGHT
unknown contracts default to endpoint instead of refusing    CAUGHT
build_sequences stops marking AMBIGUOUS rows invalid         CAUGHT
training reverts to the ENDPOINT target                      CAUGHT
verifier grades everything on the endpoint again             CAUGHT
missing intrabar path falls back to endpoint                 CAUGHT
ambiguous bars get graded anyway                             CAUGHT
```

The parity test runs through the **real** functions, not the shared helper — a test that only
exercised `target_contract` would still pass if one side stopped calling it.

Local CI: **146 OK / 2 FAIL**, up from 144 OK. Both failures are the pre-existing governance
items (oracle freeze needs a new release id; `check_feature_contract` needs a challenger retrain).
`pyflakes` caught an unused variable mid-change — that is the static-check step earning its place.

---

## What this does NOT fix

Stated plainly, because the review's central criticism is that fixes land while another production
path keeps the old proxy.

- **P0-5 backtest** — still a surrogate Random Forest on fabricated highs/lows, still fails open
  to in-sample. Untouched.
- **P0-6 dashboard recorder health** — `/api/system-health` still uses file mtime. The row-progress
  probe exists (`recorder_health.py`) and is **still not wired into the API**.
- **P0-7 datastore identity** — `database.py` still resolves `BTC_DATA_DIR/analytics.duckdb`.
- **P0-8 strict artifact identity** — `start.bat` still sets `BTC_STRICT_ARTIFACT_IDENTITY=0`.
- **P0-9 GitHub Actions** — still billing-locked. The local runner is still the only gate.
- **P0-10 recorders** — still 0/5 advancing.
- **P0-11 resolution lateness** — the verifier still resolves at the first loop iteration past
  `verify_at`, with no maximum lateness and no as-of event price.
- **P0-12 decision snapshot** — inference still reads the mutable global `data_state`.

**No model has been retrained under the corrected contract.** Every existing artifact was trained
and graded under the mismatch. The fixes change what happens from the next training run onward;
they do not retroactively repair the recorded history, and the accuracy panels still contain
grades produced by the wrong rule.
