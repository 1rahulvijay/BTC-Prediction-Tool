# Specialist Head And Champion Validator Implementation

Date: 2026-06-17

This document records the implementation of the first production-safe slice of `MODEL_OWNERSHIP_AND_CHAMPION_ENSEMBLE_PLAN_2026-06-17.md`.

The goal was not to add another raw BTC direction model. The goal was to turn the app into a stricter decision system:

```text
small specialist heads
+ probability bucket verification
+ quantile reward/risk veto
+ rules-first champion validator
+ plain-English UI output
```

---

## Implemented Components

| Component | Status | Files |
|---|---|---|
| Big-Move Keeper 4-model ensemble | Implemented | `backend/train_bigmove_keeper.py` |
| Big Drop Risk head | Implemented | `backend/train_bigdrop_keeper.py` |
| Version-aware head registration | Implemented | `backend/train_heads.py` |
| Live price-to-beat head loading | Implemented | `backend/price_to_beat.py` |
| Champion decision validator | Implemented | `backend/decision_champion.py` |
| UI champion verdict strip | Implemented | `src/main.js` |
| Big-drop risk UI gauge | Implemented | `src/main.js` |
| Probability bucket scorecard | Implemented | `backend/head_probability_buckets.py` |
| Bucket output | Implemented | `data/head_probability_buckets.parquet`, `docs/active/HEAD_PROBABILITY_BUCKETS_2026-06-17.md` |

---

## Model Heads Now Wired

### 1. Big-Move Keeper

Purpose:

```text
Is this window active enough to matter?
```

Model:

```text
Logistic Regression
Random Forest
ExtraTrees
CatBoost if installed
```

Features:

```text
rv_15m
rv_30m
compression_ratio
shock_magnitude
```

Saved model:

```text
data/saved_models/bigmove_keeper_model.pkl
```

Validation:

```text
OOS AUC: about 0.733
Bucket scorecard: top 5% event rate 58.7%
Deciles: monotonic
```

Live role:

```text
First activity gate. Quiet means WAIT unless P(hold) is already very strong.
```

---

### 2. Big Drop Risk

Purpose:

```text
Will BTC trade meaningfully lower inside the window, even if it bounces later?
```

Model:

```text
Logistic Regression
Random Forest
ExtraTrees
CatBoost if installed
```

Features:

```text
rv_15m
rv_30m
compression_ratio
shock_magnitude
```

Saved model:

```text
data/saved_models/bigdrop_keeper_model.pkl
```

Validation:

```text
OOS AUC: about 0.751
Top 5% precision: about 63.5%
Bucket scorecard top 5% event rate: 63.7%
Deciles: monotonic
```

Live role:

```text
Risk warning and long veto.
It is not a direct trade trigger.
```

Champion behavior:

```text
HIGH big-drop risk + UP setup -> AVOID_LONG
HIGH big-drop risk + DOWN confirmation + range support -> WATCH_DOWN
```

---

### 3. Quantile Range Reward/Risk

Purpose:

```text
Use expected high/low/range as a veto and zone, not just a visual.
```

Live role inside champion:

```text
thin reward room -> risk flag
80% band -> displayed zone
downside room -> required before WATCH_DOWN
```

Validation:

```text
Signed quantile 80% band coverage: about 87.5%
Average band width: about 30.1 bps
```

---

### 4. Champion Validator

Purpose:

```text
Produce one strict decision from all heads.
```

It reads:

```text
current price
current side vs price-to-beat
P(hold)
big_move_tier
big_drop_risk
expected_move_range
live direction lean
optional market ask/spread
```

It outputs:

```text
action
label
confidence
reason
risk_flags
invalidate
zone
bet_candidate
edge
```

Possible actions:

```text
AVOID
WAIT
AVOID_LONG
WATCH_DOWN
SETUP
LEAN
NO_EDGE
PAPER_BET
```

Important rule:

```text
Without a live market ask, champion can show SETUP/WAIT/AVOID,
but it cannot authorize a bet candidate.
```

The only paper-bet path is:

```text
fair_value - ask - costs - buffer > required_edge
```

---

## Verification Performed

Commands run:

```powershell
python -m py_compile backend\decision_champion.py backend\train_bigmove_keeper.py backend\train_bigdrop_keeper.py backend\head_probability_buckets.py backend\price_to_beat.py backend\train_heads.py
node --check src\main.js
python backend\train_heads.py --dry-run
```

Results:

```text
Python compile: pass
JS parse: pass
train_heads dry-run:
  selectivity      SKIP up-to-date
  signed_quantile  SKIP up-to-date
  persistence      SKIP up-to-date
  bigmove          SKIP up-to-date
  bigdrop          SKIP up-to-date
```

Direct champion smoke scenarios:

| Scenario | Expected | Result |
|---|---|---|
| Missing price | `AVOID` | Pass |
| Quiet window | `WAIT` | Pass |
| High drop risk with UP | `AVOID_LONG` | Pass |
| Strong P(hold), no ask | `SETUP` | Pass |
| Ask below fair value after buffer | `PAPER_BET` | Pass |

