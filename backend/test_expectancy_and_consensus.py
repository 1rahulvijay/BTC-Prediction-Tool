"""
`expectancy_usd` was accuracy wearing a dollar sign, and the venue "median" was not one.

EXPECTANCY  The verifier averaged `|actual_move_usd|` over hits minus misses. Under first
            touch `actual_move_usd` is `resolution_price - entry` and `resolution_price` is
            the BARRIER, whose distance is `entry * threshold` - identical on every touching
            row. The whole statistic collapsed to `barrier * (2 * accuracy - 1)`: a linear
            rescaling of accuracy, displayed as "historical EV". This consumer was NAMED in
            the 5.5/5.6/5.7 work and not converted with the others.

MEDIAN      `sorted(valid)[len(valid) // 2]` is the UPPER middle on an even count. With two
            venues reporting it returned the higher price and called it a median.

Run directly:  python backend/test_expectancy_and_consensus.py
"""

import ast
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

FAILURES = []


def chk(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILURES.append(msg)


def code_of(path: Path, name: str) -> str:
    """Function CODE only - no docstring, no comments - via `ast.unparse`."""
    src = path.read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    stmts = fn.body[1:] if (fn.body and isinstance(fn.body[0], ast.Expr)
                            and isinstance(fn.body[0].value, ast.Constant)
                            and isinstance(fn.body[0].value.value, str)) else fn.body
    return "\n".join(ast.unparse(s) for s in stmts)


def _summary(rows):
    from prediction_verifier import PredictionVerifier
    v = PredictionVerifier()
    for direction, hit, barrier_move, endpoint_move in rows:
        row = {"horizon": 5, "direction": direction, "raw_direction": direction,
               "confidence": 0.6, "hit": hit, "lean_hit": hit,
               "actual_direction": direction if hit else ("DOWN" if direction == "UP" else "UP"),
               "actual_move_usd": barrier_move, "regime": "RANGE"}
        if endpoint_move is not None:
            row["endpoint_move_usd"] = endpoint_move
        v.verified_by_horizon[5].append(row)
    v._update_accuracy_cache()
    return v.get_accuracy_summary()[5]


def test_expectancy():
    print("\nEXPECTANCY is a realised return, not accuracy in dollars")

    # 70% accurate. The barrier is +-80 on every row by construction. The ENDPOINT moves
    # are small when right and large when wrong - a real and common shape.
    a = _summary([("UP", True, 80.0, 3.0)] * 70 + [("UP", False, -80.0, -25.0)] * 30)
    old_formula = 80.0 * (2 * 0.70 - 1)
    chk(a["expectancy_usd"] == -5.4,
        f"a 70%-accurate model reports ${a['expectancy_usd']} per trade - it is LOSING money")
    chk(round(old_formula, 2) == 32.0,
        f"the old formula reported ${old_formula:.2f} on the same rows, because it averaged "
        f"a barrier distance the contract chose rather than a return the market paid")
    chk(a["expectancy_basis"] == "ENDPOINT_SIGNED_PNL" and a["expectancy_n"] == 100,
        "and the row says which quantity it is and over how many observations")

    print("\n     ... signed by the side actually served")
    short = _summary([("DOWN", True, -80.0, -12.0)] * 50 + [("DOWN", False, 80.0, 4.0)] * 50)
    chk(short["expectancy_usd"] == 4.0,
        f"a SHORT that fell 12 earns +12, and one that rose 4 loses 4 -> "
        f"${short['expectancy_usd']} - the sign follows the position, not the price")

    print("\n     ... and an unmeasurable EV is not a negative one")
    none_rows = _summary([("UP", True, 80.0, None)] * 100)
    chk(none_rows["expectancy_usd"] is None
        and none_rows["expectancy_basis"] == "UNAVAILABLE_NO_ENDPOINT_ROWS",
        "with no endpoint observation there is no expected value to report - and reporting "
        "the barrier-derived number would be reporting accuracy in dollars again")
    chk(none_rows["expectancy_n"] == 0,
        "and the observation count says so rather than implying evidence")

    src = code_of(BACKEND / "prediction_verifier.py", "_update_accuracy_cache")
    chk("endpoint_move_usd" in src,
        "the computation reads the endpoint field carried for exactly this purpose")
    chk("gross_profit" not in src and "gross_loss" not in src,
        "and the hits-minus-misses form on |actual_move_usd| is gone")

    print("\n     ... and the gate cannot read 'unmeasurable' as 'negative'")
    gate = code_of(BACKEND / "server.py", "apply_live_quality_filters")
    chk("_exp_raw = acc.get('expectancy_usd')" in gate,
        "the gate takes the raw value rather than coercing it")
    chk("float(acc.get('expectancy_usd', 0.0) or 0.0)" not in gate,
        "`float(None or 0.0)` would have produced 0.0, tripped the `<= 0` branch, and raised "
        "the safety bar citing a negative EV that was never measured")
    chk("_exp_raw is None" in gate,
        "the two states are distinguished explicitly, each with its own message")


def test_consensus_median():
    print("\nMEDIAN the venue consensus is the middle, including on an even count")
    import server

    # Exercise the SHIPPED function. Reimplementing the median here would test this file
    # rather than server.py - the same mistake that let an inverted mapping survive earlier
    # in this session.
    import time as _t

    def consensus_for(prices):
        """Feed real venue prices through build_exchanges_block and read its consensus.

        `binance` comes from the last kline; `bybit`/`kucoin` come from `multi_exchange`
        and are each aged against their own observation timestamp; `chainlink` is read
        directly. Coinbase is left absent so the venue count is exactly len(prices).
        """
        now = _t.time()
        state = {"klines": [{"close": prices[0]}]}
        extra = list(prices[1:])
        mx = {}
        for name, px in zip(("bybit", "kucoin"), extra[:2]):
            mx[name] = px
            mx[f"{name}_observed_ts"] = now
        state["multi_exchange"] = mx
        if len(extra) > 2:
            state["chainlink_price"] = extra[2]
        return server.build_exchanges_block(state)["consensus"]

    src = code_of(BACKEND / "server.py", "build_exchanges_block")
    chk("sorted(valid)[len(valid) // 2]" not in src,
        "the upper-middle expression is gone - with two venues reporting it returned the "
        "HIGHER price and called it a median, biasing every per-venue deviation_bps")
    chk("len(_sv) % 2" in src,
        "the parity of the count is what decides, which is what a median is")

    chk(consensus_for([100.0, 110.0]) == 105.0,
        "two venues 100 and 110 -> 105.0, not 110.0")
    chk(consensus_for([100.0, 104.0, 110.0, 120.0]) == 107.0,
        "four venues -> the mean of the two middles, 107.0, not 110.0")
    chk(consensus_for([100.0, 104.0, 110.0]) == 104.0,
        "and an odd count is unchanged, so nothing that was right becomes wrong")


def main():
    print("=" * 78)
    print("EXPECTANCY AND VENUE CONSENSUS")
    print("=" * 78)
    test_expectancy()
    test_consensus_median()
    print("\n" + "=" * 78)
    if FAILURES:
        print(f"EXPECTANCY AND VENUE CONSENSUS: FAIL ({len(FAILURES)})")
        for f in FAILURES:
            print("  - " + f)
        return 1
    print("EXPECTANCY AND VENUE CONSENSUS: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
