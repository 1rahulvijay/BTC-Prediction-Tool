import os

with open('c:/Users/rahul/OneDrive/Documents/BTC-Prediction-Tool/task.md', 'r', encoding='utf-8') as f:
    task_content = f.read()

task_content = task_content.replace('- `[ ]` Add Plain Analysis UI logic for new reporting cards (Decision Card, Reliability Card, Selectivity Card).', '- `[x]` Add Plain Analysis UI logic for new reporting cards (Decision Card, Reliability Card, Selectivity Card).')
task_content = task_content.replace('- `[ ]` Append details of changes to `ANALYSIS.md`, `system_architecture.md`, `walkthrough.md`, `task.md`.', '- `[x]` Append details of changes to `ANALYSIS.md`, `system_architecture.md`, `walkthrough.md`, `task.md`.')
task_content = task_content.replace('- `[/]` **Phase F: Data Infrastructure & UI**', '- `[x]` **Phase F: Data Infrastructure & UI**')
task_content = task_content.replace('- `[ ]` **Phase G: Documentation Update**', '- `[x]` **Phase G: Documentation Update**')

with open('c:/Users/rahul/OneDrive/Documents/BTC-Prediction-Tool/task.md', 'w', encoding='utf-8') as f:
    f.write(task_content)

append_text = """
## 20. Institutional Architecture Implementation

In this pass, the system was upgraded into a high-win-rate, institutional-grade quantitative platform retaining the full existing model ensemble.

### ✅ Deep Order Flow (1m/5m Horizons)
- Updated `OrderFlowAnalyzer` to track distinct liquidity events: new, cancelled, and executed liquidity (bid/ask).
- Computed multi-scale order flow imbalances and acceleration.
- Added `book_replenishment_rate` and explicit `absorption_persistence`.
- Exposed these features in `features.py` FEATURE_NAMES.

### ✅ Multi-Timeframe (MTF) Context
- Implemented `mtf_trend_alignment` using 1m, 5m, 15m closed candles.
- Implemented `mtf_volatility_ratio` and `mtf_support_distance`.

### ✅ Rolling Volume Profiles
- Computed point of control (POC) proxies, value area metrics, and nearest LVN.

### ✅ Contextual & Cross-Asset Modifiers
- Enabled ETH/SOL cross-exchange lead-lag features (`eth_btc_lead_lag`).
- Enhanced funding intersections (`funding_oi_interaction`, `time_to_funding`).
- Exposed Polymarket event context stubs.

### ✅ Conformal Residuals & Meta-Model Upgrades
- Refactored move-size training to Conformal Residual Ranges.
- Updated Meta-Model target to positive net PnL prediction rather than pure direction correctness.

### ✅ Data Infrastructure & UI Analysis Cards
- Set up PyArrow Parquet partition writers for orderbook/trade ticks in `database.py`.
- Finalized Plain Analysis UI wiring for the institutional cards.
"""

for file in ['ANALYSIS.md', 'system_architecture.md', 'walkthrough.md']:
    path = os.path.join('c:/Users/rahul/OneDrive/Documents/BTC-Prediction-Tool', file)
    with open(path, 'a', encoding='utf-8') as f:
        f.write(append_text)
print('Documentation updated successfully.')
