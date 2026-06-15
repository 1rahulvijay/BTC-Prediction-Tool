"""
data_quality_audit.py — training-DATA quality audit + optional cleaning.
========================================================================
Bad bars teach the model bad lessons. This audits the bar pipeline the heads/ensemble learn from
(1m OHLC reconstructed from SPOT aggTrades) and reports the problems; `--clean` writes a cleaned
bar set. Pairs with diagnose_model.py (that finds bad FEATURES; this finds bad DATA).

Checks:
  • bad OHLC          — high<low, close/open outside [low,high], non-positive, non-finite.
  • time integrity    — duplicate timestamps, non-monotonic, GAPS (missing minutes).
  • stale runs        — N+ consecutive identical closes (a frozen/zero-liquidity feed).
  • extreme returns   — |1m log-return| over CAP (flash-wick / glitch) → winsorize candidates.
  • label ambiguity   — beat label where |close−open| ≈ 0 (a coin-flip tie the model can't learn).
  • regime balance    — up-bar fraction; flags a one-directional window (the bias trap).

HONEST SCOPE: cleaning makes the model learn the EXISTING signal better (calibration/stability/
a point or two) — it does NOT manufacture edge. The 5m ceiling moves only with new INFORMATION.

Usage:  python backend/data_quality_audit.py --days 30            # audit (report only)
        python backend/data_quality_audit.py --start S --end E --clean   # write clean_bars.parquet
"""
import argparse
import os

import numpy as np

from train_beat_classifier import _ohlc_for_dates, beat_labels, resolve_dates

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data")
OUT_PATH = os.path.join(DATA_DIR, "clean_bars.parquet")
RET_CAP = 0.05          # |1m log-return| above this = flash/glitch (5%)
STALE_RUN = 5           # this many identical closes in a row = frozen feed
TIE_EPS = 1e-5          # |close-open|/open below this = ambiguous beat label


