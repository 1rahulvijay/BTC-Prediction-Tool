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


def test_quarantine_overrides_are_independent() -> None:
    """One override must not unlock the other module.

    Both modules read a variable named ALLOW_ENV, and polymarket_model's was set to the
    SIMULATOR's variable name. Enabling the simulator for isolated research therefore also
    un-quarantined the model, silently. The original test popped both variables and constructed
    both, so identical names passed it - it never varied one while holding the other fixed."""
    print("quarantine overrides are per-module")
    import importlib

    import polymarket_model
    import polymarket_simulator

    chk(polymarket_model.ALLOW_ENV != polymarket_simulator.ALLOW_ENV,
        "the two modules read DIFFERENT override variables "
        f"({polymarket_model.ALLOW_ENV} vs {polymarket_simulator.ALLOW_ENV})")

    cases = (
        ("BTC_ALLOW_LEGACY_PM_SIMULATOR", polymarket_model, "PolymarketModel",
         "simulator override does not unlock the model"),
        ("BTC_ALLOW_LEGACY_PM_MODEL", polymarket_simulator, "PolymarketSimulator",
         "model override does not unlock the simulator"),
    )
    for variable, module, factory_name, message in cases:
        os.environ.pop("BTC_ALLOW_LEGACY_PM_SIMULATOR", None)
        os.environ.pop("BTC_ALLOW_LEGACY_PM_MODEL", None)
        os.environ[variable] = "1"
        try:
            importlib.reload(module)
            getattr(module, factory_name)()
            chk(False, message)
        except module.QuarantinedPrototype:
            chk(True, message)
        finally:
            os.environ.pop(variable, None)
            importlib.reload(module)

    os.environ.pop("BTC_ALLOW_LEGACY_PM_SIMULATOR", None)
    os.environ.pop("BTC_ALLOW_LEGACY_PM_MODEL", None)
    for module, factory_name in ((polymarket_model, "PolymarketModel"),
                                 (polymarket_simulator, "PolymarketSimulator")):
        importlib.reload(module)
        try:
            getattr(module, factory_name)()
            chk(False, f"{factory_name} is blocked with no override set")
        except module.QuarantinedPrototype:
            chk(True, f"{factory_name} is blocked with no override set")


def test_legacy_algodesk_quarantined() -> None:
    """backend/algodesk_ml_rl_dl.py fabricates funding and open interest.

    It sets funding_rate = 8h price change * 0.05 and open_interest = 24h base volume * 3.5,
    then lets the FUND, OI and OIDIV agents trade on both. Its numbers therefore describe
    momentum and volume under the names of funding and OI, and are not comparable with
    research/algodesk/, which fetches the real series from Bybit.

    Source-level, so this costs nothing: importing the module pulls torch, xgboost and
    stable_baselines3. The behavioural refusal is asserted by the runtime check below only when
    those imports are available."""
    print("legacy algodesk prototype quarantined")
    path = Path(__file__).resolve().parent / "algodesk_ml_rl_dl.py"
    src = path.read_text(encoding="utf-8")

    chk("QUARANTINED = True" in src, "declares QUARANTINED")
    chk('ALLOW_ENV = "BTC_ALLOW_LEGACY_ALGODESK"' in src,
        "reads its OWN override variable, not another module's")
    for other in ("BTC_ALLOW_LEGACY_PM_SIMULATOR", "BTC_ALLOW_LEGACY_PM_MODEL"):
        chk(other not in src, "does not honour " + other)

    # The refusal must be the FIRST statement of create_dataset - every path to a number goes
    # through it, and refusing after the fetch would still hit the network.
    body = src.split("def create_dataset(", 1)
    chk(len(body) == 2, "create_dataset still exists")
    if len(body) == 2:
        first = [ln.strip() for ln in body[1].splitlines()[1:] if ln.strip()][:1]
        chk(bool(first) and first[0].startswith("_refuse("),
            "create_dataset refuses before fetching or computing anything")

    # The fabrications are still present in the file (it is preserved, not edited), which is
    # exactly why it must stay quarantined. If someone deletes them, revisit the quarantine.
    chk("(change_8h * 0.05)" in src, "simulated funding still present -> quarantine still needed")
    chk('df["vol_24h"] * 3.5' in src, "simulated OI still present -> quarantine still needed")


