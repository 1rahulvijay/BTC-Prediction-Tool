"""Canonical Polymarket crypto taker-fee mathematics.

Keep this module dependency-free so production, research, and package self-tests
all use the same rounded per-share fee without importing the full Champion.
"""
from __future__ import annotations


DEFAULT_CRYPTO_TAKER_FEE_RATE = 0.07


def polymarket_taker_fee_per_share(
    price: float,
    fee_rate: float = DEFAULT_CRYPTO_TAKER_FEE_RATE,
) -> float:
    p = max(0.0, min(1.0, float(price)))
    return round(max(0.0, float(fee_rate)) * p * (1.0 - p), 5)


def selftest() -> None:
    assert polymarket_taker_fee_per_share(0.0) == 0.0
    assert polymarket_taker_fee_per_share(1.0) == 0.0
    assert polymarket_taker_fee_per_share(0.5) == 0.0175
    assert polymarket_taker_fee_per_share(0.62) == 0.01649
    print("polymarket_fee self-test: ALL PASS")


if __name__ == "__main__":
    selftest()
