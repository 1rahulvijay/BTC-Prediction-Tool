"""V9 - GMM regimes tested for ECONOMIC value, not cluster counts.

WHAT THE ORIGINAL DID
    Fitted a GMM to the whole series and reported the cluster distribution. That a clustering
    algorithm produces clusters is guaranteed; it says nothing about whether the regimes are
    tradeable, and fitting on all data leaks the test period.

WHAT THIS DOES
    Fits the GMM on TRAIN ONLY, freezes it, assigns regimes causally, and trades the regime
    that was most mean-reverting during training.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import evaluate  # noqa: E402



def main():
    from sklearn.mixture import GaussianMixture

    from harness import causal_frame, split

    frame = causal_frame()
    train, _ = split(frame)

    cols = ["vol_z", "rng_z", "ret_15"]
    model = GaussianMixture(n_components=4, random_state=7, covariance_type="full")
    model.fit(train[cols].values)

    labels = model.predict(train[cols].values)
    scores = {}
    for k in range(4):
        mask = labels == k
        if mask.sum() > 200:
            scores[k] = float(np.corrcoef(train.loc[mask, "ret_15"],
                                          train.loc[mask, "fwd"])[0, 1])
    target = min(scores, key=scores.get)
    print("[V9] train regime corr(ret_15, fwd): %s"
          % {k: round(v, 4) for k, v in scores.items()})
    print("[V9] trading regime %s (most mean-reverting in TRAIN)" % target)

    def signal(part):
        k = model.predict(part[cols].values)
        return np.where(k == target, -np.sign(part["ret_15"]), 0)

    evaluate("V9 - GMM regime frozen on train (was: cluster counts only)", signal,
             notes="clustering always produces clusters; this tests whether they pay")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
