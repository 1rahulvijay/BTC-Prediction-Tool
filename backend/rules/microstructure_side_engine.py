import numpy as np

def get_side_signal(
    p_big_move: float, 
    p_tradable: float, 
    p_fail_fast: float,
    basis_bps: float, 
    cvd_div: float, 
    vpin: float,
    # Config thresholds
    t1_selectivity_thresh=0.95,
    tradable_thresh=0.50,
    fail_fast_thresh=0.60,
    basis_extreme_pct_up=95.0,
    basis_extreme_pct_down=5.0,
    cvd_extreme_pct_up=95.0,
    cvd_extreme_pct_down=5.0,
    vpin_extreme_pct_up=95.0,
    vpin_extreme_pct_down=5.0,
    basis_historical=None,
    cvd_historical=None,
    vpin_historical=None
):
    """
    Evaluates microstructure state against Selectivity predictions to determine direction.
    Historical arrays should be passed to compute dynamic percentiles, or pass raw threshold values.
    """
    
    # 1. Gate: Do we have a volatility trigger?
    if p_big_move < t1_selectivity_thresh:
        return "NO_SIDE", "T0", "Insufficient Selectivity"
        
    # 2. Risk Downgrade
    is_weak = p_fail_fast > fail_fast_thresh
    
    # Calculate extreme thresholds dynamically if history is provided
    if basis_historical is not None:
        b_up = np.percentile(basis_historical, basis_extreme_pct_up)
        b_dn = np.percentile(basis_historical, basis_extreme_pct_down)
    else:
        b_up = basis_extreme_pct_up
        b_dn = basis_extreme_pct_down
        
    if cvd_historical is not None:
        c_up = np.percentile(cvd_historical, cvd_extreme_pct_up)
        c_dn = np.percentile(cvd_historical, cvd_extreme_pct_down)
    else:
        c_up = cvd_extreme_pct_up
        c_dn = cvd_extreme_pct_down
        
    if vpin_historical is not None:
        v_up = np.percentile(vpin_historical, vpin_extreme_pct_up)
        v_dn = np.percentile(vpin_historical, vpin_extreme_pct_down)
    else:
        v_up = vpin_extreme_pct_up
        v_dn = vpin_extreme_pct_down

    # 3. Regime Routing
    if p_tradable > tradable_thresh:
        # TRADABLE BREAKOUT REGIME -> FOLLOW BASIS / VPIN
        tier = "T3" if p_big_move > 0.99 and not is_weak else "T2"
        
        # Follow Basis
        if basis_bps > b_up:
            return "LONG", tier, "Breakout Follow Basis"
        elif basis_bps < b_dn:
            return "SHORT", tier, "Breakout Follow Basis"
            
        # Follow VPIN if Basis neutral
        if vpin > v_up:
            return "SHORT", tier, "Breakout Follow VPIN (Contrarian Trap? No, direct in breakouts if toxic buy)"
            # Wait, vpin is positive when taker sell > taker buy. 
            # High VPIN = heavy selling. 
            # Following VPIN means going SHORT when VPIN is high (heavy selling -> drops).
            # Going LONG when VPIN is low (heavy buying -> rises).
        elif vpin < v_dn:
            return "LONG", tier, "Breakout Follow VPIN"
            
    else:
        # CHOP / GENERIC HIGH VOLATILITY REGIME -> FADE BASIS / CVD DIV
        tier = "T2" if not is_weak else "T1"
        
        if basis_bps > b_up:
            return "SHORT", tier, "Chop Fade Basis"
        elif basis_bps < b_dn:
            return "LONG", tier, "Chop Fade Basis"
            
        if cvd_div > c_up:
            return "SHORT", tier, "Chop Fade CVD Divergence"
        elif cvd_div < c_dn:
            return "LONG", tier, "Chop Fade CVD Divergence"

    return "NO_SIDE", "T1", "No Extreme Flow Setup"

def evaluate_vectorized(p_big_move, p_tradable, p_fail_fast, basis_bps, cvd_div, vpin, config=None):
    """
    Vectorized evaluation for backtesting on dataframes.
    Returns array of (side_signal: 1, -1, 0) and strings for tier/reason.
    """
    n = len(p_big_move)
    sides = np.zeros(n, dtype=int)
    tiers = np.full(n, "T0", dtype=object)
    reasons = np.full(n, "None", dtype=object)
    
    # Defaults
    if config is None: config = {}
    b_up = config.get("b_up", np.percentile(basis_bps[np.isfinite(basis_bps)], 95))
    b_dn = config.get("b_dn", np.percentile(basis_bps[np.isfinite(basis_bps)], 5))
    c_up = config.get("c_up", np.percentile(cvd_div[np.isfinite(cvd_div)], 95))
    c_dn = config.get("c_dn", np.percentile(cvd_div[np.isfinite(cvd_div)], 5))
    v_up = config.get("v_up", np.percentile(vpin[np.isfinite(vpin)], 95))
    v_dn = config.get("v_dn", np.percentile(vpin[np.isfinite(vpin)], 5))
    
    sel_t1 = config.get("sel_t1", np.percentile(p_big_move[np.isfinite(p_big_move)], 95))
    sel_t2 = config.get("sel_t2", np.percentile(p_big_move[np.isfinite(p_big_move)], 99))
    sel_t3 = config.get("sel_t3", np.percentile(p_big_move[np.isfinite(p_big_move)], 99.75))
    
    # Evaluate tradability threshold specifically within the high selectivity regime
    # so we effectively split the high-vol windows into Chop vs Clean
    high_vol_trad = p_tradable[p_big_move >= sel_t1]
    if len(high_vol_trad) > 0:
        trad_thresh = config.get("trad_thresh", np.percentile(high_vol_trad[np.isfinite(high_vol_trad)], 50))
    else:
        trad_thresh = config.get("trad_thresh", 0.50)
    
    for i in range(n):
        s, t, r = get_side_signal(
            p_big_move[i], p_tradable[i], p_fail_fast[i],
            basis_bps[i], cvd_div[i], vpin[i],
            t1_selectivity_thresh=sel_t1,
            tradable_thresh=trad_thresh,
            basis_extreme_pct_up=b_up, basis_extreme_pct_down=b_dn,
            cvd_extreme_pct_up=c_up, cvd_extreme_pct_down=c_dn,
            vpin_extreme_pct_up=v_up, vpin_extreme_pct_down=v_dn
        )
        
        if s == "LONG": sides[i] = 1
        elif s == "SHORT": sides[i] = -1
        
        # Override tier based on selectivity percentile directly for exact matching with user spec
        if s != "NO_SIDE":
            if p_big_move[i] >= sel_t3 and p_tradable[i] > trad_thresh:
                tiers[i] = "T3"
            elif p_big_move[i] >= sel_t2 and p_tradable[i] > trad_thresh:
                tiers[i] = "T2"
            elif p_big_move[i] >= sel_t1:
                tiers[i] = "T1"
            else:
                tiers[i] = "T0"
        
        reasons[i] = r
        
    return sides, tiers, reasons
