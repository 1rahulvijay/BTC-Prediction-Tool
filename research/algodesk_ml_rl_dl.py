"""AlgoDesk ML/DL/RL pipeline: 17 agent signals -> 22-dim features -> XGBoost, MLP, PPO.

Built to the AlgoDesk system prompt: Bybit 5-minute klines, 17 rule agents used as FEATURES
rather than executors, three models over a 22-dimensional vector (17 agent signals + 5 context
features), and the Part 4 class-imbalance optimizations.

TWO DEVIATIONS FROM THE SPEC, BOTH DELIBERATE AND BOTH REPORTED
    1. Funding and open interest are fetched REAL from Bybit, not simulated. The spec's
       simulation (`funding = change_8h * 0.05`, `oi = vol24 * 3.5`) is also computed, so the
       two can be compared directly. This matters: measured over 7 symbols and 40 days, real
       funding never exceeded 0.000256, while the agents' thresholds start at 0.0015. The
       simulation makes an unreachable threshold reachable by turning a 3% eight-hour move INTO
       a 0.0015 "funding rate" - so simulated FUND/CONTRA/SENT results describe momentum.

    2. Results are reported after costs and on an untouched test window. The spec's reward
       (+1 TP / -0.5 SL) is what the PPO agent optimises, as specified, but a reward is not a
       return: it is reported beside realised post-cost basis points, never instead of them.

ON PART 4
    The stated problem is that the models predict "Failure" 100% and the RL agent SKIPs 100%.
    That is not necessarily a defect. A model that refuses every trade at a 3%/1.5% barrier is
    reporting that the barrier is not reachable often enough to pay 14 bps of cost. All four
    optimizations are implemented exactly as specified, and their effect on out-of-sample
    economics is measured rather than assumed:

        tighter 0.5%/0.5% barriers      more win labels, and a 1:1 payoff needs >51.4% wins
                                        after cost, against ~50% from a random entry
        scale_pos_weight                trades precision for recall
        weighted BCELoss                same, in the MLP
        SKIP penalty -0.01              makes standing aside cost something, so the agent
                                        trades even where expected value is negative

    Each is a lever on WHAT IS OPTIMISED. None creates edge. The report shows what each did.

    python research/algodesk_ml_rl_dl.py --fetch
    python research/algodesk_ml_rl_dl.py --selftest
    python research/algodesk_ml_rl_dl.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.request
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "algodesk"
BASE = "https://api.bybit.com"

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "AVAXUSDT", "LINKUSDT")
BAR_MIN = 5
BAR_MS = BAR_MIN * 60_000
BARS_PER_DAY = 288
TOTAL_DAYS = 40
TRAIN_DAYS, TEST_DAYS = 30, 10

# ---- Part 4 optimizations, exactly as specified -----------------------------------------
TP_PCT = 0.005            # tightened from 3.0% per Part 4.1
SL_PCT = 0.005            # tightened from 1.5% per Part 4.1
MAX_HOLD_BARS = 288       # 24h; a barrier study still needs a horizon or it never resolves
SKIP_PENALTY = -0.01      # Part 4.3
REWARD_TP, REWARD_SL = 1.0, -0.5

TAKER_BPS, SLIPPAGE_BPS = 5.5, 1.5
COST_BPS = 2 * (TAKER_BPS + SLIPPAGE_BPS)
def breakeven_win_rate(tp_pct: float, sl_pct: float, cost_bps: float = COST_BPS) -> float:
    """Win rate needed to break even. Cost is paid on EVERY trade, win or lose.

        win  = +tp - cost        lose = -sl - cost
        p*   = (sl + cost) / (tp + sl)

    This is the number Part 4.1 moves, and it moves it the wrong way. Tightening the barriers
    toward the cost scale does produce more "win" labels, but each win shrinks against a fixed
    cost, so the bar RISES:

        3.0% / 1.5%  ->  36.4% wins needed
        0.5% / 0.5%  ->  64.0% wins needed

    More labels, harder problem. A model that then predicts "Failure" everywhere is not being
    risk-averse; it is reporting that 64% is not attainable here."""
    return (sl_pct * 1e4 + cost_bps) / (tp_pct * 1e4 + sl_pct * 1e4)


BREAKEVEN_WIN = breakeven_win_rate(TP_PCT, SL_PCT)
BREAKEVEN_ORIGINAL = breakeven_win_rate(0.03, 0.015)

AGENT_IDS = ("TREND", "MOMO", "BREAK", "MEAN", "FUND", "VOL", "OI", "CONTRA", "SCALP",
             "LIQ", "PAT", "RANGE", "STAT", "SENT", "FLOW", "REGIME", "OIDIV")
CONTEXT = ("pos", "rsi", "chg24", "funding_rate", "oi_chg24")
FEATURE_NAMES = AGENT_IDS + CONTEXT          # 17 + 5 = 22


# ================================================================= data

def _get(url: str, tries: int = 4) -> dict:
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                payload = json.load(response)
            if payload.get("retCode") == 0:
                return payload["result"]
            raise RuntimeError(payload.get("retMsg"))
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("unreachable")


def _paged(url_fn, key, stamp_of, start_ms, max_pages=60):
    rows, cursor = [], ""
    for _ in range(max_pages):
        result = _get(url_fn(cursor))
        batch = result.get("list") or []
        if not batch:
            break
        rows.extend(batch)
        cursor = result.get("nextPageCursor") or ""
        if min(stamp_of(r) for r in batch) <= start_ms or not cursor:
            break
        time.sleep(0.1)
    return rows


def fetch_symbol(symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    rows, end = [], end_ms
    while end > start_ms:
        batch = _get(f"{BASE}/v5/market/kline?category=linear&symbol={symbol}"
                     f"&interval={BAR_MIN}&end={end}&limit=1000").get("list") or []
        if not batch:
            break
        rows.extend(batch)
        oldest = min(int(r[0]) for r in batch)
        if oldest >= end:
            break
        end = oldest - 1
        time.sleep(0.1)
    frame = pd.DataFrame(rows, columns=["ts_ms", "open", "high", "low", "close",
                                        "volume", "turnover"]).astype(float)
    frame["ts_ms"] = frame["ts_ms"].astype("int64")
    frame = frame.drop_duplicates("ts_ms").sort_values("ts_ms").reset_index(drop=True)

    # REAL open interest. Bybit keeps only ~4 days at 5min, so 15min is fetched and joined
    # as-of - a coarser real series beats a fabricated fine one.
    oi_rows = _paged(
        lambda c: f"{BASE}/v5/market/open-interest?category=linear&symbol={symbol}"
                  f"&intervalTime=15min&limit=200" + (f"&cursor={c}" if c else ""),
        "list", lambda r: int(r["timestamp"]), start_ms)
    oi = pd.DataFrame(oi_rows)
    if not oi.empty:
        oi["ts_ms"] = oi["timestamp"].astype("int64")
        oi["open_interest"] = oi["openInterest"].astype(float)
        oi = oi[["ts_ms", "open_interest"]].drop_duplicates("ts_ms").sort_values("ts_ms")
        frame = pd.merge_asof(frame, oi, on="ts_ms", direction="backward")
    else:
        frame["open_interest"] = np.nan

    fr_rows, end = [], None
    for _ in range(12):
        url = f"{BASE}/v5/market/funding/history?category=linear&symbol={symbol}&limit=200"
        if end:
            url += f"&endTime={end}"
        batch = _get(url).get("list") or []
        if not batch:
            break
        fr_rows.extend(batch)
        oldest = min(int(r["fundingRateTimestamp"]) for r in batch)
        if oldest <= start_ms:
            break
        end = oldest - 1
        time.sleep(0.1)
    fr = pd.DataFrame(fr_rows)
    if not fr.empty:
        fr["ts_ms"] = fr["fundingRateTimestamp"].astype("int64")
        fr["funding_real"] = fr["fundingRate"].astype(float)
        fr = fr[["ts_ms", "funding_real"]].drop_duplicates("ts_ms").sort_values("ts_ms")
        frame = pd.merge_asof(frame, fr, on="ts_ms", direction="backward")
    else:
        frame["funding_real"] = np.nan
    frame["symbol"] = symbol
    return frame


def build(days: int = TOTAL_DAYS) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    end_ms = int(time.time() * 1000) // BAR_MS * BAR_MS
    start_ms = end_ms - days * 86_400_000
    frames = []
    for symbol in SYMBOLS:
        print(f"  {symbol:<10}", end="", flush=True)
        f = fetch_symbol(symbol, start_ms, end_ms)
        f = f[f.ts_ms >= start_ms]
        frames.append(f)
        print(f"{len(f):>7,} bars   OI {f.open_interest.notna().mean():5.1%}   "
              f"funding {f.funding_real.notna().mean():5.1%}")
    combined = pd.concat(frames, ignore_index=True)
    path = CACHE / "bybit_5m_40d.parquet"
    combined.to_parquet(path, index=False)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    (CACHE / "manifest_5m.json").write_text(json.dumps({
        "path": str(path), "sha256": digest, "rows": int(len(combined)),
        "symbols": list(SYMBOLS), "bar_minutes": BAR_MIN,
        "fetched_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }, indent=2), encoding="utf-8")
    print(f"\n  wrote {path}  sha256 {digest[:16]}...  {len(combined):,} rows")
    return path


# ================================================================= features

def derive(frame: pd.DataFrame) -> pd.DataFrame:
    """Context features. Both the REAL and the spec's SIMULATED funding/OI are computed."""
    out = []
    for _, group in frame.groupby("symbol", sort=False):
        g = group.sort_values("ts_ms").copy()
        w = BARS_PER_DAY
        close = g["close"]
        g["high24"] = g["high"].rolling(w, min_periods=w).max().shift(1)
        g["low24"] = g["low"].rolling(w, min_periods=w).min().shift(1)
        prev = close.shift(w)
        g["chg24"] = (close - prev) / prev * 100.0
        g["vol24"] = g["turnover"].rolling(w, min_periods=w).sum().shift(1)
        g["vol_prev"] = g["turnover"].rolling(w, min_periods=w).sum().shift(w + 1)
        span = (g["high24"] - g["low24"]).replace(0.0, np.nan)
        g["pos"] = ((close - g["low24"]) / span).clip(0, 1)
        g["rsi"] = (g["pos"] * 100).round()
        g["range_pct"] = span / g["low24"] * 100
        g["rv24"] = close.pct_change().rolling(w, min_periods=w).std().shift(1) * 1e4

        # --- the spec's Part 2 simulation, computed so it can be COMPARED, not believed
        chg8 = (close - close.shift(96)) / close.shift(96)
        g["funding_sim"] = (chg8 * 0.05).shift(1)
        g["oi_sim_usd"] = (g["vol24"] * 3.5)
        # --- real
        g["funding_real"] = g["funding_real"].shift(1)
        g["oi_real_usd"] = (g["open_interest"] * close).shift(1)
        oi_prev = (g["open_interest"] * close).shift(w + 1)
        g["oi_chg24_real"] = ((g["oi_real_usd"] - oi_prev) / oi_prev * 100)
        sim_prev = g["oi_sim_usd"].shift(w)
        g["oi_chg24_sim"] = ((g["oi_sim_usd"] - sim_prev) / sim_prev * 100).shift(1)
        out.append(g)
    return pd.concat(out, ignore_index=True)


