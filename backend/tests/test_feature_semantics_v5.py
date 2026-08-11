"""Pins the v5 feature-semantics fixes and the regime array alignment.

Three feature defects and one alignment defect, all found by external audit and all verified in
source before changing anything. Every one of them changes values, which is why
FEATURE_SEMANTICS_VERSION moved 4 -> 5: an unversioned semantic change is exactly the VWAP
v1->v2 skew that left 12 artifacts unverifiable.

    python backend/tests/test_feature_semantics_v5.py
"""
from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import features as F                                          # noqa: E402

CHECKS = 0


def check(cond, text):
    global CHECKS
    assert cond, text
    CHECKS += 1
    print(f"  PASS  {text}")


def old_lead_lag(eth: np.ndarray, btc: np.ndarray) -> float:
    """The retired implementation, reconstructed so the defect stays demonstrable."""
    eth_ret = eth[1:] - eth[:-1]
    ret_1m = btc[1:] - btc[:-1]
    return float((eth_ret[-1] - ret_1m[-1]) / (btc[-1] + 1e-9) * 1000)


def new_lead_lag(eth: np.ndarray, btc: np.ndarray) -> float:
    eth_log = np.zeros(len(eth))
    eth_log[1:] = np.log(eth[1:] / eth[:-1])
    btc_log = np.zeros(len(btc))
    btc_log[1:] = np.log(btc[1:] / btc[:-1])
    lagged = np.zeros(len(eth))
    lagged[1:] = eth_log[:-1]
    return float((lagged[-1] - btc_log[-1]) * 100.0)


