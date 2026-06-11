import re

with open('backend/meta_model.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add new columns to META_FEATURES
content = content.replace(
    '"quantile_width_pct", "quantile_asymmetry", "quantile_spread",',
    '"quantile_width_pct", "quantile_asymmetry", "quantile_spread", "expectancy_usd",'
)

# 2. Modify SQL query to calculate profitable target
old_sql = '''SELECT confidence, agreement, regime, ewma_vol, spread_norm,
                           wall_imbalance, sr_compression, liq_imbalance, 
                           quantile_width_pct, quantile_asymmetry, quantile_spread,
                           wf_accuracy, wf_accuracy_minus_0_5, wf_fold_std, wf_sample_count, wf_age_minutes,
                           (timestamp / 3600000) % 24 AS hour_utc, 
                           tradeability, regime_score, liquidity_score, expected_edge, hit
                    FROM predictions_{horizon}m
                    WHERE resolved = TRUE AND hit IS NOT NULL AND confidence > 0.55
                    ORDER BY timestamp'''

new_sql = '''SELECT confidence, agreement, regime, ewma_vol, spread_norm,
                           wall_imbalance, sr_compression, liq_imbalance, 
                           quantile_width_pct, quantile_asymmetry, quantile_spread,
                           wf_accuracy, wf_accuracy_minus_0_5, wf_fold_std, wf_sample_count, wf_age_minutes,
                           (timestamp / 3600000) % 24 AS hour_utc, 
                           tradeability, regime_score, liquidity_score, expected_edge, expectancy_usd, hit,
                           binance_price, actual_move, expected_slippage_usd, signal, raw_direction
                    FROM predictions_{horizon}m
                    WHERE resolved = TRUE AND hit IS NOT NULL AND confidence > 0.55 AND binance_price IS NOT NULL
                    ORDER BY timestamp'''
content = content.replace(old_sql, new_sql)

# 3. Create cost-aware target in df
old_y_logic = '''        X = df[META_FEATURES].fillna(0).values
        y = df["hit"].astype(int).values'''

new_y_logic = '''        # Cost-aware target: Did the trade actually produce positive P&L after costs?
        def calc_profitable(row):
            direction_sign = 1 if row.get("raw_direction") == "UP" else (-1 if row.get("raw_direction") == "DOWN" else 0)
            gross_pnl = direction_sign * row.get("actual_move", 0.0)
            cost_estimate = row.get("binance_price", 0.0) * 0.0010 + row.get("expected_slippage_usd", 0.0)
            return int((gross_pnl - cost_estimate) > 0)
            
        df["profitable"] = df.apply(calc_profitable, axis=1)
        X = df[META_FEATURES].fillna(0).values
        y = df["profitable"].astype(int).values'''
content = content.replace(old_y_logic, new_y_logic)

# 4. Update row dict in should_execute
content = content.replace(
    '"expected_edge": context.get("expected_edge", 0.0),',
    '"expected_edge": context.get("expected_edge", 0.0),\n                "expectancy_usd": context.get("expectancy_usd", 0.0),'
)

with open('backend/meta_model.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Refactored meta_model.py successfully!')
