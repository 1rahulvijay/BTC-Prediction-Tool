# bookDepth Veto / Regime Probe — 2026-07-02

Real P(Hold) snapshots (53,097, 100% joined to bookDepth) x Binance liquidity regime. bookDepth may only VETO/haircut — never create a trade. Judged on retained-call quality, not AUC.

## Test A — P(Hold) realized by liquidity regime

**P(Hold) ≥ 0.93** (n=12,136, overall held 95.8%):
| liquidity regime | n | held% | Wilson-LB | mean P(Hold) | vs overall |
|---|---|---|---|---|---|
| DEEP | 3,941 | 95.9 | 95.2 | 0.985 | +0.1pp |
| NORMAL | 2,927 | 95.3 | 94.4 | 0.984 | -0.5pp |
| THIN | 3,100 | 96.2 | 95.5 | 0.984 | +0.4pp |
| VACUUM | 2,168 | 95.8 | 94.9 | 0.984 | +0.1pp |

**P(Hold) ≥ 0.95** (n=10,846, overall held 96.3%):
| liquidity regime | n | held% | Wilson-LB | mean P(Hold) | vs overall |
|---|---|---|---|---|---|
| DEEP | 3,574 | 96.3 | 95.7 | 0.990 | +0.0pp |
| NORMAL | 2,586 | 95.8 | 95.0 | 0.989 | -0.5pp |
| THIN | 2,733 | 96.7 | 96.0 | 0.989 | +0.4pp |
| VACUUM | 1,953 | 96.2 | 95.3 | 0.989 | -0.1pp |

## Test B — veto: P(Hold)≥0.93  vs  +not-VACUUM
- baseline A: 12,136 calls, held 95.8%, Wilson-LB 95.4
- vetoed  B: 9,968 calls, held 95.8%, Wilson-LB 95.4
- veto dropped 2168 VACUUM calls → **avoided-bad 90** vs **lost-good 2078** → net -1988; coverage 82.1% of A

## Test C — where the VACUUM penalty concentrates (interaction)
- near-anchor (dist<median): VACUUM held 91.1% vs 93.9% → penalty +2.8pp (n_vac=948)
- late (secs_left<60): VACUUM held 92.9% vs 95.1% → penalty +2.1pp (n_vac=893)
- TREND/VOLATILE regime: VACUUM held 96.3% vs 96.2% → penalty -0.0pp (n_vac=1684)

## Verdict
**NO VETO VALUE — VACUUM penalty -0.1pp / avoided 90 vs lost 2078. bookDepth does not improve retained-call quality either. Drop it (dead for prediction AND veto at this resolution).**

_Judged on retained-call quality (held% / Wilson-LB / bad-vs-good), never AUC. bookDepth can only veto/haircut, never create a trade. Research only — shadow first if it passes._