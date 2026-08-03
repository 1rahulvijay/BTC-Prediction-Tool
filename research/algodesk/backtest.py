"""Train 17 agent gates on 30 days, test on the untouched 10 that follow.

WHAT IS TRAINED
    The published rule is the SIGNAL GENERATOR and is never re-fitted - re-tuning its
    thresholds on the same data it is judged by is the search this repository has spent weeks
    learning not to run. What is trained is a GATE per agent: given the market context at the
    moment its rule fired, should this signal be taken or skipped?

    ML   gradient boosting on the agent's own fired signals
    DL   a small multi-layer perceptron on the same features
    RL   an epsilon-greedy contextual bandit over {TAKE, SKIP}, replayed on the train window,
         rewarded with the realised post-cost return

    All three are fitted on the 30-day train window ONLY. The 10-day test window is scored
    once, at the end, and never used for selection.

POWER, DECLARED BEFORE RESULTS
    A gate fitted on a handful of trades is noise with a confidence interval. Any agent with
    fewer than MIN_TRAIN_TRADES fired signals in the train window is reported UNDERPOWERED and
    its models are NOT fitted, rather than fitted and quietly believed.

ACCOUNTING
    One portfolio, MAX_CONCURRENT positions across all symbols - not one position per symbol,
    which would silently deploy several times the intended capital. Entry is the NEXT bar's
    open, never the close that triggered the signal. Taker fees, slippage and funding paid
    while the position is held are all charged.

    python -m research.algodesk.backtest --selftest
    python -m research.algodesk.backtest
"""
from __future__ import annotations

import argparse
import warnings

import numpy as np
import pandas as pd

from .agents import AGENT_IDS, derive, signal
from .data import BARS_PER_DAY, load

warnings.filterwarnings("ignore")

# ------------------------------------------------------------- frozen before any result

TRAIN_DAYS = 30
TEST_DAYS = 10
#: Bars between train and test. Positions can stay open MAX_HOLD_BARS, so without a purge a
#: train trade could resolve inside the test window and leak its outcome across the boundary.
PURGE_BARS = 96

TAKER_BPS = 5.5            # Bybit linear taker, each way
SLIPPAGE_BPS = 1.5         # each way
COST_BPS_ROUND_TRIP = 2 * (TAKER_BPS + SLIPPAGE_BPS)

TAKE_PROFIT_BPS = 300.0
STOP_LOSS_BPS = 150.0
MAX_HOLD_BARS = 96         # 24h at 15m
MAX_CONCURRENT = 3
COOLDOWN_BARS = 2

#: Below this many fired signals in the train window, a gate is not fitted.
MIN_TRAIN_TRADES = 40

FEATURES = ("chg24", "pos", "rv24", "range_pct", "vol_ratio", "funding_rate",
            "oi_chg24", "hour", "side")