def test_canonical_algodesk_does_not_fabricate() -> None:
    """The replacement must not reintroduce what the prototype was quarantined for."""
    print("canonical algodesk uses real funding and OI")
    agents = Path(__file__).resolve().parents[1] / "research" / "algodesk" / "agents.py"
    if not agents.exists():
        chk(False, "research/algodesk/agents.py exists")
        return
    src = agents.read_text(encoding="utf-8")

    # CODE only, and that means stripping DOCSTRINGS as well as '#' comments. agents.py names
    # "change_8h * 0.05" and "volume * 3.5" in its module docstring precisely to record what it
    # refuses to do, so a raw text search fails on the sentence that documents the fix. Blanking
    # every ast docstring node is what makes the assertion mean "no such computation" rather
    # than "nobody mentioned it".
    import ast

    tree = ast.parse(src)
    doc_lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(node, "body", None)
        if not body or not isinstance(body[0], ast.Expr):
            continue
        value = body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            doc_lines.update(range(value.lineno, (value.end_lineno or value.lineno) + 1))

    code = chr(10).join(
        ln for i, ln in enumerate(src.splitlines(), start=1)
        if i not in doc_lines and not ln.strip().startswith("#"))

    chk("0.05" in src and "* 0.05" not in code,
        "documents the simulated-funding formula but never computes it")
    chk("* 3.5" not in code, "does not derive open interest from volume")
    chk("min_periods=1)" not in code, "no partial 24h windows")
    chk('g["rsi"]' not in code,
        "no column named rsi (it was range position, not RSI)")
    chk('g["range_position_pct"]' in code, "range position is named for what it computes")
    chk('g["oi_usd"] = g["open_interest"] * close' in code,
        "open interest is converted to USD notional before threshold comparison")
    chk('g["vol24"] = g["turnover"]' in code,
        "24h volume uses turnover (quote/USD), not base-asset volume")


def test_polymarket_boundary_has_no_trading_authority() -> None:
    """PolymarketClient and PolymarketVerifier remain in the live server. They are permitted to
    read market data and to diagnose; they may not submit an order, price a candidate, size a
    position, or supply promotion evidence.

    Quarantining the model and the simulator removed the two modules that invented prices. It did
    not, by itself, establish that what REMAINS at the Polymarket boundary is data-only."""
    print("Polymarket boundary is data/diagnostic only")
    import inspect

    import polymarket_client
    import polymarket_verifier

    order_tokens = (
        "post_order", "place_order", "submit_order", "create_order", "cancel_order",
        "/order", "signed_order", "L1_AUTH", "private_key", "api_secret",
    )
    for module in (polymarket_client, polymarket_verifier):
        source = inspect.getsource(module)
        code = chr(10).join(
            line for line in source.splitlines() if not line.strip().startswith("#"))
        found = [token for token in order_tokens if token in code]
        chk(not found,
            f"{module.__name__} contains no order-submission surface ({found or 'none'})")

    client_methods = {
        name for name, _ in inspect.getmembers(
            polymarket_client.PolymarketClient, inspect.isfunction)
        if not name.startswith("_")
    }
    pricing_authority = {"fair_value", "price_candidate", "size_position", "kelly_fraction",
                         "recommended_size", "expected_value"}
    overlap = client_methods & pricing_authority
    chk(not overlap, f"PolymarketClient exposes no pricing/sizing method ({overlap or 'none'})")


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
    chk("FEED_WRITER.submit_depth(" in server_src,
        "depth logging goes through the bounded writer's COALESCING lane")
    chk("FEED_WRITER.start()" in server_src and "FEED_WRITER.stop(" in server_src,
        "the application owns start AND stop, rather than a thread spawned at import")
    chk('"feed_writer": feed_writer_stats' in server_src,
        "writer stats reach runtime health, not just a dict nobody reads")
    chk("feed_writer_not_running" in server_src,
        "a dead writer becomes a trust blocker - an archive with silent holes is not evidence")

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

    full = FeedWriter(maxsize=2, name="bounded").start()
    with full._cv:                       # hold the worker so the bound is what is tested
        accepted = 0
        for i in range(10):
            if len(full._trades) >= full._maxsize:
                full.dropped_trades += 1
                full.dropped += 1
            else:
                full._trades.append((lambda _p: None, i, time.time()))
                accepted += 1
    chk(accepted == 2 and full.stats()["dropped"] == 8,
        "a full queue DROPS and counts rather than growing without bound")
    chk(full.stats()["healthy"] is False, "and reports itself unhealthy")
    full.stop(timeout=0.5)


def run() -> int:
    for test in (test_prototypes_quarantined,
                 test_quarantine_overrides_are_independent,
                 test_legacy_algodesk_quarantined,
                 test_canonical_algodesk_does_not_fabricate,
                 test_polymarket_boundary_has_no_trading_authority,
                 test_fee_formula_is_quadratic,
                 test_feed_callbacks_do_not_block):
        test()
    print("\nQUARANTINE + FEED", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1


if __name__ == "__main__":
    sys.exit(run())
