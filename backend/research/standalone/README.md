# Standalone Backend Research

This directory contains offline probes, audits, scorecards, bakeoffs, and
analysis scripts. None of these files is part of the application startup or
serving import graph.

Run a script from the repository root, for example:

```powershell
python backend/research/standalone/probe_direction_tilt.py --selftest
```

Shared code that production serving or startup training imports remains in
`backend/`; this directory is restricted to standalone entry points.
