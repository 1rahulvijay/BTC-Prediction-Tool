import os

filepath = r"c:\Users\rahul\OneDrive\Documents\BTC-Prediction-Tool\backend\model.py"
with open(filepath, "r", encoding="utf-8") as f:
    lines = f.readlines()

new_lines = []
skip = False
for i, line in enumerate(lines):
    if "for row in fold_probs:" in line:
        new_lines.append(line)
        skip = True
        # Insert the missing block!
        new_lines.append("""                                            if hasattr(fold_model, "classes_"):
                                                padded.append(self._pad_probs(row, fold_model))
                                            elif len(row) >= 3:
                                                padded.append(row[:3])
                                            else:
                                                padded.append([0.0, 1.0, 0.0])
                                        preds[val_idx] = np.array(padded)
                                    oof_features.append(preds)
                                    feature_names.append(name)
                                except Exception as e:
                                    logger.error(f"OOF generation failed for {name}: {e}")
                        
                        if len(oof_features) >= 2:
                            valid_oof = np.ones(len(X_stack), dtype=bool)
                            for preds in oof_features:
                                valid_oof &= np.isfinite(preds).all(axis=1)
                            if valid_oof.sum() < 50 or len(np.unique(y_stack[valid_oof])) < 2:
                                raise ValueError("OOF stacker has too few valid purged rows")
                            X_meta = np.hstack([preds[valid_oof] for preds in oof_features])
                            
                            import xgboost as xgb
                            meta_xgb = xgb.XGBClassifier(
                                n_estimators=100, 
                                max_depth=3, 
                                learning_rate=0.05, 
                                subsample=0.8, 
                                random_state=42,
                                eval_metric="mlogloss"
                            )
                            meta_xgb.fit(X_meta, y_stack[valid_oof])
                            self.stackers_by_regime[reg][h] = {
                                "model": meta_xgb,
                                "features": feature_names
                            }
                            logger.info(f"Trained XGBoost Stacker for {h}m in {reg} with features: {feature_names}")
                    log_component_done(h, reg, "OOFStacker", _t0)
                except Exception as e:
                    logger.error(f"Stacker training failed for h={h} reg={reg}: {e}")

        self._build_feature_reference(X)
        self.is_trained = True
        self.train_count += 1
        self._save_models()
        logger.info(
            "[TRAIN] Ensemble training finished in %.1fs. Completed/attempted components=%s/%s",
            time.time() - train_started,
            progress["done"],
            progress["total"],
        )

    def _build_move_size_stats(self, Ymag: Optional[dict], regime_indices: dict, split_idx: int):
        \"\"\"
        Build a near-zero-cost target-size prior by horizon and regime.
        Ymag is stored as fractional realized move size, so live predictions multiply
""")
    if "the prior by the current BTC price." in line:
        skip = False
        new_lines.append(line)
        continue
    if not skip:
        new_lines.append(line)

with open(filepath, "w", encoding="utf-8") as f:
    f.writelines(new_lines)

print("File patched successfully.")
