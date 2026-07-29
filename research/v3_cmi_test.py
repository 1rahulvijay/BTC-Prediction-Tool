"""V3 - Conditional mutual information, applied then TESTED.

The original computed MI values and declared a feature redundant at 0.0001 bits. Note that
sklearn's mutual_info_* returns NATS, not bits, so a "0.05 bit" threshold is not what a bare
0.05 comparison implements. More importantly, dropping a feature must be justified by model
performance, not by an information score alone - so the retained set is traded here.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import evaluate  # noqa: E402


def main():
    from sklearn.feature_selection import mutual_info_regression

    from harness import causal_frame, split

    frame = causal_frame()
    train, _ = split(frame)
    cols = ["ret_5", "ret_15", "ret_60", "vol_z", "rng_z", "z_60", "z_240"]
    mi = mutual_info_regression(train[cols].fillna(0), train["fwd"].fillna(0),
                                random_state=7)
    print("[V3] mutual information (NATS, not bits) against forward return, TRAIN only:")
    for name, value in sorted(zip(cols, mi), key=lambda kv: -kv[1]):
        print("       %-10s %.6f nats  (%.6f bits)" % (name, value, value / np.log(2)))
    best = cols[int(np.argmax(mi))]
    print("[V3] trading the single highest-MI feature: %s" % best)

    def signal(part):
        z = (part[best] - part[best].rolling(240).mean()) / part[best].rolling(240).std()
        return np.where(z.abs() > 2.0, -np.sign(z), 0)

    evaluate("V3 - highest-MI feature traded (was: MI values only)", signal,
             notes="high MI does not imply tradeable edge; that is what this checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