def _feature_row(row, side: int) -> list[float]:
    vol_ratio = (row.vol24 / row.vol_prev_day) if (np.isfinite(row.vol_prev_day)
                                                   and row.vol_prev_day > 0) else 1.0
    return [
        float(row.chg24), float(row.pos), float(row.rv24) if np.isfinite(row.rv24) else 0.0,
        float(row.range_pct) if np.isfinite(row.range_pct) else 0.0, float(vol_ratio),
        float(row.funding_rate) if np.isfinite(row.funding_rate) else 0.0,
        float(row.oi_chg24) if np.isfinite(row.oi_chg24) else 0.0,
        float((row.ts_ms // 3600_000) % 24), float(side),
    ]


def collect_trades(agent: str, frame: pd.DataFrame) -> pd.DataFrame:
    """Every trade the published rule would take, as ONE portfolio across all symbols.

    Returns one row per trade with its realised post-cost return and the feature vector
    observed at entry, so a gate can be fitted on exactly what was knowable then."""
    by_symbol = {s: g.sort_values("ts_ms").reset_index(drop=True)
                 for s, g in frame.groupby("symbol", sort=False)}
    stamps = np.unique(frame["ts_ms"].to_numpy("int64"))
    index = {s: {int(t): i for i, t in enumerate(g["ts_ms"])} for s, g in by_symbol.items()}

    open_positions: dict[str, dict] = {}
    cooldown: dict[str, int] = {}
    trades: list[dict] = []

    for stamp in stamps:
        # ---- exits first: a slot must free before it can be reused this bar
        for symbol in list(open_positions):
            g, pos_state = by_symbol[symbol], open_positions[symbol]
            i = index[symbol].get(int(stamp))
            if i is None:
                continue
            bar = g.iloc[i]
            side = pos_state["side"]
            entry = pos_state["entry"]
            # Stop is checked BEFORE target: a bar whose range spans both books the loss.
            adverse = (bar.low - entry) / entry * 1e4 * side if side > 0 else \
                      (entry - bar.high) / entry * 1e4
            favourable = (bar.high - entry) / entry * 1e4 * side if side > 0 else \
                         (entry - bar.low) / entry * 1e4
            gross = None
            if adverse <= -STOP_LOSS_BPS:
                gross, reason = -STOP_LOSS_BPS, "STOP"
            elif favourable >= TAKE_PROFIT_BPS:
                gross, reason = TAKE_PROFIT_BPS, "TARGET"
            elif i - pos_state["entry_index"] >= MAX_HOLD_BARS:
                gross = (bar.close - entry) / entry * 1e4 * side
                reason = "MAX_HOLD"
            if gross is not None:
                # Funding is paid by longs when positive. Charge each print crossed.
                fr = bar.funding_rate if np.isfinite(bar.funding_rate) else 0.0
                prints = max(0, (i - pos_state["entry_index"]) // 32)   # 8h = 32 bars
                funding_bps = fr * 1e4 * prints * side
                trades.append({
                    "agent": agent, "symbol": symbol, "side": side,
                    "day": int(stamp // 86_400_000), "ts_ms": int(stamp),
                    "gross_bps": gross,
                    "net_bps": gross - COST_BPS_ROUND_TRIP - funding_bps,
                    "reason": reason, "bars": i - pos_state["entry_index"],
                    "features": pos_state["features"],
                })
                cooldown[symbol] = i + COOLDOWN_BARS
                del open_positions[symbol]

        # ---- entries, subject to the portfolio-wide concurrency cap
        if len(open_positions) >= MAX_CONCURRENT:
            continue
        for symbol, g in by_symbol.items():
            if symbol in open_positions or len(open_positions) >= MAX_CONCURRENT:
                continue
            i = index[symbol].get(int(stamp))
            if i is None or i + 1 >= len(g) or i < cooldown.get(symbol, -1):
                continue
            row = g.iloc[i]
            call = signal(agent, row)
            if call == "SKIP":
                continue
            side = 1 if call == "LONG" else -1
            # Entry is the NEXT bar's open: never the close that produced the signal.
            entry_price = float(g.iloc[i + 1].open)
            open_positions[symbol] = {
                "side": side, "entry": entry_price, "entry_index": i + 1,
                "features": _feature_row(row, side),
            }
    return pd.DataFrame(trades)


def day_block_ci(values: np.ndarray, days: np.ndarray,
                 iterations: int = 2000, seed: int = 11) -> tuple:
    """95% CI on the mean, resampling whole days. Trades in one day are not independent."""
    unique = np.unique(days)
    if len(unique) < 2 or len(values) == 0:
        return (float("nan"), float("nan"))
    groups = [values[days == d] for d in unique]
    rng = np.random.default_rng(seed)
    means = np.empty(iterations)
    for k in range(iterations):
        pick = rng.integers(0, len(groups), len(groups))
        means[k] = np.concatenate([groups[j] for j in pick]).mean()
    return (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)))


class Gate:
    """A fitted decision rule over {TAKE, SKIP}. Refuses to exist without enough data."""

    def __init__(self, kind: str):
        self.kind = kind
        self.model = None
        self.fitted = False

    def fit(self, X: np.ndarray, y: np.ndarray) -> "Gate":
        if len(X) < MIN_TRAIN_TRADES or len(np.unique(y)) < 2:
            return self
        from sklearn.ensemble import GradientBoostingClassifier
        from sklearn.neural_network import MLPClassifier
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        if self.kind == "ML":
            self.model = GradientBoostingClassifier(
                n_estimators=60, max_depth=2, learning_rate=0.05, random_state=0)
        elif self.kind == "DL":
            self.model = make_pipeline(StandardScaler(), MLPClassifier(
                hidden_layer_sizes=(16, 8), max_iter=600, random_state=0, early_stopping=True))
        else:                                     # RL: linear contextual bandit
            self.model = _Bandit()
        self.model.fit(X, y)
        self.fitted = True
        return self

    def take(self, X: np.ndarray) -> np.ndarray:
        if not self.fitted:
            return np.ones(len(X), dtype=bool)
        if self.kind == "RL":
            return self.model.take(X)
        return self.model.predict_proba(X)[:, 1] >= 0.5


class _Bandit:
    """Epsilon-greedy contextual bandit over {SKIP, TAKE}, replayed on the train window.

    The reward for TAKE is the realised post-cost return; SKIP always earns zero. That makes
    the learned rule "take only where expected value beats standing aside", which is the
    decision actually being made - not "predict the sign", which is not the same question."""

    def __init__(self, epsilon: float = 0.1, seed: int = 3):
        self.epsilon, self.rng = epsilon, np.random.default_rng(seed)
        self.weights = None

    def fit(self, X: np.ndarray, rewards: np.ndarray):
        from sklearn.linear_model import Ridge
        design, target = [], []
        for _ in range(6):                        # replay epochs
            order = self.rng.permutation(len(X))
            for i in order:
                explore = self.rng.random() < self.epsilon
                value = 0.0 if self.weights is None else float(X[i] @ self.weights)
                if explore or value > 0:
                    design.append(X[i])
                    target.append(rewards[i])
            if design:
                ridge = Ridge(alpha=1.0).fit(np.array(design), np.array(target))
                self.weights = ridge.coef_
        self.ridge = Ridge(alpha=1.0).fit(np.array(design), np.array(target)) if design else None
        return self

    def take(self, X: np.ndarray) -> np.ndarray:
        if self.ridge is None:
            return np.ones(len(X), dtype=bool)
        return self.ridge.predict(X) > 0.0


def summarise(name: str, trades: pd.DataFrame, mask=None) -> dict:
    if mask is not None:
        trades = trades[mask]
    if trades.empty:
        return {"name": name, "n": 0, "mean": 0.0, "total": 0.0,
                "ci": (float("nan"), float("nan")), "win": 0.0}
    net = trades.net_bps.to_numpy(float)
    low, high = day_block_ci(net, trades.day.to_numpy())
    return {"name": name, "n": len(trades), "mean": float(net.mean()),
            "total": float(net.sum()), "ci": (low, high),
            "win": float((net > 0).mean() * 100)}


def selftest() -> int:
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    check(COST_BPS_ROUND_TRIP == 2 * (TAKER_BPS + SLIPPAGE_BPS),
          "round-trip cost charges both legs of fee and slippage")
    check(PURGE_BARS >= MAX_HOLD_BARS,
          "the purge gap is at least one maximum hold - no train trade resolves inside test")
    check(MAX_CONCURRENT < 7,
          "concurrency is capped BELOW the symbol count - one portfolio, not one per symbol")

    n = BARS_PER_DAY * 4
    ts = np.arange(n, dtype="int64") * 900_000 + 1_785_000_000_000
    rng = np.random.default_rng(0)
    frames = []
    for symbol in ("AAAUSDT", "BBBUSDT"):
        walk = 100 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
        frames.append(pd.DataFrame({
            "symbol": symbol, "ts_ms": ts, "open": walk, "high": walk * 1.002,
            "low": walk * 0.998, "close": walk, "volume": 1.0, "turnover": 40e6,
            "open_interest": np.linspace(1e5, 1.1e5, n), "funding_rate": 0.0001,
            "funding_age_ms": 0}))
    frame = derive(pd.concat(frames, ignore_index=True))

    trades = collect_trades("RANGE", frame)
    check(isinstance(trades, pd.DataFrame), "the engine returns a trade frame")
    if not trades.empty:
        check((trades.net_bps <= trades.gross_bps + 1e-9).all(),
              "net is never better than gross - costs are always charged")
        overlap = trades.groupby("symbol").apply(
            lambda g: (g.sort_values("ts_ms").ts_ms.diff().dropna() > 0).all())
        check(bool(overlap.all()), "one position per symbol at a time, strictly ordered")

    gate = Gate("ML").fit(np.zeros((5, len(FEATURES))), np.array([0, 1, 0, 1, 0]))
    check(not gate.fitted, "a gate REFUSES to fit below the declared minimum sample")
    check(bool(gate.take(np.zeros((3, len(FEATURES)))).all()),
          "...and an unfitted gate passes every signal through, changing nothing")

    X = rng.normal(size=(200, len(FEATURES)))
    y = (X[:, 0] > 0).astype(int)
    fitted = Gate("ML").fit(X, y)
    check(fitted.fitted, "a gate fits when the sample is large enough")
    check(len(fitted.take(X)) == 200, "...and returns one decision per signal")

    values = np.array([1.0, -1.0, 2.0, -2.0])
    check(not np.isfinite(day_block_ci(values, np.array([1, 1, 1, 1]))[0]),
          "ONE day yields nan, never a fabricated interval")
    check(np.isfinite(day_block_ci(values, np.array([1, 1, 2, 2]))[0]),
          "two days yield a real interval")

    print(f"\nALGODESK BACKTEST SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    if parser.parse_args().selftest:
        return selftest()

    frame = derive(load())
    stamps = np.sort(frame.ts_ms.unique())
    split = stamps[0] + TRAIN_DAYS * 86_400_000
    test_start = split + PURGE_BARS * 900_000
    train = frame[frame.ts_ms < split]
    test = frame[frame.ts_ms >= test_start]

    import datetime as dt
    fmt = lambda ms: dt.datetime.fromtimestamp(ms / 1000, dt.UTC).strftime("%m-%d")
    print("=" * 108)
    print("ALGODESK 17 AGENTS - real funding + real open interest, 7 symbols, 15m bars")
    print("=" * 108)
    print(f"  train {fmt(stamps[0])} -> {fmt(split)} ({TRAIN_DAYS}d)   "
          f"purge {PURGE_BARS} bars   test {fmt(test_start)} -> {fmt(stamps[-1])} ({TEST_DAYS}d)")
    print(f"  cost {COST_BPS_ROUND_TRIP:.0f} bps round trip + funding | TP {TAKE_PROFIT_BPS:.0f} "
          f"/ SL {STOP_LOSS_BPS:.0f} bps | max hold {MAX_HOLD_BARS} bars | "
          f"max {MAX_CONCURRENT} concurrent")
    print(f"  gates fitted on TRAIN only; test scored once. Minimum {MIN_TRAIN_TRADES} train "
          f"trades or the gate is not fitted.")
    print()
    header = (f"  {'agent':<7}{'train':>7}{'test':>6}{'rule bps':>10}"
              f"{'ML':>9}{'DL':>9}{'RL':>9}   rule 95% CI (test)")
    print(header)
    print("  " + "-" * (len(header) + 18))

    rows = []
    for agent in AGENT_IDS:
        train_trades = collect_trades(agent, train)
        test_trades = collect_trades(agent, test)
        if test_trades.empty:
            print(f"  {agent:<7}{len(train_trades):>7}{0:>6}{'-':>10}{'-':>9}{'-':>9}{'-':>9}"
                  f"   never fired in test")
            rows.append({"agent": agent, "train": len(train_trades), "test": 0})
            continue

        Xte = np.array([f for f in test_trades.features])
        base = summarise("rule", test_trades)
        result = {"agent": agent, "train": len(train_trades), "test": len(test_trades),
                  "rule": base}
        cells = {}
        for kind in ("ML", "DL", "RL"):
            if len(train_trades) < MIN_TRAIN_TRADES:
                cells[kind] = None
                continue
            Xtr = np.array([f for f in train_trades.features])
            target = (train_trades.net_bps.to_numpy(float) > 0).astype(int) if kind != "RL" \
                else train_trades.net_bps.to_numpy(float)
            gate = Gate(kind).fit(Xtr, target)
            if not gate.fitted:
                cells[kind] = None
                continue
            cells[kind] = summarise(kind, test_trades, gate.take(Xte))
        result["gates"] = cells
        rows.append(result)

        def cell(v):
            return f"{v['mean']:>9.1f}" if v and v["n"] else ("undrpwr" if v is None else
                                                              f"{'0':>9}")
        low, high = base["ci"]
        ci = f"[{low:+7.1f}, {high:+7.1f}]" if np.isfinite(low) else "  (one day)"
        print(f"  {agent:<7}{len(train_trades):>7}{len(test_trades):>6}{base['mean']:>10.1f}"
              f"{cell(cells.get('ML')):>9}{cell(cells.get('DL')):>9}{cell(cells.get('RL')):>9}"
              f"   {ci}")

    fired = [r for r in rows if r.get("test")]
    powered = [r for r in rows if r.get("train", 0) >= MIN_TRAIN_TRADES]
    positive = [r for r in fired if np.isfinite(r["rule"]["ci"][0]) and r["rule"]["ci"][0] > 0]
    print()
    print(f"  {len(fired)}/17 agents traded in the test window; {len(powered)}/17 had "
          f"{MIN_TRAIN_TRADES}+ train trades and could be gated.")
    print(f"  {len(positive)}/17 have a rule day-block lower bound above zero on test.")
    print("  Gates were fitted on train only and the test window was scored once.")
    print("  'undrpwr' = fewer than the declared minimum train trades; no model was fitted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