def audit_bars(t, o, h, l, c):
    n = len(c)
    fin = np.isfinite(o) & np.isfinite(h) & np.isfinite(l) & np.isfinite(c)
    pos = (o > 0) & (h > 0) & (l > 0) & (c > 0)
    ohlc_ok = (h >= l) & (h >= np.maximum(o, c) - 1e-9) & (l <= np.minimum(o, c) + 1e-9)
    bad_ohlc = int((~(fin & pos & ohlc_ok)).sum())
    dup = int(n - len(np.unique(t)))
    nonmono = int((np.diff(t) <= 0).sum())
    dt = np.diff(t)
    gaps = int(np.sum(np.maximum(dt // 60_000 - 1, 0)))   # missing minutes
    # stale runs
    same = np.concatenate([[False], c[1:] == c[:-1]])
    run = 0; stale_bars = 0; max_run = 0
    for s in same:
        run = run + 1 if s else 0
        max_run = max(max_run, run)
        if run >= STALE_RUN - 1:
            stale_bars += 1
    logc = np.log(np.where(c > 0, c, 1.0))
    ret = np.concatenate([[0.0], np.diff(logc)])
    extreme = int((np.abs(ret) > RET_CAP + 1e-9).sum())   # tol: a winsorized-to-CAP bar is OK
    up_frac = float((c >= o).mean())
    return dict(n=n, bad_ohlc=bad_ohlc, dup_times=dup, non_monotonic=nonmono, missing_minutes=gaps,
                stale_bars=stale_bars, max_stale_run=max_run + 1, extreme_returns=extreme,
                up_bar_frac=round(up_frac, 4))


def label_ambiguity(o, c, horizons=(3, 5, 15)):
    out = {}
    for hh in horizons:
        y = beat_labels(o, c, hh); m = y >= 0
        if m.sum() < 50:
            continue
        oo = o[:len(o) - hh]; cc = c[hh - 1:hh - 1 + len(oo)]
        rel = np.abs(cc - oo) / np.where(oo > 0, oo, 1.0)
        out[hh] = round(float((rel < TIE_EPS).mean()), 4)
    return out


def clean_bars(t, o, h, l, c):
    """Remove bad/dup/non-monotonic bars; winsorize extreme 1m returns. Returns cleaned arrays + log."""
    n0 = len(c)
    fin = np.isfinite(o) & np.isfinite(h) & np.isfinite(l) & np.isfinite(c)
    pos = (o > 0) & (h > 0) & (l > 0) & (c > 0)
    ohlc_ok = (h >= l) & (h >= np.maximum(o, c) - 1e-9) & (l <= np.minimum(o, c) + 1e-9)
    keep = fin & pos & ohlc_ok
    t, o, h, l, c = t[keep], o[keep], h[keep], l[keep], c[keep]
    # dedup (keep last per timestamp) + sort monotonic
    order = np.argsort(t, kind="stable"); t, o, h, l, c = t[order], o[order], h[order], l[order], c[order]
    # dedup keeping the LAST occurrence per timestamp (np.unique keeps first):
    last = {}
    for i in range(len(t)):
        last[int(t[i])] = i
    sel = np.array(sorted(last.values()))
    t, o, h, l, c = t[sel], o[sel], h[sel], l[sel], c[sel]
    # winsorize extreme returns (clip close to prior_close * exp(±CAP), clamp h/l)
    wz = 0
    for i in range(1, len(c)):
        r = np.log(c[i] / c[i - 1]) if c[i - 1] > 0 else 0.0
        if abs(r) > RET_CAP:
            c[i] = c[i - 1] * np.exp(np.sign(r) * RET_CAP)
            h[i] = max(h[i] if h[i] <= c[i] * 1.02 else c[i], c[i], o[i])
            l[i] = min(l[i] if l[i] >= c[i] * 0.98 else c[i], c[i], o[i])
            wz += 1
    return (t, o, h, l, c), dict(removed=n0 - len(c), winsorized=wz, kept=len(c))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start"); ap.add_argument("--end"); ap.add_argument("--validate")
    ap.add_argument("--days", type=int)
    ap.add_argument("--clean", action="store_true", help="write data/clean_bars.parquet")
    args = ap.parse_args()
    dates, _ = resolve_dates(args)
    if not dates:
        ap.error("provide --start/--end, --validate DATE, or --days N")

    T, O, H, L, C = _ohlc_for_dates(dates)
    if C is None or len(C) < 50:
        print("Not enough bars."); return

    a = audit_bars(T, O, H, L, C)
    print("\n" + "=" * 60 + "\nDATA QUALITY AUDIT\n" + "=" * 60)
    print(f"  bars analyzed      : {a['n']:,}")
    print(f"  bad OHLC bars      : {a['bad_ohlc']}      (high<low / out-of-range / non-positive)")
    print(f"  duplicate stamps   : {a['dup_times']}")
    print(f"  non-monotonic time : {a['non_monotonic']}")
    print(f"  missing minutes    : {a['missing_minutes']}  (gaps in the 1m grid)")
    print(f"  stale bars         : {a['stale_bars']}  (max run {a['max_stale_run']} identical closes)")
    print(f"  extreme returns    : {a['extreme_returns']}  (|1m| > {RET_CAP*100:.0f}% — winsorize)")
    print(f"  up-bar fraction    : {a['up_bar_frac']*100:.1f}%  " +
          ("<-- ONE-DIRECTIONAL window (bias risk)" if abs(a['up_bar_frac'] - 0.5) > 0.06 else "(balanced)"))
    print("  label ambiguity (ties):", {f"{k}m": f"{v*100:.2f}%" for k, v in label_ambiguity(O, C).items()})

    issues = a['bad_ohlc'] + a['dup_times'] + a['non_monotonic'] + a['extreme_returns']
    print(f"\n  -> {issues} fixable issues; {a['missing_minutes']} gaps (cannot fabricate).")
    if args.clean:
        (t, o, h, l, c), log = clean_bars(T, O, H, L, C)
        import pandas as pd
        os.makedirs(DATA_DIR, exist_ok=True)
        pd.DataFrame({"ts_ms": t, "open": o, "high": h, "low": l, "close": c}).to_parquet(OUT_PATH, index=False)
        print(f"  CLEANED: removed {log['removed']}, winsorized {log['winsorized']}, kept {log['kept']}")
        print(f"  wrote -> {OUT_PATH}")
    else:
        print("  (run with --clean to write a cleaned bar set)")


if __name__ == "__main__":
    main()
