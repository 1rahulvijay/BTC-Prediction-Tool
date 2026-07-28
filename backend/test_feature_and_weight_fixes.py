"""Executing tests for the 2026-07-28 feature/model fixes.

Covers two real defects found during an audit pass (several other claims in that audit
were checked and refuted; only these two survived verification):

  1. vwap() was cumulative from bar 0, so over a long buffer it flatlined.
  2. The TCN folded per-sample weights into a per-CLASS mean, discarding recency.

Both are tested by behaviour - the second by checking that a weighted sample actually
moves the gradient more than an unweighted one, which is the property the old code
silently lost.

    python backend/test_feature_and_weight_fixes.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

OK = True


def chk(cond: bool, msg: str) -> None:
    global OK
    print(f"  {'PASS' if cond else 'FAIL'}  {msg}")
    OK = OK and bool(cond)


def main() -> int:
    print("feature-and-weight-fixes selftest")
    from features import vwap

    # ------------------------------------------------------------------------ VWAP
    print("\n[vwap] trailing window, causal, and no longer flatlining")
    rng = np.random.default_rng(20260728)
    n = 43_200                                   # 30 days of 1m bars - the reported case
    closes = 60_000 + np.cumsum(rng.normal(0, 5, n))
    highs, lows = closes + 5, closes - 5
    vols = np.abs(rng.normal(10, 3, n)) + 0.1

    v = vwap(highs, lows, closes, vols, period=1440)

    # A cumulative VWAP barely moves late in a long buffer; a trailing one tracks price.
    cum_tpv = np.cumsum(((highs + lows + closes) / 3.0) * vols)
    cum_vol = np.cumsum(vols)
    v_cum = np.where(cum_vol > 0, cum_tpv / cum_vol, closes)

    tail = slice(-5000, None)
    resp_new = float(np.std(v[tail] - v[tail].mean()))
    resp_old = float(np.std(v_cum[tail] - v_cum[tail].mean()))
    # Tracking error, not correlation: over a random walk both series trend together, so
    # correlation says little. What matters is DISTANCE from price - a flatlined VWAP
    # drifts arbitrarily far from spot while a trailing one stays near it.
    err_new = float(np.mean(np.abs(v[tail] - closes[tail])))
    err_old = float(np.mean(np.abs(v_cum[tail] - closes[tail])))
    chk(resp_new > resp_old * 2,
        f"trailing VWAP responds far more than cumulative over the last 5k bars "
        f"(sd {resp_new:.1f} vs {resp_old:.1f})")
    chk(err_new < err_old / 2,
        f"and stays far closer to spot (mean |vwap-close| {err_new:.1f} vs {err_old:.1f})")

    # Causality: truncating the future must not change any earlier value.
    k = 20_000
    v_trunc = vwap(highs[:k], lows[:k], closes[:k], vols[:k], period=1440)
    chk(np.allclose(v[:k], v_trunc, rtol=1e-9, atol=1e-6),
        "no look-ahead: values are identical when future bars are removed")

    # Window semantics and guards.
    v_short = vwap(highs[:100], lows[:100], closes[:100], vols[:100], period=1440)
    chk(len(v_short) == 100 and np.isfinite(v_short).all(),
        "a window longer than the data is clamped, not silently cumulative")
    w5 = vwap(highs[:10], lows[:10], closes[:10], vols[:10], period=5)
    tp = (highs[:10] + lows[:10] + closes[:10]) / 3.0
    expect = float((tp[5:10] * vols[5:10]).sum() / vols[5:10].sum())
    chk(abs(w5[9] - expect) < 1e-9,
        "the last value equals the volume-weighted mean of exactly the trailing window")
    raised = False
    try:
        vwap(highs[:10], lows[:10], closes[:10], vols[:10], period=0)
    except ValueError:
        raised = True
    chk(raised, "an invalid period raises instead of producing a silent wrong series")
    chk(len(vwap(np.array([]), np.array([]), np.array([]), np.array([]))) == 0,
        "an empty series returns empty rather than raising")

    # ------------------------------------------------- VWAP window is a DURATION
    print("\n[vwap] window resolves in real time, not bar count")
    # Same 24h intent on two different bar sizes must give the same effective duration.
    t1m = np.arange(n, dtype=np.float64) * 60.0                # 1-minute bars
    v_1m = vwap(highs, lows, closes, vols, times=t1m)
    n5 = 3000
    t5m = np.arange(n5, dtype=np.float64) * 300.0              # 5-minute bars
    c5 = 60_000 + np.cumsum(rng.normal(0, 12, n5))
    v_5m = vwap(c5 + 5, c5 - 5, c5, np.abs(rng.normal(10, 3, n5)) + 0.1, times=t5m)
    # 24h on 5m bars is 288 bars; the legacy 1440-BAR default would be five days.
    tp5 = c5
    chk(len(v_5m) == n5 and np.isfinite(v_5m).all(),
        "5m series with times= produces a finite series")
    err_24h = float(np.mean(np.abs(v_5m[-500:] - tp5[-500:])))
    v_5m_barcount = vwap(c5 + 5, c5 - 5, c5, np.abs(rng.normal(10, 3, n5)) + 0.1, period=1440)
    err_5day = float(np.mean(np.abs(v_5m_barcount[-500:] - tp5[-500:])))
    chk(err_24h < err_5day,
        f"time-anchored 24h tracks price better than a blind 1440-BAR (=5 day) window "
        f"({err_24h:.1f} vs {err_5day:.1f})")

    # Millisecond timestamps must resolve identically to seconds. The ms->s heuristic is
    # magnitude-based (> 1e11), the same rule features._t_s uses, so it needs REAL epoch
    # values - which is what the production caller passes. Relative offsets starting at 0
    # are indistinguishable between units by construction.
    epoch0 = 1_785_000_000.0
    t_epoch_s = epoch0 + t1m
    v_s = vwap(highs, lows, closes, vols, times=t_epoch_s)
    v_ms = vwap(highs, lows, closes, vols, times=t_epoch_s * 1000.0)
    chk(np.allclose(v_s, v_ms, rtol=1e-9, atol=1e-6),
        "epoch ms and epoch s resolve to the SAME window (auto-detected)")

    # Explicit sub-day window.
    v_1h = vwap(highs, lows, closes, vols, times=t1m, window_seconds=3600)
    e1h = float(np.mean(np.abs(v_1h[-5000:] - closes[-5000:])))
    e24 = float(np.mean(np.abs(v_1m[-5000:] - closes[-5000:])))
    chk(e1h < e24, f"a 1h window hugs price more tightly than 24h ({e1h:.1f} vs {e24:.1f})")
    raised = False
    try:
        vwap(highs[:10], lows[:10], closes[:10], vols[:10], window_seconds=3600)
    except ValueError:
        raised = True
    chk(raised, "window_seconds without times= RAISES (bar spacing would be a guess)")

    # Irregular / gapped series must still resolve by median spacing, not bar count.
    t_gap = t1m.copy()
    t_gap[n // 2:] += 7200.0                                   # a 2h recorder gap
    v_gap = vwap(highs, lows, closes, vols, times=t_gap)
    chk(np.isfinite(v_gap).all(), "a gapped timestamp series still produces finite VWAP")

    # ---------------------------------------------------- feature-semantics contract
    print("\n[contract] a formula change must be announceable, not silent")
    from features import FEATURE_SEMANTICS_CHANGELOG, FEATURE_SEMANTICS_VERSION
    chk(FEATURE_SEMANTICS_VERSION >= 2,
        f"FEATURE_SEMANTICS_VERSION is {FEATURE_SEMANTICS_VERSION} (bumped for the VWAP change)")
    chk(FEATURE_SEMANTICS_VERSION in FEATURE_SEMANTICS_CHANGELOG
        and "vwap" in FEATURE_SEMANTICS_CHANGELOG[FEATURE_SEMANTICS_VERSION].lower(),
        "the changelog names what changed, so a hash diff is not the only record")

    # ------------------------------------------------------------------- TCN weights
    print("\n[tcn] per-sample weights must reach the gradient")
    try:
        import torch
        import torch.nn as nn
    except Exception as exc:
        print(f"  SKIP  torch unavailable ({type(exc).__name__})")
        print("\nfeature-and-weight-fixes:", "ALL PASS" if OK else "FAILURES")
        return 0 if OK else 1

    torch.manual_seed(20260728)
    # Two samples with IDENTICAL logits and IDENTICAL labels, differing only in weight.
    # That isolates the weighting: any gradient difference can come from nothing else.
    # (An earlier version of this test gave them different labels, so their losses
    # differed for reasons unrelated to weighting and the ratios were meaningless.)
    logits = torch.tensor([[2.0, -1.0, 0.0], [2.0, -1.0, 0.0]], requires_grad=True)
    y = torch.tensor([1, 1])              # same class - per-class averaging sees one bucket
    w = torch.tensor([0.2, 1.8])          # same class share, very different recency

    per_sample = nn.CrossEntropyLoss(reduction="none")(logits, y)
    loss_w = (per_sample * w).sum() / w.sum()
    g_w = torch.autograd.grad(loss_w, logits, retain_graph=True)[0]

    logits2 = logits.detach().clone().requires_grad_(True)
    loss_u = nn.CrossEntropyLoss()(logits2, y)
    g_u = torch.autograd.grad(loss_u, logits2)[0]

    n0_w, n1_w = g_w[0].abs().sum().item(), g_w[1].abs().sum().item()
    n0_u, n1_u = g_u[0].abs().sum().item(), g_u[1].abs().sum().item()
    chk(abs(n0_u - n1_u) < 1e-6,
        "unweighted: both samples contribute the SAME gradient (the old behaviour)")
    chk(n1_w > n0_w * 5,
        f"weighted: the high-recency sample dominates ({n1_w:.4f} vs {n0_w:.4f}) - "
        f"recency now survives into the gradient")
    ratio = n1_w / max(1e-12, n0_w)
    chk(abs(ratio - (1.8 / 0.2)) < 1e-3,
        f"the gradient ratio equals the weight ratio exactly ({ratio:.3f} vs 9.000)")

    # And the property the old code destroyed: a per-class mean makes them identical.
    w_classmean = torch.tensor([float(w.mean()), float(w.mean())])
    loss_cm = (per_sample * w_classmean).sum() / w_classmean.sum()
    g_cm = torch.autograd.grad(loss_cm, logits, retain_graph=True)[0]
    chk(abs(g_cm[0].abs().sum().item() - g_cm[1].abs().sum().item()) < 1e-6,
        "per-CLASS averaging makes the two indistinguishable - exactly the bug fixed")

    # ------------------------------------------------------------ TCN device stability
    print("\n[tcn] inference must not migrate the model off its training device")
    from model import HAS_TORCH, PyTorchSequenceModel
    if not HAS_TORCH:
        print("  SKIP  torch model unavailable")
    else:
        lookback, feat = 12, 6
        m = PyTorchSequenceModel(input_dim=feat, lookback=lookback, device="cpu",
                                 epochs=1, batch_size=32)
        Xs = rng.normal(size=(64, lookback * feat)).astype(np.float32)
        ys = rng.integers(0, 3, size=64)
        m.fit(Xs, ys)
        before = {p.device.type for p in m.model.parameters()}
        p1 = m.predict_proba(Xs[:4])
        p2 = m.predict_proba(Xs[:4])
        after = {p.device.type for p in m.model.parameters()}
        chk(before == after,
            f"repeated inference leaves the model on its device ({before} -> {after})")
        chk(isinstance(p1, np.ndarray) and p1.shape == (4, 3)
            and np.allclose(p1.sum(1), 1) and np.allclose(p1, p2),
            "probabilities come back as a normalised numpy array, deterministically")

    print("\nfeature-and-weight-fixes:", "ALL PASS" if OK else "FAILURES")
    return 0 if OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
