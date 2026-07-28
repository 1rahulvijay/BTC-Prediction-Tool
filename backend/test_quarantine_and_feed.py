"""Regression tests: quarantined prototypes and non-blocking feed callbacks.

Both changes are the same principle applied twice - a component that cannot produce trustworthy
output must refuse loudly rather than return a plausible number, and a component that persists
diagnostics must never be able to stall the feed that produces them.

    python backend/test_quarantine_and_feed.py
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

_OK = True


def chk(cond: object, msg: str) -> None:
    global _OK
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    _OK = bool(_OK) and bool(cond)


def test_prototypes_quarantined() -> None:
    print("quarantined prototypes")
    from polymarket_model import PolymarketModel
    from polymarket_model import QuarantinedPrototype as ModelRefusal
    from polymarket_simulator import PolymarketSimulator
    from polymarket_simulator import QuarantinedPrototype as SimRefusal

    for factory, refusal, name in (
        (PolymarketSimulator, SimRefusal, "polymarket_simulator"),
        (PolymarketModel, ModelRefusal, "polymarket_model"),
    ):
        os.environ.pop("BTC_ALLOW_LEGACY_PM_SIMULATOR", None)
        os.environ.pop("BTC_ALLOW_LEGACY_PM_MODEL", None)
        try:
            factory()
            chk(False, name + " must refuse construction")
        except refusal as exc:
            chk("QUARANTINED" in str(exc), name + " refuses at construction, with the reason")

    # CODE only. The removal note deliberately names the symbols it removed, so a raw text
    # search flags the very comment that documents the fix - the same trap as checking for a
    # truthiness fallback in a comment that explains one.
    server_src = (Path(__file__).resolve().parent / "server.py").read_text(encoding="utf-8")
    code = chr(10).join(
        line for line in server_src.splitlines() if not line.strip().startswith("#"))
    for token in ("PolymarketSimulator", "PolymarketModel", "pm_simulator", "pm_model"):
        chk(token not in code,
            "server.py CODE no longer references " + token + " (built and never called)")


def test_fee_formula_is_quadratic() -> None:
    print("taker fee stays quadratic")
    from decision_champion import DEFAULT_BUFFER as B
    from decision_champion import DEFAULT_CRYPTO_TAKER_FEE_RATE as R
    from decision_champion import DEFAULT_REQUIRED_EDGE as E
    from decision_champion import max_taker_ask
    from polymarket_fee import polymarket_taker_fee_per_share as fee

    # The official taker fee is rate*p*(1-p), so the maximum ask solves
    # target = q + rate*q*(1-q). The quadratic root satisfies that with EQUALITY.
    for fair in (0.55, 0.65, 0.75, 0.85, 0.95):
        target = fair - B - E
        q = max_taker_ask(fair)
        chk(abs((q + fee(q)) - target) < 1e-4,
            f"fair={fair:.2f}: q+fee(q) reconstructs the target exactly")
    # A linear approximation target/(1+r) ignores the rate*q^2 term and UNDERSHOOTS, rejecting
    # asks that genuinely clear the threshold. Pinned so it cannot be "simplified" back in.
    fair = 0.85
    target = fair - B - E
    linear = target / (1.0 + R)
    chk(linear + fee(linear) < target - 1e-3,
        "the linear alternative undershoots the target - it would reject tradeable asks")
    chk(max_taker_ask(fair) > linear,
        "so the quadratic admits a strictly higher, still-compliant ask")


def test_feed_callbacks_do_not_block() -> None:
    print("feed callbacks hand off instead of blocking")
    server_src = (Path(__file__).resolve().parent / "server.py").read_text(encoding="utf-8")
    chk(not re.search(r"^\s*database\.log_raw_trade_parquet\(", server_src, re.M),
        "no direct blocking trade write remains in a callback")
    chk(not re.search(r"^\s*database\.log_depth_parquet\(", server_src, re.M),
        "no direct blocking depth write remains in a callback")
    chk("FEED_WRITER.submit(database.log_raw_trade_parquet" in server_src,
        "trade logging goes through the bounded writer")
    chk("FEED_WRITER.submit(database.log_depth_parquet" in server_src,
        "depth logging goes through the bounded writer")

    import time

    from feed_writer import FeedWriter

    slow = FeedWriter(maxsize=64, name="regression").start()
    started = time.perf_counter()
    for _ in range(20):
        slow.submit(lambda _p: time.sleep(0.05), None)      # 1.0s of writer work
    elapsed = time.perf_counter() - started
    chk(elapsed < 0.05,
        f"20 submits of 50ms work return in {elapsed * 1000:.1f}ms, not ~1000ms")
    slow.stop(timeout=0.2)

    full = FeedWriter(maxsize=2, name="bounded")            # never started; nothing drains
    accepted = sum(full.submit(lambda _p: None, i) for i in range(10))
    chk(accepted == 2 and full.stats()["dropped"] == 8,
        "a full queue DROPS and counts rather than growing without bound")
    chk(full.stats()["healthy"] is False, "and reports itself unhealthy")


def run() -> int:
    for test in (test_prototypes_quarantined,
                 test_fee_formula_is_quadratic,
                 test_feed_callbacks_do_not_block):
        test()
    print("\nQUARANTINE + FEED", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1


if __name__ == "__main__":
    sys.exit(run())
