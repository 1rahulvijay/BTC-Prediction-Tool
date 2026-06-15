import numpy as np

def make_tradability_labels(bars, h=5, percentile=75, max_adverse=0.40):
    """
    Computes Tradability label: 
    1 if Absolute Return > Threshold AND Adverse Excursion < 40% of Final Move
    """
    c = bars["close"]
    high = bars["high"]
    low = bars["low"]
    n = len(c)
    
    ret = np.full(n, np.nan)
    adverse_ratio = np.full(n, np.nan)
    
    for i in range(n - h):
        start_price = c[i]
        if start_price <= 0: continue
        end_price = c[i+h]
        move = end_price - start_price
        ret[i] = move
        
        # Calculate max excursion within the forward window
        h_window = high[i+1:i+h+1]
        l_window = low[i+1:i+h+1]
        
        if len(h_window) == 0: continue
        max_high = np.max(h_window)
        min_low = np.min(l_window)
        
        if move > 0:
            # Going long. Adverse is how far it dropped below start_price
            adverse = max(0, start_price - min_low)
            adverse_ratio[i] = adverse / (move + 1e-9)
        elif move < 0:
            # Going short. Adverse is how far it spiked above start_price
            adverse = max(0, max_high - start_price)
            adverse_ratio[i] = adverse / (abs(move) + 1e-9)
        else:
            adverse_ratio[i] = 1.0 # infinite ratio
            
    absm = np.abs(ret)
    threshold = np.nanpercentile(absm, percentile)
    
    is_big = absm > threshold
    is_clean = adverse_ratio < max_adverse
    is_tradable = (is_big & is_clean).astype(int)
    
    # We also return a "chop" label for P(Avoid)
    # Chop: bottom 25% of moves OR high adverse excursion
    chop_threshold = np.nanpercentile(absm, 25)
    is_chop_or_messy = ((absm < chop_threshold) | (adverse_ratio > 0.80)).astype(int)
    
    return is_tradable, is_chop_or_messy
