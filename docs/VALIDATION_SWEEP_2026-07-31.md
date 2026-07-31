# Codebase Validation Sweep — 2026-07-31

Full validation of code, models, heads and business logic. Every number below was produced by
running the thing described; the command is given so any line can be re-checked.

**Verdict: the code and its guard rails are correct. The models are stale. The test gate had a
hole, now closed.** Those are three separate statements and conflating them is how "everything
passes" gets said about a system that cannot serve.

---

## 1. What passes

| check | command | result |
|---|---|---|
| CI gate | `python backend/run_ci_locally.py` | **72/72** (231 s) |
| pytest suites | `python -m pytest -q` | **86 passed** (was 5 collected) |
| compilation | `python -m compileall -q backend research microstructure` | exit 0 |
| frontend | `npm run build` | 597 ms, 370 kB / 113 kB gzip |
| server import | `python -c "import server"` | clean, `app` present |
| research suite | `python research/run_all_sequence.py` | 39 scripts, 0 non-zero, **0 positive OOS** |
| CI-unreachable scripts | 30 run directly | 22 OK, 5 timeout (>420 s studies), 3 fail |

### Safety boundaries, probed rather than assumed

```
trading_authority.assert_may_place_real_order('binance',   ...)  -> REFUSED
trading_authority.assert_may_place_real_order('polymarket', ...) -> REFUSED
  gaps: BTC_REAL_TRADING_AUTHORIZED not set to the acceptance phrase
        BTC_CONTROL_TOKEN not set in the deployment environment
        venue 'binance' has no active capability grant
```

`production_readiness.py` → **BLOCKED, 17 prerequisites failed. No server started, no artifact
changed.** `head_permissions.py` → enforcement **ON**; `p_hold` and `flip_risk` correctly demoted
to `CALIBRATION_ONLY` (ECE 0.0678 / 0.0655 against a 0.05 gate) — they rank, they do not price.

The failure path is fail-closed and was exercised, not read from a document.

---

## 2. The gate defect: CI never ran pytest

Two layers, and the second is why it survived so long.

1. `python -m pytest` appeared **nowhere** in `.github/workflows/invariants.yml`.
2. `pytest.ini` limited `testpaths` to `tests` and `backend/venues` — **5 tests collected while
   93 existed** (86 under the committed config, which excludes the 7-test
   `test_feed_writer_load.py` that CI already runs as a script — see section 4).

**Why it was invisible.** A missed pytest-style test does not fail loudly. Run one as
`python <file>` and it executes no test function and exits 0 — a clean pass that verified
nothing. `backend/research/test_binance_maker_conversion.py` is the proof:

| invocation | result |
|---|---|
| `python backend/research/test_binance_maker_conversion.py` | 0.2 s, **exit 0, no output, 0 of 29 assertions run** |
| `python -m unittest ...` | `Ran 0 tests` |
| `python -m pytest <file>` | **4 passed** |

Those 29 assertions had never once executed.

**Fixed:** `testpaths` broadened to `tests + backend + research`; a gating CI step runs
`python -m pytest -q`.

One detail worth recording: my first attempt put the exclusions under an `ignore =` ini key.
**pytest has no such option — it warns and continues**, so the exclusions would have been
silently inert. That is the identical failure mode being fixed, caught only by re-running.
Exclusions now live in `addopts` as `--ignore=`.

---

## 3. Defects found and fixed

### 3.1 Tests that failed depending on how they were launched

Two files raised `ModuleNotFoundError` as scripts and passed as modules. A runner that launches
them the other way records a **product failure that does not exist**.

| file | as script (before) | as script (after) |
|---|---|---|
| `backend/venues/test_deribit_option_chain_recorder.py` | exit 1 — `No module named 'backend'` | exit 0 |
| `tests/test_polymarket_market_prior_residual_v1.py` | exit 1 — `No module named 'research'` | exit 0 |

Fixed by putting the repository root on `sys.path` so both invocations are equivalent. The first
is the **newest code in the repository** — the Deribit chain recorder's own test.

### 3.2 A vacuous pass on missing input

`backend/research/test_complement_and_opening_drift.py` printed `missing <path>` and returned —
**exit 0**. Its input was absent, it verified nothing, and it reported success.

Two sibling studies in the same directory hit the same missing archive and correctly **exit 1**.
This one silently disagreed with them.

Fixed twice over, because one fix alone was not enough:
- the missing-input branch now prints `REFUSED:` and returns 1;
- `if __name__ == "__main__": main()` **discarded the return value**, so even an explicit refusal
  would still have exited 0. Now `raise SystemExit(main())`.

### 3.3 Functions pytest misread as fixture requests

Ordinary analysis routines whose names began with `test_` and took real positional arguments:

```
test_opening_drift(mk, tk, L)   ->  E  fixture 'mk' not found
test_sustained_rate(seconds)    ->  E  fixture 'seconds' not found
```

