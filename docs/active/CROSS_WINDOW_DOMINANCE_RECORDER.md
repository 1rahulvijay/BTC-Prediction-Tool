# Cross-window 5m/15m dominance recorder

`backend/cross_window_recorder.py` — **records only. It cannot place an order**, and that is
enforced by an AST scan of its own source, not by a promise.

55 selftest checks, **8/8 mutations caught**.

---

## 1. The relationship, verified before anything was built

A 15-minute round opening at `T` settles at `T+900`. A 5-minute round opening at `T+600` settles
at `T+900` too — the same instant, the same oracle. Their strikes differ because each was fixed
at its own open, ten minutes apart.

Buying **UP on the lower strike** and **DOWN on the higher strike** pays:

| settlement | UP(K_low) | DOWN(K_high) | total |
|---|---|---|---|
| ≤ K_low | 0 | 1 | **$1** |
| between | 1 | 1 | **$2** |
| > K_high | 1 | 0 | **$1** |

The floor is $1 in every state, and no directional forecast is involved. I verified this
exhaustively before writing the recorder, under **both** tie conventions.

**Leg ordering is a safety property.** Buying UP on the *higher* strike pays **$0** in the middle
band. `dominance_legs()` never returns that ordering, and the selftest pins the $0 case so it
cannot silently flip.

Confirmed live: `btc-updown-15m-1785838500` (+900) and `btc-updown-5m-1785839100` (+300) both
settle at `1785839400`.

---

## 2. Two things I had wrong until I read the actual market text

Both were caught by fetching the real Gamma description rather than assuming.

### The tie rule is UP, not DOWN

> "…resolve to **Up** if the Bitcoin price at the end of the time range … is **greater than or
> equal to** the price at the beginning of that range."

`>=` means a settlement exactly at the strike pays the UP holder. I had defaulted `tie_rule` to
`"down"`.

**Why that default was worse than a bug.** I applied the *same* wrong default to both markets, so
`equivalence_issues` compared `"down" == "down"` and **passed**. Two identical wrong values look
consistent. The rule is now parsed from the market text, and an unparseable rule returns `None`
so the caller refuses. A default here manufactures agreement without evidence.

### The strike is not published

Gamma exposes no strike field. The market resolves against "the price at the beginning of that
range" on the **Chainlink BTC/USD stream** (`resolutionSource`). So the strike must be
**observed** at each round's open and remembered.

`record_strike_observation()` accepts a price only within 20s of the anchor and keeps the
**first** valid one — a later observation cannot move a strike already used for pairing.

**Consequence:** a 15m round's strike was fixed ten minutes ago, so the recorder needs one
warm-up cycle before any pair is complete. Until then it reports `strike missing` and refuses.
That is the intended behaviour, and it is what the first live runs show.

---

## 3. What is enforced, and where

| property | enforced by | fails how |
|---|---|---|
| identical settlement instant | `find_pairs`, `equivalence_issues` | exact equality; 1s differs |
| identical oracle | `parse_market_rules` + `equivalence_issues` | unrecorded oracle is an issue |
| identical tie rule | same | unparseable ⇒ `None` ⇒ refused |
| dominating leg ordering | `dominance_legs` | inverse ordering never returned |
| equal strikes | `dominance_legs` | returns `None` (complete set, other lane) |
| books are one decision | `MAX_BOOK_SKEW_MS = 1500` | skewed books are an issue |
| executable depth | `walk_book` | partial fill reported, never priced |
| both fees | `pair_cost` | `0.07·p·(1−p)` per share, per leg |
| market tradeable | `equivalence_issues` | closed/resolved/paused refused |
| **cannot trade** | AST scan of own source | any order-placement identifier fails the test |

The no-trading guard is **parsed, not grepped** — a text scan matched its own list of forbidden
names — and it is negative-tested against a probe module that imports and calls a CLOB client.

---

## 4. The economics are hostile, and the recorder says so

Polymarket's crypto taker fee is `0.07 × p × (1−p)` per share, peaking at **1.75¢** at p=0.50.
Both legs pay it, so a pair costs up to **3.5¢** in fees alone.

