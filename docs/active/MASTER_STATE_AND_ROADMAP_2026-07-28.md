# Historical Master State & Roadmap - 2026-07-28

> **SUPERSEDED FOR CURRENT IMPLEMENTATION STATUS.** This is a dated audit snapshot. Several items
> it lists as future work, including verified serving loaders and runtime hardening, were implemented
> afterward. Use `CURRENT_IMPLEMENTATION_TEST_AND_GAP_LEDGER_2026-07-31.md` for the current
> implementation/test/gap matrix and `report_master_runtime_state.py` for machine state. Preserve
> this file only for the research decisions and evidence known on 2026-07-28.

This file previously served as the single source of truth and superseded the "Phase 1-5 / Grand
Blueprint" documents. It no longer has current-status authority.

Stable research conclusions only. Current runtime state (commit, serviceable artifacts,
archive rows, calibration mode, migration progress) is GENERATED — see §11. Every
test result below is a **local exit code**, not CI-verified.

---

## 1. The one-paragraph state

The system is in a deliberate, honest hole. Feature semantics changed twice today, so all
12 model artifacts are unprovable and should not be trusted. No strategy has ever passed
its own promotion gate. The multi-venue archive has zero rows. What *has* improved is the
machinery that makes those facts visible instead of silent: artifact verdicts, a
fail-closed calibration path, an event-campaign protocol that refuses to run on absent
data, and a horizon gate that killed the lane the previous campaign died in.

**Nothing here increases predictive accuracy. It increases trustworthiness.**

---

## 2. Fixed — verified, committed

| fix | evidence | commit |
|---|---|---|
| VWAP cumulative from bar 0 → trailing | mean \|vwap−close\| **313 → 68** over 30d of 1m bars | `3a68a71` |
| VWAP bar-count window over-reached gaps | a "1440 bar" window spanned **29.98h**, now ≤ **24.00h** across a 6h gap | `3a68a71` |
| Backwards/NaN timestamps silently repaired | now **rejected**; duplicates still allowed, deterministic | `3a68a71` |
| `window_seconds=0` falsy → silent 24h default | now raises | `3a68a71` |
| TCN per-class weight averaging killed recency | gradient ratio **9.000 == weight ratio 9.000** | earlier |
| TCN moved whole model to CPU every inference | stays on `self.device`; only probabilities cross back | earlier |
| WS/REST kline unit split (a *regression* introduced by an audit) | both seconds; `handle_kline` behavioural test | earlier |
| Calibrator persisted knot **count**, not knots | lossless; serving reproduces fit at **0.00e+00** | `921b98b` |
| Calibration failed **open** to raw | `required` mode returns `None`, revokes `may_price` | `921b98b` |
| Legacy flag mapped to weak mode | `BTC_APPLY_PHOLD_CALIBRATION=1` → **required** | `921b98b` |
| Head-health measured but never enforced | `may_price("p_hold")` gates the PAPER_BET override | earlier |
| `promotable` defaulted `True` | now `False` — fails closed | earlier |
| `WAIT` row documented, not enforced | `__post_init__` raises on missing/duplicate/nonzero | earlier |
| Horizon gate used point estimates | day-block **LB95**; excluded 60s maker (21.46% pass → 19.59% fail) | earlier |
| Segmentation on time only | also reconnect, sequence regression, schema change, clock regression | earlier |
| Bybit/Coinbase "optional" inside a required list | `CORE_REQUIRED` / `VARIANT_REQUIRED` tiers + named variants | earlier |
| `sys.path` mutation in `__init__.py` | relative imports throughout | earlier |

**Current contracts:** `FEATURE_SEMANTICS_VERSION = 4`,
`TRAINING_SEMANTICS_VERSION = 3`. Version 4 removes two non-causal feature
construction paths; version 3 makes direction labels, calibration folds, OOF
stacking, class priors, and PSI references causal and training-only.

---

## 3. Built but not wired (the honest gap)

| built | not wired |
|---|---|
| `phold_calibrator` (19 assertions, exact round-trip) | `MODE=off`; nothing in serving consumes it |
| `event_conditional_v1` (79 assertions, frozen protocol) | every family `NOT_READY`, archive empty |
| `head_permissions.may_price` | `may_rank` / `may_display_confidence` still **advisory** |

