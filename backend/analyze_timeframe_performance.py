"""
analyze_timeframe_performance.py — which TIMEFRAME, TIME-OF-DAY, and DAY the model performs best.
=================================================================================================
Reads the logged price-to-beat rounds and answers:
  1. TIMEFRAME — over the last N hours (default 24), which horizon (1/3/5/7/10/15/30m) wins most,
     split MODEL (committed lean) vs FALLBACK (~coin-flip tilt), with Wilson lower bounds + 5m-vs-15m.
  2. TIME-OF-DAY — win-rate by hour-of-day in your chosen --tz (default Europe/Berlin = CEST), the best
     contiguous window, and the 15:00–20:00 block vs the rest.
  3. PER-DAY — win-rate for every calendar day (in --tz) + a day-of-week summary.
  4. Last K resolved rounds (default 100) for 5m and 15m (printed + full CSV).

--source pyth   → the Polymarket settlement anchor (always in the DB).
--source binance→ the Binance-priced mirror (persisted with source='binance'; only rows that accrued
                  AFTER persistence was enabled + a restart).

Read-only. Run with the backend stopped (DuckDB blocks cross-process reads while the app holds the lock).

Usage:  python backend/analyze_timeframe_performance.py [--hours 24] [--tod-days 0] [--last 100]
                                                        [--source pyth] [--tz Europe/Berlin]
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import duckdb

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
DB = os.path.join(DATA, "analytics.duckdb")
HORIZONS = (1, 3, 5, 7, 10, 15, 30)

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

_TZ = None        # set in main() from --tz
_TZ_FALLBACK_H = 2  # CEST = UTC+2 (used only if zoneinfo/tzdata is unavailable)


def local_dt(ts_ms: int):
    dt = datetime.fromtimestamp(ts_ms / 1000, timezone.utc)
    if _TZ is not None:
        return dt.astimezone(_TZ)
    return dt + timedelta(hours=_TZ_FALLBACK_H)


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    cen = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (100 * (cen - half), 100 * (cen + half))


def md(rows, headers):
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join("–" if v is None else (f"{v:.1f}" if isinstance(v, float) else str(v)) for v in r) + " |")
    return "\n".join(out)


def main():
    global _TZ
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--tod-days", type=int, default=0, help="time-of-day/per-day window in days (0=all)")
    ap.add_argument("--last", type=int, default=100)
    ap.add_argument("--source", choices=["pyth", "binance"], default="pyth")
    ap.add_argument("--tz", default="Europe/Berlin", help="IANA tz for hour/day buckets (e.g. Europe/Berlin=CEST)")
    ap.add_argument("--block", type=int, default=4, help="hour-block size for the time-of-day block table")
    ap.add_argument("--target-block", type=int, default=20, help="block start hour to test for daily repeatability")
    a = ap.parse_args()
    src = a.source
    if ZoneInfo is not None:
        try:
            _TZ = ZoneInfo(a.tz)
        except Exception:
            _TZ = None
    tzname = a.tz if _TZ is not None else f"UTC+{_TZ_FALLBACK_H} (fallback)"
    anchor = "Polymarket / Pyth" if src == "pyth" else "Binance mirror"
    out_path = os.path.join(ROOT, "docs", "active", f"TIMEFRAME_PERFORMANCE_{src}_{date.today().isoformat()}.md")

    if not os.path.exists(DB):
        print("no analytics.duckdb"); return
    try:
        c = duckdb.connect(DB, read_only=True)
    except Exception as e:
        print("analytics.duckdb is locked by the running app — stop the backend briefly and re-run.\n"
              f"  ({str(e)[:90]})"); return

    _cols = [r[1] for r in c.execute("PRAGMA table_info('price_to_beat')").fetchall()]
    if "source" in _cols:
        SRC = f"COALESCE(source,'pyth') = '{src}'"
    else:
        SRC = "1=1" if src == "pyth" else "1=0"

    L = [f"# Timeframe / Time-of-Day / Per-Day Performance — {anchor} — {date.today().isoformat()}", "",
         f"Source = `{src}`. Win-rate = directional hit on UP/DOWN. MODEL = committed lean; FALLBACK = "
         f"two-way tilt (~coin-flip). Wilson-LB = 95% lower bound. **All clock buckets are in `{tzname}`.**", ""]

    tot = c.execute(f"SELECT count(*) FROM price_to_beat WHERE resolved AND our_direction IN ('UP','DOWN') AND {SRC}").fetchone()[0]
    if tot == 0:
        L.append(f"_No resolved `{src}` rounds yet._" + (
            "  Binance persistence was just enabled — restart the backend, let rounds accrue, then re-run "
            "with `--source binance`." if src == "binance" else ""))
        c.close()
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(L))
        print("\n".join(L)); print(f"\nWrote {out_path}"); return

    # ── 1. TIMEFRAME (last --hours) ──
    cutoff = int(time.time() * 1000) - a.hours * 3600 * 1000
    rows = c.execute(f"""SELECT horizon, COALESCE(lean_source,'model') ls, CASE WHEN hit THEN 1 ELSE 0 END
                         FROM price_to_beat WHERE resolved AND our_direction IN ('UP','DOWN')
                         AND {SRC} AND timestamp >= {cutoff}""").fetchall()
    L.append(f"## 1. Best timeframe (last {a.hours}h)")
    if not rows:
        L.append(f"_No resolved rounds in the last {a.hours}h._")
    else:
        agg = defaultdict(lambda: {"all": [0, 0], "model": [0, 0], "fallback": [0, 0]})
        for h, ls, hh in rows:
            agg[h]["all"][0] += 1; agg[h]["all"][1] += hh
            k = ls if ls in ("model", "fallback") else "model"
            agg[h][k][0] += 1; agg[h][k][1] += hh
        table = []
        for h in HORIZONS:
            if h not in agg:
                continue
            n, kk = agg[h]["all"]; mn, mk = agg[h]["model"]; fn, fk = agg[h]["fallback"]
            table.append((f"{h}m", n, 100 * kk / n, wilson(kk, n)[0], mn,
                          (100 * mk / mn if mn else None), fn, (100 * fk / fn if fn else None)))
        L.append(md(table, ["hz", "n", "win %", "Wilson-LB", "model n", "model win %", "fb n", "fb win %"]))
        L.append(f"_{a.hours}h is small — rankings noisy; weigh Wilson-LB and the model/fallback split._")

    # ── shared pull for time-of-day + per-day ──
    tod_cutoff = 0 if a.tod_days <= 0 else int(time.time() * 1000) - a.tod_days * 86400 * 1000
    todrows = c.execute(f"""SELECT timestamp, COALESCE(lean_source,'model') ls, CASE WHEN hit THEN 1 ELSE 0 END, horizon
                            FROM price_to_beat WHERE resolved AND our_direction IN ('UP','DOWN')
                            AND {SRC} AND timestamp >= {tod_cutoff}""").fetchall()
    span = "all history" if a.tod_days <= 0 else f"last {a.tod_days}d"
    by_hour = defaultdict(lambda: {"all": [0, 0], "model": [0, 0]})
    by_day = defaultdict(lambda: {"all": [0, 0], "model": [0, 0]})
    by_wd = defaultdict(lambda: {"all": [0, 0], "model": [0, 0]})
    WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for ts, ls, hh, _hz in todrows:
        dt = local_dt(ts)
        ism = ls != "fallback"
        for bucket, key in ((by_hour, dt.hour), (by_day, dt.date().isoformat()), (by_wd, dt.weekday())):
            bucket[key]["all"][0] += 1; bucket[key]["all"][1] += hh
            if ism:
                bucket[key]["model"][0] += 1; bucket[key]["model"][1] += hh

    # ── 2. TIME-OF-DAY ──
    L.append(f"\n## 2. Best time-of-day ({tzname}, {span}, n={len(todrows)})")
    htable = []
    for hr in range(24):
        d = by_hour.get(hr)
        if not d or d["all"][0] == 0:
            continue
        n, kk = d["all"]; mn, mk = d["model"]
        htable.append((f"{hr:02d}:00", n, 100 * kk / n, mn, (100 * mk / mn if mn else None)))
    L.append(md(htable, [f"{tzname.split('/')[-1]} hour", "n", "win %", "model n", "model win %"]))

    def block(hours):
        return (sum(by_hour[h]["all"][0] for h in hours), sum(by_hour[h]["all"][1] for h in hours),
                sum(by_hour[h]["model"][0] for h in hours), sum(by_hour[h]["model"][1] for h in hours))
    blocks = []
    for start in range(24):
        hrs = [(start + i) % 24 for i in range(5)]
        n, k, mn, mk = block(hrs)
        if n >= 30:
            blocks.append((start, n, 100 * k / n, wilson(k, n)[0], mn, (100 * mk / mn if mn else 0)))
    blocks.sort(key=lambda x: -x[2])
    if blocks:
        L.append("\n**Best 5-hour windows (≥30 rounds):**")
        L.append(md([(f"{s:02d}:00–{(s+5)%24:02d}:00", n, w, lb, mn, mw) for (s, n, w, lb, mn, mw) in blocks[:3]],
                    ["window", "n", "win %", "Wilson-LB", "model n", "model win %"]))
    win_hours = [15, 16, 17, 18, 19]
    wn, wk, wmn, wmk = block(win_hours)
    rest = [h for h in range(24) if h not in win_hours]
    rn, rk, _, _ = block(rest)
    if wn and rn:
        L.append(f"\n**15:00–20:00 {tzname} vs rest:** window **{100*wk/wn:.1f}%** "
                 f"(n={wn}, LB {wilson(wk,wn)[0]:.1f}%, model {(100*wmk/wmn if wmn else 0):.1f}%) "
                 f"vs rest **{100*rk/rn:.1f}%** — "
                 + ("window is stronger." if (wk/wn) > (rk/rn) else "window is NOT stronger."))

    # ── 2b. N-HOUR BLOCKS (focus: short horizons) ──
    nb = max(1, min(12, a.block))
    starts = list(range(0, 24, nb))

    def block_table(hf):
        agg = {s: [0, 0, 0, 0] for s in starts}  # n, k, model_n, model_k
        for ts, ls, hh, hz in todrows:
            if hf is not None and hz != hf:
                continue
            s = (local_dt(ts).hour // nb) * nb
            agg[s][0] += 1; agg[s][1] += hh
            if ls != "fallback":
                agg[s][2] += 1; agg[s][3] += hh
        rows = []
        for s in starts:
            n, k, mn, mk = agg[s]
            if n == 0:
                continue
            rows.append((f"{s:02d}:00–{(s+nb)%24 or 24:02d}:00", n, 100*k/n, wilson(k, n)[0],
                         (100*mk/mn if mn else None)))
        cands = [s for s in starts if agg[s][0] >= 20]
        best = max(cands, key=lambda s: agg[s][1]/agg[s][0]) if cands else None
        return rows, best, (agg[best] if best is not None else None)

    L.append(f"\n## 2b. Time-of-day in {nb}-hour blocks ({tzname})")
    for label, hf in (("All horizons", None), ("5m only (shortest tradeable)", 5), ("15m only", 15)):
        rows, best, bd = block_table(hf)
        if not rows:
            L.append(f"\n**{label}:** no rounds."); continue
        L.append(f"\n**{label}:**")
        L.append(md(rows, ["block", "n", "win %", "Wilson-LB", "model win %"]))
        if best is not None and bd:
            n, k = bd[0], bd[1]
            L.append(f"- **best block: {best:02d}:00–{(best+nb)%24 or 24:02d}:00** at {100*k/n:.1f}% "
                     f"(n={n}, Wilson-LB {wilson(k, n)[0]:.1f}%)"
                     + ("" if wilson(k, n)[0] > 50 else " — note: LB < 50%, not a real edge yet."))

    # ── 3. PER-DAY + day-of-week ──
    L.append(f"\n## 3. Per-day ({tzname}, {span})")
    dtable = []
    for day in sorted(by_day):
        n, kk = by_day[day]["all"]; mn, mk = by_day[day]["model"]
        dtable.append((day, n, 100 * kk / n, wilson(kk, n)[0], mn, (100 * mk / mn if mn else None)))
    L.append(md(dtable, ["day", "n", "win %", "Wilson-LB", "model n", "model win %"]))
    best_day = max(by_day, key=lambda d: by_day[d]["all"][1] / by_day[d]["all"][0]) if by_day else None
    if best_day:
        bn, bk = by_day[best_day]["all"]
        L.append(f"\n- **Best day:** {best_day} at {100*bk/bn:.1f}% (n={bn}). "
                 "Day-to-day swing is mostly which regime dominated that day, not a repeatable signal.")
    L.append("\n**By day-of-week:**")
    wtable = []
    for wd in range(7):
        d = by_wd.get(wd)
        if not d or d["all"][0] == 0:
            continue
        n, kk = d["all"]; mn, mk = d["model"]
        wtable.append((WD[wd], n, 100 * kk / n, wilson(kk, n)[0], (100 * mk / mn if mn else None)))
    L.append(md(wtable, ["weekday", "n", "win %", "Wilson-LB", "model win %"]))
    L.append("\n_Time-of-day / per-day need many days to be trustworthy; with limited history each bucket is "
             "thin. Re-run as data grows and weigh the Wilson-LB (a band straddling 50% = no real edge)._")

    # ── 3b. REPEATABILITY: does the target block beat the rest of the day, day after day? ──
    tgt = a.target_block % 24
    tgt_hours = set((tgt + i) % 24 for i in range(nb))
    tgt_label = f"{tgt:02d}:00–{(tgt+nb)%24 or 24:02d}:00 {tzname}"
    L.append(f"\n## 3b. Is the {tgt_label} edge repeating? (per-day)")
    for label, hf in (("All horizons", None), ("15m only", 15)):
        per = defaultdict(lambda: [0, 0, 0, 0])  # block_n, block_k, rest_n, rest_k
        for ts, ls, hh, hz in todrows:
            if hf is not None and hz != hf:
                continue
            dt = local_dt(ts); day = dt.date().isoformat()
            if dt.hour in tgt_hours:
                per[day][0] += 1; per[day][1] += hh
            else:
                per[day][2] += 1; per[day][3] += hh
        rows = []; above = 0; beat = 0; ndays = 0
        for day in sorted(per):
            bn, bk, rn, rk = per[day]
            if bn == 0:
                continue
            ndays += 1
            bw = 100 * bk / bn; rw = (100 * rk / rn if rn else None)
            if bw > 50:
                above += 1
            mark = "–"
            if rw is not None:
                if bw > rw:
                    beat += 1; mark = "✓ better"
                else:
                    mark = "✗ worse"
            rows.append((day, bn, bw, rw, mark))
        if not rows:
            L.append(f"\n**{label}:** no rounds in that block."); continue
        L.append(f"\n**{label} — the {tgt_label} block, each day:**")
        L.append(md(rows, ["day", "block n", "block win %", "rest-of-day %", "vs rest"]))
        L.append(f"- Block was **>50% on {above}/{ndays} days**; **beat the rest of that day on {beat}/{ndays} days**. "
                 + ("A real time-of-day edge should beat the rest of the day on most days — "
                    f"{beat}/{ndays} is {'consistent.' if ndays and beat/ndays >= 0.6 else 'NOT consistent (looks like a few days carrying it, i.e. noise).'}"))

    # ── 4. last K rounds ──
    L.append(f"\n## 4. Last {a.last} rounds — 5m & 15m ({anchor})")
    for h in (5, 15):
        recent = c.execute(f"""SELECT to_timestamp(timestamp/1000), round(price_to_beat,2), round(actual_price,2),
                                  our_direction, CASE WHEN hit THEN 'WON' ELSE 'LOST' END,
                                  COALESCE(lean_source,'model'), COALESCE(regime,'?'), timestamp
                               FROM price_to_beat WHERE horizon={h} AND resolved AND our_direction IN ('UP','DOWN')
                               AND {SRC} ORDER BY timestamp DESC LIMIT {a.last}""").fetchall()
        if not recent:
            L.append(f"\n**{h}m:** no resolved rounds."); continue
        wins = sum(1 for r in recent if r[4] == "WON")
        L.append(f"\n**{h}m — last {len(recent)}: {wins} WON / {len(recent)-wins} LOST ({100*wins/len(recent):.0f}%)**")
        L.append(md([(local_dt(r[7]).strftime("%Y-%m-%d %H:%M"), r[1], r[2], r[3], r[4], r[5], r[6]) for r in recent[:25]],
                    [f"time ({tzname.split('/')[-1]})", "beat", "close", "dir", "result", "lean", "regime"]))
        csvp = os.path.join(DATA, f"last_rounds_{src}_{h}m.csv")
        with open(csvp, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f); w.writerow([f"time_{tzname}", "beat", "close", "dir", "result", "lean", "regime"])
            for r in recent:
                w.writerow([local_dt(r[7]).strftime("%Y-%m-%d %H:%M"), r[1], r[2], r[3], r[4], r[5], r[6]])
        L.append(f"_Full {len(recent)} → `{os.path.relpath(csvp, ROOT)}`_")

    c.close()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("\n".join(L))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
