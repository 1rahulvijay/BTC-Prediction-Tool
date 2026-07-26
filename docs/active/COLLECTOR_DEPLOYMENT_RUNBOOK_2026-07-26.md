# DEPLOYMENT RUNBOOK — collector hardening + Oracle security (2026-07-26)

> **UPDATED 2026-07-26 — required stream health is 9/9, not 8/8.**
> `bybit_perp/publicTrade` was missing from the health gate while the preregistration names it as
> Class-A input, so an episode could report 8/8 healthy with a required input absent. Episode
> qualification additionally now enforces REST/WS age limits, counts PERSISTED (not parsed) rows,
> and refuses to start the evidence clock unless an insert actually succeeded.
> See `COLLECTOR_INTEGRITY_FIXES_2026-07-26.md`. Deploying against the pre-fix collector would
> re-open every one of those defects.

**Who executes this:** whoever holds Oracle shell access. It is not held in the session that wrote
this document, so every step below is written to be executed verbatim by someone else, with an
explicit pass/fail check after each one.

**What it does:** (1) closes the unauthenticated admin surface on the public box, (2) restarts the
Polymarket recorder onto the 62-column schema, (3) brings up the multi-venue collector as a managed
service so the `BINANCE_VOLATILITY_MOMENTUM_V1` evidence clock can legitimately start.

**What it must NOT do:** change any rule threshold. The Oracle box is mid-way through an 8-week
evidence clock (started 2026-07-04, matures ≈ 2026-08-30). A threshold edit inside this deploy
resets that clock to zero. Restarting a process does not; the ledger and recorder state are durable
in DuckDB.

---

## 0. Preconditions

```
[ ] shell access to the Oracle box as the service user
[ ] repo checked out at the deploy path (existing units reference it)
[ ] existing units present:  btc-backend.service  btc-recorder.service  btc-frontend.service
[ ] disk headroom >= 20 GB   (the collector writes ~1.5-2.5 GB/week; see section 5)
```

Nothing here requires credentials for any exchange. The collector uses **public read-only market
data endpoints only** and cannot place an order — there is no key to leak.

---

## 1. Close the unauthenticated admin surface

`/api/relearn`, `/api/backtest` and `/api/historical-replay/run` are CPU-expensive and, with
`BTC_ADMIN_TOKEN` unset, ungated. The box is publicly reachable.

The token goes in the **deployment environment**, not in the unit file and not in Git.

```bash
# generate - do not reuse a token from anywhere else, do not paste it into chat or a commit
openssl rand -hex 32
```

```bash
# store it where only the service user can read it
sudo touch /home/ubuntu/quant-app/.env
sudo chmod 600 /home/ubuntu/quant-app/.env
sudo chown ubuntu:ubuntu /home/ubuntu/quant-app/.env
# then append the single line:  BTC_ADMIN_TOKEN=<the value printed above>
sudo -u ubuntu editor /home/ubuntu/quant-app/.env
```

Reference it from the unit with `EnvironmentFile=`, never `Environment=`:

```ini
[Service]
EnvironmentFile=/home/ubuntu/quant-app/.env
```

```bash
sudo systemctl daemon-reload
sudo systemctl restart btc-backend
```

### Verification (all four must pass)

```bash
# 1. no token -> rejected
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8000/api/relearn      # expect 401/403

# 2. wrong token -> rejected
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8000/api/relearn \
     -H 'X-Admin-Token: wrong'                                                          # expect 401/403

# 3. file permissions are 600 and owned by the service user
stat -c '%a %U' /home/ubuntu/quant-app/.env                                             # expect: 600 ubuntu

# 4. the token never reaches the logs
sudo journalctl -u btc-backend --since '30 min ago' | grep -c "$(grep -oP '(?<=BTC_ADMIN_TOKEN=).*' /home/ubuntu/quant-app/.env)"
#    expect: 0
```

Check 4 is the one people skip. A token that is correctly stored and then echoed into
`journalctl` is a token that is public — `journalctl` is readable by more accounts than the `.env`
file is.

> If check 1 or 2 returns 200, **stop**. The gate is not active; do not proceed to section 2 while
> the box is publicly reachable and ungated.