Feature-provenance enforcement is now wired into active standalone loaders and
verified before deserialization. Existing artifacts remain unavailable when
their manifest or feature/training identity does not match the running code.

---

## 4. Measured facts worth not re-deriving

**Endpoint cost-clearance rate** — the share of anchors where a *perfect-direction* oracle
could clear the round trip under fixed-horizon endpoint exit. 129 of 1,286 days,
2023-01-16 → 2026-07-19, `DESIGN_ONLY`:

| horizon | median \|move\| | taker 12bps (pt / **LB95**) | maker 6bps (pt / **LB95**) |
|---:|---:|---:|---:|
| 30s | 1.97 bps | 2.49% / **2.08%** | 12.12% / **10.80%** |
| 180s | 5.18 bps | 16.97% / **15.29%** | 40.44% / **38.07%** |
| 900s | 11.59 bps | 44.04% / **41.59%** | 67.70% / **65.72%** |
| 3600s | 22.65 bps | 66.44% / **64.16%** | 82.38% / **81.03%** |

Reproduced every year (30s taker: 1.94 / 3.36 / 2.08 / 2.60 %). This explains
`PROFIT_CAMPAIGN_V1`'s profit factor of exactly **0.0000** across 374 trades: it traded a
30s horizon at 12 bps every 15 seconds, so ~97.5% of its anchors could not clear cost even
with perfect foresight. *Economically consistent, not mathematically forced* — a
sufficiently selective signal could in principle trade only the eligible 2.49%.

**Calibration (Oracle snapshot, research-only):**

| | n | ECE | log-loss | predicted vs realized |
|---|---|---|---|---|
| 5m isotonic | 5,079 | 0.0883 → **0.0136** | 0.9398 → **0.3429** | 95.5% → 87.4% vs 86.8% |
| 15m logistic | 1,646 | 0.0267 → **0.0050** | 0.2296 → **0.1242** | 98.0% → 96.0% vs 95.7% |

**Consequence nobody should skip:** calibrated P(hold) saturates near **0.923**, so
`PHOLD_STRONG = 0.93` would never fire. Those thresholds were tuned against raw
overconfident probabilities. Re-derive them economically — **do not lower them until
signals reappear**, which re-introduces the overconfidence just removed.

**Prior evidence (do not re-litigate):** 5m/15m direction ~coin-flip across 13 model
families (0.50–0.535 AUC, incl. 2 neural nets) · 2,880 static TP/SL cells, 0 positive ·
`LATE_LEADER_30S_V1` failed its own gate (block-LB −0.60c) · fade model DORMANT after
failing pre-declared gates · direction dead across all 17 microstructure features.

---

## 5. Blocked — and what unblocks it

**1. Recorder: `venue_events` = 0 rows, never started.** It *is* wired
(`start.bat:383` → `start_recorders_once.ps1`); no log files exist at all, so
`start.bat` simply hasn't run since the launcher landed. Every research family and all
forward evidence depends on this. Run on the **always-on host** — laptop sleep produces
non-qualifying episodes.

```bash
python backend/venues/multi_venue_recorder.py --report
```

**2. Retrain: 12/12 artifacts unprovable.** Must wait for manifest writing, or it produces
another generation of unprovable artifacts.

**3. Calibration adoption:** blocked on the retrain — the fitted knots/coefficients map
*today's* score distribution and become wrong after retraining.

---

## 6. Next work — `ARTIFACT_MANIFEST_AND_VERIFIED_LOADING_V1`

Measured scope: **25 save paths**, **57 load paths** (excluding research/tests).

Four isolated commits, in this order. The invariant: **no loader enforcement goes live
until every authoritative trainer writes manifests.** A partial migration is worse than
none — enforcement would permanently block everything.

- **A** — `model_registry.py` + `model_artifacts.py`: immutable staging bundles, atomic
  rename, member hashing, manifest checksum, read-back verification, champion pointer,
  typed refusals, synthetic atomicity/tampering/concurrency tests. No migration.
