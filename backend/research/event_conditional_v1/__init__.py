"""BINANCE_EVENT_CONDITIONAL_PROFIT_V1 - research only.

Phase 1: data contract, event labels, executing tests, readiness report.
No models, no promotion, no orders. PROFIT_CAMPAIGN_V1 is frozen and untouched.

Modules import each other with explicit relative imports and are run as package
entry points:

    python -m backend.research.event_conditional_v1.selftest
    python -m backend.research.event_conditional_v1.readiness
    python -m backend.research.event_conditional_v1.viability

There is deliberately no sys.path manipulation here: it would hide incorrect
imports, make script and package execution diverge, and depend on the working
directory.
"""
