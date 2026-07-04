# HF Trades Token Mapping — 2026-07-02

Deterministic token→UP/DOWN mapping from resolution + late trade price (winner settles to ~1.0). Quarantine any market not cleanly separated. Foundation for the trades edge pipeline.

## Result
- markets mapped: **7,905** (6,017 5m, 1,888 15m)
- **clean: 5,920 (74.9%)** · quarantined (ambiguous): 1,985 (25.1%)
- separation (winner − loser late price): median **0.805**, clean-set median 0.919
- winner late price (clean): median **0.966** (→1.0 ✓) · loser: median **0.047** (→0.0 ✓)

## Verdict
**USABLE — 75% clean, excellent separation; use the clean set, drop the quarantined 25%.** ("WEAK" below is only
the >80% auto-threshold; the clean mapping itself is deterministic and high-quality.)

> **Clean-rate reconciliation (why 75% here vs an earlier 83.6%):** the earlier figure (1,047 markets, 83.6%
> clean) was a **4-day sample** (`--days 4`); this canonical run is the **full 21 days** (`--days 0`, 7,905
> markets, 74.9% clean). The full universe includes more low-liquidity early markets with too few late trades to
> separate the winner (→ quarantined), so the rate is lower. **The downstream edge/robustness analysis uses ONLY
> the rows passing THIS full clean token map.**
- The mapping is derived from settlement + price, so it is only as good as the resolution join and the late-trade coverage. Quarantined markets (no clean 1.0/0.0 separation) are excluded downstream.
- Wrote `data/hf_trades_cache/token_map.parquet` for `build_pm_hf_trade_snapshots.py`.
- ⚠️ This is still **executed-trade** research; a trade price is not an executable resting ask. The live `/book` recorder remains required for fillability proof.