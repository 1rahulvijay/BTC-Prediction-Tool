import numpy as np
import datetime

def compute_polymarket_features(market: dict, quote: dict, btc_price: float, expected_volatility: float, btc_engine_payload: dict):
    """
    Computes mathematical baseline inputs and microstructure features.
    """
    features = {}
    
    ref_price = market.get('reference_price', 0.0)
    end_date = market.get('end_date')
    
    # Contract state
    seconds_remaining = 0.0
    if end_date:
        try:
            dt = datetime.datetime.fromisoformat(end_date.replace('Z', '+00:00'))
            seconds_remaining = max(0.0, dt.timestamp() - datetime.datetime.now().timestamp())
        except Exception:
            pass
            
    features['seconds_remaining'] = seconds_remaining
    features['btc_minus_reference_price_pct'] = (btc_price - ref_price) / ref_price if ref_price > 0 else 0.0
    
    # Distance in vol units (Z-score)
    vol = expected_volatility if expected_volatility > 0 else 0.10
    t_years = seconds_remaining / (365 * 24 * 3600)
    vol_scaled = vol * np.sqrt(t_years) + 1e-6
    
    features['distance_to_reference_in_vol_units'] = features['btc_minus_reference_price_pct'] / vol_scaled
    features['distance_x_inverse_time'] = features['btc_minus_reference_price_pct'] * (1.0 / (seconds_remaining + 1.0))
    
    # Microstructure
    features['yes_spread'] = quote.get('yes_spread', 1.0)
    features['yes_order_book_imbalance'] = quote.get('yes_imbalance', 0.0)
    features['yes_depth_near_mid'] = quote.get('yes_depth', 0.0)
    
    # BTC Engine context
    best_pred = None
    if btc_engine_payload and btc_engine_payload.get('predictions'):
        preds = btc_engine_payload['predictions']
        best_pred = max(preds, key=lambda p: p.get('confidence', 0))
        
    if best_pred:
        features['btc_p_up'] = best_pred.get('probUp', 0.0)
        features['btc_p_down'] = best_pred.get('probDown', 0.0)
        features['expected_btc_move'] = best_pred.get('expectedMove', 0.0)
        features['move_uncertainty'] = best_pred.get('quantileSpread', 0.0)
    else:
        features['btc_p_up'] = 0.0
        features['btc_p_down'] = 0.0
        features['expected_btc_move'] = 0.0
        features['move_uncertainty'] = 0.0
        
    return features
