"""A BARRIER_FALLBACK row is classification evidence, never economic evidence.

THE DEFECT
    When no real horizon-end observation exists, the verifier substitutes the first-touch
    BARRIER price and computes `endpoint_move_usd` from it. It correctly LABELS the row
    `endpoint_price_basis = "BARRIER_FALLBACK"` - the safeguard was built.

    `_update_accuracy_cache`'s `_signed_endpoint_pnl()` then read `endpoint_move_usd` without
    ever consulting that label. So a classification barrier entered the economic expectancy as
    if it were a realised endpoint return: `expectancy_usd` became a barrier distance wearing
    a dollar sign, exactly the statistic the surrounding comment says it replaced.

    `meta_model` already filters `AND endpoint_price_basis = 'ENDPOINT'`. One consumer read
    the label and one did not, which is what made this invisible.

WHY THE FIXTURE USES PROFITABLE-LOOKING FALLBACK ROWS
    A test that injects neutral rows passes whether or not the guard exists. These rows are
    strongly positive, so if the guard is absent the expectancy is large and obviously wrong -
    the assertion has to be able to fail loudly.

    python backend/test_endpoint_basis_economic_admissibility.py
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CHECKS = 0


def check(cond, text):
    global CHECKS
    assert cond, text
    CHECKS += 1
    print(f"  PASS  {text}")


def _pnl_fn():
    """The exact closure under test, rebuilt from the verifier's own source.

    Extracted rather than reimplemented: a local copy would test the copy, and this defect was
    precisely a consumer that drifted from the contract it was meant to honour.
    """
    import inspect
    import prediction_verifier as pv
    src = inspect.getsource(pv)
    start = src.index("            def _signed_endpoint_pnl(v):")
    end = src.index("            _pnls = [", start)
    body = "\n".join(line[12:] for line in src[start:end].splitlines())
    ns: dict = {}
    exec(body, ns)
    return ns["_signed_endpoint_pnl"]


def main() -> int:
    signed = _pnl_fn()

    # 100 highly profitable-looking rows whose economics are a BARRIER, not an endpoint.
    fallback = [{"endpoint_move_usd": 250.0, "direction": "UP",
                 "endpoint_price_basis": "BARRIER_FALLBACK"} for _ in range(100)]
    got = [x for x in (signed(v) for v in fallback) if x is not None]
    check(got == [],
          "100 profitable-looking BARRIER_FALLBACK rows contribute NOTHING to economic "
          "expectancy - without the guard they would have reported +$250 average EV from a "
          "classification barrier")

    # One genuine endpoint row is admissible, and signs correctly by side.
    real_up = {"endpoint_move_usd": 40.0, "direction": "UP",
               "endpoint_price_basis": "ENDPOINT"}
    real_dn = {"endpoint_move_usd": 40.0, "direction": "DOWN",
               "endpoint_price_basis": "ENDPOINT"}
    check(signed(real_up) == 40.0, "a genuine ENDPOINT row IS admissible")
    check(signed(real_dn) == -40.0,
          "and is signed by the side actually served - a DOWN prediction into a +$40 move is "
          "a $40 loss")

    mixed = fallback + [real_up]
    pnls = [x for x in (signed(v) for v in mixed) if x is not None]
    check(len(pnls) == 1 and pnls[0] == 40.0,
          "in a mixed batch only the endpoint row survives: economic_n = 1, not 101 - the "
          "fallback rows remain valid CLASSIFICATION evidence, they simply carry no economics")

    # Absence and malformation must both refuse, not default.
    for bad in ({"endpoint_move_usd": 99.0, "direction": "UP"},
                {"endpoint_move_usd": 99.0, "direction": "UP", "endpoint_price_basis": ""},
                {"endpoint_move_usd": 99.0, "direction": "UP",
                 "endpoint_price_basis": "endpoint"},
                {"endpoint_move_usd": None, "direction": "UP",
                 "endpoint_price_basis": "ENDPOINT"}):
        check(signed(bad) is None,
              f"refused: {bad.get('endpoint_price_basis', '<missing>')!r} basis / "
              f"{bad.get('endpoint_move_usd')!r} move - a missing or unrecognised basis is "
              f"not an endpoint, and lowercase must not pass by accident")

    print(f"\nENDPOINT BASIS ECONOMIC ADMISSIBILITY: PASS ({CHECKS} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