- **B** — migrate all 25 writers. Bypass scan → 0 authoritative direct saves.
- **C** — migrate all 57 loaders; verify **before deserialization** (never execute an
  unknown pickle to decide if it is valid). Behind `BTC_ENFORCE_VERIFIED_MODEL_LOADING=0`.
- **D** — activate. `DEGRADED_MODEL_BLOCKED`: recording, reporting, health and
  reduce-only management stay available; predictions, pricing, ranking, sizing and new
  entries are blocked.

Then: retrain → verify manifests → new OOF/forward P(hold) scores → refit calibrators →
untouched evaluation → shadow → economic thresholds.

### Known weaknesses to fix in that phase

- **Semantic-version binding ≠ exact source-model binding.** The calibrator currently
  checks feature/training *versions*. Two different retrains under v3/v2 would both pass.
  Exact binding needs `source_model_artifact_sha256`, `source_bundle_id`,
  `source_training_dataset_sha256`, `target_contract_sha256` — fields that require
  manifests to exist.
- **`deployable` is a self-declared field.** A loader must derive eligibility from
  verified evidence, not trust an editable boolean.
- **Calibrator selection bias.** Isotonic-vs-logistic was chosen on the same holdout whose
  metrics are reported. Needs four stages: train / fit / select / untouched.
- **Overall ECE hides tail behaviour.** Report per side, decile, regime, and the
  ≥0.90 / ≥0.95 region where trades actually happen.
- **Isotonic stability untested** — 23 knots, no bootstrap knot-stability or
  minimum-support-per-region checks.
- **Archive readiness is shallow** — counts rows and span, not valid UTC days, qualifying
  episodes, p50/p90/p99 cadence, reconnects, or all-stream overlap.

---

## 7. Runtime hardening — untouched, required before real money

From the external audit, none of this is done: legacy Polymarket modules still hold
authority; two Binance paper engines both default to `binance_paper.duckdb`; the hard-off
gate also disables *risk management* (a recovered position cannot be marked or closed);
pending orders are not reserved or re-risked at arrival; DB writes happen inside WebSocket
callbacks; background tasks are unsupervised; migrations swallow real failures;
Binance-paper mutating endpoints are unauthenticated with wildcard CORS.

> A profitable model connected to an unsafe order state machine can still lose the account.

---

## 8. Rejected — checked against the code and refuted

| claim | verdict |
|---|---|
| `max_taker_ask` should be linear | **False, harmful.** Polymarket fee is `rate·p·(1−p)` — quadratic. Current code matches brute force to 1e-6; the linear form gives 0.822 where the true max ask is 0.872, rejecting profitable bets. **Repeatedly proposed; permanently rejected.** |
| ORB has a ms/s mismatch | **False.** Caller normalizes at `features.py`. The "fix" would divide twice and *create* the bug. |
| Remove `//1000` from the WS kline | **False and applied once as a live-breaking regression.** REST emits seconds too. Effect measured: one orphan bar with a 1000× timestamp, then the genuine candle freezes while all live data lands in the orphan. |
| `stochastic_rsi → 50` on flat RSI | Design choice, not a defect. |
| Kelly probe floor `0.005` | Risk-policy call, not a defect. |
| "Fixes successfully executed" (Phases 2–5) | Several described changes were **never in `master`**. Treat those docs as brainstorming only. |

---

## 9. Strategy ideas — ranked honestly

**Tier 1 (test after the archive exists):** cross-venue executable lead-lag ·
perp/spot OFI divergence · liquidation continuation / exhaustion · funding-basis-OI
dislocation · **queue-aware maker conversion** · options IV regime · survival/hazard
models for barrier timing.

Maker conversion is the highest-leverage: at 30s, moving 12 bps → 4 bps lifts clearance
2.49% → 22.97%. But that 9.2× holds *only* if the order still fills, so it needs
`P(fill)`, queue position, partial fills, adverse selection, cancel latency and taker
fallback before any claim.

**Tier 2 (later):** self-supervised regime embeddings (a visually distinct cluster is not
a profitable regime) · stablecoin/macro liquidity (4h+ horizons) · Hawkes intensity —
must beat rolling counts / EWMA / Poisson / GBM baselines before earning its complexity.

