import sys
from pathlib import Path

# Same failure the Deribit recorder test had: `python tests/test_...py` died on
# ModuleNotFoundError: No module named 'research', because the import below only resolved when
# pytest put the repository root on sys.path. A test whose result depends on how it was launched
# is a false failure waiting to be recorded as a real one.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.polymarket_market_prior_residual_v1.run import (  # noqa: E402
    OffsetLogit,
    _taker_fee,
)

import numpy as np  # noqa: E402


def test_offset_logit_learns_incremental_signal() -> None:
    rng = np.random.default_rng(19)
    feature = rng.normal(size=(700, 1))
    market_logit = rng.normal(scale=0.7, size=700)
    market_probability = 1.0 / (1.0 + np.exp(-market_logit))
    true_probability = 1.0 / (
        1.0 + np.exp(-(market_logit + 1.1 * feature[:, 0]))
    )
    target = rng.binomial(1, true_probability)
    model = OffsetLogit(4.0).fit(
        feature[:500], target[:500], offset=market_logit[:500]
    )
    prediction = model.predict(feature[500:], offset=market_logit[500:])
    baseline_error = np.mean((target[500:] - market_probability[500:]) ** 2)
    model_error = np.mean((target[500:] - prediction) ** 2)
    assert model_error < baseline_error


def test_crypto_fee_formula_is_bounded_and_symmetric() -> None:
    prices = np.array([0.0, 0.25, 0.5, 0.75, 1.0])
    fees = _taker_fee(prices)
    assert np.allclose(fees, fees[::-1])
    assert fees[2] == max(fees)
    assert np.all(fees >= 0.0)
