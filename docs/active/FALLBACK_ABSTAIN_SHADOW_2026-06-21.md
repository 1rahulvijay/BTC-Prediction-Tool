# Fallback-Abstain Shadow — 2026-06-21

Read-only replay over **705 regime-era directional rounds** (2026-06-18 → 2026-06-21, 5m+15m). No live behavior changed. A policy is promotable only when the **abstained set is weak** (≈ coin-flip) AND the **retained set's Wilson-LB improves and holds** as n grows.

## lean_source × regime reference
| lean_source | regime | n | acc % | Wilson-LB |
|---|---|---|---|---|
| fallback | LOW_VOLATILITY | 27 | 63.0 | 44.2 |
| fallback | RANGE | 166 | 55.4 | 47.8 |
| fallback | TRENDING_DOWN | 133 | 51.1 | 42.7 |
| fallback | TRENDING_UP | 162 | 50.0 | 42.4 |
| fallback | HIGH_VOLATILITY | 24 | 45.8 | 27.9 |
| model | LOW_VOLATILITY | 14 | 64.3 | 38.8 |
| model | RANGE | 52 | 59.6 | 46.1 |
| model | TRENDING_UP | 53 | 47.2 | 34.4 |
| model | TRENDING_DOWN | 50 | 46.0 | 33.0 |
| model | HIGH_VOLATILITY | 24 | 45.8 | 27.9 |

_The fallback × TRENDING cells are the suspected coin-flips this policy targets._

## All regime-era rounds  (n=705)
| policy | kept | coverage % | retained acc % | Wilson-LB | cut (n@acc) | LB>50? |
|---|---|---|---|---|---|---|
| baseline (keep all) | 705 | 100.0 | 52.2 | 48.5 | — | — |
| abstain fallback in TRENDING | 410 | 58.2 | 53.4 | 48.6 | 295 @ 51% | · |
| abstain fallback in TRENDING+HIGH_VOL | 386 | 54.8 | 53.9 | 48.9 | 319 @ 50% | · |
| abstain ALL fallback leans | 193 | 27.4 | 51.3 | 44.3 | 512 @ 53% | · |

## 5m only  (n=531)
| policy | kept | coverage % | retained acc % | Wilson-LB | cut (n@acc) | LB>50? |
|---|---|---|---|---|---|---|
| baseline (keep all) | 531 | 100.0 | 50.7 | 46.4 | — | — |
| abstain fallback in TRENDING | 285 | 53.7 | 53.3 | 47.5 | 246 @ 48% | · |
| abstain fallback in TRENDING+HIGH_VOL | 265 | 49.9 | 54.0 | 47.9 | 266 @ 47% | · |
| abstain ALL fallback leans | 97 | 18.3 | 46.4 | 36.8 | 434 @ 52% | · |

## 15m only  (n=174)
| policy | kept | coverage % | retained acc % | Wilson-LB | cut (n@acc) | LB>50? |
|---|---|---|---|---|---|---|
| baseline (keep all) | 174 | 100.0 | 56.9 | 49.5 | — | — |
| abstain fallback in TRENDING | 125 | 71.8 | 53.6 | 44.9 | 49 @ 65% | · |
| abstain fallback in TRENDING+HIGH_VOL | 121 | 69.5 | 53.7 | 44.9 | 53 @ 64% | · |
| abstain ALL fallback leans | 96 | 55.2 | 56.2 | 46.3 | 78 @ 58% | · |

## Read
- **What the target policy cuts:** 295 fallback-in-TRENDING calls at **50.5%** (Wilson-LB 44.8%) — a weak/coin-flip set worth dropping.
- **Effect on retained calls:** baseline 52.2% (LB 48.5%, n=705) → retained 53.4% (LB 48.6%, n=410) at 58% coverage.
- ✅ = retained Wilson-LB > 50% at this n. Promote only if it **holds** as n grows AND the cut set stays weak. Until then this is shadow-only — `_bet_lean()` is untouched.

**If promoted (later, with sign-off):** in `_bet_lean()`, return `NEUTRAL` when `rawDirection` is NEUTRAL (fallback path) AND `p['regime']` is a TRENDING regime — i.e. don't tilt a coin-flip side into a trend; let the window show 'no call'.

---

## VERDICT (2026-06-21): DO NOT promote — the data does not support it
The shadow did its job: it killed a change the live 15m card *suggested* but the evidence refutes.
1. **No policy clears the bar.** Every retained Wilson-LB stays **< 50%** (abstain-fallback-in-TRENDING: 48.6%, barely above the 48.5% baseline). The accuracy lift is marginal (+1.2pp) because the cut set isn't *bad*, it's just *coin-flip* (50.5%) — removing ~50% calls can't lift the remainder much.
2. **At 15m it's actively harmful.** Fallback-in-TRENDING at 15m is **65% (n=49)** — winners. Abstaining them drops retained 15m accuracy **56.9% → 53.6%**. The pooled 50.5% hides this: the weakness is a **5m** effect (48%, n=246), not 15m.
3. **The screenshot was small-sample noise.** That 15m card's "fallback 0/4" is a 4-round streak; the broader 15m fallback-in-TRENDING is 65%. Exactly why we shadow-test instead of reacting to 7 rounds.
4. **Singling out *fallback* is the wrong cut anyway.** In TRENDING, the **model** (committed) leans are *weaker* than fallback: model-TRENDING_UP 47.2% / model-TRENDING_DOWN 46.0% vs fallback ~50–51%. Trending is hard for *everything*; the lean_source split isn't where the edge is.

**Action: keep `_bet_lean()` unchanged.** The better-founded lever remains regime-level abstention (the [regime_gate_shadow](REGIME_GATE_SHADOW_2026-06-21.md) "act only RANGE/LOW_VOL", which abstains *all* leans in trend) — and even that is unconfirmed on the recent window. Re-run this if curious as n grows, but there is no promote case today.