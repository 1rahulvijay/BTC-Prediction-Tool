# Impact → Reversal / Big-Drop Probe — 2026-06-30 (NEGATIVE, logged)

**Hypothesis (user):** market *impact / absorption* predicts **reversal & downside flush**, even though it does
not predict direction or |move| size:
- big flow + tiny move = **absorption** → the book ate the order → expect **reversal / mean-revert**;
- small flow + big move = **fragile** book → expect **snap-back**;
- big move + persistence = real repricing → continues.
Targets = the user's line-cross / P(Hold)-failure / big-drop, proxied on the 1m matrix.

**Why this probe exists:** `probe_impact_residual.py` already tested impact vs **|move| magnitude** → redundant
over rv_15m (+0.001). That answered "will a big move happen," **not** "will the move *revert*" — the actual
absorption claim. This probe tests the reversal/big-drop *shape* target, which nothing else covered.

## Method — v2 (corrected after a feature critique)
v1 was rejected as a possible construction artifact: `absorption`/`fragility` were exact negatives dominated by
`|bar_move|` (already in the baseline), the impact scale `k` was hard-coded, and the test was unconditional with
a 1-bar anchor. **v2 fixes all three:**
- 518,400 1m rows. Features at `close[t]`; **square-root-law scale `b` FIT on train rows only** (leak-free);
  labels strictly future; temporal 70/30.
- **Impact block (rebuilt):** `impact_resid` (signed: moved more/less than flow predicts, `b=2.83e4` fit),
  `impact_resid_abs`, `absorbed_ratio = |impulse| / pred_mag`, `elasticity = |impulse| / |flow_K|`
  (NOT collinear with |impulse|), `vpin`, `|large_trade_imbalance|`, `|cvd_divergence|`.
- **Multi-bar impulse anchor** `impulse = close[t] − close[t−3]` with `flow_K/vol_K` over the same window.
- **rv baseline:** `rv_15m, rv_30m, rv_60m, |impulse|`.
- **REVERSAL** = next-5m move opposite the impulse (≥ $20), tested **unconditionally AND conditioned on the
  top-30% impulse subset** (|impulse| ≥ $67 — where the absorption claim lives). **BIG-DROP** = `min(low[t+1..t+5]) − close[t] ≤ −$50`.
- Test = incremental AUC (rv vs rv+impact) + **shuffled-null on the impact block** (permute the block, 100×).

## Result — NO LIFT (the corrected construction is a STRONGER null)
| Target | base rate | rv baseline AUC | +impact AUC | lift | null p |
|---|---|---|---|---|---|
| Reversal (all) | 38.9% | **0.575** | 0.568 | **−0.007** | 1.000 |
| **Reversal (top-30% impulse, conditional)** | 44.1% | **0.538** | 0.533 | **−0.004** | 0.610 |
| Big-drop | 44.7% | **0.698** | 0.695 | **−0.002** | 1.000 |

On the **conditional** subset — the strongest form of the hypothesis — every impact feature is univariate
**~0.50–0.52 (coin-flip)**, and the block adds nothing (p=0.61). Telling detail: even *rv* only reaches 0.538
on big-impulse reversal, so **reversal-after-a-move is near coin-flip at 1m for *every* feature**, not just flow.
This is no longer attributable to a broken construction — the scale is fit, the features are orthogonal, the
test is conditioned. The 1m matrix simply has no reversal-timing information.

## Conclusion
**The 1-minute matrix order-flow cannot improve reversal or big-drop prediction beyond realized volatility.**
This is the **third** independent confirmation that the matrix flow has no incremental edge — timing (earlier
flow-proxy), and now reversal *and* downside flush. The absorption/fragility idea is conceptually right but
the effect lives **sub-second**, where 1m bars average it away.

- **rv is the right input for big-drop** (baseline AUC 0.697 alone) — consistent with the live big-drop keeper
  (gated AUC ~0.75 on the keepers). Flow adds nothing there.
- **The only untested ground is sub-second:** `probe_l2_linecross.py` on `microstructure.duckdb` (OFI / microprice
  / book imbalance at 5–60s). That is **gated on the recorder accruing days of 1s snapshots** — do not build an
  impact head until that probe clears its own cost-survival gate. Honest prior (arXiv 2602.00776): BTC's L2 edge
  did not survive costs even at 3s, so expect marginal.

## Reproduce
```
python backend/research/standalone/probe_impact_reversion.py --horizon 5 --drop 50 --shuffle-null 100
python backend/research/standalone/probe_impact_reversion.py --selftest
python backend/research/standalone/probe_impact_residual.py        # the |move|-timing sibling (also redundant)
python backend/research/standalone/probe_l2_linecross.py           # the sub-second test (gated on the recorder)
```
