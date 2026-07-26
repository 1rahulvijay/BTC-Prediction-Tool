# Decision lockdown + P(hold) calibration — 2026-07-26

**Status: implemented, tested, and behaviour-changing. Read section 1 before running the app.**

Two linked pieces of work:

* **Lockdown** — the champion may no longer authorize a paper bet from a probability that is
  measurably wrong, may no longer size with Kelly, and may no longer silently swap models mid-run.
* **Calibration** — a challenger that measures how wrong P(hold) is and how much of that is
  repairable, plus a daily monitor that decides which heads are still allowed to affect a decision.

The lockdown is what the evidence demanded. The calibration is what unlocks it again — and only
by an explicit operator decision, never automatically.

---

## 0. The evidence that forced this

From 6,725 officially resolved rounds over the 21-day Oracle deployment
(`HEAD_CALIBRATION_2026-07-25.md`):

| finding | value |
|---|---|
| P(hold) predicted vs realized | **96.1% vs 89.3%** (−6.7pp) |
| the band where the app acts (90–95%) | realizes **81.2%** |
| the 85–90% band | realizes **72.3%** |
| `PAPER` action bucket held | **69.4%** |
| `WAIT` action bucket held | **89.6%** |
| flip-risk skill (BSS) | **+0.002** (none) |
| $20-shock skill (BSS) | **−0.013** (negative) |

`decision_champion.PHOLD_STRONG = 0.93` is the exact gate that authorized `PAPER_BET`. At a
predicted 0.93 the realized rate is ~81%. **The gate fired on a claim ~12pp optimistic, and the
tier presented as strongest was the weakest on the board.**

---

## 1. BEHAVIOUR CHANGE — `PAPER_BET` is disabled by default

`decision_champion.py` now *displays* a qualifying candidate and *refuses to authorize* it:

```text
default          NO_EDGE     "CANDIDATE UP - uncalibrated, not authorized"
BTC_ENABLE_PAPER_BET=1       PAPER_BET, paper_quantity=1, no Kelly stake
  + BTC_ENABLE_KELLY_SIZING=1  PAPER_BET with a Kelly stake fraction
```

Both switches default **off** and are explicit operator overrides. Neither should be turned on to
"see more signals."

**Consequence for the live paper ledger:** with defaults, the champion contributes no new
`PAPER_BET` rows. The frozen `LATE_LEADER_30S_V1` rule is unaffected — it does not consult
`p_hold` and continues logging normally, so its 8-week evidence clock is not disturbed.

### Kelly disabled → fixed quantity 1

Kelly multiplies a miscalibrated probability into a position size, and measured EV moves *more*
negative with size (−0.26c/share at 25, −1.48c/share at 250). In testing this setup, Kelly sized at
**the full 10% bankroll cap** on a probability 12pp optimistic — the concrete version of the risk.

Capacity belongs to a ladder-walking function, not a bankroll fraction. Until one exists,
`paper_quantity = 1`.

### Frozen artifacts are now immutable

`BTC_FREEZE_MODEL=1` exists so an evidence run measures ONE bundle — but every loader in
`price_to_beat.py` hot-reloaded on mtime, so any other process replacing a `.pkl` silently swapped
the model mid-run, and the live evidence would describe two different models with no record of the
change.

Under freeze, the first successfully loaded artifact is authoritative for the life of the process.
A later change is refused and alerted once, loudly:

```text
[frozen] ARTIFACT CHANGED WHILE FROZEN - refusing to load: persistence_model (P(hold)) |
serving sha=564879a6… (mtime …559), on disk sha=1c21dcce… (mtime …560) |
evidence from this process describes the SERVING artifact
```

Applied to **all five** loaders: `persistence_model`, `path_forecaster`, `fade_model`,
`window_favorability`, `signed_quantile_model`. Verified end-to-end for two of them (a frozen
process refuses the swap; an unfrozen one still hot-reloads); the other two returned non-dict test
stubs so the guard was present but not behaviourally exercised.

---

## 2. P(hold) recalibration challenger

`backend/phold_challenger.py` — fits a calibrator and scores it head-to-head against
raw. It is a challenger in the strict sense: **it never writes into the serving path, never selects
a threshold, and is adopted only by an explicit operator decision.**

Frozen protocol: separate calibrators per horizon · ≥500 independent resolved rounds · one
observation per round (never per tick — pooling inflates n ~13×) · broad time-left groups only ·
logistic first, isotonic only at ≥2000 rounds · rolling window · **temporal split, never random**.

### Result on the real 21-day data

| 5m (n=5,079) | raw | calibrated (isotonic) |
|---|---|---|
| predicted vs realized | 95.5% vs 86.8% (**+8.70pp**) | 87.4% vs 86.8% (**+0.57pp**) |
| Brier | 0.11151 | **0.10264** |
| log-loss | 0.93983 | **0.34300** |
| ECE | 0.08833 | **0.01361** |
| **skill (BSS)** | +0.0268 | **+0.1043** |

