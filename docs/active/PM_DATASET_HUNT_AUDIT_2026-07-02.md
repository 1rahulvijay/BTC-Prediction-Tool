# Polymarket Dataset Hunt — Inside-Spread Gate Audit — 2026-07-02

Ran the decisive gate on every candidate free historical Polymarket 5m/15m dataset: **does it have a REAL inside
book** (bids/asks near 0.45–0.55, spread 1–5c) — or the barbell we found in the HF audit (0.01–0.10 / 0.90–0.99,
$0.80 spread, no tradeable inside)? Only a real inside book + settlement can replace the live recorder.

## Results (PASS = usable for live-style ask/depth replay · PARTIAL = trades/resolutions only · FAIL = unusable)
| Dataset | Book gate (median spread) | Verdict |
|---|---|---|
| **`obadiaha/polymarket-crypto-5m-15m`** (HF) | **$0.80 barbell** (0.1/0.9), 0% tight | **PARTIAL** — orderbook DEAD; trades + resolutions usable (see the trade-edge pipeline, which turned out P(Hold)-independent + unproven) |
| **`BrockMisner/polymarket-crypto-5m-15m`** (HF) | **$0.80 barbell** (bid 0.100 / ask 0.900), 0% tight — **identical to obadiaha** | **PARTIAL** — same lineage; orderbook DEAD; has trades/resolutions |
| **`krish301/polymarket-raw-5m`** (HF) | — (repo has **only README + .gitattributes, NO data files**) | **FAIL** — empty; the "continuous raw tick" claim is unbacked |
| **Kaggle `debayan31415/polymarket-5-minutes-btc-up-down-data`** | not downloadable without Kaggle API credentials | **UNAUDITED** — needs auth; given the family pattern, expect the same barbell risk. Must pass the gate before any use. |
| **Reddit 5m up/down "second-by-second"** | the post's download resolves to the same HF family | **≈ PARTIAL** — same barbell book; not an independent inside-book source |
| GitHub `poly_data` / `Polymarket_data` / `orderbook-substreams` | on-chain **trades/fills** + metadata, not off-chain CLOB quote lifecycle | **PARTIAL/FAIL** for inside-book — useful only for fill/activity research |
| GitHub `manja316/polymarket-historical-data` | 15-minute snapshots | **FAIL** for 5m execution — too coarse; rough probability only |

## Conclusion
- **The barbell book is SYSTEMATIC across the entire accessible HF family** (obadiaha + BrockMisner confirmed
  byte-for-byte similar; the Reddit post links into it; krish301 is empty). No accessible free dataset provides a
  usable inside quote. This is not a one-off — it is the state of free historical Polymarket 5m/15m book data.
- Consistent with the public report that Polymarket's `/orderbook-history` endpoint was **decommissioned** — free
  *official* historical book is unavailable, so third-party recordings or your own recorder are the only routes,
  and the third-party recordings we can reach do not capture a real inside book.
- **Net: no free historical dataset can replace the live recorder for executable-edge / fillability research.**
  The usable pieces (trades, resolutions, markets) support only *research* — and the one trade-based result we
  produced (leader-price anomaly) is P(Hold)-independent and fillability-unproven (`HF_EDGE_ROBUSTNESS`).

## The gate (reuse for any future candidate before ingest)
```
1. active spread: median often 1c–5c, NOT 80c
2. complement: UP ask + DOWN ask ≈ 1.00–1.05
3. ladder: bids/asks exist near the active probability (0.45–0.55), not only 0.01–0.10 / 0.90–0.99
4. timestamps inside [window_start, window_end]; cadence matches the claim
5. settlement join: each market has a resolved Up/Down outcome
6. execution: top ask size > 0; VWAP for 1/5/10 shares computable
FAIL any → research-only (trades/resolutions at best); never an executable-edge source.
```

## Final direction
The dataset hunt is **exhausted**: nothing free replaces the recorder. Stop searching for a historical inside-book
shortcut. The only path to executable-edge / fillability / edge-duration proof is the **live `/book` recorder**
(`start_recorder.bat` + `run_polymarket_l2_recorder.bat`), running continuously to accrue real tight-book rounds.
