# Core app — validation and complete bug register

Written **2026-08-11** against HEAD `804a108`, working tree carrying 62 uncommitted files from a
parallel session.

**Authorisation for this pass: read-only.** The operator directed "right now only document, no
code modification". No source file, no configuration and **no database** was modified. Every
proposed change below is written as a patch to apply deliberately, not applied.

This register consolidates rounds 1-4 (`REPO_SCAN_FINDINGS_*`) and adds this pass's findings, so
it is the single file to read. Where an earlier round is superseded or was wrong, it says so.

---

## A. Scope — what this validates, and what it does not

Validated by execution: the local CI gate, the pytest suite, every registered selftest, and
direct read-only inspection of the live DuckDB stores and model artifacts.

**Not established.** "The entire core app has no bugs" is not a claim this pass can make and is
not made anywhere below. `backend/` is 570 modules and ~160k lines; what follows is the set of
defects that specific, targeted measurement surfaced. Absence of a finding in an area means that
area was not exercised, not that it is correct. The areas deliberately *not* swept this pass:
numerical correctness of the fee/PnL arithmetic, the calibration mathematics, regime transition
logic, and the 5,092-line frontend beyond its numeric-default handling.

---

## B. Validation results

| gate | result |
| --- | --- |
| `python -m pytest -q` | **155 passed**, 0 failed, 13 warnings |
| `backend/tests/test_terminal_outcomes_not_replaceable.py` | PASS (7 checks, 0 known-unfixed) |
| `backend/recorder_health.py --selftest` | PASS (29 checks) |
| `backend/audit/recorder_evidence_check.py --selftest` | PASS (10 checks) |
| `backend/binance_paper/*` (5 selftests, `-m` form) | all exit 0 |
| `backend/tests/run_ci_locally.py` | **PASS** — all 212 steps, 245 command executions, 629 s, exit 0 |

    LOCAL CI: PASS - every gating step in the workflow succeeded

Not run by the local runner, by design: dependency installs, and the Node frontend
lockfile/build/audit step (`--all` re-enables it). The `start.bat` launcher itself is excluded by
operator instruction.

**Two non-gating `note` lines, and one of them matters more than anything else in this register.**
Both are the same command, which appears in both workflow blocks wrapped in `|| true`:

    note [200/212] [invariants] Feature-contract readiness (stale artifacts vs current semantics)
    note [212/212] [startbat] Every invariant selftest passes on Windows

See **FC-1** below. The command exits 1; `|| true` discards that, so CI reports PASS.

---

## C. Bug register

Status key: **FIXED** = repaired in the working tree and verified by me. **OPEN** = still present.
**DECISION** = not a code defect; needs an operator choice.

### Fixed and verified during this engagement

