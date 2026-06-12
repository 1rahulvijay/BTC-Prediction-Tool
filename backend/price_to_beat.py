"""
Price-to-Beat 5m/15m Tracker (Chainlink, fixed clock windows)
=============================================================
A self-contained directional game (replaces the broken Polymarket "Value Engine",
which could only see long-dated BTC markets).

Design:
- Uses **fixed clock-aligned windows**, not arbitrary prediction timing:
  5m windows snap to :00, :05, :10 … and 15m windows snap to :00, :15, :30, :45.
- The **price to beat** is the **Chainlink** BTC/USD price captured at the window start,
  and resolution at the window end is also against Chainlink (live oracle reference).
- At window start we record our ensemble's UP/DOWN call + action + Kronos's call; once the
  window closes we check whether Chainlink finished above or below the price to beat and
  whether our call was right.

Persists to DuckDB (`price_to_beat`); surfaced as
`payload.price_to_beat = {latest, accuracy, recent}`.
"""

import logging
from collections import deque

import database

logger = logging.getLogger(__name__)


def _hms(ms: int) -> str:
    """HH:MM clock label for a window boundary, in US Eastern (ET) to match Polymarket's
    market clock. NOTE: for 5m/15m windows the *boundary* is identical in any whole-hour
    timezone (ET is UTC-4/-5), so this only changes the displayed hour, never the actual
    reference-price lock time or resolution time."""
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        return datetime.fromtimestamp(ms / 1000.0, ZoneInfo("America/New_York")).strftime("%H:%M ET")
    except Exception:
        import time as _t
        return _t.strftime("%H:%M", _t.localtime(ms / 1000.0))


