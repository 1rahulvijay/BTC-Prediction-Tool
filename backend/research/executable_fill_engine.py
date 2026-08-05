"""PHASE 3 -- the shared deterministic executable fill engine.

Canonical blueprint (2026-07-25). ONE engine serves all three tests (TP-before-SL surface,
first-profitable-exit hazard, 5m->15m catch-up) so they cannot silently disagree about what a
fill is.

THE RULES (each one exists because violating it manufactures fake profit):
  * The book that PRODUCED a decision can never fill it. Entry uses the first book at or after
    decision_ts + latency.
  * Entry walks the ASK ladder; exit walks the BID ladder. Midpoint never appears.
  * Only visible size fills. Unfillable size is reported, never assumed.
  * Exit is the FIRST book meeting the exit rule scanning forward -- never the best one in
    hindsight.
  * Fees are charged on both legs from the frozen taker formula. The snapshot's recorded
    fee_rate_bps is all-zero and is never read (Phase-2 finding).
  * One entry per (round, configuration).

Deterministic: same snapshot + same config => byte-identical results.

Selftest (no DB required):
    python backend/research/executable_fill_engine.py --selftest
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, asdict, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from executable_surface_config import taker_fee, FEE_RATE  # noqa: E402

NS_PER_S = 1_000_000_000

#: Every eligibility gate this engine applies. A caller must supply all of them.
ELIGIBILITY_KEYS = ("min_ask", "max_ask", "max_spread", "min_top_ask_size",
                    "max_book_staleness_s")


class EligibilityIncomplete(ValueError):
    """A gate was not configured. Refusing is the only safe response."""


def require_eligibility(elig: dict) -> dict:
    """Fail closed on a missing gate.

    THE DEFECT THIS REMOVES
        Every gate was read with a permissive default:

            max_book_staleness_s -> 1e9      (~31 years of staleness allowed)
            min_ask / max_ask    -> 0.0/1.0  (the entire probability range)
            max_spread           -> 1.0      (any spread whatsoever)
            min_top_ask_size     -> 0.0      (any size, including none)

        So a missing or misspelled key did not raise - it silently DELETED that gate, and the
        run then reported more fills at better prices. Every default failed in the direction
        that flatters the result, which is the one direction a backtest must never fail in.

        Note the distinction this preserves: `build_complete_trade_dataset` sets max_spread to
        1.0 on purpose, because it is building a dataset rather than filtering one. An explicit
        1.0 is a decision; an absent key that becomes 1.0 is an accident, and in the output the
        two were indistinguishable. Now only the first is possible.
    """
    missing = [k for k in ELIGIBILITY_KEYS if k not in elig]
    if missing:
        raise EligibilityIncomplete(
            "eligibility config is missing " + ", ".join(missing)
            + ". Every gate must be stated explicitly - a defaulted gate is a disabled gate, "
            "and a disabled gate produces more fills at better prices without saying so.")
    return elig


# ---------------------------------------------------------------------------------------------
# Book state
# ---------------------------------------------------------------------------------------------
@dataclass
class BookState:
    """One synchronized book observation for one asset."""
    seq: int
    recv_ts_ns: int
    best_bid: float
    best_ask: float
    best_bid_size: float
    best_ask_size: float
    spread: float
    asks: list = field(default_factory=list)   # [(price, size)] ascending price
    bids: list = field(default_factory=list)   # [(price, size)] descending price

    @property
    def ts_s(self) -> float:
        return self.recv_ts_ns / NS_PER_S


def ladder_vwap(levels, qty: float) -> tuple:
    """Walk a price ladder for `qty` shares.

    Returns (vwap, filled_qty). Partial fills are reported honestly: if the visible ladder
    cannot supply `qty`, filled_qty < qty and vwap covers only what filled. Never invents size.
    """
    if qty <= 0 or not levels:
        return (None, 0.0)
    remaining = float(qty)
    cost = 0.0
    filled = 0.0
    for price, size in levels:
        if remaining <= 1e-12:
            break
        take = min(remaining, float(size))
        if take <= 0:
            continue
        cost += float(price) * take
        filled += take
        remaining -= take
    if filled <= 0:
        return (None, 0.0)
    return (cost / filled, filled)


def first_book_at_or_after(books, ts_ns: int, start_idx: int = 0) -> int:
    """Index of the first book with recv_ts_ns >= ts_ns, or -1. Books must be seq-ordered."""
    for i in range(start_idx, len(books)):
        if books[i].recv_ts_ns >= ts_ns:
            return i
    return -1


# ---------------------------------------------------------------------------------------------
# Trade simulation
# ---------------------------------------------------------------------------------------------
@dataclass
class TradeResult:
    eligible: bool
    reason: str = ""
    entry_seq: int = 0
    entry_ts_s: float = 0.0
    entry_vwap: float = 0.0
    entry_qty: float = 0.0
    requested_qty: float = 0.0
    entry_fee_per_share: float = 0.0
    exit_kind: str = ""              # TP | SL | SETTLE | NO_EXIT_DATA
    exit_seq: int = 0
    exit_ts_s: float = 0.0
    exit_gross_per_share: float = 0.0
    exit_fee_per_share: float = 0.0
    net_per_share: float = 0.0
    net_total: float = 0.0
    holding_s: float = 0.0
    mfe_per_share: float = 0.0       # best net-of-fees P/L reachable while open
    mae_per_share: float = 0.0       # worst
    first_profitable_s: float = None  # None = censored (never profitable while open)
    first_profitable_net: float = 0.0
    books_scanned: int = 0

    def as_dict(self):
        return asdict(self)


def simulate_trade(books, decision_ts_ns: int, latency_ms: int, qty: float,
                   tp_cents: float, sl_cents: float, settle_value: float,
                   fee_rate: float = FEE_RATE, eligibility: dict = None) -> TradeResult:
    """Simulate ONE entry and its exit against a real book timeline.

    `books`      : seq-ordered BookState list for the asset being bought.
    `settle_value`: 1.0 if this asset wins the round, else 0.0 (hold-to-settlement payoff).
    TP/SL are NET per-share thresholds in cents, evaluated on the executable bid.
    """
    elig = require_eligibility(eligibility or {})
    res = TradeResult(eligible=False, requested_qty=float(qty))

    entry_idx = first_book_at_or_after(books, int(decision_ts_ns) + int(latency_ms) * 1_000_000)
    if entry_idx < 0:
        res.reason = "no_book_after_latency"
        return res
    b = books[entry_idx]

    # staleness: the entry book must itself be fresh relative to the decision instant
    stale_s = (b.recv_ts_ns - decision_ts_ns) / NS_PER_S
    if stale_s > float(elig["max_book_staleness_s"]):
        res.reason = "entry_book_stale"
        return res
    if b.best_ask is None or b.best_bid is None:
        res.reason = "no_two_sided_book"
        return res
    if not (elig["min_ask"] <= b.best_ask <= elig["max_ask"]):
        res.reason = "ask_out_of_band"
        return res
    if b.spread is not None and b.spread > elig["max_spread"]:
        res.reason = "spread_too_wide"
        return res
    if b.best_ask_size < elig["min_top_ask_size"]:
        res.reason = "insufficient_top_ask_size"
        return res

    entry_vwap, filled = ladder_vwap(b.asks, qty)
    if entry_vwap is None or filled <= 0:
        res.reason = "no_ask_liquidity"
        return res

    entry_fee = taker_fee(entry_vwap, fee_rate)
    res.eligible = True
    res.entry_seq, res.entry_ts_s = b.seq, b.ts_s
    res.entry_vwap, res.entry_qty = entry_vwap, filled
    res.entry_fee_per_share = entry_fee

    tp = float(tp_cents) / 100.0
    sl = float(sl_cents) / 100.0
    cost = entry_vwap + entry_fee

    mfe, mae = -1e9, 1e9
    scanned = 0
    for j in range(entry_idx + 1, len(books)):
        nb = books[j]
        scanned += 1
        exit_vwap, exit_filled = ladder_vwap(nb.bids, filled)
        if exit_vwap is None or exit_filled < filled - 1e-9:
            continue                                   # cannot fully exit here; keep holding
        net = exit_vwap - taker_fee(exit_vwap, fee_rate) - cost
        mfe = max(mfe, net)
        mae = min(mae, net)
        if res.first_profitable_s is None and net > 0:
            res.first_profitable_s = nb.ts_s - b.ts_s
            res.first_profitable_net = round(net, 6)
        if net >= tp or net <= -sl:
            res.exit_kind = "TP" if net >= tp else "SL"
            res.exit_seq, res.exit_ts_s = nb.seq, nb.ts_s
            res.exit_gross_per_share = exit_vwap
            res.exit_fee_per_share = taker_fee(exit_vwap, fee_rate)
            res.net_per_share = net
            res.holding_s = nb.ts_s - b.ts_s
            break
    else:
        # never hit a barrier -> hold to official settlement (no exit fee on settlement)
        net = float(settle_value) - cost
        mfe = max(mfe, net)
        mae = min(mae, net)
        res.exit_kind = "SETTLE"
        res.exit_gross_per_share = float(settle_value)
        res.exit_fee_per_share = 0.0
        res.net_per_share = net
        res.exit_ts_s = books[-1].ts_s if books else b.ts_s
        res.holding_s = res.exit_ts_s - b.ts_s

    res.books_scanned = scanned
    res.mfe_per_share = round(mfe if mfe > -1e8 else res.net_per_share, 6)
    res.mae_per_share = round(mae if mae < 1e8 else res.net_per_share, 6)
    res.net_per_share = round(res.net_per_share, 6)
    res.net_total = round(res.net_per_share * filled, 6)
    return res


# ---------------------------------------------------------------------------------------------
# Path form -- the SAME semantics as simulate_trade, factored for grid scale
#
# The realized net-P/L path after entry does not depend on TP/SL; only the stopping rule does.
# So the expensive forward scan runs ONCE per (round, side, checkpoint, latency, qty), and every
# barrier pair is then evaluated against that one path. This is a pure refactor: `net_path` +
# `first_barrier` are asserted equal to `simulate_trade` in the selftest, so the fast path can
# never drift from the reference implementation.
# ---------------------------------------------------------------------------------------------
@dataclass
class EntryPath:
    eligible: bool
    reason: str = ""
    entry_seq: int = 0
    entry_ts_s: float = 0.0
    entry_vwap: float = 0.0
    entry_fee_per_share: float = 0.0
    filled_qty: float = 0.0
    requested_qty: float = 0.0
    ts: list = field(default_factory=list)      # seconds since entry, exit-able books only
    net: list = field(default_factory=list)     # net per-share P/L if exiting at that book
    seqs: list = field(default_factory=list)
    settle_net: float = 0.0                     # net per-share if held to settlement


def net_path(books, decision_ts_ns: int, latency_ms: int, qty: float, settle_value: float,
             fee_rate: float = FEE_RATE, eligibility: dict = None) -> EntryPath:
    """Entry + the full forward net-P/L path. Same rules as simulate_trade, no stopping rule."""
    elig = require_eligibility(eligibility or {})
    p = EntryPath(eligible=False, requested_qty=float(qty))

    idx = first_book_at_or_after(books, int(decision_ts_ns) + int(latency_ms) * 1_000_000)
    if idx < 0:
        p.reason = "no_book_after_latency"
        return p
    b = books[idx]
    if (b.recv_ts_ns - decision_ts_ns) / NS_PER_S > float(elig["max_book_staleness_s"]):
        p.reason = "entry_book_stale"
        return p
    if b.best_ask is None or b.best_bid is None:
        p.reason = "no_two_sided_book"
        return p
    if not (elig["min_ask"] <= b.best_ask <= elig["max_ask"]):
        p.reason = "ask_out_of_band"
        return p
    if b.spread is not None and b.spread > elig["max_spread"]:
        p.reason = "spread_too_wide"
        return p
    if b.best_ask_size < elig["min_top_ask_size"]:
        p.reason = "insufficient_top_ask_size"
        return p

    entry_vwap, filled = ladder_vwap(b.asks, qty)
    if entry_vwap is None or filled <= 0:
        p.reason = "no_ask_liquidity"
        return p

    entry_fee = taker_fee(entry_vwap, fee_rate)
    cost = entry_vwap + entry_fee
    p.eligible = True
    p.entry_seq, p.entry_ts_s = b.seq, b.ts_s
    p.entry_vwap, p.entry_fee_per_share, p.filled_qty = entry_vwap, entry_fee, filled

    for j in range(idx + 1, len(books)):
        nb = books[j]
        ev, ef = ladder_vwap(nb.bids, filled)
        if ev is None or ef < filled - 1e-9:
            continue                       # cannot fully exit here
        p.ts.append(nb.ts_s - b.ts_s)
        p.net.append(ev - taker_fee(ev, fee_rate) - cost)
        p.seqs.append(nb.seq)
    p.settle_net = float(settle_value) - cost
    return p


def first_barrier(path: EntryPath, tp_cents: float, sl_cents: float) -> dict:
    """Apply one TP/SL pair to a precomputed path. First touch wins; else hold to settlement."""
    tp, sl = float(tp_cents) / 100.0, float(sl_cents) / 100.0
    mfe, mae = (max(path.net) if path.net else path.settle_net,
                min(path.net) if path.net else path.settle_net)
    for i, net in enumerate(path.net):
        if net >= tp or net <= -sl:
            sub = path.net[:i + 1]
            return {"exit_kind": "TP" if net >= tp else "SL", "exit_seq": path.seqs[i],
                    "holding_s": path.ts[i], "net_per_share": round(net, 6),
                    "mfe_per_share": round(max(sub), 6), "mae_per_share": round(min(sub), 6)}
    return {"exit_kind": "SETTLE", "exit_seq": path.seqs[-1] if path.seqs else path.entry_seq,
            "holding_s": path.ts[-1] if path.ts else 0.0,
            "net_per_share": round(path.settle_net, 6),
            "mfe_per_share": round(max(mfe, path.settle_net), 6),
            "mae_per_share": round(min(mae, path.settle_net), 6)}


def first_profitable(path: EntryPath) -> tuple:
    """(seconds_to_first_profitable_exit, net_at_that_exit) or (None, None) => censored."""
    for t, net in zip(path.ts, path.net):
        if net > 0:
            return (t, round(net, 6))
    return (None, None)


# ---------------------------------------------------------------------------------------------
# Selftest -- validates the LOGIC offline, no DB needed
# ---------------------------------------------------------------------------------------------
def _mk(seq, t_s, bids, asks):
    return BookState(seq=seq, recv_ts_ns=int(t_s * NS_PER_S),
                     best_bid=bids[0][0] if bids else None,
                     best_ask=asks[0][0] if asks else None,
                     best_bid_size=bids[0][1] if bids else 0.0,
                     best_ask_size=asks[0][1] if asks else 0.0,
                     spread=(asks[0][0] - bids[0][0]) if (bids and asks) else None,
                     asks=asks, bids=bids)


def selftest() -> int:
    ok = True

    def check(name, cond, extra=""):
        nonlocal ok
        print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  ' + extra) if extra else ''}")
        ok = ok and cond

    print("executable_fill_engine selftest")

    # Every gate stated EXPLICITLY. The checks below exercise fill MECHANICS, so the gates are
    # opened deliberately - which is now the only way they can be opened. Previously these calls
    # passed no eligibility at all, so the suite validated the engine in a gates-disabled mode
    # that production never runs.
    OPEN = {"min_ask": 0.0, "max_ask": 1.0, "max_spread": 1.0, "min_top_ask_size": 0.0,
            "max_book_staleness_s": 1e9}

    # 1. ladder VWAP walks levels and reports partial fills honestly
    v, f = ladder_vwap([(0.54, 20), (0.55, 40), (0.56, 100)], 50)
    check("ladder VWAP walks multiple levels", abs(v - ((20*0.54 + 30*0.55) / 50)) < 1e-12,
          f"vwap={v:.6f} filled={f}")
    v2, f2 = ladder_vwap([(0.54, 5)], 50)
    check("partial fill reported, not invented", f2 == 5 and abs(v2 - 0.54) < 1e-12)

    # 2. the decision book can never fill the trade
    books = [_mk(1, 100.0, [(0.50, 100)], [(0.51, 100)]),
             _mk(2, 100.4, [(0.50, 100)], [(0.90, 100)]),   # +400ms: ask jumped
             _mk(3, 101.0, [(0.95, 100)], [(0.96, 100)])]
    r = simulate_trade(books, decision_ts_ns=int(100.0 * NS_PER_S), latency_ms=500, qty=1,
                       tp_cents=5, sl_cents=5, settle_value=1.0, eligibility=OPEN)
    check("entry uses first book AFTER latency (not the decision book)",
          r.eligible and r.entry_seq == 3, f"entry_seq={r.entry_seq}")

    # 3. exit is the FIRST barrier touch, not the best hindsight price.
    #    NOTE the fee bite: buying at 0.50 and selling at 0.56 is +6c GROSS but only +2.5c NET
    #    after both-leg taker fees, so a 3c TP would NOT trigger here. That is the whole reason
    #    this suite exists -- the barrier must be evaluated net, never gross.
    entry_fee = taker_fee(0.50)
    exit_fee = taker_fee(0.56)
    net_at_56 = 0.56 - exit_fee - 0.50 - entry_fee
    check("6c gross is only ~2.5c net after both-leg fees",
          0.024 < net_at_56 < 0.026, f"net={net_at_56:.5f}")

    books = [_mk(1, 0.0, [(0.50, 100)], [(0.50, 100)]),
             _mk(2, 1.0, [(0.56, 100)], [(0.57, 100)]),     # clears a 2c NET tp
             _mk(3, 2.0, [(0.99, 100)], [(0.99, 100)])]     # far better later: must NOT be used
    r = simulate_trade(books, decision_ts_ns=0, latency_ms=0, qty=1,
                       tp_cents=2, sl_cents=50, settle_value=1.0, eligibility=OPEN)
    check("exit takes FIRST barrier touch, no hindsight best",
          r.exit_kind == "TP" and r.exit_seq == 2, f"kind={r.exit_kind} seq={r.exit_seq}")

    # 3b. a 3c TP is NOT reachable at that same book -> engine must keep holding
    r3 = simulate_trade(books, decision_ts_ns=0, latency_ms=0, qty=1,
                        tp_cents=3, sl_cents=50, settle_value=1.0, eligibility=OPEN)
    check("net-based barrier: 3c TP skips the 6c-gross book", r3.exit_seq == 3,
          f"exit_seq={r3.exit_seq}")

    # 4. fees charged on both legs from the frozen formula (recorded 0.0 never used)
    check("both-leg taker fees applied", abs(r.net_per_share - net_at_56) < 1e-6,
          f"net={r.net_per_share:.6f} expected={net_at_56:.6f}")
    check("fee is non-zero at mid prices", entry_fee > 0.017, f"fee@0.50={entry_fee}")

    # 5. a losing round settles at 0 and loses the full cost
    books = [_mk(1, 0.0, [(0.49, 100)], [(0.50, 100)]),
             _mk(2, 1.0, [(0.49, 100)], [(0.50, 100)])]
    r = simulate_trade(books, decision_ts_ns=0, latency_ms=0, qty=1,
                       tp_cents=50, sl_cents=50, settle_value=0.0, eligibility=OPEN)
    check("hold-to-settle loser = -(entry + entry fee)",
          r.exit_kind == "SETTLE" and abs(r.net_per_share - (0.0 - 0.50 - taker_fee(0.50))) < 1e-9,
          f"net={r.net_per_share:.6f}")

    # 6. first-profitable-exit censoring when profit never appears
    check("censored when never profitable", r.first_profitable_s is None)

    # 7. eligibility vetoes fail closed.
    #    NOTE the {**OPEN, ...} form. This check used to pass eligibility={"max_ask": 0.97}
    #    alone, so while it asserted ONE veto it was silently relying on the other four gates
    #    defaulting open - testing the veto in a configuration production never runs.
    books = [_mk(1, 0.0, [(0.10, 100)], [(0.99, 0.2)])]
    r = simulate_trade(books, decision_ts_ns=0, latency_ms=0, qty=1, tp_cents=5, sl_cents=5,
                       settle_value=1.0, eligibility={**OPEN, "max_ask": 0.97})
    check("ask band veto fails closed", (not r.eligible) and r.reason == "ask_out_of_band",
          r.reason)

    # 7b. EVERY gate vetoes, and each one is reachable. A gate nobody exercises is a gate that
    #     can be silently deleted.
    wide = [_mk(1, 0.0, [(0.10, 100)], [(0.60, 100)])]      # spread 0.50
    r = simulate_trade(wide, decision_ts_ns=0, latency_ms=0, qty=1, tp_cents=5, sl_cents=5,
                      settle_value=1.0, eligibility={**OPEN, "max_spread": 0.05})
    check("spread veto fails closed", (not r.eligible) and r.reason == "spread_too_wide",
          r.reason)

    thin = [_mk(1, 0.0, [(0.49, 100)], [(0.50, 1.0)])]
    r = simulate_trade(thin, decision_ts_ns=0, latency_ms=0, qty=1, tp_cents=5, sl_cents=5,
                      settle_value=1.0, eligibility={**OPEN, "min_top_ask_size": 25.0})
    check("top-size veto fails closed",
          (not r.eligible) and r.reason == "insufficient_top_ask_size", r.reason)

    late = [_mk(1, 30.0, [(0.49, 100)], [(0.50, 100)])]     # book 30s after the decision
    r = simulate_trade(late, decision_ts_ns=0, latency_ms=0, qty=1, tp_cents=5, sl_cents=5,
                      settle_value=1.0, eligibility={**OPEN, "max_book_staleness_s": 2.0})
    check("staleness veto fails closed", (not r.eligible) and r.reason == "entry_book_stale",
          r.reason)

    # 7c. A MISSING gate is refused outright. Every default was permissive, so an absent or
    #     misspelled key used to delete that gate and report more fills at better prices.
    for omit in ELIGIBILITY_KEYS:
        partial = {k: v for k, v in OPEN.items() if k != omit}
        try:
            simulate_trade(books, 0, 0, 1, 5, 5, 1.0, eligibility=partial)
            check(f"missing '{omit}' is refused", False, "it was silently defaulted")
        except EligibilityIncomplete as exc:
            check(f"missing '{omit}' is refused", omit in str(exc))
    try:
        net_path(books, 0, 0, 1, 1.0, eligibility={"min_ask": 0.0})
        check("net_path enforces the same contract", False)
    except EligibilityIncomplete:
        check("net_path enforces the same contract", True)

    # 8. determinism
    books = [_mk(i, i * 0.5, [(0.50 + i * 0.01, 50)], [(0.51 + i * 0.01, 50)]) for i in range(20)]
    a = simulate_trade(books, 0, 100, 5, 3, 3, 1.0, eligibility=OPEN).as_dict()
    b = simulate_trade(books, 0, 100, 5, 3, 3, 1.0, eligibility=OPEN).as_dict()
    check("deterministic (identical inputs -> identical output)", a == b)

    # 9. the fast path form must AGREE with the reference implementation on every barrier pair.
    #    This is what licenses using net_path/first_barrier at grid scale.
    import random
    rng = random.Random(20260725)
    agree = mismatch = 0
    for trial in range(40):
        n = rng.randint(4, 25)
        bk, px = [], 0.50
        for i in range(n):
            px = min(0.97, max(0.03, px + rng.uniform(-0.06, 0.06)))
            sz = rng.choice([1.0, 5.0, 50.0, 200.0])
            bk.append(_mk(i, i * 0.25, [(round(px, 3), sz)], [(round(min(0.99, px + 0.01), 3), sz)]))
        sv = float(rng.choice([0.0, 1.0]))
        q = rng.choice([1, 5, 10])
        lat = rng.choice([0, 100, 500])
        pth = net_path(bk, 0, lat, q, sv, eligibility=OPEN)
        for tp in (1, 3, 5, 10):
            for sl in (1, 3, 5, 10):
                ref = simulate_trade(bk, 0, lat, q, tp, sl, sv, eligibility=OPEN)
                if not pth.eligible:
                    if not ref.eligible:
                        agree += 1
                    else:
                        mismatch += 1
                    continue
                fast = first_barrier(pth, tp, sl)
                same = (ref.exit_kind == fast["exit_kind"]
                        and abs(ref.net_per_share - fast["net_per_share"]) < 1e-9
                        and abs(ref.mfe_per_share - fast["mfe_per_share"]) < 1e-9
                        and abs(ref.mae_per_share - fast["mae_per_share"]) < 1e-9)
                agree += same
                mismatch += (not same)
    check("fast path == reference on 640 randomized barrier pairs",
          mismatch == 0, f"agree={agree} mismatch={mismatch}")

    print("executable-fill-engine:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        raise SystemExit(selftest())
    print("nothing to do; use --selftest (engine is a library)")
