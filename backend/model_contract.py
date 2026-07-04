"""Lightweight source of truth for the persisted main-model contract.

This module deliberately imports no ML/GPU runtime. Launch preflights can check
an artifact bundle without initializing XGBoost, LightGBM, CatBoost, or Torch.
"""
from __future__ import annotations

import hashlib
import logging
import os

from features import FEATURE_NAMES, NUM_FEATURES


logger = logging.getLogger(__name__)
DL_ARCH = os.getenv("BTC_DL_ARCH", "TCN").upper()


def resolve_model_feature_schema() -> tuple[list[int], list[str], str]:
    mode = os.getenv("BTC_MODEL_FEATURE_PRUNING", "SAFE").strip().upper()
    if mode in {"0", "OFF", "FALSE", "NONE", "FULL"}:
        return list(range(NUM_FEATURES)), list(FEATURE_NAMES), "off"

    allowed = {
        value.strip().upper()
        for value in os.getenv("BTC_MODEL_FEATURE_ACTIONS", "KEEP,PARITY-FIX").split(",")
        if value.strip()
    }
    try:
        from dead_feature_classifier import classify

        classified = classify()
        indices = [
            index for index, name in enumerate(FEATURE_NAMES)
            if (classified.get(name, ("", "", ""))[1] or "").upper() in allowed
        ]
        if len(indices) < 20:
            raise ValueError(f"pruned feature set too small: {len(indices)}")
        return indices, [FEATURE_NAMES[index] for index in indices], ",".join(sorted(allowed))
    except Exception as exc:
        logger.warning("Feature pruning disabled; falling back to full feature schema: %s", exc)
        return list(range(NUM_FEATURES)), list(FEATURE_NAMES), "fallback-full"


MODEL_FEATURE_INDICES, MODEL_FEATURE_NAMES, MODEL_FEATURE_PRUNING = resolve_model_feature_schema()
MODEL_NUM_FEATURES = len(MODEL_FEATURE_INDICES)
MODEL_FEATURE_SCHEMA_HASH = hashlib.sha1(
    "\n".join(MODEL_FEATURE_NAMES).encode("utf-8")
).hexdigest()[:12]
MODEL_ARCH_VERSION = (
    f"2026-06-15-v11-pruned{MODEL_NUM_FEATURES}-{MODEL_FEATURE_SCHEMA_HASH}-"
    f"2horizon-5-15-rf-persist-split98-classbal-vrts-session-136-{DL_ARCH.lower()}"
)
