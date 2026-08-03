# RESULT — BYBIT_L2_MAKER_V2_TRADE_DRIVEN

**Scored `2026-08-04`.** Supersedes `BYBIT_L2_MAKER_RESULT_2026-08-03.md`, which carries a
banner pointing here. BTCUSDT, Bybit linear perpetual, 2026-08-02, 200-level book joined to
845,890 real trade prints.

## What changed and why

V1 filled a resting order whenever depth at its price fell below what stood ahead at posting:

```python
if level <= order["queue_ahead"] - ORDER_SIZE:   # -> FILLED
```

Depth falls for two reasons and only one is an execution. A trade consumes the queue and fills
you. A **cancellation** ahead of you advances your position and executes nobody. Counting both
as fills is what produced a 99.5% fill rate — that number was the tell.

V2 fills only on real aggressor volume reaching the price:

```text
cumulative opposing-side volume at-or-through P since posting  >=  queue_ahead + ORDER_SIZE
```

A resting BUY at P is executed only by SELL aggressors printing at or below P. Cancellations are
treated conservatively as **not** advancing us: aggregated L2 cannot say whether a cancellation
sat ahead of or behind our order, and assuming "ahead" is the assumption that flatters the
result. The corrected fill rate is therefore a **lower bound**.

## Result

| | V1 (depth rule) | V2 (trade-driven) |
|---|---:|---:|
| fill rate | 99.5% | **39.8%** |
| gross markout 60s | +0.080 bps | **−0.562 bps** |
| net per FILLED order | −0.920 bps | **−1.562 bps** |
| net per SUBMITTED order | −0.916 bps | −0.622 bps |

Two things the depth rule was hiding.

**Roughly 60% of V1's fills were cancellations.** Visible in the 99.5% figure alone.

**Gross markout flipped sign.** V1 reported adverse selection as *benign* precisely because
cancellation-fills are the benign case — nobody crossed against you. Filtered to real executions
the profile is textbook and monotone in horizon:

```text
1s  -0.218    5s  -0.318    15s  -0.450    30s  -0.520    60s  -0.562
```

You are systematically filled by someone better informed, and it worsens the longer you hold.
**V1 measured the opposite sign of the effect it was built to measure.**

Net per *submitted* order appears to improve (−0.916 → −0.622). That is not an improvement:
fewer orders fill, so the fee is paid less often. Per actual fill the economics are ~70% worse.

## Conclusion

Unchanged, and now resting on an honest number: **a passive fill does not cover the 1.0 bps
maker fee.** It is short by 1.56 bps per fill, not 0.92.

## Limits

One day. The conservative cancellation assumption makes 39.8% a floor, not an estimate. The sign
and magnitude of adverse selection are the robust parts; the exact fill rate is not.

## Artefacts

```text
rule + 14 assertions   research/bybit_trade_driven_fill.py
runner                 research/bybit_l2_maker_v2_trade_driven.py
trade tape             public.bybit.com/trading/BTCUSDT/  (gitignored, refetchable)
commit                 77f5db7
```

V1 is left untouched and its frozen result stands as published, so the two remain comparable
rather than one silently replacing the other.
