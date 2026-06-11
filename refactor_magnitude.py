import re

with open('backend/model.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Remove mag_q25, mag_q50, mag_q75 from models_by_regime
content = re.sub(r'\"mag_q25\": \{\},\s*\"mag_q50\": \{\},\s*\"mag_q75\": \{\},', '', content)

# Add conformal_residuals initialization
if 'self.conformal_residuals =' not in content:
    content = content.replace('self.stackers_by_regime = {reg: {} for reg in self.regimes}', 'self.stackers_by_regime = {reg: {} for reg in self.regimes}\n        self.conformal_residuals = {reg: {} for reg in self.regimes}')

# 2. Remove mag_q* from save and load models (note: architecture checks)
content = content.replace('\"mag\", \"mag_q25\", \"mag_q50\", \"mag_q75\"', '\"mag\"')

# 3. Modify train() magnitude regressor block to calculate conformal residuals
old_mag_block = '''                        reg_mag = HistGradientBoostingRegressor(
                            loss="squared_error",
                            max_iter=MOVE_SIZE_MAX_ITER,
                            max_leaf_nodes=15,
                            learning_rate=0.06,
                            l2_regularization=0.05,
                            random_state=46,
                        )
                        reg_mag.fit(X_reg, mag_target, sample_weight=sw_reg)
                        self.models_by_regime[reg]["mag"][h] = reg_mag
                        log_component_done(h, reg, "MoveSizeRegressorFast", _t0)'''

new_mag_block = '''                        reg_mag = HistGradientBoostingRegressor(
                            loss="squared_error",
                            max_iter=MOVE_SIZE_MAX_ITER,
                            max_leaf_nodes=15,
                            learning_rate=0.06,
                            l2_regularization=0.05,
                            random_state=46,
                        )
                        reg_mag.fit(X_reg, mag_target, sample_weight=sw_reg)
                        self.models_by_regime[reg]["mag"][h] = reg_mag
                        
                        # Conformal Residuals
                        preds = reg_mag.predict(X_reg)
                        residuals = mag_target - preds
                        self.conformal_residuals[reg][h] = {
                            "q25": float(np.quantile(residuals, 0.25)),
                            "q50": float(np.quantile(residuals, 0.50)),
                            "q75": float(np.quantile(residuals, 0.75)),
                        }
                        log_component_done(h, reg, "MoveSizeRegressorFast_with_Conformal", _t0)'''
content = content.replace(old_mag_block, new_mag_block)

# 4. Remove the Quantile model training block
q_train_regex = r'                    # Quantile move-size models.*?except Exception as e:\s*logger\.error\(f"Quantile magnitude models failed for h=\{h\} reg=\{reg\}: \{e\}"\)'
content = re.sub(q_train_regex, '', content, flags=re.DOTALL)

# 5. Fix generate_ensemble_prediction magnitude fallback
old_q_eval = '''        q_reg = reg if h in self.models_by_regime[reg]["mag_q50"] else "GLOBAL"
        if last_price > 0 and h in self.models_by_regime[q_reg]["mag_q50"]:
            try:
                q_vals = {}
                for q_name, out_name in [("mag_q25", "low"), ("mag_q50", "median"), ("mag_q75", "high")]:
                    if h in self.models_by_regime[q_reg][q_name]:
                        frac = float(self.models_by_regime[q_reg][q_name][h].predict(_xflat)[0])
                        q_vals[out_name] = abs(frac) * last_price
                if "median" in q_vals and q_vals["median"] > 0:
                    if exp_move > 0:
                        exp_move = float(np.clip(q_vals["median"], 0.1 * exp_move, 8.0 * exp_move))
                    else:
                        exp_move = q_vals["median"]
                if q_vals:
                    low = q_vals.get("low", exp_move)
                    high = q_vals.get("high", exp_move)
                    move_range = {
                        "low": round(float(min(low, high)), 2),
                        "median": round(float(q_vals.get("median", exp_move)), 2),
                        "high": round(float(max(low, high)), 2),
                    }
            except Exception:
                pass'''

new_q_eval = '''        if last_price > 0 and h in self.conformal_residuals.get(mag_reg, {}):
            try:
                resids = self.conformal_residuals[mag_reg][h]
                pred_frac = float(self.models_by_regime[mag_reg]["mag"][h].predict(_xflat)[0])
                
                low_frac = pred_frac + resids["q25"]
                median_frac = pred_frac + resids["q50"]
                high_frac = pred_frac + resids["q75"]
                
                low_val = abs(low_frac) * last_price
                median_val = abs(median_frac) * last_price
                high_val = abs(high_frac) * last_price
                
                if median_val > 0:
                    if exp_move > 0:
                        exp_move = float(np.clip(median_val, 0.1 * exp_move, 8.0 * exp_move))
                    else:
                        exp_move = median_val
                
                move_range = {
                    "low": round(float(min(low_val, high_val)), 2),
                    "median": round(float(median_val), 2),
                    "high": round(float(max(low_val, high_val)), 2),
                }
            except Exception:
                pass'''
content = content.replace(old_q_eval, new_q_eval)

# 6. Change persistence for conformal_residuals
old_save = '''joblib.dump(self.model_accuracies, os.path.join(MODEL_DIR, "accuracies.pkl"))'''
new_save = '''joblib.dump(self.model_accuracies, os.path.join(MODEL_DIR, "accuracies.pkl"))\n            joblib.dump(self.conformal_residuals, os.path.join(MODEL_DIR, "conformal_residuals.pkl"))'''
content = content.replace(old_save, new_save)

old_load = '''acc_path = os.path.join(MODEL_DIR, "accuracies.pkl")
            if os.path.exists(acc_path):
                self.model_accuracies = joblib.load(acc_path)'''
new_load = '''acc_path = os.path.join(MODEL_DIR, "accuracies.pkl")
            if os.path.exists(acc_path):
                self.model_accuracies = joblib.load(acc_path)
            
            res_path = os.path.join(MODEL_DIR, "conformal_residuals.pkl")
            if os.path.exists(res_path):
                try:
                    self.conformal_residuals = joblib.load(res_path)
                except Exception:
                    self.conformal_residuals = {reg: {} for reg in self.regimes}'''
content = content.replace(old_load, new_load)

with open('backend/model.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Refactored magnitude models successfully!')
