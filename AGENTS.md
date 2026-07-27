# Repository Workflow - Mandatory

Canonical development branch:

```text
quant-platform-v1
```

Rules:

1. Never create another feature branch.
2. Never switch to another feature branch.
3. Never commit directly to `master`.
4. Before editing, run:

   ```powershell
   git fetch origin --prune
   git switch quant-platform-v1
   git pull --ff-only origin quant-platform-v1
   git status --short
   git branch --show-current
   ```

5. Stop immediately unless the active branch is exactly `quant-platform-v1`.
6. Commit each completed phase separately.
7. Do not merge into `master` until the full platform gate is green.
8. Do not force-push or rewrite pushed commits.
9. Do not claim a feature is complete until its executable tests pass.
10. Real Binance and Polymarket orders remain disabled unless the user explicitly
    authorizes a later live-deployment phase.
11. Do not rebuild evidence datasets, retrain models, or score frozen experiments
    as part of unrelated engineering work.
12. Keep Polymarket and Binance economics, fills, fees, settlement, positions, and
    databases isolated even when they use shared infrastructure.
13. Fail closed on stale data, sequence gaps, missing models, invalid bundles,
    unknown positions, incomplete fills, or unavailable risk state.

The repository source of truth is:

```text
C:\Users\rahul\Documents\BTC-Prediction-Tool
```

The similarly named OneDrive directory is not this Git worktree.