| 15m (n=1,646) | raw | calibrated (logistic) |
|---|---|---|
| predicted vs realized | 98.0% vs 95.7% | 96.0% vs 95.7% |
| ECE | 0.02668 | **0.00498** |
| skill (BSS) | +0.1127 | **+0.1577** |

All four groups return `CHALLENGER_WINS` — beating raw on Brier **and** log-loss **and** ECE.
Adoption requires all three, because a calibrator that improves ECE while worsening log-loss has
moved the bias into the tails, which is not an improvement.

**The important reading is not the calibration fix — it is that skill roughly quadrupled** (BSS
+0.027 → +0.104 at 5m). The raw head carried usable information all along; the scaling was throwing
most of it away. That is an argument for recalibrating rather than replacing it.

**Still not applied.** `applied_to_serving: false` is emitted in every result.

---

## 3. Head health — which heads may affect a decision

`backend/monitoring/head_health.py` — daily state per head, from live outcomes:

| state | may price | may rank | may show as confidence |
|---|---|---|---|
| `USABLE` | yes | yes | yes |
| `CALIBRATION_ONLY` | **no** | yes | no |
| `DRIFTED` | no | yes | no |
| `DISABLED_NO_SKILL` | no | **no** | no |
| `SHADOW` / `INSUFFICIENT_DATA` | no | no | no |

Rules: `BSS <= 0` → cannot influence a decision (predicting the base rate would have scored as
well). `BSS > 0` but `ECE > 0.05` → may rank, may not supply fair value. `n < 500` → shadow only.
It **never retrains and never edits a threshold** — it fails closed and reports; the fix is an
offline challenger.

### Result on the real data

```text
head          n       state              BSS      ECE     pred    real   permissions
p_hold        6725    CALIBRATION_ONLY   +0.0440  0.0678  96.1%   89.3%  rank
flip_risk     6725    CALIBRATION_ONLY   +0.0430  0.0655   4.2%   10.7%  rank

CHAMPION ACTION TIERS (as presented, strongest first)
   PAPER_BET    n=433    realized=64.0%
   SETUP        n=2386   realized=99.4%
   WAIT         n=1601   realized=71.4%
   NO_EDGE      n=2093   realized=96.6%
   monotone=False   may_display_as_confidence=False
```

**`PAPER_BET` realizes 64.0% while `SETUP` realizes 99.4% and `NO_EDGE` 96.6%.** The tier that
authorizes action is the worst on the board. That independently confirms section 1: disabling
`PAPER_BET` was not caution, it was the live data's verdict.

---

## 4. Tests

```text
phold_challenger --selftest   13 assertions   PASS
head_health --selftest         7 assertions   PASS
```

Both suites assert the failure modes rather than the happy path: a known-overconfident head must be
detected *and* repaired; an already-calibrated head must **not** be "improved" (no free lunch,
ΔBrier < 0.004); n<500 refuses to fit; isotonic is withheld below 2000 rounds; a winning challenger
is **still** not applied; inverted tiers (`PAPER` 69.4% < `WAIT` 89.6%) are refused as a confidence
scale.

### Two corrections made to my own work

Both times the code was right and the **test** was wrong; both are now permanent assertions:

1. A head-health fixture using `tp + (1−tp)·0.55` landed in `DISABLED_NO_SKILL`, not
   `CALIBRATION_ONLY`. Correct — that distortion destroys all Brier skill. Miscalibration severe
   enough to kill skill is not "needs recalibration", it is "carries no usable information". The
   fixture was retuned to the *real* failure mode (BSS stays positive, ECE blows past the pricing
   threshold), which is what live P(hold) actually does.
2. A `PAPER_BET` test that looked like a failure was the learned meta-filter vetoing downstream — a
   second, independent gate working as intended.

### Audit corrections after the first report

- Calibration and head-health queries now accept only `settlement_source LIKE 'official:%'`.
  Two non-official rows were removed; this is why the final sample is 6,725 rather than 6,727.
- Recent-window drift is ordered by snapshot time before taking the trailing 30%. The previous
  query relied on unspecified SQL row order and did not guarantee a chronological window.
- The shared decision composer fails closed when execution mode or execution cost is unknown.
  Its old default (`MAKER_MAKER` with zero cost) could manufacture a pass before maker fillability
  had been proved.
- Challenger v2 applies the all-three-metric eligibility gate before using Brier as a tiebreaker.
  V1 could select the lowest-Brier candidate first and reject it for log-loss/ECE even if another
  candidate legitimately improved all three metrics.
- Production can require `BTC_ADMIN_TOKEN` mechanically:
  `BTC_DEPLOYMENT_ENV=production` defaults `BTC_REQUIRE_ADMIN_TOKEN=1`, and startup aborts if the
  token is missing. Development remains locally usable unless the requirement is explicitly enabled.

