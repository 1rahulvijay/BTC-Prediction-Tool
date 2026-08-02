"""Phase 5B dispatch with fail-closed source and schema handling."""
from __future__ import annotations

from research.phase5_standalone.common.causal_loader import DataUnavailable, SchemaUnavailable
from research.phase5_standalone.common.engine_types import EngineContext, EngineResult
from research.phase5_standalone.common.metrics import EMPTY_ECONOMICS

from .engines_forecast import run_ensemble_audit, run_expiry_calibration
from .engines_matrix import run_matrix_research
from .engines_microstructure import run_event_research, run_l2_research
from .engines_polymarket import run_pm_l2_research, run_pm_research
from .engines_system import run_readiness, run_system_research


ENGINES = {
    "readiness": run_readiness,
    "ensemble_audit": run_ensemble_audit,
    "expiry_calibration": run_expiry_calibration,
    "matrix_research": run_matrix_research,
    "event_research": run_event_research,
    "l2_research": run_l2_research,
    "pm_research": run_pm_research,
    "pm_l2_research": run_pm_l2_research,
    "system_research": run_system_research,
}


def execute(context: EngineContext) -> EngineResult:
    engine = ENGINES.get(context.protocol.engine)
    if engine is None:
        return EngineResult("BLOCKED_SCHEMA", "Unknown Phase 5B engine", {},
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
    except (ValueError, IndexError, KeyError) as exc:
        return EngineResult("INSUFFICIENT_SAMPLE", "Could not form a valid causal sample", {},
                            dict(EMPTY_ECONOMICS), [f"{type(exc).__name__}: {exc}"], {}, {})