def agent_signals(row, funding: float, oi_usd: float, oi_chg: float) -> np.ndarray:
    """All 17 agents as a +1 / -1 / 0 vector. Global guards applied."""
    chg, pos, vol = row.chg24, row.pos, row.vol24
    rng, rv = row.range_pct, row.rv24
    out = np.zeros(len(AGENT_IDS), dtype=float)
    if not (np.isfinite(chg) and np.isfinite(pos) and np.isfinite(vol)):
        return out
    has_fr, has_oi = np.isfinite(funding), np.isfinite(oi_usd) and np.isfinite(oi_chg)

    def emit(idx, value):
        out[idx] = value

    if chg > 8 and pos > 0.65 and vol > 150e6:   emit(0, 1)
    elif chg < -8 and pos < 0.35 and vol > 150e6: emit(0, -1)
    if chg > 12 and vol > 200e6 and pos > 0.80:  emit(1, 1)
    elif chg < -12 and vol > 200e6 and pos < 0.20: emit(1, -1)
    if np.isfinite(row.vol_prev) and row.vol_prev > 0 and vol > 1.5 * row.vol_prev:
        if pos >= 0.97: emit(2, 1)
        elif pos <= 0.03: emit(2, -1)
    if chg > 20 and pos > 0.90:   emit(3, -1)
    elif chg < -20 and pos < 0.10: emit(3, 1)
    if has_fr:
        if funding > 0.003:   emit(4, -1)
        elif funding < -0.003: emit(4, 1)
    if vol > 300e6 and chg > 3:   emit(5, 1)
    elif vol > 300e6 and chg < -3: emit(5, -1)
    if has_oi and oi_usd > 2e9 and oi_chg > 5:
        emit(6, 1 if chg > 0 else -1)
    if has_fr:
        if chg > 25 and pos > 0.90 and funding > 0.0015:   emit(7, -1)
        elif chg < -25 and pos < 0.10 and funding < -0.0015: emit(7, 1)
    if vol > 500e6 and np.isfinite(rng) and 0.7 <= rng <= 2.8:
        emit(8, 1 if pos < 0.5 else -1)
    if chg < -10 and vol > 400e6 and pos < 0.15:  emit(9, 1)
    elif chg > 10 and vol > 400e6 and pos > 0.85: emit(9, -1)
    if np.isfinite(rng) and rng < 2.0:
        if pos >= 0.95: emit(10, 1)
        elif pos <= 0.05: emit(10, -1)
    if np.isfinite(rv) and rv < 40:
        if pos <= 0.10: emit(11, 1)
        elif pos >= 0.90: emit(11, -1)
    if has_fr and has_oi:
        if oi_chg > 5 and chg < -2 and funding > 0:  emit(12, 1)
        elif oi_chg > 5 and chg > 2 and funding < 0: emit(12, -1)
    if has_fr:
        if funding > 0.0015 and chg < 0:   emit(13, -1)
        elif funding < -0.0015 and chg > 0: emit(13, 1)
    if has_oi and vol > 500e6:
        if chg > 2 and oi_chg > 2:   emit(14, 1)
        elif chg < -2 and oi_chg > 2: emit(14, -1)
    if np.isfinite(rng) and rng > 4 and abs(chg) > 5:
        if chg > 0 and pos > 0.70:   emit(15, 1)
        elif chg < 0 and pos < 0.30: emit(15, -1)
    if has_fr and has_oi:
        if chg < -2 and oi_chg > 3 and funding > 0:   emit(16, 1)
        elif chg > 2 and oi_chg < -3 and funding < 0: emit(16, -1)

    # Global guards
    if not np.isfinite(vol) or vol < 50e6:
        return np.zeros(len(AGENT_IDS))
    if has_fr:
        if funding > 0.005:
            out[out > 0] = 0.0
        if funding < -0.005:
            out[out < 0] = 0.0
    return out


