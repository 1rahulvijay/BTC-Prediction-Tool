"""
Rules-first Champion Decision Validator.

This is not another raw direction model. It combines the specialist heads already
computed on a price-to-beat round into one conservative, plain-English decision:
big-move timing, big-drop risk, P(hold), quantile reward/risk room, direction as
confirmation, and optionally a live Polymarket ask.

The only path to a bet candidate is:

    fair_value - ask - costs - buffer > required_edge

When no live market ask is supplied, the validator reports probability/risk only.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional


PHOLD_STRONG = 0.93
PHOLD_GOOD = 0.85

DEFAULT_BUFFER = 0.03
DEFAULT_CRYPTO_TAKER_FEE_RATE = 0.07
DEFAULT_REQUIRED_EDGE = 0.0
# First qualifying 5m/15m entries at P(Hold)>=0.93 realized 92.6% with a 91.3%
# Wilson lower bound. Raw snapshot probabilities averaged 97.8% in that selected set.
# Cap fair value at the rounded lower bound until entry-specific recalibration is proven.
DEFAULT_ENTRY_FAIR_CAP = 0.91

_META_MODEL = None
_META_CHECKED = False
_META_ERROR = ""

META_GATE_MIN = 0.55
META_GATED_ACTIONS = {"PAPER_BET", "SETUP", "LEAN", "WATCH_UP", "WATCH_DOWN"}


def polymarket_taker_fee_per_share(price: float, fee_rate: float = DEFAULT_CRYPTO_TAKER_FEE_RATE) -> float:
    """Return the current crypto-market taker fee for one share.

    Polymarket's fee formula is C * fee_rate * p * (1-p). This helper uses C=1.
    Maker orders have zero protocol fee and should pass an explicit cost of 0 instead.
    """
    p = max(0.0, min(1.0, float(price)))
    return max(0.0, float(fee_rate)) * p * (1.0 - p)


def max_taker_ask(
    fair_value: float,
    buffer: float = DEFAULT_BUFFER,
    required_edge: float = DEFAULT_REQUIRED_EDGE,
    fee_rate: float = DEFAULT_CRYPTO_TAKER_FEE_RATE,
) -> float:
    """Highest ask that clears fair - ask - taker_fee - buffer > required_edge."""
    target = max(0.0, min(1.0, float(fair_value) - float(buffer) - float(required_edge)))
    rate = max(0.0, float(fee_rate))
    if rate == 0.0:
        return target
    # rate*q^2 - (1+rate)*q + target = 0; choose the root in [0, 1].
    disc = max(0.0, (1.0 + rate) ** 2 - 4.0 * rate * target)
    return max(0.0, min(1.0, ((1.0 + rate) - disc ** 0.5) / (2.0 * rate)))


KELLY_FRACTION = 0.25   # quarter-Kelly: estimation error in P(hold) makes full Kelly reckless
KELLY_CAP = 0.10        # never suggest more than 10% of bankroll on one round


def kelly_stake(fair_value, ask, effective_costs=0.0,
                fraction=KELLY_FRACTION, cap=KELLY_CAP) -> float:
    """Suggested stake as a FRACTION of bankroll for a binary share bought at `ask` plus per-share
    costs, sized by quarter-Kelly. Effective cost c = ask + fees; a win pays 1/share, so full Kelly
    is f* = (p - c) / (1 - c). Returns fraction*f* capped at `cap`; 0.0 when there is no positive
    edge. Pure and crash-safe (returns 0.0 on any bad input) -- sizing SUPPORT, not an order."""
    try:
        p = float(fair_value)
        c = float(ask) + max(0.0, float(effective_costs))
        if not (0.0 < c < 1.0) or not (0.0 < p <= 1.0) or p <= c:
            return 0.0
        return round(max(0.0, min(float(cap), float(fraction) * (p - c) / (1.0 - c))), 4)
    except Exception:
        return 0.0


def _load_meta_model():
    """Load the learned champion meta-filter if enough live data has trained one."""
    global _META_MODEL, _META_CHECKED, _META_ERROR
    if _META_CHECKED:
        return _META_MODEL
    _META_CHECKED = True
    try:
        import joblib
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data_dir = os.environ.get("BTC_DATA_DIR") or os.path.join(root, "data")
        path = os.path.join(data_dir, "saved_models", "champion_meta_model.pkl")
        if os.path.exists(path):
            _META_MODEL = joblib.load(path)
    except Exception as exc:
        _META_ERROR = str(exc)
        _META_MODEL = None
    return _META_MODEL


def _meta_probability(round_data: Dict[str, Any], action: str, confidence: float) -> Optional[float]:
    bundle = _load_meta_model()
    if not bundle:
        return None
    try:
        import pandas as pd
        model = bundle["model"]
        numeric = bundle.get("numeric_features") or []
        categorical = bundle.get("categorical_features") or []
        row = {
            "horizon": round_data.get("horizon"),
            "seconds_left": round_data.get("seconds_left"),
            "current_move": round_data.get("current_move"),
            "p_hold": round_data.get("p_hold"),
            "p_big_move": round_data.get("p_big_move"),
            "p_big_drop": round_data.get("p_big_drop"),
            "p_big_up": round_data.get("p_big_up"),
            "p_big_down": round_data.get("p_big_down"),
            "p_activity": round_data.get("p_activity"),
            "champion_confidence": confidence,
            "current_position": round_data.get("current_position"),
            "big_move_tier": round_data.get("big_move_tier"),
            "big_drop_risk": round_data.get("big_drop_risk"),
            "big_up_tier": round_data.get("big_up_tier"),
            "big_down_tier": round_data.get("big_down_tier"),
            "activity_tier": round_data.get("activity_tier"),
            "regime": round_data.get("regime"),
            "champion_action": action,
        }
        prob = model.predict_proba(pd.DataFrame([{k: row.get(k) for k in numeric + categorical}]))[:, 1][0]
        return float(prob)
    except Exception:
        return None


def _f(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _reward_room(round_data: Dict[str, Any], side: str) -> Optional[float]:
    """Return favorable travel room in dollars from the current 80% band."""
    band = round_data.get("expected_move_range") or {}
    low = _f(band.get("low"))
    high = _f(band.get("high"))
    if low is None or high is None:
        return None
    if side == "UP":
        return high
    if side == "DOWN":
        return -low
    return max(high, -low)


def champion_decision(
    round_data: Dict[str, Any],
    market: Optional[Dict[str, Any]] = None,
    *,
    costs: Optional[float] = None,
    buffer: float = DEFAULT_BUFFER,
    required_edge: float = DEFAULT_REQUIRED_EDGE,
) -> Dict[str, Any]:
    """Validate one price-to-beat round into an action dictionary.

    `market` may include `ask`, `spread`, and `depth`. Without `ask`, this function
    never returns a bet candidate.
    """
    flags: list[str] = []

    def out(
        action: str,
        label: str,
        confidence: float,
        reason: str,
        *,
        bet: bool = False,
        edge: Optional[float] = None,
        stake_frac: Optional[float] = None,
        invalidate: str = "",
    ) -> Dict[str, Any]:
        zone = None
        band = round_data.get("expected_move_range") or {}
        current_price = _f(round_data.get("current_price"))
        low = _f(band.get("low"))
        high = _f(band.get("high"))
        if current_price is not None and low is not None and high is not None:
            zone = {
                "low": round(current_price + low, 2),
                "high": round(current_price + high, 2),
                "source": round_data.get("band_source") or "indicative",
            }
        result = {
            "action": action,
            "label": label,
            "confidence": int(max(0, min(100, round(confidence)))),
            "reason": reason,
            "risk_flags": flags,
            "invalidate": invalidate,
            "zone": zone,
            "bet_candidate": bool(bet),
            "edge": round(edge, 4) if edge is not None else None,
            "stake_frac": round(stake_frac, 4) if stake_frac else None,
        }
        meta_p = _meta_probability(round_data, action, result["confidence"])
        if meta_p is not None:
            result["meta_hold_probability"] = round(meta_p, 4)
            if action in META_GATED_ACTIONS and meta_p < META_GATE_MIN:
                flags.append("meta champion below gate")
                result.update({
                    "action": "WAIT",
                    "label": "WAIT - meta rejected",
                    "confidence": int(min(result["confidence"], round(meta_p * 100))),
                    "reason": (
                        f"The learned champion meta-filter estimates only {meta_p * 100:.0f}% "
                        "chance that the current side holds. Skip despite the rules-first setup."
                    ),
                    "bet_candidate": False,
                    "edge": None,
                    "stake_frac": None,
                    "invalidate": "Meta hold probability rises above 55% with the same setup.",
                })
        return result

    current_price = _f(round_data.get("current_price"))
    if current_price is None:
        return out(
            "AVOID",
            "AVOID - feed stale",
            0,
            "No fresh BTC price is available for this tick. Do not act until the feed recovers.",
            invalidate="Fresh BTC price ticks resume.",
        )

    position = round_data.get("current_position")
    lean = round_data.get("live_lean")
    lean_source = round_data.get("lean_source")
    p_hold = _f(round_data.get("p_hold"))
    drop_risk = (round_data.get("big_drop_risk") or "").upper()
    move_tier = (round_data.get("big_move_tier") or "").lower()
    up_tier = (round_data.get("big_up_tier") or "").upper()
    down_tier = (round_data.get("big_down_tier") or "").upper()
    activity_tier = (round_data.get("activity_tier") or "").lower()
    regime = (round_data.get("regime") or "UNKNOWN").upper()

    if drop_risk == "HIGH":
        flags.append("HIGH big-drop risk")
    elif drop_risk == "ELEVATED":
        flags.append("elevated downside path risk")
    if up_tier == "HIGH":
        flags.append("big-up confirmation high")
    if down_tier == "HIGH":
        flags.append("big-down confirmation high")
    if activity_tier == "quiet":
        flags.append("low activity / quiet range")
    if regime in ("HIGH_VOLATILITY", "LOW_VOLATILITY"):
        flags.append(f"regime {regime.lower()}")

    if move_tier == "quiet":
        flags.append("quiet round")
        if not (p_hold is not None and p_hold >= PHOLD_STRONG and position in ("UP", "DOWN")):
            return out(
                "WAIT",
                "WAIT - quiet round",
                35,
                "The big-move head expects a quiet window. There is not enough expected movement to justify a new action.",
                invalidate="Big-move tier rises to moderate, elevated, or likely.",
            )

    if position not in ("UP", "DOWN"):
        return out(
            "WAIT",
            "WAIT - price at the line",
            30,
            "Price is sitting near the line. No side has a clear hold edge yet.",
            invalidate="Price moves clearly onto one side with rising P(hold).",
        )

    if drop_risk == "HIGH":
        if position == "UP" or lean == "UP":
            return out(
                "AVOID_LONG",
                "AVOID LONG - downside risk",
                40,
                "Big-drop risk is HIGH while the setup sits or leans UP. Avoid longs unless drop risk fades.",
                invalidate="Big-drop risk drops to LOW, or price flushes and a clean DOWN setup forms.",
            )
        room = _reward_room(round_data, "DOWN")
        if position == "DOWN" and lean == "DOWN" and lean_source == "model" and (room is None or room >= 10.0):
            confidence = 55 + (10 if (p_hold or 0.0) >= PHOLD_GOOD else 0)
            return out(
                "WATCH_DOWN",
                "POSSIBLE DOWN - downside setup",
                confidence,
                "Big-drop risk is HIGH, price holds DOWN, and the direction head confirms DOWN. This is a setup, not a bet trigger.",
                invalidate="Drop risk eases, DOWN lean fades, or the range band collapses.",
            )

    if p_hold is None:
        return out(
            "WAIT",
            "WAIT - no calibrated P(hold)",
            35,
            "P(hold) is unavailable this tick, so there is no calibrated fair value to validate.",
            invalidate="P(hold) starts producing values again.",
        )

    if activity_tier == "quiet" and p_hold < PHOLD_STRONG:
        return out(
            "WAIT",
            "WAIT - low activity",
            min(65.0, p_hold * 100.0),
            "The activity head sees a quiet range and P(hold) is not strong enough to act. Wait for movement or stronger hold odds.",
            invalidate="Activity rises to moderate/elevated, or P(hold) climbs above 93%.",
        )

    if position == "UP" and down_tier == "HIGH" and p_hold < PHOLD_STRONG:
        return out(
            "WAIT",
            "WAIT - downside confirmation conflict",
            min(60.0, p_hold * 100.0),
            "Price is on the UP side, but the directional big-down head is HIGH. Skip until the conflict clears.",
            invalidate="Big-down confirmation fades, or P(hold) strengthens above 93%.",
        )
    if position == "DOWN" and up_tier == "HIGH" and p_hold < PHOLD_STRONG:
        return out(
            "WAIT",
            "WAIT - upside confirmation conflict",
            min(60.0, p_hold * 100.0),
            "Price is on the DOWN side, but the directional big-up head is HIGH. Skip until the conflict clears.",
            invalidate="Big-up confirmation fades, or P(hold) strengthens above 93%.",
        )

    if lean in ("UP", "DOWN") and lean_source == "model" and lean != position and p_hold < PHOLD_STRONG:
        return out(
            "WAIT",
            "WAIT - model disagreement",
            max(35.0, p_hold * 100.0 - 5.0),
            f"The model leans {lean}, but the live price is holding {position}. Do not act until model and price side agree.",
            invalidate="The model lean and live price side agree, or P(hold) becomes strong enough to override the conflict.",
        )

    fair_value = min(p_hold, DEFAULT_ENTRY_FAIR_CAP)
    if p_hold > fair_value:
        flags.append(f"entry fair value conservatively capped at {fair_value*100:.0f}c")
    tier = round_data.get("tier")
    confidence = p_hold * 100.0

    if lean == position and lean_source == "model":
        confidence = min(99.0, confidence + 3.0)
    elif lean in ("UP", "DOWN") and lean != position:
        confidence -= 5.0
        flags.append(f"direction lean {lean} disagrees with held side {position}")

    if drop_risk == "ELEVATED" and position == "UP":
        confidence -= 3.0

    room = _reward_room(round_data, position)
    gap = abs(_f(round_data.get("current_move")) or 0.0)
    seconds_left = _f(round_data.get("seconds_left")) or 0.0
    horizon = max(1, int(_f(round_data.get("horizon")) or 5))
    late_max = min(120.0, horizon * 60.0 * 0.4)
    structural_late_entry = (
        p_hold >= PHOLD_STRONG
        and 15.0 < seconds_left <= late_max
        and gap >= 10.0
    )
    if room is not None and room < max(5.0, gap * 0.25) and seconds_left > 30:
        flags.append("thin reward room")

    if (position == "UP" and lean == "UP" and lean_source == "model"
            and up_tier == "HIGH" and drop_risk != "HIGH"
            and p_hold >= PHOLD_GOOD and p_hold < PHOLD_STRONG
            and (room is None or room >= 10.0)):
        return out(
            "WATCH_UP",
            "POSSIBLE UP - upside setup",
            min(90.0, confidence + 5.0),
            "Price holds UP, the model confirms UP, and the directional big-up head is HIGH. This is a setup, not a bet trigger.",
            invalidate="Big-up confirmation fades, price loses the UP side, or big-drop risk spikes.",
        )

    ask = _f((market or {}).get("ask"))
    spread = _f((market or {}).get("spread"))
    depth = _f((market or {}).get("depth"))
    if ask is not None:
        if not 0.0 < ask < 1.0:
            return out(
                "AVOID",
                "AVOID - invalid market ask",
                0,
                f"The supplied ask ({ask}) is outside the valid 0-1 share-price range.",
                invalidate="A fresh exact-round order book supplies a valid executable ask.",
            )
        fee_rate = _f((market or {}).get("fee_rate"))
        if fee_rate is None:
            fee_rate = DEFAULT_CRYPTO_TAKER_FEE_RATE
        fees_enabled = (market or {}).get("fees_enabled") is not False
        effective_costs = (float(costs) if costs is not None else
                           polymarket_taker_fee_per_share(ask, fee_rate) if fees_enabled else 0.0)
        net_edge = fair_value - ask - effective_costs - buffer
        if spread is not None and (spread < 0.0 or spread > 0.03):
            flags.append(f"wide spread ({spread * 100:.0f}c)")
        edge_line = (
            f"fair {fair_value * 100:.0f}c - ask {ask * 100:.0f}c - "
            f"fee {effective_costs * 100:.1f}c - buffer {buffer * 100:.0f}c = "
            f"{net_edge * 100:+.1f}c"
        )
        if not structural_late_entry:
            return out(
                "WAIT",
                "WAIT - outside audited late-entry gate",
                confidence,
                f"The quote is available, but a paper entry requires P(hold)>=93%, at least $10 from "
                f"the line, and 15-{int(late_max)} seconds left. Current: P(hold)={p_hold*100:.0f}%, "
                f"distance=${gap:.0f}, seconds={seconds_left:.0f}.",
                invalidate="All audited late-entry conditions become true on the same fresh quote.",
            )
        if spread is not None and (spread < 0.0 or spread > 0.03):
            return out(
                "NO_EDGE",
                "NO EDGE - spread too wide",
                confidence,
                f"The executable spread is {spread*100:.1f}c, above the 3c liquidity gate. {edge_line}.",
                invalidate="Spread narrows to 3c or less while the structural and edge gates still hold.",
            )
        if depth is not None and depth <= 0:
            return out(
                "NO_EDGE",
                "NO EDGE - no displayed ask depth",
                confidence,
                "The quoted ask has no displayed top-level size. Treat it as non-executable.",
                invalidate="Positive ask depth appears on a fresh exact-round quote.",
            )
        if net_edge > required_edge and not (drop_risk == "HIGH" and position == "UP"):
            # Lever 2: PROPORTIONAL sizing. The bet decision is edge-based (fair - ask - costs), and
            # the suggested stake scales WITH that edge (quarter-Kelly, 10% bankroll cap) instead of
            # a fixed unit. Sizing support for the paper ledger -- not an order, not real-money advice.
            stake = kelly_stake(fair_value, ask, effective_costs)
            stake_line = (f" Suggested paper stake: {stake * 100:.1f}% of bankroll "
                          f"(quarter-Kelly, 10% cap)." if stake > 0 else "")
            return out(
                "PAPER_BET",
                f"PAPER-BET {position} - net edge {net_edge * 100:+.0f}c",
                min(99.0, confidence),
                f"Calibrated fair value beats the market after costs: {edge_line}. Paper-bet candidate only."
                + stake_line,
                bet=True,
                edge=net_edge,
                stake_frac=stake,
                invalidate="Ask rises to fair value, P(hold) decays, or spread widens.",
            )
        return out(
            "NO_EDGE",
            "HIGH PROBABILITY - no edge",
            confidence,
            f"P(hold) is high at {p_hold * 100:.0f}%, but the market already prices it: {edge_line}. Skip.",
            invalidate="Ask falls below fair value by more than the buffer.",
        )

    if p_hold >= PHOLD_STRONG:
        label = f"SETUP {position} - P(hold) {p_hold * 100:.0f}%"
        if tier:
            label += f" - {tier}"
        return out(
            "SETUP",
            label,
            confidence,
            f"High calibrated hold odds on the {position} side. This is a probability read, not a bet; a live market ask is required for edge validation.",
            invalidate="P(hold) drops below 93%, drop risk spikes, or price loses the side.",
        )

    if p_hold >= PHOLD_GOOD:
        return out(
            "LEAN",
            f"LEAN {position} - P(hold) {p_hold * 100:.0f}%",
            confidence,
            f"Moderate hold odds on the {position} side. Wait for stronger P(hold) or a proven market edge.",
            invalidate="P(hold) climbs above 93% or fades below 85%.",
        )

    return out(
        "WAIT",
        f"WAIT - P(hold) only {p_hold * 100:.0f}%",
        confidence,
        f"P(hold) is only {p_hold * 100:.0f}% on the {position} side. Too close to act.",
        invalidate="P(hold) strengthens above 85%.",
    )