---

## 2. Restart the Polymarket recorder onto the 62-column schema

The schema change is **additive** (`ADD COLUMN IF NOT EXISTS`), so old rows stay valid and simply
carry NULL in the new columns. No migration or backfill is required, and no data is lost.

```bash
# schema invariant first - this fails loudly if the row literal and COLS have diverged,
# which would otherwise break EVERY insert silently at runtime
python backend/polymarket/live_btc_updown_recorder.py --selftest

sudo systemctl restart btc-recorder
```

### Verification

```bash
sudo systemctl is-active btc-recorder                     # expect: active
sudo journalctl -u btc-recorder --since '5 min ago' | tail -20
python backend/polymarket/live_btc_updown_recorder.py --report
```

Expect new rows to carry non-NULL `top_bid_size` and the ladder/provenance columns. `top_bid_size`
is the point of the restart: without it every exit in the capacity study assumed one share, which
is why no size-aware exit VWAP could be computed at all.

---

## 3. Bring up the multi-venue collector

### 3.1 Pre-flight, before installing anything

```bash
python backend/venues/multi_venue_recorder.py --selftest
```

```bash
python backend/venues/venue_admissibility.py --selftest
```

Expect `SELFTEST PASS` from both: **24 checks** for the recorder (parsers, provenance stamping,
event-identity coverage, episode accounting — including that a stalled collector materialises as
*excluded* episodes rather than silently absent ones) and **33 checks** for the admissibility gate
(backlog prohibition, first-observation dedupe across a simulated reconnect, causality, Class-B age
limit, receive-basis feature naming, and every invalid lead-lag pairing). Neither makes a network
call.

```bash
python backend/venues/multi_venue_recorder.py --smoke --seconds 60
```

Expect `stream health: 9/9 expected streams live (all healthy)` and `unstamped rows: 0`. The smoke
run writes to `:memory:` and **cannot** touch the evidence DB or start the evidence clock.

> If stream health is < 9/9, record which stream is missing and **do not** start the service.
> A venue silently serving zero messages is exactly how the perp `aggTrade` gap went unnoticed for
> weeks; starting collection anyway produces episodes that are all non-qualifying.

### 3.2 The unit

`/etc/systemd/system/btc-venues.service`

```ini
[Unit]
Description=BTC multi-venue event-time collector (public market data, read-only)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/quant-app
EnvironmentFile=/home/ubuntu/quant-app/.env
Environment=PYTHONUNBUFFERED=1
ExecStartPre=/home/ubuntu/quant-app/venv/bin/python backend/venues/multi_venue_recorder.py --selftest
ExecStartPre=/home/ubuntu/quant-app/venv/bin/python backend/venues/venue_admissibility.py --selftest
ExecStart=/home/ubuntu/quant-app/venv/bin/python backend/venues/multi_venue_recorder.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

# This process reads public market data and writes one DuckDB file. It has no reason to
# write anywhere else, and no reason to hold privileges it cannot use.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/home/ubuntu/quant-app/data

[Install]
WantedBy=multi-user.target
```

The two `ExecStartPre` gates are deliberate: if a venue changes a payload shape, or if the
admissibility rules are edited so that backlog or mixed-basis data could reach a feature, the
service refuses to start rather than quietly recording rows the parsers no longer understand or
silently loosening the contract the evidence depends on.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now btc-venues
```

### 3.3 Verification

```bash
sudo systemctl is-active btc-venues                       # expect: active
sudo journalctl -u btc-venues --since '2 min ago'         # expect 4 "connected" lines, no repeats

# after >= 10 minutes of running:
python backend/venues/multi_venue_recorder.py --report
```

The report must show:

```
[ ] all 8 expected venue/stream pairs present with non-trivial counts
[ ] clock drift medians in the tens-to-hundreds of ms for WS venues (NOT tens of seconds)
[ ] episode coverage section present, with qualifying episodes > 0
[ ] collection start   <a unix timestamp>        (not "(unmarked)")
```

---

## 4. Reading the episode report honestly

This is the part that decides whether the data counts, so it gets its own section.

```
uptime            = the service was running
qualifying        = all 8 required streams produced events in that 5-minute window
                    AND there was no reconnect in it AND the window was complete