Renamed to `analyse_complement`, `analyse_opening_drift`, `run_sustained_rate` — they are
analysis and load drivers, not tests, and now say so.

### 3.4 Files named `test_*.py` that cannot fail

**14 of 30** CI-unreachable files named `test_*.py` contained **zero assertions**. They are
research studies: they load data, compute statistics, write a markdown report. Nothing in them
can fail, so any runner records `OK`, and a reader seeing 30 green test files believes 30 things
were verified when 14 verified nothing.

New gate: `backend/test_naming_honesty.py`. Every `test_*.py` must either contain a way to fail
(assert / raise / failing exit path) or be listed in an explicit `STUDIES` set. A **new**
zero-assertion test file fails CI, so the list cannot grow silently. It also fails on a **stale**
entry — a declared study whose file no longer exists — because a list that outlives its files
stops being read.

Negative-tested both ways: dropping in a zero-assertion `test_*.py` fails; pointing `STUDIES` at
a non-existent file fails. The check also self-tests that it can detect an offender before it
reports anything.

---

## 4. Deliberately not fixed, with the reason

| item | why |
|---|---|
| 3 files excluded from pytest collection (`test_serving_integration`, `test_120d_conditional_ev_pipeline`, `test_120d_trade_policy_heads`) | They fail pytest **collection** on relative imports (`attempted relative import beyond top-level package`) that resolve correctly under `python -m`, which is how CI already runs them, where they pass. Rewriting those imports to satisfy pytest would risk the invocation CI actually depends on. They are gated — just not by this step. |
| `backend/test_feed_writer_load.py` excluded from pytest | CI runs it as a script, where `main()` executes all seven test functions **plus** `run_sustained_rate()` — a strict superset of what pytest collects. Including it doubled the pytest step from 7 s to 54 s for zero extra coverage. Its fixture collision is fixed regardless. |
| 5 scripts exceeding a 420 s timeout | `test_180d_anchor_roundtrip_strategy`, `test_180d_decision_heads`, `test_180d_path_dynamics`, `test_180d_round_state_and_stopping`, `test_5m_15m_30d`. Long research studies, not unit tests. They belong in an operator-run lane, not a CI gate that must stay minutes long. |
| 2 scripts failing on missing Kaggle data | `test_polymarket_structural_edges`, `test_virtue_complexity_late_leader` — `FileNotFoundError` on `Kaggle Data/archive (7).zip`. Missing **input**, not a code defect, and both fail **loudly**, which is the correct behaviour. |
| 14 studies not renamed to `study_*.py` | The clean fix is a wide rename across files owned by work in flight. The declared-list gate is the honest intermediate and blocks new offenders today. |

---

## 5. What remains genuinely wrong: the models

```
[model-preflight] INCOMPATIBLE saved main ensemble
  saved:   2026-06-15-v11-pruned69-7977e0559560-2horizon-5-15-rf-persist-...
  current: 2026-07-31-v14-pruned63-864622d65e85-2horizon-5-15-rf-persist-...
```

`python backend/check_feature_contract.py` → **0 STALE, 12 UNKNOWN of 12 present artifacts.**
Every artifact was trained under an unrecorded VWAP version. VWAP changed twice:

- **v1 → v2**: cumulative-from-bar-0 → trailing time-anchored. A v1-trained model consumed a
  near-constant VWAP column and is now fed a materially different one.
- **v2 → v3**: bar-count window → true duration window (a 6 h gap made a "1440 bar" window span
  29.98 h).

`python backend/production_readiness.py` → **0/11 heads would load** under strict identity; all
lack manifests. Calibration inactive on both 5m and 15m.

This is train/serve skew: **it will not raise, it will just be quietly wrong.**
`BTC_STRICT_ARTIFACT_IDENTITY` must stay 0 until a retrain writes manifests — enabling it today
would make the app serve blind while logging one error per artifact.

Unblocking requires a full retrain plus explicit challenger promotion. Neither is automatic, and
neither is done here: promotion stays a deliberate, gated act.

---

## 6. What this sweep does not establish

- **That the business logic is semantically correct** beyond what its tests encode. Coverage is
  now measured and gated, which is a different and weaker claim than correctness.
- **That any model's forecasts are accurate.** The 39 research scripts already answered that:
  0 positive out-of-sample.
- **That the system is profitable.** Nothing found in the research is wired, and nothing should
  be — there is no candidate.

## 7. Standing constraints, unchanged

Real orders **DISABLED**. Trading mode **PAPER / SHADOW ONLY**. No funded credentials in
testing. One DuckDB writer. Thresholds declared before results are seen. No promotion without
preregistered protocol, fold-local fitting, untouched chronological evaluation, day-block lower
confidence bounds, a complete cost model and forward shadow evidence.

## 8. Reproduce

```bash
python backend/run_ci_locally.py
python -m pytest -q
python backend/test_naming_honesty.py
python backend/check_feature_contract.py
python backend/production_readiness.py
python research/run_all_sequence.py
```
