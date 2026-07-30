# Research Launchers

This directory contains standalone offline experiments, research reports, shadow
evaluators, and explicit research-training commands. These launchers are not part
of normal application startup.

Run them from any working directory. Each launcher resolves the repository root
before accessing `backend/`, `data/`, or model artifacts.

Examples from the repository root:

```powershell
.\research\launchers\run_profit_campaign_v1.bat
.\research\launchers\run_180d_sequence_only.bat
.\research\launchers\report_hierarchical_ensemble.bat
```

Operational application controls remain at the repository root:

- `start.bat`
- `start_instant.bat`
- `run_backend.bat`
- `frontend.bat`
- `backfill.bat`
- recorder launchers

Research launchers must not place live orders or silently modify production
serving behavior.