```

**These are different numbers and only the second one advances the promotion contract.** The
preregistration requires **≥ 1,000 qualifying non-overlapping 5-minute episodes across ≥ 4 weeks**.
A month of 100% uptime with one flaky venue produces close to *zero* qualifying episodes, and
`--report` will say so:

```
  episodes recorded  8,064
  QUALIFYING         21     (0.3% of recorded)
  prereg requires    >= 1,000 qualifying AND >= 4 weeks -> NOT MET
    excluded  8,043  missing:coinbase/ticker
```

Every excluded episode is stored **with its reason**. Outages are never interpolated and never
dropped — an absent row and a healthy quiet period would otherwise be indistinguishable, which is
precisely the ambiguity that lets a lane claim four weeks of data it does not have.

Per-episode the collector preserves: `episode_start`, `episode_end`, `stream_counts` (per stream),
`streams_live` / `streams_required`, `max_ws_age_ms`, `max_rest_age_ms`, `reconnects`,
`qualifying`, `exclusion_reason`.

WS and REST feature ages are tracked **separately and deliberately**. Pooling them would let a
~54-second REST poll lag mask a healthy 20 ms WebSocket feed, and the two are gated on different
limits by the admissibility contract.

### Class A / Class B, enforced in the data rather than by convention

Every row carries `source_mode` (`WS` / `REST_POLL`), `timestamp_basis`, and — for REST rows —
`poll_id`. Class A and Class B are therefore separable **in SQL alone**, without knowing which
stream names happen to be REST-backed today.

Two measured facts that this deployment locks in, both of which change what is admissible:

1. **Binance perp `aggTrade` and `markPrice@1s` do not deliver over WebSocket from this host.**
   They arrive by REST poll and are therefore **Class B** — slow aggregate state only, never
   lead-lag or timing evidence.
2. **Binance *spot* `bookTicker` carries no exchange timestamp at all** (no `E`, no `T`). Its
   `exch_ts` is stored NULL, never 0 — storing 0 made `recv_ts - exch_ts` read as the entire Unix
   epoch. For that stream `recv_ts` is the only honest time.

### Both facts are enforced in code, not in prose

`backend/venues/venue_admissibility.py` is **the only sanctioned path** from `venue_events` to a
decision feature (`--selftest`: 33 checks). Prose caveats depend on an analyst remembering them at
the exact moment a plausible result is on screen; these do not.

**Invariant 1 — REST backlog is prohibited from features, not merely filterable.** The first poll
after every (re)connect returns up to 1,000 historical trades; measured ages were **255–334
seconds**. Aggregated naively, several minutes of backlog collapse into the first live decision
window and read as an enormous, entirely fictitious flow impulse. The gate is in SQL, so it cannot
be bypassed by post-filtering a DataFrame:

```
source_mode = REST_POLL and poll_id <= 1   ->  provenance/audit storage only, never a feature
```

A REST event becomes feature-eligible only when **all** hold: `poll_id >= 2`; it is the *first*
observation of that `seq` (a restart re-polls trades already recorded, and the second sighting
carries a `recv_ts` that never reflected when we could first have known it); `recv_ts <=
decision_ts`; and age `<= CLASS_B_MAX_AGE_S`.

> **Frozen Class-B age limit: 60 s.** Declared 2026-07-26, before any production row existed and
> before any M0 score, filling in the limit that section 10 of the preregistration names but leaves
> unvalued. It matches that document's own ">= 60s aggregation" language. Observed steady-state
> ages sit far below it (premiumIndex ~1.0 s, openInterest ~6.1–8.0 s, perp aggTrades ~1–2 s once
> past the backlog poll), so it excludes malfunction, not normal operation. **Revising it after
> seeing M0 results invalidates the experiment.**

**Invariant 2 — timestamp bases may not be mixed in lead-lag.** Comparing Binance spot's `recv_ts`
against Bybit's `exch_ts` measures network latency and calls it market leadership. Every row is
stamped with a derived basis, and `require_leadlag()` raises `InadmissiblePairing` on any pair
without a shared one:

| `timestamp_basis` | meaning | usable for lead-lag in |
|---|---|---|
| `EXCHANGE_TIME` | push delivery, trustworthy venue event time | exchange time **or** receive time |
| `RECEIVE_TIME` | push delivery, venue clock present but implausible (>60 s late or >5 s ahead) | receive time only |
| `RECEIVE_ONLY` | push delivery, venue sends no event time (Binance spot `bookTicker`) | receive time only |
| `POLL_RECEIVE_TIME` | REST poll | **nothing** |

`POLL_RECEIVE_TIME` maps to the empty set deliberately. Its `exch_ts` is genuine but delivery is
delayed and batched, so ordering by it *is* the Class C prohibition verbatim; its `recv_ts` is
dominated by poll cadence rather than by market events, so ordering by that is no better. A polled
stream can carry slow aggregate state. It can never carry leadership.

The basis is stamped centrally in `Writer.add()` rather than in each parser, so a parser added for
a new venue next year cannot forget to label its clock — it never gets the chance to.

**Receive-time order is a property of the observer, not the market.** `RECEIVE_TIME A precedes
RECEIVE_TIME B` means *A reached this collector first* — an ordering that also contains network
routing, venue publication latency, WebSocket batching, event-loop scheduling, reconnect state and
parser delay. `leadlag_feature_name()` therefore refuses to let a receive-basis feature be called
`venue_lead`; the permitted names are `observer_time_lead` and `collector_arrival_lead`. Every row
carries `process_start_id`, `connection_id`, `queue_delay_ms` and `processing_delay_ms`, resolved
through `collector_sessions` to host, pid, start time and code hashes — without that, "A arrived
before B" is an unattributable claim. See
[`PREREG_BINANCE_V1_CLARIFICATION_002.md`](PREREG_BINANCE_V1_CLARIFICATION_002.md).

### Every polled stream must have a stable natural event identity

`seq` is for **gap detection**; `event_key` is what deduplication partitions on, and it must come
from the venue. A synthetic poll-local counter would be useless: it restarts at 1 in a fresh
process, so a re-fetched observation after a reconnect would not be recognised as a repeat.

| stream | identity | source |
|---|---|---|
| `aggTrade`, `aggTrade_rest` | `a:<id>` | venue aggregate-trade id |
| `bookTicker`, `orderbook.1` | `u:<id>` | venue update id |
| `publicTrade` | `i:<id>` | venue trade id |
| `ticker` (Coinbase) | `s:<n>` | venue sequence |
| `premiumIndex`, `openInterest`, `markPrice` | `t:<ms>` | instrument + venue publication time |

A polled row that cannot produce one is **recorded but barred from features**, because it could
silently double-count after a reconnect.

> This was a live defect, not a hypothetical. `premiumIndex` and `openInterest` previously carried
> no identity at all, and they are polled every 5s against a venue that republishes less often. A
> 45-second smoke run recorded **openInterest: 10 rows, 9 unique - one observation stored twice**,
> which would have been counted twice in any OI-change feature. All nine streams now show zero
> keyless rows; `--smoke` prints the identity-coverage table so a regression is visible immediately.

---

## 5. Ongoing operation

```bash
# weekly: coverage against the promotion contract
python backend/venues/multi_venue_recorder.py --report

