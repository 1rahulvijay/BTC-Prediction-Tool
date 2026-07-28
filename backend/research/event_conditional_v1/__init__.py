"""BINANCE_EVENT_CONDITIONAL_PROFIT_V1 - research only.

Phase 1: data contract, event labels, executing tests, readiness report.
No models, no promotion, no orders. PROFIT_CAMPAIGN_V1 is frozen and untouched.
"""
import os as _os
import sys as _sys

# Package modules import each other by bare name. Under `python -m ...` this package
# __init__ loads first, so putting both directories on the path here makes every entry
# point (-m, direct script, and the selftest) resolve identically.
_HERE = _os.path.dirname(_os.path.abspath(__file__))
for _p in (_HERE, _os.path.dirname(_HERE)):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
