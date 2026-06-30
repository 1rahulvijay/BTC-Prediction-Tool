# Path → Champion Lift — 2026-06-30

Pre-registered: does `path_play` improve P(Hold)-gated retained-call quality (held% / line-cross / Wilson-LB) **independent of average P(Hold)**? n=36,492 joined snapshots with a reconstructed play (70% of 52,374) · source `pyth` · play split: WATCH=20207, FADE-SETUP=9898, SKIP=4289, RIDE=2098


## Full sample  (n=36,492)
| policy | n | cov% | held% | cross% | Wilson-LB | avg P(Hold) | Δheld vs A |
|---|---|---|---|---|---|---|---|
| A baseline      P(Hold)>=.93 | 8262 | 22.6 | 96.4 | 3.6 | 96.0 | 0.984 | — |
| B remove SKIP   .93 & play!=SKIP | 7486 | 20.5 | 96.9 | 3.1 | 96.5 | 0.984 | +0.5 |
| C strong path   .93 & RIDE/FADE | 3035 | 8.3 | 96.9 | 3.1 | 96.2 | 0.985 | +0.5 |
| D high-conf     .95 & play!=SKIP | 6601 | 18.1 | 97.5 | 2.5 | 97.1 | 0.989 | +1.2 |
| E conservative  .95 & RIDE/FADE | 2717 | 7.4 | 97.4 | 2.6 | 96.7 | 0.99 | +1.0 |

- **B vs A:** dropped 776 SKIP rows → **avoided-bad 68** (line-cross removed) · **lost-good 708** (held calls cut) · net avoided failures = **-640**

## Final holdout — NOT AVAILABLE
- `champion_snapshots` spans only **5.5 days** (2026-06-18 → 2026-06-23), so the last 30d window is the **entire sample**. There is **no independent temporal holdout** — every number below is in-sample over ~6 days. This caps the verdict at WATCH no matter how strong the in-sample lift is.

## Where the SKIP risk-signal bites (held% by P(Hold) band)
| P(Hold) band | n | non-SKIP held% | SKIP held% | SKIP penalty | SKIP share |
|---|---|---|---|---|---|
| 0.50-0.70 | 16585 | 61.6 | 58.8 | +2.8 | 13% |
| 0.70-0.80 | 5206 | 78.0 | 76.9 | +1.1 | 12% |
| 0.80-0.90 | 4895 | 87.9 | 84.6 | +3.2 | 11% |
| 0.90-0.93 | 1257 | 92.7 | 84.6 | +8.1 | 8% |
| 0.93-1.00 | 8262 | 96.9 | 91.2 | +5.7 | 9% |

_Decision-relevance: at P(Hold)≥0.93 even SKIP rows are ~91% holds, so the signal rarely flips a bet — it matters most in the LOWER bands, near the betting threshold, where a SKIP penalty can push a marginal call below the edge line. That is where to USE it (graded sizing / extra required edge), not as a binary drop at the high-confidence gate._

## Matched control — is the lift independent of P(Hold)?
- Across **11 matched strata** (n=2,081), weighted held% difference **play!=SKIP minus play==SKIP = +4.20pp** (controlling for P(Hold)/horizon/regime/seconds-left). A positive value = path adds value the P(Hold) level alone does not.

## Shuffled null (1000×)
- Real held% lift (play!=SKIP − play==SKIP, within the P(Hold)≥.93 gate) = **+5.68pp** (keep 96.9% vs skip 91.2%). Null mean +0.01pp, 95th pct +1.27pp → **p=0.000**.

## VERDICT
**WATCH — path play is a REAL, P(Hold)-independent risk signal (SKIP fails ~2-3x more, matched + shuffled-null significant), BUT it is NOT a good binary filter at the 0.93 gate (drops more winners than losers) and there is no independent temporal holdout yet**

- Signal real? **True** (matched +4.20pp, shuffled-null p=0.000). Net-positive binary filter? **False** (B drops 776 SKIP rows: avoided-bad 68 vs lost-good 708 → **net -640 winners**). Independent holdout? **False** (span 6d).
- B−A: held +0.53pp · Wilson-LB +0.54pp · shuffled-null p=0.000 · matched-control +4.20pp.
- Promote `play!=SKIP` into `champion_shadow` (shadow only) **only on PASS**; on WATCH keep logging; on FAIL drop it. Per the protocol: no live wiring on this single offline read.