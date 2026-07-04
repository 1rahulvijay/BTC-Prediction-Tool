# HF Edge Robustness — NULL + Stability — 2026-07-02

Falsification of the +27% leader-edge result (37,789 leader snapshots, 5,893 rounds). Round-level, price=vwap30, buffer=0.02. The edge must beat its nulls and hold across blocks.

## Null tests
- **BASELINE** (buy leader every round, no filter): n=5,893 win=0.656 LB=0.643 price=0.574 ROI=+0.143 pnl=+0.082
- **REAL** (edge=P(Hold)−price≥2c): n=5,269 win=0.596 LB=0.583 price=0.470 ROI=+0.270 pnl=+0.127
- **NULL-shuffle** (P(Hold) permuted within horizon×secs_left): n=5,266 win=0.595 LB=0.581 price=0.473 ROI=+0.257 pnl=+0.121
- **NULL-invert** (same rounds, trade the TRAILING side): n=5,154 win=0.412 LB=0.399 price=0.550 ROI=-0.249 pnl=-0.137

**Reading:**
- edge over baseline: +0.127 ROI — the P(Hold) filter ADDS selection
- shuffle null ROI +0.257 vs real +0.270 — ⚠️ shuffle still profitable → edge is PRICE-selection, not P(Hold)
- invert null ROI -0.249 — ✅ trailing side loses (pipeline consistent)

## Stability (real edge by block — must not be carried by one)
| block | result |
|---|---|
| month 2026-03 | n=5,269 win=0.596 LB=0.583 price=0.470 ROI=+0.270 pnl=+0.127 |
| 5m | n=3,973 win=0.612 LB=0.597 price=0.472 ROI=+0.296 pnl=+0.140 |
| 15m | n=1,296 win=0.548 LB=0.521 price=0.454 ROI=+0.208 pnl=+0.094 |
| UP leader | n=3,517 win=0.636 LB=0.620 price=0.483 ROI=+0.317 pnl=+0.153 |
| DOWN leader | n=3,474 win=0.637 LB=0.621 price=0.478 ROI=+0.332 pnl=+0.159 |

## Verdict
**DOES NOT cleanly survive — shuffle null still profitable (price-selection, not P(Hold)).** Do not treat the +27% as a P(Hold) edge until resolved.

_Executed-trade research; a trade price is not an executable resting ask. Live /book required regardless._