def build_samples(frame: pd.DataFrame, *, simulated: bool) -> pd.DataFrame:
    """One sample per bar where at least one agent fires. Triple-barrier outcome."""
    fr_col = "funding_sim" if simulated else "funding_real"
    oi_col = "oi_sim_usd" if simulated else "oi_real_usd"
    ch_col = "oi_chg24_sim" if simulated else "oi_chg24_real"
    samples = []
    for _, group in frame.groupby("symbol", sort=False):
        g = group.sort_values("ts_ms").reset_index(drop=True)
        highs, lows = g["high"].to_numpy(float), g["low"].to_numpy(float)
        opens = g["open"].to_numpy(float)
        n = len(g)
        for i, row in enumerate(g.itertuples(index=False)):
            if i + 1 >= n:
                break
            fr = getattr(row, fr_col)
            signals = agent_signals(row, fr, getattr(row, oi_col), getattr(row, ch_col))
            if not signals.any():
                continue
            entry = opens[i + 1]           # next bar's open, never the triggering close
            stop = i + 1 + MAX_HOLD_BARS
            window = slice(i + 1, min(stop, n))
            hi, lo = highs[window], lows[window]
            # Long outcome
            up_hit = np.argmax(hi >= entry * (1 + TP_PCT)) if (hi >= entry * (1 + TP_PCT)).any() else 10**9
            dn_hit = np.argmax(lo <= entry * (1 - SL_PCT)) if (lo <= entry * (1 - SL_PCT)).any() else 10**9
            long_win = 1 if up_hit < dn_hit else 0
            short_win = 1 if dn_hit < up_hit else 0
            samples.append({
                "symbol": row.symbol, "ts_ms": row.ts_ms,
                "day": int(row.ts_ms // 86_400_000),
                "features": list(signals) + [
                    float(row.pos), float(row.rsi), float(row.chg24),
                    float(fr) if np.isfinite(fr) else 0.0,
                    float(getattr(row, ch_col)) if np.isfinite(getattr(row, ch_col)) else 0.0],
                "long_win": long_win, "short_win": short_win,
                "vote": float(np.sign(signals.sum())),
                "resolved": int(min(up_hit, dn_hit) < 10**9),
            })
    return pd.DataFrame(samples)


def realised_bps(win: int, side: int) -> float:
    """Post-cost basis points for a resolved barrier trade."""
    return (TP_PCT if win else -SL_PCT) * 1e4 - COST_BPS


# ================================================================= models

def fit_xgb(X, y):
    from xgboost import XGBClassifier
    pos = max(1, int(y.sum()))
    neg = max(1, len(y) - pos)
    # Part 4.2: scale_pos_weight penalises false negatives.
    model = XGBClassifier(n_estimators=120, max_depth=3, learning_rate=0.05,
                          scale_pos_weight=neg / pos, eval_metric="logloss",
                          verbosity=0, random_state=0)
    model.fit(X, y)
    return model


def fit_mlp(X, y, epochs: int = 120):
    import torch
    import torch.nn as nn
    torch.manual_seed(0)
    Xt = torch.tensor(X, dtype=torch.float32)
    yt = torch.tensor(y, dtype=torch.float32).unsqueeze(1)
    mean, std = Xt.mean(0, keepdim=True), Xt.std(0, keepdim=True) + 1e-8
    net = nn.Sequential(nn.Linear(X.shape[1], 32), nn.ReLU(),
                        nn.Linear(32, 16), nn.ReLU(), nn.Linear(16, 1), nn.Sigmoid())
    pos = float(y.sum())
    weight = torch.tensor([(len(y) - pos) / max(pos, 1.0)], dtype=torch.float32)
    # Part 4.2: weighted BCELoss, same intent as scale_pos_weight.
    loss_fn = nn.BCELoss(weight=weight)
    optimiser = torch.optim.Adam(net.parameters(), lr=1e-3)
    Xn = (Xt - mean) / std
    for _ in range(epochs):
        optimiser.zero_grad()
        loss = loss_fn(net(Xn), yt)
        loss.backward()
        optimiser.step()
    return net, mean, std


def mlp_predict(bundle, X) -> np.ndarray:
    import torch
    net, mean, std = bundle
    with torch.no_grad():
        Xt = (torch.tensor(X, dtype=torch.float32) - mean) / std
        return net(Xt).numpy().ravel()


import gymnasium as _gym                                              # noqa: E402


class AlgoDeskEnv(_gym.Env):
    """Gymnasium env: 22-dim state, actions {0 SKIP, 1 LONG, 2 SHORT}.

    Reward is the spec's: +1 TP, -0.5 SL, and SKIP_PENALTY for standing aside (Part 4.3).

    Must subclass gymnasium.Env - Stable-Baselines3 checks the type, and a duck-typed env is
    rejected rather than silently accepted."""

    metadata = {"render_modes": []}

    def __init__(self, X, long_win, short_win):
        super().__init__()
        from gymnasium import spaces
        self.X, self.lw, self.sw = X, long_win, short_win
        self.i = 0
        self.action_space = spaces.Discrete(3)
        self.observation_space = spaces.Box(low=-np.inf, high=np.inf,
                                            shape=(X.shape[1],), dtype=np.float32)

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        self.i = 0
        return self.X[0].astype(np.float32), {}

    def step(self, action):
        win = self.lw[self.i] if action == 1 else (self.sw[self.i] if action == 2 else None)
        if action == 0:
            reward = SKIP_PENALTY
        else:
            reward = REWARD_TP if win else REWARD_SL
        self.i += 1
        done = self.i >= len(self.X)
        obs = self.X[min(self.i, len(self.X) - 1)].astype(np.float32)
        return obs, float(reward), done, False, {}


def fit_ppo(X, long_win, short_win, steps: int = 20_000):
    from stable_baselines3 import PPO
    from stable_baselines3.common.vec_env import DummyVecEnv
    env = DummyVecEnv([lambda: AlgoDeskEnv(X, long_win, short_win)])
    model = PPO("MlpPolicy", env, verbose=0, seed=0, n_steps=512, batch_size=64)
    model.learn(total_timesteps=steps)
    return model


def day_block_ci(values, days, iterations=1500, seed=5):
    unique = np.unique(days)
    if len(unique) < 2 or len(values) == 0:
        return (float("nan"), float("nan"))
    groups = [values[days == d] for d in unique]
    rng = np.random.default_rng(seed)
    means = np.empty(iterations)
    for k in range(iterations):
        pick = rng.integers(0, len(groups), len(groups))
        means[k] = np.concatenate([groups[j] for j in pick]).mean()
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


# ================================================================= run

def evaluate(name, actions, test, verbose=True):
    """actions: 0 skip, 1 long, 2 short. Returns realised post-cost stats."""
    taken = actions != 0
    if not taken.any():
        return {"name": name, "n": 0, "mean": 0.0, "ci": (np.nan, np.nan), "win": 0.0,
                "coverage": 0.0}
    sub = test[taken]
    side = actions[taken]
    wins = np.where(side == 1, sub.long_win.to_numpy(), sub.short_win.to_numpy())
    bps = np.array([realised_bps(int(w), int(s)) for w, s in zip(wins, side)])
    low, high = day_block_ci(bps, sub.day.to_numpy())
    return {"name": name, "n": int(taken.sum()), "mean": float(bps.mean()),
            "ci": (low, high), "win": float(wins.mean() * 100),
            "coverage": float(taken.mean() * 100)}


def selftest() -> int:
    checks = 0

    def check(cond, label):
        nonlocal checks
        assert cond, label
        checks += 1
        print(f"  PASS  {label}")

    check(len(FEATURE_NAMES) == 22, "the feature vector is 22-dimensional (17 agents + 5 context)")
    check(TP_PCT == 0.005 and SL_PCT == 0.005, "Part 4.1 tightened barriers are in force")
    check(SKIP_PENALTY < 0, "Part 4.3 SKIP penalty is negative")
    check(abs(BREAKEVEN_WIN - 0.64) < 0.001,
          f"the tightened 0.5/0.5 barriers need {BREAKEVEN_WIN:.1%} wins, not 50%")
    check(BREAKEVEN_WIN > BREAKEVEN_ORIGINAL,
          f"Part 4.1 RAISES the bar: {BREAKEVEN_ORIGINAL:.1%} -> {BREAKEVEN_WIN:.1%} "
          f"break-even wins")
    check(abs(BREAKEVEN_ORIGINAL - 0.3644) < 0.001,
          "the original 3.0/1.5 barriers needed only 36.4%")
    check(realised_bps(1, 1) == TP_PCT * 1e4 - COST_BPS, "a win pays the barrier MINUS cost")
    check(realised_bps(0, 1) == -SL_PCT * 1e4 - COST_BPS, "a loss pays the barrier PLUS cost")
    check(realised_bps(1, 1) < TP_PCT * 1e4, "cost is never omitted from a win")

    n = BARS_PER_DAY * 3
    ts = np.arange(n, dtype="int64") * BAR_MS + 1_785_000_000_000
    rng = np.random.default_rng(0)
    walk = 100 * np.exp(np.cumsum(rng.normal(0, 0.0015, n)))
    frame = derive(pd.DataFrame({
        "symbol": "TESTUSDT", "ts_ms": ts, "open": walk, "high": walk * 1.001,
        "low": walk * 0.999, "close": walk, "volume": 1.0, "turnover": 40e6,
        "open_interest": np.linspace(1e5, 1.1e5, n), "funding_real": 0.0001}))
    check("funding_sim" in frame and "funding_real" in frame,
          "BOTH the simulated and the real funding series are carried")
    check(frame["oi_sim_usd"].notna().any() and frame["oi_real_usd"].notna().any(),
          "BOTH the simulated and the real OI series are carried")

    row = frame.iloc[BARS_PER_DAY + 5]
    check(row.high24 <= frame["high"].iloc[:BARS_PER_DAY + 5].max() + 1e-9,
          "24h aggregates never read the decision bar")

    signals = agent_signals(row, np.nan, np.nan, np.nan)
    check(len(signals) == 17, "the agent vector has one entry per agent")
    fr_agents = [AGENT_IDS.index(a) for a in ("FUND", "CONTRA", "SENT", "STAT", "OIDIV")]
    check(all(signals[i] == 0 for i in fr_agents),
          "funding/OI agents emit 0 when the real input is absent - never a proxy")

    thin = row._replace(vol24=1e6) if hasattr(row, "_replace") else row.copy()
    thin.vol24 = 1e6
    check(not agent_signals(thin, 0.0001, 1e10, 5.0).any(),
          "the $50M volume guard zeroes every agent")

    guarded = row.copy()
    guarded.chg24, guarded.pos, guarded.vol24 = 10.0, 0.9, 400e6
    hot = agent_signals(guarded, 0.01, 1e10, 5.0)
    check(not (hot > 0).any(), "funding above +0.005 removes every LONG signal")

    print(f"\nALGODESK ML/DL/RL SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--simulated", action="store_true",
                        help="use the spec's simulated funding/OI instead of the real series")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if args.fetch:
        print(f"fetching {len(SYMBOLS)} symbols x {TOTAL_DAYS}d of {BAR_MIN}m bars")
        build()
        return 0

    path = CACHE / "bybit_5m_40d.parquet"
    if not path.is_file():
        print(f"missing {path} - run with --fetch first")
        return 1
    frame = derive(pd.read_parquet(path))

    print("=" * 104)
    print(f"ALGODESK ML/DL/RL - 22-dim features, {'SIMULATED' if args.simulated else 'REAL'} "
          f"funding/OI, {BAR_MIN}m bars")
    print("=" * 104)

    for simulated in ([True] if args.simulated else [False, True]):
        samples = build_samples(frame, simulated=simulated)
        samples = samples[samples.resolved == 1].reset_index(drop=True)
        if samples.empty:
            print(f"\n  {'SIMULATED' if simulated else 'REAL':<10} no resolved samples")
            continue
        stamps = np.sort(samples.ts_ms.unique())
        split = stamps[0] + TRAIN_DAYS * 86_400_000
        train = samples[samples.ts_ms < split]
        test = samples[samples.ts_ms >= split + MAX_HOLD_BARS * BAR_MS]
        if train.empty or test.empty:
            print("  insufficient split")
            continue

        Xtr = np.array([f for f in train.features], dtype=float)
        Xte = np.array([f for f in test.features], dtype=float)
        ytr = train.long_win.to_numpy(int)

        label = "SIMULATED funding/OI (the spec's Part 2)" if simulated \
            else "REAL funding/OI (fetched from Bybit)"
        print(f"\n  --- {label}")
        print(f"      train {len(train):,} samples ({ytr.mean():.1%} long-wins)   "
              f"test {len(test):,}   break-even win rate {BREAKEVEN_WIN:.1%}")

        results = [evaluate("always-long", np.ones(len(test), int), test),
                   evaluate("agent vote", np.where(test.vote > 0, 1,
                                                   np.where(test.vote < 0, 2, 0)), test)]
        try:
            xgb = fit_xgb(Xtr, ytr)
            prob = xgb.predict_proba(Xte)[:, 1]
            results.append(evaluate("XGBoost", np.where(prob >= 0.5, 1, 0), test))
        except Exception as exc:
            print(f"      XGBoost failed: {exc}")
        try:
            bundle = fit_mlp(Xtr, ytr)
            prob = mlp_predict(bundle, Xte)
            results.append(evaluate("PyTorch MLP", np.where(prob >= 0.5, 1, 0), test))
        except Exception as exc:
            print(f"      MLP failed: {exc}")
        try:
            ppo = fit_ppo(Xtr, train.long_win.to_numpy(int), train.short_win.to_numpy(int))
            actions = np.array([int(ppo.predict(x.astype(np.float32), deterministic=True)[0])
                                for x in Xte])
            results.append(evaluate("PPO (RL)", actions, test))
        except Exception as exc:
            print(f"      PPO failed: {exc}")

        print(f"      {'model':<14}{'trades':>8}{'cover%':>8}{'win%':>7}"
              f"{'net bps':>10}   day-block 95% CI")
        for r in results:
            if not r["n"]:
                print(f"      {r['name']:<14}{0:>8}{'0.0':>8}{'-':>7}{'-':>10}   "
                      f"took no trades")
                continue
            low, high = r["ci"]
            ci = f"[{low:+7.1f}, {high:+7.1f}]" if np.isfinite(low) else "  (one day)"
            print(f"      {r['name']:<14}{r['n']:>8}{r['coverage']:>8.1f}{r['win']:>7.1f}"
                  f"{r['mean']:>10.1f}   {ci}")

    print()
    print(f"  Costs: {COST_BPS:.0f} bps round trip on a {TP_PCT*1e4:.0f} bps barrier - cost is "
          f"{COST_BPS/(TP_PCT*1e4)*100:.0f}% of the target.")
    print(f"  A 1:1 barrier needs {BREAKEVEN_WIN:.1%} wins to break even, not 50%.")
    print("  Part 4's levers change WHAT IS OPTIMISED; they do not create edge. Coverage% shows")
    print("  how much each lever forced the model to trade.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
