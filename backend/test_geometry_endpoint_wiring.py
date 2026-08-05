"""The conditional path head is wired to the REAL round fields, and has no authority.

    python backend/test_geometry_endpoint_wiring.py

WHY THIS EXISTS SEPARATELY FROM THE HEAD'S OWN SELFTEST
    geometry_endpoint_head.py is pure, so its selftest proves the arithmetic and nothing about
    the wiring. The first version of this wiring read `seconds_left` and `current_price` off the
    round view. Neither exists: the round state carries `price_to_beat`, `window_start` and
    `window_end`, and the price comes from the kline feed. Every round would have returned
    "unavailable" forever, and the pure selftest would have stayed green throughout.

    A head that silently emits nothing looks exactly like a quiet market.
"""
from __future__ import annotations

import math
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import geometry_endpoint_head as cph                 # noqa: E402
import server                                       # noqa: E402

_OK = True


def chk(cond: object, msg: str) -> None:
    global _OK
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    _OK = bool(_OK) and bool(cond)


def _walk(n=80, seed=7, start=100_000.0):
    rng = random.Random(seed)
    out = [start]
    for _ in range(n):
        out.append(out[-1] * math.exp(rng.gauss(0, 0.0008)))
    return out


def main() -> int:
    px = _walk()
    server.data_state["klines"] = [
        {"close": p, "high": p, "low": p, "is_closed": True} for p in px]
    now = int(time.time() * 1000)
    last = px[-1]

    ptb = {
        5:  {"price_to_beat": last * 0.999, "window_end": now + 120_000, "horizon": 5},
        15: {"price_to_beat": last * 1.001, "window_end": now + 300_000, "horizon": 15},
        12: {"price_to_beat": last,         "window_end": now - 5_000,   "horizon": 12},
    }
    block = server._conditional_path_block(ptb)

    print("the block is produced from the real round fields")
    chk(block.get("sigma_per_min") is not None,
        f"a causal sigma is computed from closed bars ({block.get('sigma_per_min'):.6f})")
    chk(abs((block.get("price_now") or 0) - last) < 1e-6,
        "the current price comes from the kline feed, not from the round view")
    chk("5" in block["rounds"] and "15" in block["rounds"],
        "open rounds produce a payload keyed by horizon")

    print("the probabilities respond to the state, not to a constant")
    five = block["rounds"]["5"]
    fifteen = block["rounds"]["15"]
    chk(five.get("settlement_probability", 0) > 0.5,
        f"ABOVE the anchor -> settlement probability above 0.5 "
        f"({five.get('settlement_probability')})")
    chk(fifteen.get("settlement_probability", 1) < 0.5,
        f"BELOW the anchor -> below 0.5 ({fifteen.get('settlement_probability')})")
    cps = five["checkpoints"]
    chk(len(cps) >= 2 and cps[0]["p_above_anchor"] > cps[-1]["p_above_anchor"],
        "a nearer checkpoint is more confident than a farther one at the same edge")

    print("closed and unusable states REFUSE, with a reason")
    chk(block["rounds"]["12"].get("unavailable_reason") == "round already closed",
        "a round past its window_end is refused, not extrapolated")
    chk(all(c["minutes_remaining"] > 0 for c in cps),
        "and no checkpoint that has already passed is forecast")

    server.data_state["klines"] = [{"close": 100.0, "is_closed": True}] * 3
    thin = server._conditional_path_block(ptb)
    chk(thin.get("unavailable_reason") and thin.get("sigma_per_min") is None,
        "too little history yields a stated reason and NO sigma - never a fabricated constant")

    print("it cannot crash the tick")
    for bad in (None, {}, {5: None}, {5: {"price_to_beat": None, "window_end": None}},
                {"x": {"price_to_beat": 1.0, "window_end": now + 60_000}}):
        try:
            server.data_state["klines"] = [
                {"close": p, "high": p, "low": p, "is_closed": True} for p in px]
            server._conditional_path_block(bad)
            chk(True, f"malformed input {str(bad)[:34]!s:36s} handled")
        except Exception as exc:
            chk(False, f"malformed input raised {type(exc).__name__}: {exc}")

    print("it has no authority, and says so everywhere")
    chk(cph.AUTHORITY == "NONE" and block["authority"] == "NONE",
        "the head and the payload both declare NONE")
    src = (Path(__file__).resolve().parent / "server.py").read_text(encoding="utf-8")
    start = src.index("def _conditional_path_block(")
    end = src.index("def _install_hmm_state(")
    body = src[start:end]
    for banned in ("signal", "direction =", "should_trade", "size", "order"):
        chk(banned not in body.replace("conditional path", ""),
            f"the builder contains no '{banned}' - it informs, it does not decide")
    chk("geometry_endpoint" in src and "DISPLAY ONLY" in src,
        "and the broadcast site marks it display-only")

    print("the target contract is unmistakable")
    chk(cph.TARGET_CONTRACT == "ENDPOINT_ABOVE_ANCHOR",
        "the head declares an ENDPOINT target, not a path one - the old name "
        "'conditional_path_head' invited exactly the first-touch/endpoint confusion this "
        "repository keeps paying for")
    chk(cph.MODEL_ASSUMPTION and cph.FORMULA_FAMILY,
        "the stochastic assumption and formula family are pinned, not implied")

    print("sigma units agree with the time units they multiply")
    chk(cph.SIGMA_UNITS == "PER_MINUTE_LOG_RETURN_STDDEV" and cph.TIME_UNITS == "MINUTES",
        "sigma is per-MINUTE and time is in MINUTES")
    # The pairing, checked numerically. A per-minute sigma multiplied by sqrt(SECONDS) would
    # be a silent sqrt(60) error making every probability ~7.7x too confident.
    z_min = cph.anchor_z(100.10, 100.0, 0.001, 4.0)          # 4 minutes
    z_sec = cph.anchor_z(100.10, 100.0, 0.001, 240.0)        # 240 "minutes" == the sec bug
    chk(abs(z_min / z_sec - math.sqrt(60.0)) < 0.01,
        f"feeding seconds where minutes are expected changes z by exactly sqrt(60) "
        f"({z_min / z_sec:.3f}) - which is why the units are declared and asserted")

    print("checkpoints carry ABSOLUTE timestamps")
    cell = five["checkpoints"][0]
    chk(cell.get("checkpoint_at_ms") and cell["checkpoint_at_ms"] > now,
        "each cell states WHEN it resolves, in epoch ms")
    chk(cell.get("seconds_until_checkpoint") is not None,
        "and how far away that is - '4m' alone is ambiguous between minute four of the "
        "round, four minutes remaining, and four minutes from now")

    print("the forming bar cannot enter sigma")
    # The old guard was `if closed and closed is kl` - dead code, because a comprehension
    # always allocates. With a feed that omits is_closed, the forming bar leaked into sigma.
    marked = [{"close": p, "is_closed": True} for p in px]
    unmarked = [{"close": p} for p in px[:-1]] + [{"close": 9_999_999.0}]
    server.data_state["klines"] = marked
    sig_marked = server._conditional_path_block(ptb)["sigma_per_min"]
    server.data_state["klines"] = unmarked
    sig_unmarked = server._conditional_path_block(ptb)["sigma_per_min"]
    chk(sig_unmarked is None or abs(sig_unmarked - sig_marked) < sig_marked,
        "an unmarked final bar with an absurd close does not blow up sigma")
    chk(sig_unmarked is None or sig_unmarked < sig_marked * 5,
        f"the forming bar is excluded: sigma {sig_unmarked} vs {sig_marked} - including a "
        f"9,999,999 close would have multiplied it")

    print("no trading module imports the head")
    import ast as _ast
    here = Path(__file__).resolve().parent
    offenders = []
    for name in ("decision_champion.py", "decision_gate.py", "trading_simulator.py",
                 "order_lifecycle.py", "ab_testing.py", "model_promotion.py"):
        f = here / name
        if not f.exists():
            continue
        for node in _ast.walk(_ast.parse(f.read_text(encoding="utf-8"))):
            mods = []
            if isinstance(node, _ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, _ast.ImportFrom):
                mods = [node.module or ""]
            if any("geometry_endpoint_head" in (m or "") for m in mods):
                offenders.append(name)
    chk(not offenders,
        f"no decision, sizing or order module imports the geometry head (offenders: "
        f"{offenders}) - checked by AST, not by keyword")

    print("\nGEOMETRY ENDPOINT WIRING:", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
