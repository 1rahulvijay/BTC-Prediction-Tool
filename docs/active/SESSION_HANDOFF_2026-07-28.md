# Session Handoff — 2026-07-28

For the next Claude session. Read this first, then run the state command in §1.
Everything here is what you cannot reconstruct from the code alone.

---

## 1. Get the real state before doing anything

```bash
python backend/report_master_runtime_state.py
```

That regenerates `data/reports/MASTER_RUNTIME_STATE.{json,md}` — commit, serviceable
artifacts, archive rows, calibration mode, migration progress. **Trust it over any prose,
including this document.** Hand-written state goes stale; that report does not.

Then read `docs/active/MASTER_STATE_AND_ROADMAP_2026-07-28.md` §1–10 for the stable
research conclusions (what was measured, what was refuted, what is deferred and why).

At handoff: `master` = `5cf57de`, pushed, tree clean, **no GitHub Actions run has ever
executed** — every test result in the repo is a local exit code.

---

## 2. Your job: `ARTIFACT_FOUNDATION_V1`

This is the open P0. Right now `check_feature_contract.verdict_for()` can prove an
artifact's identity, `--enforce-serving` reports `0/12 serviceable` — and **no loader
calls it**. Stale models still load and serve silently.

**Commit A only.** Do not migrate save/load paths in the same commit.

- `backend/model_registry.py` — one canonical `ModelRegistryEntry` list. It must drive
  save names, load verification, readiness, startup health and tests. The current
  hardcoded 12-artifact list in `check_feature_contract.py` is what this replaces; a new
  model added to that list-by-hand is a model that bypasses enforcement.
- `backend/model_artifacts.py` — the only module allowed to serialize. Immutable staging
  directory → fsync members → hash members → write manifest → write `MANIFEST_SHA256` →
  read back and verify → **atomic directory rename** → atomic champion-pointer update.
  Never overwrite a serving bundle in place.
- Verification **before deserialization**. Do not `joblib.load()` an unknown pickle and
  then check whether it was valid — that executes it first.
- Typed refusals: `MODEL_UNAVAILABLE_{MISSING,UNKNOWN_IDENTITY,STALE_ARTIFACT,TAMPERED,
  MIXED_BUNDLE,WRONG_TARGET,UNAUTHORIZED,INCOMPLETE_BUNDLE}`.
- Fault-injection tests: crash before/during/after each write stage, reader during staged
  write, reader during promotion, concurrent writers, interrupted champion pointer. A
  reader must see the complete old bundle or the complete new one — never a mix.

Final status line must say **`FOUNDATION_ONLY`**. It is not enforcement until B, C and D.

### Then, in separate commits

- **B** — migrate all **25** save paths. Bypass scan → 0 authoritative direct saves.
- **C** — migrate all **57** load paths, behind `BTC_ENFORCE_VERIFIED_MODEL_LOADING=0`.
- **D** — activate. `DEGRADED_MODEL_BLOCKED`: recording, reporting, health and
  **reduce-only position management** stay available; predictions, pricing, ranking,
  sizing and new entries are blocked.

**The invariant:** no loader enforcement goes live until every trainer writes manifests.
Half-migrated is worse than not started — enforcement would permanently block everything.

---

## 3. Do not do these

**Do not retrain.** 12/12 artifacts are unprovable under feature semantics v3 / training
semantics v2. Retraining before Commit B produces another generation of unprovable
artifacts. This is the single most likely well-intentioned mistake.

**Do not make `max_taker_ask` linear.** It has been proposed four times. The Polymarket
fee is `rate·p·(1−p)` — quadratic in price. The current quadratic root matches brute force
to 1e-6; the linear form returns 0.822 where the true max ask is 0.872, rejecting
profitable bets. `report_master_runtime_state.py --selftest` asserts it stays quadratic.

**Do not enable calibration.** `BTC_PHOLD_CALIBRATION_MODE=off`. The fitted calibrators
are `deployable=false` / `SOURCE_MODEL_REQUIRES_RETRAINING` — they map the score
distribution of models that are about to be replaced.

**Do not lower `PHOLD_STRONG`.** Calibrated P(hold) saturates near 0.923, so the 0.93 gate
would never fire. Lowering it re-introduces the overconfidence calibration just removed.
Thresholds must be re-derived economically, after the retrain.

**Do not build:** RL/PPO execution, delta-neutral hedging, L3 spoof detection, wallet
copying, Rust rewrite, live/authenticated orders. Reasons are in master doc §9.

---

## 4. The trap that has fired twice

Two synthetic simulations landed in `docs/` in one day, both reporting confident wins:

- **PPO execution sandbox** — reported the agent beat naive taking by 88%. It paid a maker
  *rebate* of +1.5 bps; Binance USD-M charges a maker *fee* of 2.0 bps. Flipping only that
  sign: agent `+0.57` → `−2.88` bps. Corrected and relabelled in `5cf57de`.
- **`quant_advanced_simulator.py`** — four strategies, three conclusions refuted by their
  own tables: a "delta-neutral" portfolio with a $12,000 spread between outcomes; an IV
  filter that *underperformed* the naive baseline it claimed to save (1.3827x vs 1.4745x);
  26/49 = 53.1% (p=0.388) described as "deterministic"; and a note claiming self-exciting
  arrivals "follow a Poisson distribution" — which is the null Hawkes exists to reject.
  **Left uncorrected and uncommitted.** The user decides whether it stays.

**If you are handed a report with numbers, check the numbers against the conclusion before
acting on it.** Synthetic data generated to exhibit a phenomenon, then reported as having
discovered it, is circular — and it is currently landing in the same folder as real
results. There is no mechanism yet that distinguishes them. That is the actual argument
for finishing artifact enforcement.

---

## 5. Operating constraints (non-negotiable)

- **DuckDB is single-writer.** Never run duplicate launchers. Snapshot before research —
  use `data/btc_duckdbs/` (Oracle copies), never the live writer.
- **`data/btc_duckdbs/` is read-only evidence.** Live DBs go directly in `data/`.
- **Frozen gates, no post-hoc tuning.** A near miss is a miss. `profit_campaign_v1` and
  `event_conditional_v1` protocols are frozen; `verify_prereg_hashes.py` enforces it.
- **ASCII-only console output** (Windows cp1252).
- **A parallel session co-edits this repo** and has committed mid-flight snapshots twice.
  Reconcile file-by-file, never clobber, and commit early so your work isn't half-captured.
- **No real-money routing anywhere.**

---

## 6. Two things only the user can do

Both are blocking and independent of your work:

**Start the recorder.** `venue_events` = 0 rows, never launched. It *is* wired
(`start.bat:383` → `start_recorders_once.ps1`); no log files exist, so `start.bat` simply
hasn't run since the launcher landed. Every research family and all forward evidence
depends on it. Belongs on the always-on host — laptop sleep produces non-qualifying
episodes.

```bash
python backend/venues/multi_venue_recorder.py --report
```

**Retrain** — but only after Commit B.

---

## 7. Validate like this

```bash
python -m compileall -q backend
python -m pyflakes backend
python backend/report_master_runtime_state.py --selftest
python backend/check_feature_contract.py --enforce-serving      # exits 1 = correct today
python -m backend.research.event_conditional_v1.selftest
python -m backend.research.profit_campaign_v1.selftest
python backend/research/verify_prereg_hashes.py
```

Report exact exit codes. Never pipe a test through `tail`/`grep` and read `$?` — that is
the pipe's status, not the test's. `check_feature_contract` exiting 1 is the correct
current state, not a failure to fix.