# disk (the collector writes ~1.5-2.5 GB/week at observed rates)
du -h /home/ubuntu/quant-app/data/multi_venue.duckdb
df -h /
```

**One DuckDB writer.** Never run research against the live collector DB — snapshot it first:

```bash
sudo systemctl stop btc-venues
cp /home/ubuntu/quant-app/data/multi_venue.duckdb /home/ubuntu/snapshots/multi_venue_$(date +%F).duckdb
sudo systemctl start btc-venues
```

Stopping for the copy costs the episodes it spans; they will be recorded as excluded, which is the
correct accounting. Do not copy a DuckDB file that is being written.

---

## 6. Rollback

```bash
sudo systemctl disable --now btc-venues      # collector only; leaves the evidence DB intact
sudo systemctl restart btc-backend           # after reverting the .env change, if section 1 fails
```

The collector is strictly additive: no existing service reads its DB, so disabling it cannot affect
the backend, the recorder, or the paper ledger.

---

## 7. Completion record (signed off once, kept with the evidence)

Collect the values first:

```bash
date -u +%FT%TZ                                             # deployment timestamp
git -C /home/ubuntu/quant-app rev-parse HEAD                # git commit SHA
sha256sum backend/venues/multi_venue_recorder.py \
          backend/venues/venue_admissibility.py             # collector code hash