**A pair must be acquirable below ~96.5¢ before any edge exists.** The selftest pins this: a pair
at 99¢ has a *negative* guaranteed edge once fees are added.

Negative-edge observations are recorded, not discarded. The distribution of near-misses is the
evidence this recorder exists to gather — "how often, and by how much, does it fail to clear" is
the question, and dropping the failures would answer it with a survivorship-biased sample.

A capacity ladder (10 / 50 / 100 / 500 / 1000 shares) is recorded per observation, because an
edge that exists only for 10 shares is a curiosity.

---

## 5. What this does NOT establish

- **`admissible` does not mean tradeable.** It means an executable pair with a positive
  guaranteed floor existed at one instant. It does **not** model one-leg fill risk, which is the
  dominant practical risk: fill one leg and the floor argument disappears entirely, leaving a
  naked directional position.
- **No preregistered study exists yet.** This is a recorder. Scoring belongs to a sealed
  protocol written before the data is looked at.
- **No forward sample yet.** Strikes require a warm-up cycle; nothing has been collected.

---

## 6. Running it

```bash
python backend/cross_window_recorder.py --selftest
```

```bash
python backend/cross_window_recorder.py --forever --interval 5
```

Writes `data/cross_window.duckdb`, table `cross_window_observations`, one row per pair per pass:
strikes, chosen sides, settlement timestamp, the full capacity ladder as JSON, the guaranteed
edge, and every equivalence issue that blocked it.

Leave it running for at least a warm-up cycle before expecting complete pairs. Like every other
recorder in this repository, it produces nothing while it is not running.


---

## 7. Six defects found by external audit, all mine, all fixed

The first version passed its own selftest while being unable to work at all. Recorded here
because the pattern matters more than the individual bugs.

### It never captured a strike (the fatal one)

`record_strike_observation()` was defined and called **only from the selftest**. The live
`collect_once()` path called `observe_strike()` — a read — and nothing ever wrote. So
`_STRIKE_CACHE` stayed empty in production and every candidate was refused forever.

**The selftest passed because it populated the cache by hand.** A test that supplies the input
production never produces is not testing production. The live loop now observes every round's
open on every pass.

### Simultaneity was mistaken for freshness

`MAX_BOOK_AGE_MS` was declared and never referenced. `equivalence_issues` compared only the
*difference* between the two books, so two books both 30 seconds old with 100 ms of skew passed
as an executable opportunity. Per-leg age is now enforced, and an unknown timestamp is refused
rather than assumed fresh.

### Timestamps could be laundered

Each market's freshness came from `max(up_book.recv_ts, down_book.recv_ts)` — across **both**
tokens, while only one is priced. A stale bought leg inherited freshness from the untouched
opposite token. Worse, a missing timestamp fell back to `time.time()`, converting absent
evidence into apparent freshness. Timestamps are now per-token, the priced leg is selected
explicitly, and absent stays absent.

### Market status defaulted to open

`_market_rules()` computed a status that was then discarded by `r.get("status") or "open"`,
because discovery never supplies that field. A closed market always looked tradeable. Status now
comes from the fetched rules, defaulting to `unknown` — which is an issue, not a pass.

### One failed book aborted the whole pass

`get_book()` returns `None` on failure, and the caller did `.get()` on it. One unavailable token
raised `AttributeError` and abandoned the entire collection pass. The outer `except` kept the
process alive — but *process alive is not evidence advancing*. Books are now coerced and the
observation is recorded as refused.

### A proxy strike could have claimed a guaranteed floor

`get_btc()` prefers **Pyth**, falling back to Binance. These markets settle on **Chainlink**. A
wrong strike can invert the dominance ordering, and the inverted pair pays **$0** in the middle
band rather than $1. Strikes now carry their source, and anything that is not the settlement
oracle is recorded as evidence and **refused as a floor**.

This is why the live output currently reads `0 with an observed strike`: every visible round
opened minutes ago, and an observation more than 20s from the anchor is refused. The recorder
must be running when a round opens. That is the honest constraint, not a bug.
