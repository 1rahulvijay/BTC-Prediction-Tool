"""Phase 5 engine dispatch with fail-closed data/schema handling."""
from __future__ import annotations

from .causal_loader import DataUnavailable, SchemaUnavailable
from .engine_types import EngineContext, EngineResult
from .engines_market import (run_candidate_audit, run_event_flow, run_l2_capacity,
                             run_l2_hazard, run_ledger, run_pm_cross_expiry,
                             run_pm_dynamic, run_pm_settlement)
from .engines_matrix import (run_alpha_upper_bound, run_btc_signal, run_crossvenue,
                             run_feed_ablation, run_history_transport,
                             run_magnitude_diagnostic, run_readiness,
                             run_signal_context)
from .metrics import EMPTY_ECONOMICS


ENGINES = {
    "alpha_upper_bound": run_alpha_upper_bound,
    "history_transport": run_history_transport,
    "feed_ablation": run_feed_ablation,
    "btc_signal": run_btc_signal,
    "signal_context": run_signal_context,
    "btc_magnitude": run_magnitude_diagnostic,
    "crossvenue": run_crossvenue,
    "event_flow": run_event_flow,
    "l2_hazard": run_l2_hazard,
    "pm_settlement": run_pm_settlement,
    "pm_dynamic": run_pm_dynamic,
    "pm_cross_expiry": run_pm_cross_expiry,
    "l2_capacity": run_l2_capacity,
    "ledger": run_ledger,
    "candidate_audit": run_candidate_audit,
    "readiness": run_readiness,
}


def execute(context: EngineContext) -> EngineResult:
    engine = ENGINES.get(context.protocol.engine)
    if engine is None:
        return EngineResult("BLOCKED_SCHEMA", "Unknown experiment engine", {},
                            dict(EMPTY_ECONOMICS),
                            [f"engine {context.protocol.engine!r} is not registered"], {}, {})
    try:
        return engine(context)
    except DataUnavailable as exc:
        return EngineResult("BLOCKED_DATA", "Required data is unavailable", {},
                            dict(EMPTY_ECONOMICS), [str(exc)], {}, {})
    except SchemaUnavailable as exc:
        return EngineResult("BLOCKED_SCHEMA", "Required causal schema is unavailable", {},
                            dict(EMPTY_ECONOMICS), [str(exc)], {}, {})
    except (ValueError, IndexError) as exc:
        return EngineResult("INSUFFICIENT_SAMPLE", "Experiment could not form a valid sample", {},
                            dict(EMPTY_ECONOMICS), [f"{type(exc).__name__}: {exc}"], {}, {})