sha256sum docs/active/PREREG_BINANCE_VOLATILITY_MOMENTUM_V1.md           docs/active/PREREG_BINANCE_V1_CLARIFICATION_001.md           docs/active/PREREG_BINANCE_V1_CLARIFICATION_002.md   # compare against PREREG_HASH.txt
sudo systemctl is-active btc-venues btc-recorder btc-backend
python - <<'EOF'
import duckdb, os
c = duckdb.connect(os.environ.get("BTC_VENUE_DB", "data/multi_venue.duckdb"), read_only=True)
print("collection_start_ts", c.execute(
    "SELECT v FROM venue_collection_meta WHERE k='collection_start_ts'").fetchall())
print("first_persistent_row", c.execute("SELECT MIN(recv_ts) FROM venue_events").fetchone())
print("first_sealed_episode", c.execute(
    "SELECT MIN(episode_start), MIN(episode_end) FROM venue_episodes").fetchone())
EOF
python backend/venues/multi_venue_recorder.py --report        # stream-health + coverage
```

Then fill in and keep:

```
deployment timestamp        ______________________
git commit SHA              ______________________
collector code hash         ______________________  (multi_venue_recorder.py)
                            ______________________  (venue_admissibility.py)
preregistration hash        0973744b73651e8287b44309c976530f72a3964ceb082703c6b49400564c72f7
                            [ ] verified byte-identical to PREREG_HASH.txt
clarification 001 hash      12bf5e1e5829d320b4d6bbe9a7c3b94af23b33e433b7bdc6782bcb8f7a7db7d6
                            (CLASS_B_MAX_AGE_S = 60.0)          [ ] verified
clarification 002 hash      320631b2a83aaaca5b21e888d5fcfdf51e416bb1f4429c1bdb988207e3700d3f
                            (receive-basis interpretation rule)  [ ] verified
clarification 003 hash      05e3ab773b80e81bb833d38f0e728d8ca9609009ee2c78be890132bcd512f5e7
                            (9/9 health + stale-silence semantics) [ ] verified
collection_start_ts         ______________________  <- the evidence clock for
                                                       BINANCE_VOLATILITY_MOMENTUM_V1 starts HERE
systemd service status      btc-venues ______  btc-recorder ______  btc-backend ______
database path               ______________________
first persistent row        ______________________  (MIN(recv_ts) in venue_events)
first sealed episode        ______________________  (MIN(episode_start) in venue_episodes)
stream-health report        ____ / 9 streams live   [ ] --report output attached
admin-token verification    [ ] 401/403 without token   [ ] 401/403 with wrong token
                            [ ] .env is 600 + service-user owned
                            [ ] token count in journalctl = 0
Polymarket recorder status  [ ] restarted   [ ] non-NULL top_bid_size confirmed on new rows
thresholds changed          NONE                    <- must remain NONE
executed_by                 ______________________
```

If **any** of the four hashes does not match, **stop and report it**. A changed protocol or
clarification file invalidates the experiment regardless of how good the data is. The clarification
records exist precisely so that completed limits and interpretation rules live in separately hashed
artifacts, rather than only in source code where they could be edited without trace.

---

## 8. What this does not unlock

The `BINANCE_VOLATILITY_MOMENTUM_V1` M0 gate still **cannot run**, and this deployment does not
change that. Section 12 of the frozen preregistration
(`PREREG_BINANCE_VOLATILITY_MOMENTUM_V1.md`, sha256 `0973744b73651e82…`) requires ≥ 4 continuous
weeks at 9/9 stream health covering ≥ 1,000 non-overlapping qualifying episodes. Historical
archives cannot substitute: they carry `exch_ts` only, and inventing a `recv_ts` for them violates
section 0 of that contract.

This runbook starts the clock. It does not shorten it.
