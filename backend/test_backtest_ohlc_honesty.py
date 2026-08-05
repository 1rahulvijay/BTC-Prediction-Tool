"""The backtester's neutral band must come from the REAL intrabar range.

    python backend/test_backtest_ohlc_honesty.py

THE DEFECT
        highs = closes * 1.001   # "Approximate - real highs not available here"
        lows  = closes * 0.999

    Every bar was handed an identical 0.2% range. ATR therefore became a constant multiple of
    price, and `compute_adaptive_threshold_series` - which is ATR-derived - produced a neutral
    band that did not vary with volatility. That band decides which outcomes count as UP, DOWN
    or NEUTRAL, so every hit rate the backtester reported was graded against a barrier that
    corresponded to no real market condition: far too wide in quiet periods, far too narrow in
    violent ones.

    The comment was also wrong. The real extremes WERE available - both callers build `closes`
    from kline dicts carrying `high` and `low`, and simply never passed them.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from features import atr, compute_adaptive_threshold_series      # noqa: E402

_OK = True


def chk(cond: object, msg: str) -> None:
    global _OK
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    _OK = bool(_OK) and bool(cond)


def main() -> int:
    rng = np.random.default_rng(3)
    n = 600
    closes = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.001, n)))

    # A market with a genuinely CHANGING range: calm first half, violent second half.
    span = np.concatenate([np.full(n // 2, 0.0005), np.full(n - n // 2, 0.010)])
    real_high = closes * (1.0 + span)
    real_low = closes * (1.0 - span)

    fake_high = closes * 1.001
    fake_low = closes * 0.999

    print("the fabricated range is constant by construction")
    real_rng = (real_high - real_low) / closes
    fake_rng = (fake_high - fake_low) / closes
    chk(np.std(fake_rng) < 1e-12,
        f"the synthetic range has ZERO variation (std {np.std(fake_rng):.2e})")
    chk(np.std(real_rng) > 1e-4,
        f"while the real one varies (std {np.std(real_rng):.2e}) - calm vs violent halves")

    print("so the adaptive threshold stops tracking volatility")
    t_real = compute_adaptive_threshold_series(closes, atr(real_high, real_low, closes))
    t_fake = compute_adaptive_threshold_series(closes, atr(fake_high, fake_low, closes))
    calm, wild = slice(60, n // 2), slice(n // 2 + 60, n)
    real_ratio = float(np.median(t_real[wild]) / max(np.median(t_real[calm]), 1e-12))
    fake_ratio = float(np.median(t_fake[wild]) / max(np.median(t_fake[calm]), 1e-12))
    chk(real_ratio > 2.0,
        f"the real band widens {real_ratio:.1f}x from the calm half to the violent half")
    chk(abs(fake_ratio - 1.0) < 0.05,
        f"the fabricated band barely moves ({fake_ratio:.2f}x) - it cannot tell the two "
        f"regimes apart")

    print("and that changes which outcomes are graded as directional")
    # A move of this size is NEUTRAL under the real (wide) violent-half band but DIRECTIONAL
    # under the fabricated one, so the two graders disagree about the same market.
    # Chosen from the MEASURED bands (fabricated ~0.0008, real ~0.0029 in the violent half),
    # not guessed. A probe outside that gap proves nothing, which is how the first attempt at
    # this check passed both ways.
    band_real = float(np.median(t_real[wild]))
    band_fake = float(np.median(t_fake[wild]))
    chk(band_fake < band_real,
        f"the fabricated band ({band_fake:.4f}) is narrower than the real one ({band_real:.4f})")
    probe = (band_fake + band_real) / 2.0
    real_neutral = probe <= band_real
    fake_neutral = probe <= band_fake
    chk(real_neutral != fake_neutral,
        f"a {probe*100:.1f}% move in the violent half is "
        f"{'NEUTRAL' if real_neutral else 'DIRECTIONAL'} under the real band and "
        f"{'NEUTRAL' if fake_neutral else 'DIRECTIONAL'} under the fabricated one - the same "
        f"outcome, two different labels")

    print("the backtester refuses to hide a fabricated run")
    from backtester import Backtester

    src = (Path(__file__).resolve().parent / "backtester.py").read_text(encoding="utf-8")
    chk('self.results["ohlc_source"] = ohlc_source' in src,
        "the result carries WHICH extremes produced it")
    chk('self.results["valid_for_promotion"] = (ohlc_source == "REAL")' in src,
        "and a fabricated run is marked ineligible for promotion")
    chk("highs=None, lows=None" in src or "highs=None" in src,
        "run() accepts the real extremes")

    bt = Backtester()
    feats = np.zeros((200, 4), dtype=np.float32)
    out = bt.run(feats, closes[:200], [5], lambda *a, **k: (0.3, 0.4, 0.3), lookback=60,
                 highs=real_high[:200], lows=real_low[:200])
    chk(out.get("ohlc_source") == "REAL" and out.get("valid_for_promotion") is True,
        "supplying real extremes yields a promotion-eligible result")
    out2 = bt.run(feats, closes[:200], [5], lambda *a, **k: (0.3, 0.4, 0.3), lookback=60)
    chk(out2.get("ohlc_source") == "FABRICATED" and out2.get("valid_for_promotion") is False,
        "omitting them yields a result that says so, rather than one that looks identical")

    print("both production call sites supply them")
    server = (Path(__file__).resolve().parent / "server.py").read_text(encoding="utf-8")
    chk(server.count('highs=np.array([k["high"]') == 2,
        "both backtester.run call sites pass real highs")
    chk(server.count('lows=np.array([k["low"]') == 2,
        "and both pass real lows")
    chk("closes * 1.001" not in server,
        "the server never fabricates them itself")

    print("\nBACKTEST OHLC HONESTY:", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