**Tier 3 — deferred, and why:**

- **RL / PPO execution agent.** Proposed repeatedly. An RL policy needs a faithful
  simulator and a reward that is actually attainable; we have neither — the fill model is
  unproven and the archive is empty. It would learn to exploit simulator artifacts.
  Revisit only after queue-aware fills are validated forward.
- **Cross-venue delta-neutral hedging ("the holy grail").** Fails at its premise. A binary
  contract's delta is nonlinear and time-varying; a linear perp short does not hedge it,
  and rebalancing adds hedging error, basis, funding, double fees, legging and settlement
  risk. Worse: the "mispricing" is computed from `fair_value`, which comes from the head
  head-health says may not price. Hedging strips the directional noise *masking* a
  miscalibrated probability and levers up the error.
- **L3 market-by-order spoof detection.** Public Binance depth is level-aggregated; there
  are no stable per-order identities. Rename any such research
  `LEVEL_AGGREGATED_LIQUIDITY_CANCELLATION_V1` and claim only what the feed supports.
- **Wallet copy-trading.** Attribution is unreliable, flow is seen after the move, and it
  raises privacy/compliance questions. Aggregated pseudonymous flow only.
- **Rust/C++ rewrite, AWS region selection.** The system is not losing to milliseconds. It
  is losing to absent data, unproven fills, miscalibration and unsafe runtime. Measure the
  four latency legs first; a poorly designed Rust system is not better than a correct
  Python one.
- **Live/authenticated execution.** No promoted strategy exists. Adding order authority
  converts research uncertainty into financial risk, not profit.

---

## 10. Operational reference

| switch | default | meaning |
|---|---|---|
| `BTC_PHOLD_CALIBRATION_MODE` | `off` | `off` raw · `optional` shadow · `required` fail-closed |
| `BTC_APPLY_PHOLD_CALIBRATION` | `0` | legacy; `1` maps to **required** |
| `BTC_ENFORCE_HEAD_HEALTH` | `1` | a head that cannot price may not price |
| `BTC_ENABLE_PAPER_BET` | `0` | also requires `may_price("p_hold")` |
| `BTC_STRICT_ARTIFACT_IDENTITY` | `0` | flip to `1` after the retrain writes manifests |
| `BTC_SKIP_VENUE_COLLECTOR` | unset | `1` disables the multi-venue recorder |

```bash
python backend/check_feature_contract.py --enforce-serving
```

```bash
python backend/venues/multi_venue_recorder.py --report
```

**Standing constraints:** single DuckDB writer — never run duplicate launchers, snapshot
before research · frozen gates, no post-hoc tuning, a near miss is a miss · ASCII-only
console (Windows cp1252) · no real-money routing anywhere · a parallel session co-edits
this repo, so reconcile file-by-file and never clobber.

---

## 11. Runtime state is GENERATED, not written here

Sections 1-10 are **stable research conclusions** - they stay true until new evidence
overturns them. Anything that changes when the recorder starts or a model is retrained
(commit, serviceable artifacts, archive rows, calibration mode, migration progress) is
generated, because a hand-written "single source of truth" goes stale silently:

```bash
python backend/report_master_runtime_state.py
```

writes `data/reports/MASTER_RUNTIME_STATE.{json,md}`.

`--selftest` is a documentation consistency test wired into both CI jobs. It asserts only
what code can prove (semantics v4/v3, calibration default off, legacy flag fails closed,
calibrators non-deployable, loader enforcement wired, max_taker_ask still quadratic)
and deliberately does **not** assert runtime values, so CI never depends on a recorder.

## 12. Local validation as of this commit

```text
compileall                     0        pyflakes                       0
workflow YAML                  valid    prereg hashes                  0
test_feature_and_weight_fixes  0        test_kline_time_contract       0
phold_calibrator --selftest    0        head_permissions --selftest    0
event_conditional_v1 selftest  0        profit_campaign_v1 selftest    0
collector_integrity            0        check_feature_contract         1  (blocked, correct)
```

`profit_campaign_v1` unmodified. No model retrained, no calibrator activated, no threshold
changed, no head promoted, no paper/live authority enabled.