Operational note:

`train_heads.py --dry-run` also reported the legacy `beat` head as missing. That is not part of this specialist-head change. The orchestrator now skips missing legacy heads by default to keep boot fast. Set `BTC_TRAIN_LEGACY_HEADS=1` only when you intentionally want to rebuild old research heads.

---

## What Changed In The UI

The price-to-beat card now has:

```text
Champion validator strip
Big-drop risk line
Plain-English reason
Risk flags
80% expected price zone when available
Invalidation rule
```

The UI wording was cleaned so the champion strip uses ASCII text rather than garbled special characters.

---

## What This Does Not Yet Prove

This implementation improves the decision architecture, but it does not prove profitable trading.

Still required:

```text
Persistent recorder data
live Polymarket ask/spread/depth
edge table after costs
paper-agent results
micro-live validation
```

The model is an input. The edge exists only when:

```text
calibrated fair value > market ask + costs + safety buffer
```

---

## Next Best Improvements

1. Run the app after restart and confirm the champion strip appears on 5m/15m cards.
2. Keep the recorder running until there are enough resolved rounds.
3. Run the edge analyzer after enough recorded rounds.
4. Add persistent bucket tables into DuckDB, not just parquet/markdown.
5. Add a dedicated UI panel showing each head's vote:

```text
Big move: quiet/moderate/elevated/likely
Big drop: low/elevated/high
P(hold): percent and tier
Range: thin/healthy/wide
Champion: wait/setup/avoid/paper-bet candidate
```

6. Train a meta-skip model only after enough live resolved examples exist.

---

## Final Status

The model ownership plan is implemented for the first deployable slice:

```text
big-move timing
big-drop risk
P(hold)
quantile reward/risk
strict champion validator
plain-English card output
```

The remaining roadmap is not "add more direction models." It is:

```text
prove live edge after market price and costs
then paper trade
then micro-live only if paper trading survives
```

---

## Second Slice Implemented

Additional implementation completed on 2026-06-17:

| Component | Status | Files |
|---|---|---|
| Big-move isotonic calibration | Implemented | `backend/train_bigmove_keeper.py`, `data/saved_models/bigmove_keeper_model.pkl` |
| Big-drop isotonic calibration | Implemented | `backend/train_bigdrop_keeper.py`, `data/saved_models/bigdrop_keeper_model.pkl` |
| Separate `big_up` / `big_down` confirmation heads | Implemented | `backend/train_directional_keeper.py`, `data/saved_models/directional_keeper_model.pkl` |
| Activity/range specialist head | Implemented | `backend/train_activity_keeper.py`, `data/saved_models/activity_keeper_model.pkl` |
| Live specialist-head loading | Implemented | `backend/price_to_beat.py` |
| Symmetric `WATCH_UP` branch | Implemented | `backend/decision_champion.py` |
| Hard conflict skip rules | Implemented | `backend/decision_champion.py` |
| Champion snapshot persistence | Implemented | `backend/database.py`, `champion_snapshots` table |
| Data-gated champion meta-model trainer | Implemented | `backend/train_champion_meta.py` |
| Optional runtime meta reject filter | Implemented | `backend/decision_champion.py` |
| UI rows for directional/activity heads | Implemented | `src/main.js` |
| Bucket report expanded to all new heads | Implemented | `backend/head_probability_buckets.py` |

### Latest Head Metrics

Training and scorecard results from this implementation pass:

| Head | OOF / OOS AUC | Top 5% event rate | Role |
|---|---:|---:|---|
| Big move | `0.733` | `~58.7%` | Movement/timing gate |
| Big drop | `0.751` | `~63.7-64.0%` | Downside risk veto |
| Big up confirmation | `0.712` | `~32.0-32.4%` | Confirmation only |
| Big down confirmation | `0.710-0.711` | `~32.2-32.6%` | Confirmation only |
| Activity/range | `0.868-0.869` | `~91.2-91.3%` | Strong activity filter |
| Signed quantile band | n/a | `87.5%` coverage | Price zone / reward-risk band |

Important interpretation:

```text
Activity/range is strong enough to be a real filter.
Big-drop is strong enough to be a long-veto / downside warning.
Big-move is useful as a timing gate.
Big-up and big-down are not strong enough to trade alone; they only explain confirmation/conflict.
```

### Champion Behavior After Second Slice

The champion now reads:

```text
P(hold)
big_move_tier
big_drop_risk
big_up_tier
big_down_tier
activity_tier
regime
quantile reward/risk band
live model lean
optional Polymarket ask/spread
optional learned meta-champion probability
```

New behavior:

```text
HIGH big-down while price is UP and P(hold)<93% -> WAIT
HIGH big-up while price is DOWN and P(hold)<93% -> WAIT
quiet activity and P(hold)<93% -> WAIT
model side disagrees with live price side and P(hold)<93% -> WAIT
UP side + model UP + HIGH big-up + P(hold)>=85% + room -> WATCH_UP
trained meta-champion below 55% -> WAIT - meta rejected
```

### Meta-Champion Status

The meta-model trainer is implemented but intentionally data-gated:

```text
python backend/train_champion_meta.py
```

Current result:

```text
[champion-meta] not enough resolved snapshots: 0 < 500. Skipping.
```

This is correct. The app must first collect resolved `champion_snapshots` rows from real live operation. Once enough rows exist, the meta model trains on:

```text
horizon
seconds_left
current_move
P(hold)
big-move / big-drop / big-up / big-down / activity probabilities
specialist tiers
regime
champion action
champion confidence
```

Target:

```text
Did the current price-to-beat side hold to resolution?
```

### Verification Added

Commands run after the second slice:

```powershell
python -m py_compile backend\train_bigmove_keeper.py backend\train_bigdrop_keeper.py backend\train_directional_keeper.py backend\train_activity_keeper.py backend\train_champion_meta.py backend\price_to_beat.py backend\decision_champion.py backend\database.py backend\train_heads.py backend\head_probability_buckets.py
node --check src\main.js
python backend\train_heads.py --dry-run
python backend\train_bigmove_keeper.py
python backend\train_bigdrop_keeper.py
python backend\train_directional_keeper.py
python backend\train_activity_keeper.py
python backend\train_champion_meta.py
python backend\head_probability_buckets.py
```

Results:

```text
Python compile: pass
JS parse: pass
Champion smoke: WATCH_UP / WAIT conflict / SETUP pass
Specialist models saved
Meta champion skipped safely until 500+ resolved snapshots
Missing legacy beat head skipped by default; set BTC_TRAIN_LEGACY_HEADS=1 to build
Bucket report written to docs/active/HEAD_PROBABILITY_BUCKETS_2026-06-17.md
Bucket parquet written to data/head_probability_buckets.parquet
```

---

## Third Slice Implemented: Horizon-Aware Dollar Heads

Additional implementation completed on 2026-06-18:

| Component | Status | Files |
|---|---|---|
| Shared keeper-head trainer utility | Implemented | `backend/keeper_head_training.py` |
| Big-move head by horizon | Implemented | `backend/train_bigmove_keeper.py` |
| Big-drop head by horizon | Implemented | `backend/train_bigdrop_keeper.py` |
| Big-up / big-down heads by horizon | Implemented | `backend/train_directional_keeper.py` |
| Activity/range head by horizon | Implemented | `backend/train_activity_keeper.py` |
| Live per-horizon head selection | Implemented | `backend/price_to_beat.py` |
| Live move-bucket metadata | Implemented | `move_buckets_usd`, `move_threshold_usd` on each pending price-to-beat round |
| Polymarket specialist guide | Implemented | `public/polymarket-specialist-guide.html` |

The specialist heads now target:

```text
1m, 3m, 5m, 7m, 10m, 15m, 30m
```

The default move buckets are:

```text
BTC_MOVE_BUCKETS_USD_BY_HORIZON=1:10|20|40;3:20|35|70;5:30|60|100;7:40|80|140;10:50|100|180;15:60|120|300;30:100|200|600
```

Each horizon has three dollar boundaries:

```text
quiet       < first value
meaningful >= first value
large      >= second value
extreme    >= third value
```

The binary specialist heads still train on the first boundary (`meaningful`) so the labels stay simple and stable. The full bucket map is saved in each model bundle so the live app can explain whether a move is normal, meaningful, large, or extreme.

Default bucket interpretation:

| Horizon | Quiet | Meaningful | Large | Extreme |
|---|---:|---:|---:|---:|
| 1m | `< $10` | `$10-$20` | `$20-$40` | `>= $40` |
| 3m | `< $20` | `$20-$35` | `$35-$70` | `>= $70` |
| 5m | `< $30` | `$30-$60` | `$60-$100` | `>= $100` |
| 7m | `< $40` | `$40-$80` | `$80-$140` | `>= $140` |
| 10m | `< $50` | `$50-$100` | `$100-$180` | `>= $180` |
| 15m | `< $60` | `$60-$120` | `$120-$300` | `>= $300` |
| 30m | `< $100` | `$100-$200` | `$200-$600` | `>= $600` |

This reflects the fact that a meaningful 5m move can be around `$30-$100`, while 15m and 30m need wider bands and can occasionally produce much larger shocks. Override the bucket map before startup if market volatility changes.

Important distinction:

```text
5m and 15m are the real Polymarket-style horizons in the UI.
1m, 3m, 7m, 10m, and 30m are practice/evidence mirrors unless a matching tradable market exists.
```

The next app start will retrain these four specialist heads once because their `HEAD_VERSION` changed to the bucket-aware build. After that, `train_heads.py` will skip them again until the version changes.