class PriceToBeatTracker:
    def __init__(self, horizons=(5, 15), neutral_band=0.0):  # 0.0 = strict up/down (Polymarket mirror)
        # neutral_band = 0.0 makes resolution STRICT (any close above the open = UP, below
        # = DOWN), mirroring Polymarket's BTC up/down markets — where even a +$50 move on
        # $63k (0.08%) is a clear UP, not "stayed near reference". The cost-floored band
        # belongs on the trade-decision side, not on this market-outcome mirror.
        self.horizons = list(horizons)
        self.neutral_band = neutral_band
        self.pending: list[dict] = []
        self.history = {h: deque(maxlen=500) for h in self.horizons}
        self.recent_rounds = deque(maxlen=60)         # resolved rounds for the UI feed
        self.latest_round = {}                        # horizon -> current/most-recent round
        self.current_window = {}                      # horizon -> window-start ms we already opened

    @staticmethod
    def _bet_lean(p: dict) -> str:
        """The side to bet on a Polymarket binary up/down market. Polymarket has NO
        neutral outcome — the window ALWAYS settles UP or DOWN — so we always surface
        the side the model favors. Prefer the 3-class lean when it actually picks a
        direction; otherwise fall back to the model's TWO-WAY probability (P(UP) vs
        P(DOWN)), which still has a favored side even when the NEUTRAL/timeout class
        wins the argmax (which it usually does at 5-15m, since BTC rarely clears the
        cost floor that fast). NEUTRAL only when there is no probability signal at all.

        This deliberately separates 'which side' (shown every window, here) from 'is it
        worth betting' (decided later by fair_value_cents = value betting). Before, the
        bet used the raw 3-class lean directly, so most windows showed NEUTRAL/'No bet'
        even though the model still favored a side — making directional calls look rare."""
        raw = p.get("rawDirection", p.get("direction", "NEUTRAL"))
        if raw in ("UP", "DOWN"):
            return raw
        pu = float(p.get("probUp", 0.0) or 0.0)
        pd = float(p.get("probDown", 0.0) or 0.0)
        if pu <= 0.0 and pd <= 0.0:
            return "NEUTRAL"
        return "UP" if pu >= pd else "DOWN"

    def _direction(self, price: float, ref_price: float) -> str:
        """Polymarket BTC up/down resolution rule: UP if the price is GREATER THAN OR EQUAL
        to the reference (start) price, else DOWN. There is no NEUTRAL outcome — a tie
        resolves UP. (NEUTRAL only when we have no valid reference to anchor against.)"""
        if ref_price <= 0:
            return "NEUTRAL"
        return "UP" if price >= ref_price else "DOWN"

    @staticmethod
    def _price_at_boundary(ts_ms: int, klines, fallback: float) -> float:
        """True price AT a window boundary, recovered from 1m klines (kline `time` is in
        SECONDS). The boundary price = the OPEN of the candle starting there (or the CLOSE
        of the candle just before). Used when the ticker fires LATE (event-loop stall):
        without this, a late tick anchored/resolved the round at whatever the price was
        seconds AFTER the boundary — the operator's 'reference price isn't actual' bug."""
        if not klines:
            return fallback
        t = int(ts_ms // 1000)
        for k in reversed(list(klines)[-20:]):
            kt = int(k.get("time", 0) or 0)
            if kt == t and k.get("open"):
                return float(k["open"])
            if kt == t - 60 and k.get("close"):
                return float(k["close"])
            if kt < t - 120:
                break
        return fallback

    LATE_MS = 3000  # ticks later than this use the kline-recovered boundary price

    def update(self, now_ms: int, ref_price: float, predictions_by_h: dict,
               kronos_dir_by_h: dict, klines=None, feed_fresh: bool = True):
        """Open new clock-aligned rounds and resolve elapsed ones. Call once per tick.

        ref_price: fresh Binance aggTrade BTC/USD (sub-100ms live price). This anchors the
            window, drives the live position, and resolves the round (UP if end >= start).
        predictions_by_h: {horizon: ensemble prediction dict}.
        kronos_dir_by_h: {horizon: kronos direction string}.
        """
        if not ref_price or ref_price <= 0:
            # Can't anchor a round without a reference price; still try to resolve.
            self._resolve(now_ms, ref_price, klines)
            return

        for h in self.horizons:
            win_len = h * 60_000
            win_start = (now_ms // win_len) * win_len
            win_end = win_start + win_len
            p = (predictions_by_h or {}).get(h) or {}

            if self.current_window.get(h) != win_start:
                if not feed_fresh:
                    # Frozen feed (e.g. retrain stall): an anchor taken now would be a
                    # stale price masquerading as the window open. Skip opening this
                    # round entirely — resolution/refresh below still run with kline
                    # recovery. Mark the window consumed so we don't open it mid-way.
                    self.current_window[h] = win_start
                    continue
                # Open a new clock-aligned round at the window boundary. If this tick is
                # LATE (event-loop stall), anchor at the TRUE boundary price recovered
                # from the 1m klines instead of the current (drifted) live price.
                self.current_window[h] = win_start
                late_ms = now_ms - win_start
                anchor = ref_price
                if late_ms > self.LATE_MS:
                    anchor = self._price_at_boundary(win_start, klines, ref_price)
                entry = {
                    "id": f"ptb_{h}m_{win_start}",
                    "timestamp": win_start,
                    "horizon": h,
                    "price_to_beat": round(anchor, 2),
                    "ref_captured_late_ms": int(late_ms) if late_ms > self.LATE_MS else 0,
                    # Grade the model's directional LEAN (rawDirection) — the bet you'd
                    # place on a binary Polymarket up/down market. The final gated action
                    # (signal) is usually WAIT and can't be expressed on a binary market,
                    # so keep it as our_action for the "lean UP, action WAIT" display.
                    "our_direction": self._bet_lean(p),
                    # Which rule produced the lean. EVIDENCE (DuckDB 2026-06-10, 9.6h): the
                    # model's committed 3-class leans win ~64% at 5m, but the two-way
                    # probability FALLBACK leans are ~coin-flip — mixing them dragged the
                    # mirror from 58.7% to 51.5%. Track separately so the betting guidance
                    # can say "bet model leans, skip fallback leans".
                    "lean_source": ("model" if p.get("rawDirection") in ("UP", "DOWN")
                                    else "fallback"),
                    # Stage-3 setup grade computed server-side (model lean + regime +
                    # flow confirmations). A ≈ the high-precision subset; C ≈ skip.
                    "confluence": p.get("confluence"),
                    "our_action": p.get("signal", p.get("direction", "NEUTRAL")),
                    "signal": p.get("signal", p.get("direction", "NEUTRAL")),
                    "conviction": float(p.get("conviction", 0.0) or 0.0),
                    "actionable": bool(p.get("actionable", False)),
                    "kronos_direction": (kronos_dir_by_h or {}).get(h) or "NONE",
                    "target_price": p.get("targetPrice"),
                    "verify_at": win_end,
                    "window_start": win_start,
                    "window_end": win_end,
                    "window_label": f"{_hms(win_start)}-{_hms(win_end)}",
                }
                self.pending.append(entry)
                self.latest_round[h] = {**entry, "status": "pending"}
                try:
                    database.log_price_to_beat(entry)
                except Exception as e:
                    logger.debug(f"Price-to-beat log failed: {e}")

            # Live in-window decision support: refresh the OPEN round every tick so a user
            # holding a Polymarket bet can decide hold-vs-early-exit.
            rnd = self.latest_round.get(h)
            if rnd and rnd.get("status") == "pending":
                self._refresh_live(rnd, now_ms, ref_price, p, win_end)

        self._resolve(now_ms, ref_price, klines)

    def _refresh_live(self, rnd: dict, now_ms: int, ref_price: float, p: dict, win_end: int):
        """Attach live intra-window state + hold/exit advice to an open round."""
        ptb = rnd.get("price_to_beat") or 0.0
        cur_move = round(ref_price - ptb, 2)
        cur_pos = self._direction(ref_price, ptb)          # where it stands RIGHT NOW (strict)
        # Model's CURRENT lean — must use the SAME _bet_lean rule that opened the bet
        # (two-way prob fallback). Using rawDirection here while the bet used _bet_lean made
        # the advice see "lean faded to NEUTRAL" on nearly every tick (rawDirection is usually
        # NEUTRAL at 5-15m) and wrongly push LOCK-IN/EXIT while the model still favored the side.
        live_lean = self._bet_lean(p)
        live_exp = p.get("expectedMove")                   # model's expected move (magnitude)
        secs_left = max(0, int((win_end - now_ms) // 1000))
        rnd["current_price"] = round(ref_price, 2)
        rnd["current_move"] = cur_move                     # signed $ vs price-to-beat
        rnd["current_position"] = cur_pos                  # UP/DOWN/NEUTRAL right now
        rnd["seconds_left"] = secs_left
        rnd["live_lean"] = live_lean
        rnd["live_expected_move"] = (round(float(live_exp), 2) if live_exp is not None else None)
        # Magnitude FORECAST for the Polymarket card: the model's directional move-size
        # regressor (conformal low/median/high band) projected onto a close estimate vs
        # the price-to-beat. Lets the bettor see "expected drop/rise of ~$X" and whether
        # that PROJECTS to clear the line, not just the UP/DOWN sign.
        _emr = p.get("expectedMoveRange") or {}
        if _emr.get("low") is not None and _emr.get("high") is not None:
            rnd["expected_move_range"] = {
                "low": round(float(_emr["low"]), 2),
                "median": round(float(_emr.get("median") or live_exp or 0.0), 2),
                "high": round(float(_emr["high"]), 2),
            }
            # Signed projection: which way the model leans × the magnitude, added to NOW.
            _sgn = 1.0 if live_lean == "UP" else -1.0 if live_lean == "DOWN" else 0.0
            if _sgn != 0.0 and live_exp is not None:
                rnd["projected_close"] = round(ref_price + _sgn * abs(float(live_exp)), 2)
                rnd["projected_vs_beat"] = round(rnd["projected_close"] - ptb, 2)
                # p_up (A2-lite): P(close >= beat) ≈ Φ(edge/σ), σ from the conformal
                # IQR (q75−q25 ≈ 1.349σ), floored so we never print false certainty.
                # This is the number a Polymarket share is actually WORTH — compare
                # it to the market's ask to see edge.
                try:
                    import math as _m
                    _sigma = max(8.0, abs(float(_emr["high"]) - float(_emr["low"])) / 1.349)
                    _edge = float(rnd["projected_close"]) - float(ptb)
                    rnd["p_up"] = round(0.5 * (1.0 + _m.erf(_edge / (_sigma * _m.sqrt(2)))), 3)
                except Exception:
                    pass
        # PATH OUTLOOK — a plain-English "how will price travel vs the line" forecast:
        # built from the model's expected move vs the distance to the line, with odds from
        # MEASURED precision (expectedPrecision) when available, else the two-way prob.
        rnd["path_outlook"] = self._path_outlook(
            cur_move, cur_pos, live_lean, p, rnd.get("lean_source"))
        rnd["advice"] = self._advice(rnd.get("our_direction", "NEUTRAL"),
                                     cur_pos, live_lean, secs_left, cur_move)
        # COHERENCE override (operator-caught, 2026-06-12): when the path outlook says
        # STRETCH — expected travel can't plausibly cover the gap to the line — the
        # advice must NOT say "hold, reversal possible". The lean can be RIGHT about
        # direction and still unable to cross in time; for THIS binary window that is
        # a losing hold, and the math two boxes up already says so. Keep the boxes
        # agreeing with each other.
        _po = rnd.get("path_outlook") or {}
        if _po.get("scenario") == "STRETCH" and isinstance(rnd.get("advice"), dict):
            _gap = abs(float(cur_move or 0.0))
            _trav = abs(float(live_exp)) if live_exp is not None else 0.0
            # SIDE-AWARE (operator-caught bug, 2026-06-13): the outlook describes the
            # LIVE lean. If that stretched lean IS the bet side → it can't cross →
            # exit/skip. But if the bet side is the OPPOSITE one (lean flipped while
            # the bet is ahead), the stretch means the OPPONENT is counted out — the
            # held side is near-certain. The old text told a winning DOWN holder
            # (ahead $91) to "exit; do not enter". Exactly backwards.
            if rnd.get("our_direction") == live_lean:
                rnd["advice"]["action"] = "EXIT / SKIP"
                rnd["advice"]["tone"] = "bad"
                rnd["advice"]["text"] = (
                    f"Counted out for THIS window: typical travel ~${_trav:.0f} vs a "
                    f"${_gap:.0f} gap with {secs_left}s left — the lean may be right about "
                    f"direction but cannot cross the line in time. Exit if holding; do not enter."
                )
            elif (rnd.get("our_direction") in ("UP", "DOWN")
                  and live_lean in ("UP", "DOWN") and rnd.get("our_direction") != live_lean):
                rnd["advice"]["action"] = "HOLD — opponent counted out"
                rnd["advice"]["tone"] = "good"
                rnd["advice"]["text"] = (
                    f"Your {rnd['our_direction']} side leads by ${_gap:.0f} and the flipped "
                    f"{live_lean} lean only expects ~${_trav:.0f} of travel with {secs_left}s "
                    f"left — it cannot cross back in time. Strong hold."
                )
        # Weak-lean warning: a "fallback" lean is the two-way tilt of an otherwise-NEUTRAL
        # model — live evidence shows it's near coin-flip, unlike committed model leans.
        if rnd.get("lean_source") == "fallback" and isinstance(rnd.get("advice"), dict):
            rnd["advice"]["text"] = ("[Weak lean — model is near-neutral, side from probability "
                                     "tilt only. Best skipped as a bet.] ") + rnd["advice"].get("text", "")
        # Setup grade (Stage 3): surfaced in the advice so the bettor sees signal quality.
        # LABELED as "live": this is the CURRENT prediction's grade and can legitimately
        # differ from the grade-at-open shown in the card header (flows shift mid-round).
        # Unlabeled, the two looked like a contradiction (header C vs advice A).
        cfl = p.get("confluence") or rnd.get("confluence")
        if isinstance(cfl, dict) and isinstance(rnd.get("advice"), dict):
            _open_g = (rnd.get("confluence") or {}).get("grade") if isinstance(rnd.get("confluence"), dict) else None
            _live_g = cfl.get("grade")
            _delta = (f", opened {_open_g}" if _open_g and _open_g != _live_g else "")
            rnd["advice"]["text"] += f" [Live grade {_live_g} ({cfl.get('score')}/5{_delta}).]"
        # Stage-4 LATE ENTRY: with little time left and price already ahead on the leaned
        # side, the bet becomes "no reversal in N seconds" — conditional win probability is
        # far higher than at window open (the most reliable 80%+ mechanism that exists).
        # The share price reflects some of this; the flag marks the favorable structure.
        # Late-entry window scales with the horizon (fixed 15-120s covered nearly a
        # whole 1m round once the 1m/3m practice mirrors were added): final ~40% of
        # the window, capped at 120s; min-ahead distance softer for the fast mirrors.
        _h_secs = int(rnd.get("horizon", 5)) * 60
        _late_win = min(120, int(_h_secs * 0.4))
        _min_ahead = 10.0 if rnd.get("horizon", 5) >= 5 else 5.0
        # ⚡ gated to COMMITTED model leans (2026-06-13): flagging late-entry on a
        # ⚠ fallback lean put "persistence odds strongly favor X" on a card whose
        # own badge says "WEAK — skip". Contradictory coaching; model leans only.
        late = (live_lean in ("UP", "DOWN") and cur_pos == live_lean
                and rnd.get("lean_source") == "model"
                and 15 < secs_left <= _late_win and abs(cur_move) >= _min_ahead)
        rnd["late_entry"] = bool(late)
        if late and isinstance(rnd.get("advice"), dict):
            rnd["advice"]["text"] += (f" [LATE-ENTRY WINDOW: {secs_left}s left, already "
                                      f"{cur_move:+.0f} on the {live_lean} side and the model still "
                                      f"agrees — persistence odds strongly favor {live_lean}.]")
        # EARLY-EXIT hint (A13, from the repo scan): when one side is near-certain by
        # p_up, SELLING at ~that price captures nearly the whole win and deletes 100%
        # of the tail risk — every profitable external bot monetizes the path, not
        # just resolution. Decision-support text only.
        _pu = rnd.get("p_up")
        if _pu is not None and isinstance(rnd.get("advice"), dict):
            if _pu >= 0.97 and rnd.get("our_direction") == "UP":
                rnd["advice"]["text"] += (f" [EXIT-EDGE: UP ≈{_pu*100:.0f}% — if holding UP shares, "
                                          f"selling near {_pu*100:.0f}¢ locks the win without tail risk.]")
            elif _pu <= 0.03 and rnd.get("our_direction") == "DOWN":
                rnd["advice"]["text"] += (f" [EXIT-EDGE: DOWN ≈{(1-_pu)*100:.0f}% — if holding DOWN shares, "
                                          f"selling near {(1-_pu)*100:.0f}¢ locks the win without tail risk.]")

    @staticmethod
    def _path_outlook(cur_move: float, cur_pos: str, lean: str, p: dict,
                      lean_source: str) -> dict:
        """Expected price JOURNEY vs the price-to-beat line, in plain English.
        Scenarios: HOLD (already on the leaned side, expect wobbles that hold),
        CROSS (on the wrong side but expected move covers the distance — dip/pop then
        cross), STRETCH (lean exists but expected move < distance — unlikely to make it),
        CHOP (weak/fallback lean — oscillation, coin-flip close). Odds quoted are the
        MEASURED win-rate for this setup (expectedPrecision) when available."""
        exp_move = abs(float(p.get("expectedMove") or 0.0))
        dist = abs(float(cur_move or 0.0))
        wobble = max(5.0, exp_move * 0.4)  # typical intra-window wiggle around the path

        odds = p.get("expectedPrecision")
        if odds is None:
            pu = float(p.get("probUp", 0.0) or 0.0)
            pd = float(p.get("probDown", 0.0) or 0.0)
            if (pu + pd) > 0 and lean in ("UP", "DOWN"):
                odds = (pu if lean == "UP" else pd) / (pu + pd)
        odds_txt = f" Measured odds for this setup: {odds * 100:.0f}%." if odds else ""

        if lean not in ("UP", "DOWN") or lean_source == "fallback":
            return {"scenario": "CHOP", "text":
                    f"No strong drift expected — price will likely wobble ±${wobble:.0f} "
                    f"around the line and the closing side is near coin-flip. Best skipped."}
        side = "above" if lean == "UP" else "below"
        if cur_pos == lean:
            return {"scenario": "HOLD", "text":
                    f"Price is already {side} the line ({cur_move:+.0f}$). Expect wobbles of "
                    f"about ±${wobble:.0f} that test the line, but the model expects it to "
                    f"HOLD {side} into the close.{odds_txt}"}
        if exp_move >= dist:
            return {"scenario": "CROSS", "text":
                    f"Price is on the wrong side ({cur_move:+.0f}$) but the model expects "
                    f"~${exp_move:.0f} of travel — enough to wobble here first, then CROSS "
                    f"the line and finish {lean} (needs ${dist:.0f}).{odds_txt}"}
        return {"scenario": "STRETCH", "text":
                f"Model leans {lean} but expects only ~${exp_move:.0f} of travel while the "
                f"line is ${dist:.0f} away — crossing is a stretch. Low-quality setup; "
                f"consider skipping or waiting for price to come closer to the line."}

    @staticmethod
    def _advice(bet_dir: str, cur_pos: str, live_lean: str, secs_left: int, cur_move: float) -> dict:
        """Hold / exit guidance for a placed bet. bet_dir = the lean locked at window open."""
        if bet_dir not in ("UP", "DOWN"):
            return {"action": "NO BET", "tone": "muted",
                    "text": "No directional lean for this window — sit this one out."}
        winning = (cur_pos == bet_dir)
        lean_agrees = (live_lean == bet_dir)
        urgent = secs_left <= 30
        mv = f"{cur_move:+.0f}"
        if winning and lean_agrees:
            return {"action": "HOLD", "tone": "good",
                    "text": f"Ahead {mv} and the model still leans {bet_dir}. Let it ride to settle."}
        if winning and not lean_agrees:
            return {"action": "LOCK IN", "tone": "warn",
                    "text": f"Ahead {mv} but the model's lean faded to {live_lean or 'NEUTRAL'}. "
                            f"{'Little time left — ' if urgent else ''}consider selling to lock the gain."}
        if not winning and lean_agrees:
            return {"action": "HOLD / WAIT", "tone": "warn",
                    "text": f"Behind {mv} but the model still leans {bet_dir} — reversal possible. "
                            f"{'Time is short, weigh an exit.' if urgent else 'Hold unless it worsens.'}"}
        return {"action": "EXIT", "tone": "bad",
                "text": f"Behind {mv} and the model flipped to {live_lean or 'NEUTRAL'}. "
                        f"Consider cutting the bet early to limit loss."}

    def _resolve(self, now_ms: int, ref_price: float, klines=None):
        if not ref_price or ref_price <= 0:
            return
        still = []
        for p in self.pending:
            if now_ms >= p["verify_at"]:
                # Late resolution (loop stall): grade against the TRUE window-end price
                # recovered from klines, not the drifted current price.
                end_price = ref_price
                if now_ms - p["verify_at"] > self.LATE_MS:
                    end_price = self._price_at_boundary(p["verify_at"], klines, ref_price)
                actual_dir = self._direction(end_price, p["price_to_beat"])
                move = round(end_price - p["price_to_beat"], 2)
                # Only count rounds where we actually had a directional lean to bet — a
                # NEUTRAL lean is "no play" on a binary market, so it must NOT drag down
                # the win-rate (otherwise the mirror looks 0% while the model abstains).
                bet = p["our_direction"] in ("UP", "DOWN")
                hit = (p["our_direction"] == actual_dir) if bet else None
                if bet:
                    # (hit, lean_source) so accuracy() can split model-vs-fallback win rates.
                    self.history[p["horizon"]].append(
                        (1 if hit else 0, p.get("lean_source", "model")))
                resolved = {**p, "actual_price": round(end_price, 2),
                            "actual_direction": actual_dir, "hit": hit,
                            "move": move, "status": "resolved"}
                self.recent_rounds.appendleft(resolved)
                if self.latest_round.get(p["horizon"], {}).get("id") == p["id"]:
                    self.latest_round[p["horizon"]] = resolved
                try:
                    database.resolve_price_to_beat(p["id"], end_price, actual_dir, hit, move,
                                                   late_entry=bool(p.get("late_entry", False)))
                except Exception as e:
                    logger.debug(f"Price-to-beat resolve failed: {e}")
            else:
                still.append(p)
        self.pending = still

    def latest(self) -> dict:
        """Current open (or last resolved) round per horizon, for the UI cards."""
        return {h: self.latest_round.get(h) for h in self.horizons}

    def recent(self, n: int = 20) -> list:
        return list(self.recent_rounds)[:n]

    def accuracy(self) -> dict:
        out = {}
        for h in self.horizons:
            hh = list(self.history[h])
            # Back-compat: older persisted entries are plain ints; normalize to tuples.
            hh = [(e, "model") if isinstance(e, int) else e for e in hh]
            n = len(hh)
            hits = sum(e[0] for e in hh)
            model = [e for e in hh if e[1] == "model"]
            fb = [e for e in hh if e[1] == "fallback"]
            out[h] = {
                "total": n,
                "hits": int(hits),
                "accuracy": round(hits / n, 4) if n else 0.0,
                # The split that matters for betting: committed model leans vs weak fallback.
                "model_total": len(model),
                "model_accuracy": round(sum(e[0] for e in model) / len(model), 4) if model else 0.0,
                "fallback_total": len(fb),
                "fallback_accuracy": round(sum(e[0] for e in fb) / len(fb), 4) if fb else 0.0,
                "pending": sum(1 for p in self.pending if p["horizon"] == h),
            }
        return out
