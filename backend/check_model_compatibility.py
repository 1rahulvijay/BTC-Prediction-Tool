"""Fast preflight for launchers that are not allowed to retrain the main ensemble."""
from __future__ import annotations

import os
import sys

import joblib
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
VERSION_PATH = os.path.join(DATA_DIR, "saved_models", "architecture_version.pkl")


def main() -> int:
    try:
        from features import LOOKBACK
        from model import MODEL_ARCH_VERSION, MODEL_FEATURE_SCHEMA_HASH, MODEL_NUM_FEATURES
    except Exception as exc:
        print(f"[model-preflight] Cannot import current model architecture: {exc}")
        return 2

    if not os.path.exists(VERSION_PATH):
        print(f"[model-preflight] No saved main-model architecture at {VERSION_PATH}")
        return 3

    try:
        saved = joblib.load(VERSION_PATH)
    except Exception as exc:
        print(f"[model-preflight] Cannot read saved architecture: {exc}")
        return 4

    if saved != MODEL_ARCH_VERSION:
        print("[model-preflight] INCOMPATIBLE saved main ensemble")
        print(f"  saved:   {saved}")
        print(f"  current: {MODEL_ARCH_VERSION}")
        return 5

    schema_path = os.path.join(DATA_DIR, "saved_models", "model_feature_schema.pkl")
    try:
        schema = joblib.load(schema_path)
    except Exception as exc:
        print(f"[model-preflight] Cannot read saved feature schema: {exc}")
        return 6
    if (int(schema.get("model_count", -1)) != int(MODEL_NUM_FEATURES)
            or schema.get("hash") != MODEL_FEATURE_SCHEMA_HASH):
        print("[model-preflight] INCOMPATIBLE saved feature schema")
        return 7

    # Validate a required GLOBAL model for every active horizon. This catches a
    # partially-written/corrupt bundle that an architecture string alone cannot detect.
    dummy = np.zeros((1, LOOKBACK * MODEL_NUM_FEATURES), dtype=np.float32)
    for horizon in (5, 15):
        component = os.path.join(DATA_DIR, "saved_models", "GLOBAL", f"xgb_{horizon}.pkl")
        try:
            model = joblib.load(component)
            model.predict(dummy)
        except Exception as exc:
            print(f"[model-preflight] Invalid required component xgb_{horizon}: {exc}")
            return 8

    print(f"[model-preflight] Compatible saved main ensemble: {MODEL_ARCH_VERSION} "
          f"({MODEL_NUM_FEATURES} features; GLOBAL 5m/15m validated)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