def main() -> int:
    check(F.FEATURE_SEMANTICS_VERSION >= 5,
          f"feature semantics is at v{F.FEATURE_SEMANTICS_VERSION} - value changes are versioned")
    check(5 in F.FEATURE_SEMANTICS_CHANGELOG,
          "and v5 is described in the changelog, so a stale artifact can be explained")

    # ---- LVN: a low-volume NODE, not the emptiest slot -------------------------------
    # The window must SPAN a price region that never traded, or the defect is invisible.
    # Half the window sits at 100 with a trace of volume, half at 109 with heavy volume;
    # the bins between them are empty, and argmin() over ALL bins used to select one of those.
    closes = np.concatenate([np.full(60, 100.0), np.full(60, 200.0)])
    volumes = np.concatenate([np.full(60, 1.0), np.full(60, 50.0)])
    n_bins = 40
    _poc, lvn, _va = F.rolling_volume_profile(closes, volumes, window=120, n_bins=n_bins)
    last = len(closes) - 1
    lvn_price = closes[last] - lvn[last] * closes[last]
    bin_width = (200.0 - 100.0) / n_bins
    nearest_traded = min(abs(lvn_price - 100.0), abs(lvn_price - 200.0))
    check(nearest_traded <= bin_width,
          f"the LVN sits within one bin ({bin_width:.2f}) of a price that actually traded "
          f"(got {lvn_price:.2f}) - argmin() over ALL bins selected the first EMPTY bin "
          f"between the two clusters, which is neither a node nor a traded price")
    check(100.0 - bin_width <= lvn_price <= 200.0 + bin_width,
          "and it stays inside the observed price range")

    flat_closes = np.full(130, 100.0)
    flat_volumes = np.full(130, 10.0)
    _p, flat_lvn, _v = F.rolling_volume_profile(flat_closes, flat_volumes, 120, 40)
    check(np.all(np.isfinite(flat_lvn)),
          "a single-price window still produces a finite LVN rather than raising")

    # ---- funding phase: one cosine cannot encode a cycle ------------------------------
    # Exercised through the REAL feature builder. Comparing np.cos arithmetic inline proved
    # nothing about production - a mutation restoring the cosine survived that version.
    def funding_slot(fraction_into_cycle: float) -> float:
        cycle_s = 8 * 3600
        base = 1_700_000_000 - (1_700_000_000 % cycle_s)
        start_ms = int((base + fraction_into_cycle * cycle_s) * 1000)
        klines = [{"time": start_ms + i * 60_000, "open": 100.0, "high": 100.5,
                   "low": 99.5, "close": 100.0, "volume": 10.0,
                   "quote_volume": 1000.0, "trades": 10, "taker_buy_base": 5.0,
                   "is_closed": True} for i in range(260)]
        matrix = F.build_features_from_klines(klines)
        return float(matrix[-1, 104])

    quarter_value = funding_slot(0.25)
    three_quarter_value = funding_slot(0.75)
    check(abs(quarter_value - three_quarter_value) > 0.2,
          f"25% and 75% through the funding cycle now differ in the REAL feature matrix "
          f"({quarter_value:.3f} vs {three_quarter_value:.3f}) - a single cos() mapped both "
          f"to the same value, so just-settled and about-to-settle were indistinguishable")
    check(0.0 <= quarter_value <= 1.0 and 0.0 <= three_quarter_value <= 1.0,
          "and the slot stays in [0, 1], the fraction of the cycle remaining")

    # ---- lead-lag: same units, and actually lagged ------------------------------------
    # Through the REAL builder. The previous version asserted on a local reimplementation, so
    # a mutation removing the lag from production survived it.
    def leadlag_slot(eth_series):
        n = 260
        klines = [{"time": 1_700_000_000_000 + i * 60_000, "open": 100.0, "high": 100.5,
                   "low": 99.5, "close": 60_000.0, "volume": 10.0, "quote_volume": 1000.0,
                   "trades": 10, "taker_buy_base": 5.0, "is_closed": True} for i in range(n)]
        matrix = F.build_features_from_klines(
            klines, signal_history={"eth_price": np.asarray(eth_series, dtype=float)})
        return float(matrix[-1, 100])

    n = 260
    # ETH steps up on the SECOND-TO-LAST bar; BTC is flat throughout. Only a LAGGED reading
    # can see it on the final bar.
    eth_step = np.full(n, 3000.0)
    eth_step[-2:] = 3030.0
    fires = leadlag_slot(eth_step)
    check(abs(fires) > 1e-6,
          f"ETH stepping up one bar before the decision produces a NON-ZERO reading "
          f"({fires:+.5f}) on the final bar - only a lagged term can see that")

    eth_flat = np.full(n, 3000.0)
    check(abs(leadlag_slot(eth_flat)) < 1e-9,
          "a flat ETH series with flat BTC reads exactly zero, so the signal above is the "
          "ETH move and not a constant")

    # And the retired formula, reconstructed, has its usefulness inverted.
    follows = (np.array([3000.0, 3030.0, 3030.0]), np.array([60000.0, 60000.0, 60600.0]))
    leads_only = (np.array([3000.0, 3030.0, 3030.0]), np.array([60000.0, 60000.0, 60000.0]))
    check(abs(old_lead_lag(*leads_only)) < 1e-9,
          "the retired formula was SILENT when ETH led and BTC did not follow - it compared "
          "simultaneous bars and never used a lag")
    check(abs(old_lead_lag(*follows)) > 5.0,
          "...and fired hard when both moved together, the uninformative case - its "
          "usefulness was inverted")

    # ---- regime alignment: the arrays must be sliced together -------------------------
    server_source = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    check("adx_arr[:_n_closed], atr_arr[:_n_closed]" in server_source,
          "detect_regime receives adx/atr sliced to the SAME closed bars as closes/volumes - "
          "unsliced, closes[-1] was the newest CLOSED bar while adx_arr[-1] described the "
          "still-forming one")
    check("closes[:_n_closed]" in server_source,
          "...and closes is sliced by the same bound, so one variable governs all four")

    print(f"\nFEATURE SEMANTICS V5: PASS ({CHECKS} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
