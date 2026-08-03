"""
Signal scorecard — reusable analysis of every signal source in the app.

Run anytime to get the CURRENT state:
    python backend/analyze_signals.py

Covers: Polymarket mirror (price_to_beat), the combined ensemble leans, every base
model's directional votes, Kronos, and the FSR-PPO challenger — with directional-signal
counts and precision (NEUTRAL = "no bet", excluded from precision).

Read-only + lock-tolerant, so it is safe to run while the app is live.
"""
import os, time, datetime

os.environ.setdefault("BTC_DATA_DIR", os.path.join(os.path.dirname(__file__), "..", "data"))
import duckdb
import database


def connect(retries=20):
    for _ in range(retries):
        try:
            return duckdb.connect(database.DB_PATH, read_only=True)
        except Exception:
            time.sleep(1.0)
    return None


def pct(hit, n):
    return f"{(hit / n * 100):.0f}%" if n else "n/a"


def main():
    con = connect()
    if con is None:
        print("DB is locked by the backend (heavy write). Try again in a moment.")
        return
    print("=" * 72)
    print(f"SIGNAL SCORECARD  —  {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"DB: {database.DB_PATH}")
    print("=" * 72)

    # 1) Polymarket mirror -------------------------------------------------
    print("\n[1] POLYMARKET MIRROR (price_to_beat) — committed UP/DOWN bets only")
    for h in (5, 15):
        b = con.execute(f"""
            SELECT count(*) FILTER(WHERE our_direction IN('UP','DOWN')) bets,
                   count(*) FILTER(WHERE our_direction IN('UP','DOWN') AND resolved) res,
                   count(*) FILTER(WHERE our_direction IN('UP','DOWN') AND resolved AND hit) hit,
                   count(*) FILTER(WHERE our_direction='NEUTRAL') nobet
            FROM price_to_beat WHERE horizon={h}""").fetchone()
        print(f"   {h:>2}m: bets={b[0]} resolved={b[1]} correct={b[2]} "
              f"precision={pct(b[2], b[1])}  (no-bet rounds={b[3]})")

    # 2) Combined ensemble leans ------------------------------------------
    print("\n[2] COMBINED ENSEMBLE — directional leans (raw_direction), strict by move sign")
    tot = res = cor = sig = 0
    for h in (1, 3, 5, 7, 10, 15, 30):
        rows = con.execute(f"""SELECT raw_direction, actual_move, resolved, signal
                               FROM predictions_{h}m WHERE raw_direction IN('UP','DOWN')""").fetchall()
        rh = [r for r in rows if r[2]]
        ch = [r for r in rh if r[1] is not None and ((r[0] == 'UP' and r[1] > 0) or (r[0] == 'DOWN' and r[1] < 0))]
        sg = con.execute(f"SELECT count(*) FROM predictions_{h}m WHERE signal IN('UP','DOWN')").fetchone()[0]
        tot += len(rows); res += len(rh); cor += len(ch); sig += sg
        if rows:
            print(f"   {h:>2}m: leans={len(rows)} resolved={len(rh)} correct={len(ch)} "
                  f"precision={pct(len(ch), len(rh))}  committed_BUY/SELL={sg}")
    print(f"   TOTAL: leans={tot} resolved={res} precision={pct(cor, res)}  committed_BUY/SELL={sig}")

    # 3) Per-base-model ----------------------------------------------------
    print("\n[3] INDIVIDUAL BASE MODELS (model_predictions) — committed votes only")
    print("    NEUTRAL abstentions are excluded; 50% is the no-edge directional baseline.")
    rows = con.execute("""WITH unique_votes AS (
                           SELECT * EXCLUDE(occurrence) FROM (
                             SELECT *, ROW_NUMBER() OVER (
                               PARTITION BY model,horizon,timestamp
                               ORDER BY CASE WHEN contains(id,'::') THEN 0 ELSE 1 END,id
                             ) occurrence
                             FROM model_predictions
                           ) WHERE occurrence=1
                         )
                         SELECT model,
                           count(*) FILTER(WHERE direction IN('UP','DOWN')) dv,
                           count(*) FILTER(WHERE direction IN('UP','DOWN') AND resolved AND hit IS NOT NULL) rs,
                           count(*) FILTER(WHERE direction IN('UP','DOWN') AND resolved AND hit IS TRUE) ht
                         FROM unique_votes GROUP BY 1 ORDER BY 4*1.0/NULLIF(3,0) DESC, model""").fetchall()
    for m, dv, rs, ht in sorted(rows, key=lambda r: -(r[3] / r[2] if r[2] else 0)):
        print(f"   {m:10s}: votes={dv:4d} resolved={rs:4d} correct={ht:4d} precision={pct(ht, rs)}")

    # 4) Kronos + FSR-PPO --------------------------------------------------
    print("\n[4] KRONOS (fallback) + FSR-PPO CHALLENGER")
    k = con.execute("""SELECT count(*) FILTER(WHERE direction IN('UP','DOWN')),
                        count(*) FILTER(WHERE direction IN('UP','DOWN') AND resolved),
                        count(*) FILTER(WHERE direction IN('UP','DOWN') AND resolved AND hit)
                      FROM kronos_predictions""").fetchone()
    print(f"   Kronos : signals={k[0]} resolved={k[1]} correct={k[2]} precision={pct(k[2], k[1])} (50%=coin flip)")
    try:
        f = con.execute("SELECT action, count(*) FROM fsr_ppo_decisions GROUP BY 1 ORDER BY 2 DESC").fetchall()
        print(f"   FSR-PPO: actions={f}")
    except Exception:
        print("   FSR-PPO: no table")
    con.close()
    print("\nRe-run this same script in a few days to compare against the saved baseline.")


if __name__ == "__main__":
    main()