| id | defect | where | verified how |
| --- | --- | --- | --- |
| R1-1 | Terminal-outcome fence blind to bound-parameter, no-column-list and f-string REPLACE forms | `test_terminal_outcomes_not_replaceable.py` | `TERMINAL_TABLES` rule + `ast.JoinedStr`; flagged writers now contain no REPLACE at all |
| R1-3 | 39 selftests never executed by any gate (19 core app) | `invariants.yml`, `start.bat` | `binance_paper` suite registered; 21 references now present |
| R1-5 | Registration needs opposite invocation forms per file | `invariants.yml` | registered as `python -m backend.binance_paper.*`; all 5 exit 0 |
| R1-4 | `binance_paper/types.py` shadowed the stdlib `types` module, breaking `enum` import | `backend/binance_paper/` | renamed to `paper_types.py` |
| R1-6 | `spread_norm = 0.5` fabricated on exception, grading a signal from an invented liquidity input | `model.py:2485` | fallback removed |
| R1-7 | `if seq.shape[1] > 50` silently skipped the meta-model trust filter (the branch forcing NEUTRAL) | `model.py:2860` | guard removed |
| R1-8 | Hard-coded positional feature indices (`seq[-1,15]`, `[49]`, `[50]`, `[56]`) | `model.py` | now `FEATURE_NAMES.index("spread_norm")` etc. at `model.py:51-54` |
| R2-1 | `binance_l2_recorder` clock declared a column that never existed → permanent `SCHEMA_DRIFT` | `recorder_health.py:69` | `ts_ms` → `received_ts_ms`; probe now returns a real age |
| R2-2 | Declaration drift could never fail a gate | `recorder_evidence_check.py` | now exits nonzero on `SCHEMA_DRIFT`/`UNIT_MISMATCH` — **but see OPEN-3** |
| R2-3 | `wired_recorders()` counted the audit tooling itself as recorders | `recorder_evidence_check.py:64` | skips `--selftest` and `backend\audit\`; decoy test added |
| R2-4 | `_ptb_alltime_accuracy` stamped freshness *before* the fetch, serving stale data as current forever | `server.py:1495` | failure path now marks the payload stale |
| R2-5 | `_ANCHOR_CONN` bypassed `_connect()`, losing the retry the comment above it promised | `database.py:108` | `_ANCHOR_CONN = _connect()` |

### Open

| id | severity | defect | where |
| --- | --- | --- | --- |
| FC-1 | **blocker (serving + retrain)** | All 12 serving artifacts have unrecorded feature semantics and predate three value-changing feature migrations — live train/serve skew, and the check that says so is non-gating | `data/saved_models/*.pkl` |
| OPEN-1 | **blocker (retrain)** | Canonical store lacks `head_identity_json`; `head_health` therefore refuses to certify any head | `data/btc_duckdbs/analytics.duckdb` |
| OPEN-2 | high | Canonical store is an older *schema* — missing the resolution vocabulary and whole tables | same, + `execution_layer.duckdb` |
| OPEN-3 | medium | Drift gate wired into the **selftest**, re-coupling CI to production data | `recorder_evidence_check.py` |
| OPEN-4 | medium | `l2_gaps` has never recorded a row; gap forensics lost on the path that actually fires | `venues/binance_l2_recorder.py:513` |
| OPEN-5 | medium | `TERMINAL_TABLES` is hand-maintained with no link to the schema | `test_terminal_outcomes_not_replaceable.py:38` |
| OPEN-6 | low | Binance L2: 12 of 16 sessions ended `ERROR`, 1 `SYNCED` | `binance_l2.duckdb` |
| DEC-1 | **blocker (retrain)** | 11-day hole in `price_to_beat` (2026-07-26 → 08-05) spanning both stores | data |
| DEC-2 | high | No artifact carries `may_price` authority; Polymarket EV purposes refuse | artifacts |

---

## D. Open bugs — detail and patches

### FC-1 — every serving artifact predates three value-changing feature migrations, and the check that says so cannot fail CI

This is the single most consequential finding of the pass, and it directly answers "is the app
ready to retrain": **a retrain is not optional, it is overdue.**

`python backend/check_feature_contract.py --report` (exit **1**):

    running FEATURE_SEMANTICS_VERSION : 5
    BTC_STRICT_ARTIFACT_IDENTITY      : 1 (enforcing)

    artifact                        trained under   status
    persistence_model.pkl           (unrecorded)    UNKNOWN - cannot prove it matches
    path_forecaster.pkl             (unrecorded)    UNKNOWN - cannot prove it matches
    fade_model.pkl                  (unrecorded)    UNKNOWN - cannot prove it matches
    signed_quantile_model.pkl       (unrecorded)    UNKNOWN - cannot prove it matches
    round_state_heads.pkl           (unrecorded)    UNKNOWN - cannot prove it matches
    bigmove_keeper_model.pkl        (unrecorded)    UNKNOWN - cannot prove it matches
    bigdrop_keeper_model.pkl        (unrecorded)    UNKNOWN - cannot prove it matches
    directional_keeper_model.pkl    (unrecorded)    UNKNOWN - cannot prove it matches
    activity_keeper_model.pkl       (unrecorded)    UNKNOWN - cannot prove it matches
    selectivity_models.pkl          (unrecorded)    UNKNOWN - cannot prove it matches
    champion_meta_model.pkl         (unrecorded)    UNKNOWN - cannot prove it matches
    magnitude_model.pkl             (unrecorded)    UNKNOWN - cannot prove it matches

    VERDICT
      0 STALE, 12 UNKNOWN of 12 present artifacts.

Three of the five declared semantics versions landed **after** these artifacts were built. The
`saved_models` pkls date 2026-07-02 → 07-04; `architecture_version.pkl` reads
`2026-06-15-v11-pruned69-...-136-tcn`. The changelog since:

| version | date | change |
| --- | --- | --- |
| v3 | 2026-07-28 | `vwap()` bar-count window → true duration window (a 6 h gap made a "1440 bar" window span 29.98 h) |
| v4 | 2026-07-31 | `cvd_slope_divergence` full-dataset std → causal trailing scale; cross-asset gaps no longer backfill from a future first observation |
| v5 | 2026-08-04 | three value-changing defects: `volume_profile_lvn_distance` measured the range bottom rather than a low-volume node; `time_to_funding`'s single `cos()` mapped 25% and 75% of the cycle to the same value; `cross_exchange_lead_lag` subtracted an ETH dollar change from a BTC dollar change with no lag |

Each of v2 and v5 is annotated in-source with "MUST be retrained". The script's own words for the
consequence: *"That is train/serve skew: it will not raise, it will just be quietly wrong."*

Two things compound it:

1. **The status is UNKNOWN, not STALE.** The artifacts record no `feature_semantics_version` at
   all, so the mismatch cannot even be proven — only inferred from their mtimes. `model_artifacts`
   lists `feature_semantics_version` in `REQUIRED_PROVENANCE`, and the versioned bundles do carry
   it (the `crossing_heads` bundle records `4`), so this is specifically the legacy `saved_models`
   pkl lane that records nothing.
2. **The check cannot fail CI.** `invariants.yml:823` is `python backend/check_feature_contract.py
   --report || true`. `run_ci_locally.py:114-115` faithfully preserves that as advisory. So the one
   gate that detects live train/serve skew is, by construction, incapable of turning CI red — and
   CI reported PASS on this very run while this check exited 1.

**This is the same defect class as the rest of this register**: a check that reports rather than
gates, so the condition it exists to catch persists indefinitely while the suite stays green.

**Patch — make it gate, once the retrain has landed.** Do not flip this before retraining or CI
goes red immediately (which is the honest state, but it should be a deliberate step):

```yaml
# invariants.yml:822-823 -- after a retrain has produced artifacts recording their semantics
- name: Feature-contract readiness (stale artifacts vs current semantics)
  run: python backend/check_feature_contract.py --report
```

**Patch — record the version at write time**, so the status can be STALE/OK rather than UNKNOWN.
Every trainer writing into `data/saved_models/` should stamp `feature_semantics_version` alongside
the artifact, the way the bundle lane already does. That is the change that makes this check
meaningful going forward rather than merely alarming.


### OPEN-1 / OPEN-2 — the canonical store is behind, in schema and in columns

`_resolve_db_path()` resolves to `data/btc_duckdbs/analytics.duckdb` (no env override set).
That file lacks `head_identity_json` on `round_state_snapshots` and `champion_snapshots`.
`head_health.py:148-153` detects this and raises a blocker rather than pooling unattributable
rows — correct behaviour, and the reason **no head can currently be certified**.

Beyond that column, the canonical files are consistently the older schema:

- `predictions_5m`, `predictions_15m` — missing `actual_direction`, `resolution_status`,
  `resolution_basis`, `resolution_price`, `resolution_event_ts`, `target_contract`,
  `label_version`, `neutral_band`, `release_id`, `invalid_reason`
- `price_to_beat` — missing `grade_usable`, `grading_contract`, `horizon_overlap`, `pred_contract`
- `ab_results`, `kronos_predictions`, `fsr_ppo_decisions`, `model_predictions` — missing
  `resolution_status`, `invalid_reason`
- `execution_layer.duckdb` canonical — missing whole tables: `pm_reference_prices` (43,462 rows
  in the other copy), `rejection_events` (70,795), `shadow_signals` (638),
  `pm_round_truth_attempts` (481), `round_settlement_truth`, `settlement_checkpoint`,
  `shadow_orders`, `pm_export_health`; and `pm_round_snapshots` missing 19 columns including the
  whole book surface (`up_ladder`, `down_ladder`, `up_b1/b2/b5`, book hashes, `book_age_s`,
  `decision_ts`, `artifact_hash`)
- `pm_round_meta` canonical — missing `required_reference_source`, which
  `_persist_round_truth` reads first (`live_btc_updown_recorder.py:718-723`). Against the
  canonical store every round would quarantine.

**Runbook (not run).** The migrations are additive and already declared in `database.py`
(`:791`, `:816`, and the `add_columns` blocks). Back up first — the file is ~140 MB:

```bash
copy "data\btc_duckdbs\analytics.duckdb" "data\btc_duckdbs\analytics.duckdb.bak-20260811"
```

```bash
python -c "import sys; sys.path.insert(0,'backend'); import database; database.init_db(); print(database.DB_PATH)"
```

Then re-check, expecting `present` on both:

```bash
python -c "import duckdb; c=duckdb.connect(r'data/btc_duckdbs/analytics.duckdb',read_only=True); print([r[0] for r in c.execute('DESCRIBE round_state_snapshots').fetchall()])"
```

Migrating adds the columns. It does **not** backfill them — `head_identity_json` will be
`'{}'` for existing rows, so artifact-attributed head health still needs newly collected
outcomes. That is what `head_health`'s blocker text already says.

### OPEN-3 — move the drift gate off the selftest

`recorder_evidence_check.selftest()` now calls `audit()`, which reads the live stores, and fails
on `SCHEMA_DRIFT`/`UNIT_MISMATCH`. The same file records that this coupling was deliberately
removed once (`derive_status` docstring, `:109-117`): *"A selftest that depends on production data
tells you about production, not about the code."* CI runs the selftest (`invariants.yml:590`,
`:927`), so the first genuine drift turns CI red for a data condition.

My round-2 wording invited this; the parenthetical "(or the audit's exit code)" was the right half.

**Patch — move the block out of `selftest()` into `main()`/`audit()`'s exit path**, and register
the audit as its own CI step:

```python
# in recorder_evidence_check.py -- REMOVE from selftest(), ADD to the audit entrypoint
def main() -> int:
    rows = audit()
    render(rows)                      # existing reporting
    drift = [r for r in rows if r["status"] in ("SCHEMA_DRIFT", "UNIT_MISMATCH")]
    if drift:
        print("\n  FATAL DECLARATION DRIFT - recorder health cannot be measured:")
        for r in drift:
            print(f"    {r['recorder']}: {r['status']} - {r.get('detail') or ''}")
        return 1
    return 0
```

```yaml
# invariants.yml -- add alongside the existing --selftest step
- name: Recorder declarations still describe the data
  run: python backend/audit/recorder_evidence_check.py
```

Minor, same block: the current code prints `RECORDER EVIDENCE SELFTEST: PASS (10 checks)` and
*then* the FATAL lines before returning 1. Move the summary below the drift check so a failing
run does not print PASS.

### OPEN-4 — `l2_gaps` never records, on the path that actually fires

`BookSequenceGap` has two origins in `backend/venues/binance_l2_recorder.py`:

- **Path A, instrumented** — `apply_and_record` catches at `:383`, writes a
  `disposition="SEQUENCE_GAP"` diff, calls `store.gap(...)` at `:393` (inserts `l2_gaps`,
  `gap_count = gap_count + 1`), re-raises.
- **Path B, not instrumented** — `:467` raises directly during synchronisation
  (`"buffer did not overlap the REST snapshot"`); it reaches `:513`, which calls
  `store.finish(session_id, "GAP", ...)`. `finish()` (`:340-365`) writes status only.

The store proves only Path B has ever fired:

    l2_gaps rows              : 0
    sum(l2_sessions.gap_count): 0
    session status            : ERROR 12, GAP 3, SYNCED 1   (16 sessions)
    l2_diffs disposition      : APPLIED 519,672, STALE 34   (zero SEQUENCE_GAP)

Three sessions died of sequence gaps and `gap_count` is zero — and `gap_count` is exactly what the
recorder's health summary aggregates (`:643`), so it reports a gap-free stream.

**Patch — make `finish()` the place that cannot be bypassed:**

```python
    def finish(self, session_id, status, update_id, applied, stale, error=None) -> None:
        self.flush()
        if status == "GAP":
            # Path B (sync-time gap) reaches here without an event dict, so the venue-supplied
            # U/u/pu are NULL. That NULL pattern is itself the diagnostic: it distinguishes
            # "our buffer never overlapped the snapshot" from "the venue skipped ids".
            self.db.execute(
                "INSERT INTO l2_gaps VALUES (?, ?, ?, ?, ?, ?, ?)",
                [session_id, _now_ms(), update_id, None, None, None,
                 (error or "sequence gap")[:1000]],
            )
            self.db.execute(
                "UPDATE l2_sessions SET gap_count = gap_count + 1 WHERE session_id = ?",
                [session_id],
            )
        self.db.execute(
            """
            UPDATE l2_sessions
            SET ended_ts_ms = ?, status = ?, final_update_id = ?,
                applied_diffs = ?, stale_diffs = ?, error = ?
            WHERE session_id = ?
            """,
            [_now_ms(), status, update_id, applied, stale,
             error[:1000] if error else None, session_id],
        )
```

Note Path A already calls `store.gap()` *before* raising, and its handler then calls `finish()`.
With the patch above that would double-count. Either guard Path A's handler (pass a flag), or
drop the `store.gap()` call at `:393` and let `finish()` own it — the second is simpler and keeps
one writer. Whichever is chosen, add a selftest asserting `l2_gaps` gains a row for **both**
origins; there is currently none.

### OPEN-5 — bind `TERMINAL_TABLES` to the schema

The table rule is only as good as its literal list, and a table not on it falls back to the
column+literal heuristic the fence's own probe shows is blind to three statement forms. Parsing
all 126 `CREATE TABLE` statements: 14 hold a terminal column, **7 are not on the list**.

Assessed individually — `hf_crossing_events.move` is a false positive (an immutable measured
event property; outcomes live in `hf_crossing_labels`), and `historical_replay_predictions` writes
`actual_price` in the same statement that creates the row. The two that matter:

- **`shadow_orders.resolved`** — the direct sibling of `shadow_signals`, same schema block
  (`decision/shadow_store.py:69-85`), same `resolved BOOLEAN DEFAULT FALSE`. One was added to
  `TERMINAL_TABLES`, the other was not.
- **`predictions_5m.hit, resolved`** — a core live predictions table.

Neither has a REPLACE writer today, so there is no live destruction path. The gap is that the
protection is one forgotten edit away.

**Patch — derive the candidate set, keep an explicit exemption list:**

```python
EXEMPT_TABLES = {
    # `move` here is the measured price move at the crossing instant - an immutable property
    # of the event, not an outcome written later. Outcomes live in hf_crossing_labels.
    "hf_crossing_events",
}

def schema_evidence_tables() -> set:
    """Tables whose CREATE TABLE declares a TERMINAL_COLUMNS column."""
    found = set()
    for path in sorted(BACKEND.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        for node in ast.walk(tree):
            sql = _sql_text(node)
            if not sql:
                continue
            m = re.search(r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+([A-Za-z_]\w*)\s*\((.*)",
                          sql, re.I | re.S)
            if not m:
                continue
            cols = {ln.strip().split()[0].lower()
                    for ln in m.group(2).split(",") if ln.strip().split()}
            if cols & set(TERMINAL_COLUMNS):
                found.add(m.group(1).lower())
    return found

# add to main():
missing = schema_evidence_tables() - {t.lower() for t in TERMINAL_TABLES} - EXEMPT_TABLES
check(not missing,
      f"every table declaring a terminal-outcome column is protected by the table rule "
      f"(unprotected: {sorted(missing) or 'none'}) - add it to TERMINAL_TABLES, or to "
      f"EXEMPT_TABLES with the reason")
```

Applying this makes the check fail immediately on `shadow_orders`, `predictions_5m`,
`complete_trade_forecasts_v2`, `model_revision_outcomes`, `open_position_action_outcomes` and
`historical_replay_predictions`. That is the point — each then gets a deliberate decision rather
than silence.

### OPEN-6 — the Binance L2 recorder has one healthy session in sixteen

    ERROR 12   GAP 3   SYNCED 1     (519,672 diffs applied, 34 stale)

Combined with R2-1 (its health declaration was broken from the start) and
`recorder_evidence_check`'s docstring recording that it once held zero rows while CI-gated, this
component has a history of being wired and green while not working. **I did not read the 12
`error` strings** — that is the obvious next step and costs one query:

```bash
python -c "import duckdb; c=duckdb.connect(r'data/binance_l2.duckdb',read_only=True); [print(r) for r in c.execute('SELECT status, error, started_ts_ms FROM l2_sessions ORDER BY started_ts_ms DESC LIMIT 16').fetchall()]"
```

---

## E. Retrain readiness

**Operator decision recorded: union both segments.** That is the safe choice and the measurement
supports it — the two stores hold **zero** overlapping rounds by id, by `(timestamp, horizon)`, or
by calendar day, so a union cannot double-count.

| | resolved rounds | officially settled |
| --- | --- | --- |
| canonical `btc_duckdbs/` (2026-07-05 → 07-25) | 14,368 | 6,725 |
| `data/analytics.duckdb` (2026-06-12 → 07-04, + 08-06/08) | 19,120 | 693 |
| **union** | **33,488** | **7,418** |

Note the asymmetry: 91% of all official settlement evidence is in the canonical 21-day window,
and `head_health` filters on `settlement_source LIKE 'official:%'`. The union is much larger in
rows but only marginally larger in the evidence that can certify a head.

**Blockers before a retrain is meaningful:**

1. **OPEN-1** — migrate the canonical store, or head certification cannot run at all.
2. **DEC-1, the 11-day hole.** No `price_to_beat` round exists anywhere between **2026-07-26 and
   2026-08-05**. Recording resumes 08-06 with 7 rounds against a normal 650-760/day. Smaller holes
   at 06-17, 06-24 → 06-26, 07-02. A window specified as "1000 days" or "since June" silently
   spans this; the 7-round partial day is the kind of tail that skews a per-day cluster bootstrap.
   Decide explicitly whether to exclude it.
3. **Recorders are not running.** At 05:30 UTC eight recorders were ~20.8 h stale (last write
   2026-08-10 08:41), only `crossing_recorder_hf` advancing. Every hour that continues is evidence
   not collected.
4. **The served artifacts are old.** `architecture_version.pkl` reads
   `2026-06-15-v11-pruned69-...-136-tcn`; the `saved_models` pkls date from 2026-07-02 to 07-04.
   The training window question therefore also decides how much of the gap between June and now
   the new artifact is expected to cover.

Also unresolved from the earlier rounds, unchanged: R1-2, the recorded blast radius of the
original `log_price_to_beat` defect is wrong for DuckDB — the signature to look for is
`resolved = FALSE` with a non-null `actual_price`, not blanked outcome fields. Measured across all
four analytics stores, **zero rows match**, so no residual damage is present.

---

## F. Polymarket / Binance — the lanes are deliberately NOT in sync

This was asked as "make sure they are in sync". The measurement says the opposite is the design,
and that unifying them would remove a safety property rather than add one.

`target_contract.py` declares four contracts and a `PURPOSE_REQUIREMENTS` map binding each
consumer to the contract that answers its question:

| purpose | required contract |
| --- | --- |
| `polymarket_settlement_ev`, `polymarket_hold_exit_ev` | `polymarket_binary_settlement_v1` |
| `binance_directional_paper_ev`, `binance_direction_confirmation`, `proxy_settlement_research`, `quote_revision_research`, `cross_venue_propagation_research`, `path_continuation_research` | `rolling_exchange_return_sign_v1` |
| `binance_directional_ev` | `endpoint_settlement_v1` |
| `stop_target_planning`, `path_stop_management`, `path_excursion_forecast` | `first_touch_triple_barrier_v1` |

The separation is explicit and reasoned in-source: Polymarket resolves on a strict comparison with
no neutral band, so the three-class endpoint contract is refused there *even though* both produce
a float in [0,1]. `ContractMisuse` is raised rather than returned so a caller that only checks a
boolean cannot ignore it. The Binance proxy head is barred from every Polymarket EV purpose.

**So "in sync" should mean: the separation is enforced, and each lane is served by an artifact
under its own contract.** On the first, the machinery is present and the CI steps covering it pass
("Target contract (train and serve label identically)", "Target contract parity end-to-end",
"P1-3 per-model panel grades the trained contract" — all OK in this run).

On the second, **DEC-2**: the in-source comment at `target_contract.py:262-263` states the
Polymarket purposes "require the ORACLE-sourced contract. No artifact exists under it, so both
still refuse." Consistent with that, of the 21 bundle manifests on disk, the only two carrying an
`authority` block (`crossing_heads`) declare `may_price: false, may_rank: false, may_size: false`,
and **no bundle anywhere declares `may_price: true`**.

**Caveat, stated because it limits the claim:** the main ensemble in `data/saved_models/*.pkl`
does not use the `MANIFEST.json` authority format, and bundles record `target_contract_hash`
rather than the contract name, so I could not resolve every artifact to a contract by name. What
is established: no bundle carrying an authority block may price, and the Polymarket EV path is
designed to refuse without an oracle-contract artifact. What is **not** established: that the
main ensemble is likewise unable to price a Polymarket decision. Resolving that needs the hash
preimages — a short follow-up, and worth doing before treating the two lanes as safely separated
in practice rather than in design.

If the goal is a Polymarket lane that can actually price, the retrain must produce an artifact
under `polymarket_binary_settlement_v1`, and that requires official-settlement labels — which is
precisely the 6,725 officially settled rounds concentrated in the canonical Jul 5-25 window. That
ties DEC-2 directly to OPEN-1: the labels needed live in the store that has not been migrated.

---

## G. Suggested order

0. **FC-1** — the retrain itself. Every serving artifact predates three value-changing feature
   migrations, so the app is currently serving with known train/serve skew. Nothing else on this
   list changes that. Stamp `feature_semantics_version` at write time as part of the retrain, then
   make the check gating.
1. **OPEN-1 + OPEN-2** — back up, migrate the canonical store. Unblocks head certification.
2. **DEC-1** — decide the training window explicitly against the 11-day hole.
3. **Restart the recorders**, then confirm `ADVANCING` before trusting any freshness.
4. **DEC-2** — resolve the `target_contract_hash` preimages; decide whether the retrain produces a
   Polymarket-contract artifact.
5. **OPEN-3, OPEN-4, OPEN-5** — three small, self-contained patches, all specified above.
6. **OPEN-6** — read the 12 error strings.
7. Re-run `backend/tests/run_ci_locally.py` to completion and regenerate `SOURCE_STATE` with a clean tree.

Nothing in this register changes what a model learns. OPEN-1, OPEN-2 and DEC-1 change what
evidence a retrain can see, which is upstream of that.

---

## Resolution after this read-only register was written

This register describes an earlier working-tree snapshot. Current status:

- **OPEN-1 / OPEN-2 fixed operationally:** canonical analytics and active execution stores were
  backed up with SHA-256 verification, migrated additively, and checked read-only for every named
  identity/resolution/reference/book column. Existing rows remain unattributed and cannot certify
  a new artifact.
- **OPEN-3 fixed:** selftest no longer queries live stores; the separately registered evidence
  audit is fatal only for schema/unit drift.
- **OPEN-4 fixed:** synchronization-time L2 gaps now write one forensic row and increment one
  session gap count through an idempotent shared finalizer. The three historical GAP sessions
  were reconciled after a verified backup; the archive now reports `gap_count=3` and three rows.
- **OPEN-5 fixed:** terminal-table protection is derived from production schemas, covers dynamic
  prediction horizons, and requires reviewed exemptions.
- **FC-1 is the reason for the retrain, not an unnoticed serving state:** strict identity is on,
  legacy UNKNOWN artifacts are refused, forced training is allowed to create its output, and the
  current trainer writes feature/training semantics, source identity, code state and cutoff into
  new manifests. The feature-contract report remains advisory in source CI because this checkout
  intentionally contains pre-retrain artifacts; ordinary startup still refuses them.
- **DEC-1 remains recorded data history:** the 11-day forward-evidence hole is not filled or
  hidden. It does not alter Binance candle/microstructure rows in the 1000-day research matrix,
  and partial days must remain excluded or explicitly represented in outcome studies.
- **DEC-2 remains fail-closed:** no existing artifact is promoted to Polymarket pricing authority
  by this pass. That requires a separately validated settlement-contract artifact and fresh
  official, artifact-attributed outcomes.
- **Startup meta-contract isolation fixed:** the offline positive-training fixture no longer
  inherits `BTC_EVIDENCE_MODE=1` from `start.bat`; the explicit dark-evidence case still proves
  that live adaptation refuses stopped recorders. Startup also reports the exact command that
  fails inside its multi-command head/model contract block.