---

## 5. What this does NOT do

- It does **not** apply the calibrator. `PAPER_BET` stays disabled until a calibrated probability
  is served *and* wins forward on rounds it never saw.
- It does not yet consume `head_health` *everywhere*. As of 2026-07-26 the **pricing** permission is
  enforced in the champion (§5b below); `may_rank` and `may_display_confidence` are still advisory,
  so a `CALIBRATION_ONLY` head can still shape ordering and the displayed tier.
- `DEFAULT_ENTRY_FAIR_CAP = 0.91` still derives from a **raw** P(hold) lower bound, so any research
  reading `fair_value` inherits the bias even with betting disabled. Fixing that belongs with the
  calibrator's adoption, not as a separate patch — two competing corrections would be worse than
  one known one.
- `DecisionEnvelope` and the Champion action relabelling are **not** implemented. Section 3 makes
  the relabelling urgent and evidence-backed: the tiers are demonstrably not a confidence scale.

---

## 5b. Head-health enforcement (Blueprint §31.2) — added 2026-07-26

`backend/head_permissions.py` is the reader that was missing. `monitoring/head_health.py` had been
computing per-head permissions from live outcomes and writing them to
`data/research/head_health/head_health.json`, and **nothing consumed them** — so a head measured as
unable to price could still price.

**What is enforced.** In `decision_champion.py`, the `PAPER_BET` branch now additionally requires
`may_price("p_hold")`. The point is narrow and specific:

> `BTC_ENABLE_PAPER_BET=1` is an operator override. Before this change, that one flag also
> re-enabled betting on exactly the probability the live data says cannot supply a fair value.
> The switch was a way to overrule the evidence. Now it can only act on a head that currently
> measures as `USABLE`.

Measured today, against the real report, the override is inert — which is the correct outcome:

```
p_hold        CALIBRATION_ONLY    price=False  rank=True   ECE 0.0678 > 0.05
flip_risk     CALIBRATION_ONLY    price=False  rank=True   ECE 0.0655 > 0.05
```

| scenario | result |
|---|---|
| default (override off) | `NO_EDGE` — "CANDIDATE UP - uncalibrated, not authorized" |
| override **on**, `p_hold=CALIBRATION_ONLY` | `NO_EDGE` — "CANDIDATE UP - **p_hold may not price**" |
| override on + `BTC_ENFORCE_HEAD_HEALTH=0` | `PAPER_BET`, qty 1 (deliberate observe-only escape hatch) |

**Design choices worth keeping.**

- *Fail-open with a reason.* A missing or unreadable report returns permissive **plus** a reason
  string ("permissions not measured"). This module must never be able to take serving down by
  failing to find a file — but a missing measurement must never read as a passing grade either.
- *Stale is not evidence.* A report older than 14 days reports `STALE` rather than being trusted.
- *The gate re-opens by itself.* Nothing has to be un-done by hand: when a head returns to `USABLE`
  in the next report, pricing is permitted again. That is why enforcement is safe to leave on.
- *The check cannot crash the decision path.* The import and call are wrapped; on any exception the
  champion proceeds as before.
- `ENFORCED` is read at import time, matching `PAPER_BET_ENABLED`. Set the env var in `start.bat`
  before launch — toggling it inside a running process has no effect.

Verified: `head_permissions --selftest` 6/6, `test_paper_trading_integrity` PASS,
`test_collector_integrity` PASS, `executable_fill_engine --selftest` PASS,
`venue_admissibility --selftest` PASS, compile sweep clean.

---

## 6. Operating notes

| switch | default | meaning |
|---|---|---|
| `BTC_ENABLE_PAPER_BET` | `0` | champion may authorize a paper bet from raw P(hold) — now *also* requires `p_hold` to be permitted to price |
| `BTC_ENFORCE_HEAD_HEALTH` | `1` | a head measured as unable to price may not price. `0` = observe only |
| `BTC_ENABLE_KELLY_SIZING` | `0` | Kelly stake fraction instead of fixed quantity 1 |
| `BTC_FREEZE_MODEL` | `1` | artifacts pinned at first load; changes refused and alerted |
| `BTC_DEPLOYMENT_ENV` | `development` | set `production` on an exposed host |
| `BTC_REQUIRE_ADMIN_TOKEN` | `0` development / `1` production | startup fails if required and token is absent |
| `BTC_ADMIN_TOKEN` | unset locally | gates `/api/relearn`, `/api/backtest`, `/api/historical-replay/run` |
| `BTC_DISABLED_PAPER_RULES` | empty | comma-separated rule names to silence (changes no threshold) |

Run the two monitors against a snapshot, never the live writer DB:

```bash
python backend/phold_challenger.py --db data/btc_duckdbs/analytics.duckdb
python backend/monitoring/head_health.py --db data/btc_duckdbs/analytics.duckdb
```
