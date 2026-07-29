"""V5 - Fractional differencing as an ABLATION with an economic test.

WHAT THE ORIGINAL DID
    Reported an ADF p-value and declared "80% memory retained". An ADF statistic is a
    stationarity diagnostic, not a predictive or economic result, and "80% memory" was never
    defined. d was also chosen by scanning the whole sample, which selects on the test period.

WHAT THIS DOES
    Selects d on the TRAINING period only, freezes it, and asks the question that matters:
    does trading a fractionally differenced signal beat costs OUT OF SAMPLE?
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import evaluate  # noqa: E402



def frac_diff_weights(d, size=60):
    w = [1.0]
    for k in range(1, size):
        w.append(-w[-1] * (d - k + 1) / k)
    return np.asarray(w[::-1])


def frac_diff(series, d, size=60):
    w = frac_diff_weights(d, size)
    return series.rolling(size).apply(lambda x: float(np.dot(w, x)), raw=True)


def main():
    from harness import causal_frame, split

    frame = causal_frame()
    train, _ = split(frame)

    logp = np.log(train["close"])
    chosen = 1.0
    for d in (0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0):
        fd = frac_diff(logp, d).dropna()
        if len(fd) > 1000 and abs(fd.mean()) < fd.std() * 0.05:
            chosen = d
            break
    print("[V5] fractional order selected on TRAIN ONLY: d=%s" % chosen)

    def signal(part):
        fd = frac_diff(np.log(part["close"]), chosen)
        z = (fd - fd.rolling(240).mean()) / fd.rolling(240).std()
        return np.sign(-z.fillna(0)) * (z.abs() > 2.0).astype(int)

    evaluate("V5 - fractional differencing d=%s (was: ADF p-value only)" % chosen, signal,
             notes="ADF is a diagnostic; this asks whether it pays after costs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
