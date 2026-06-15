import sqlite3
import json
import time
import os
import pandas as pd
from datetime import datetime

class LiveShadowLogger:
    """
    Manages the 14-30 day Live Shadow Protocol.
    Logs every active signal (T1, T2, T3) and resolves it 5-minutes later to calculate real out-of-sample Paper EV.
    Requires 500+ resolved signals before any model is promoted to LIVE_ACTIVE.
    """
    def __init__(self, db_path="live_shadow.db"):
        self.db_path = db_path
        self._init_db()
        
    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS shadow_signals (
                signal_id TEXT PRIMARY KEY,
                timestamp DATETIME,
                tier TEXT,
                side TEXT,
                reason TEXT,
                p_big_move REAL,
                p_tradable REAL,
                p_fail_fast REAL,
                expected_move_bps REAL,
                
                -- Signal triggers
                entry_price REAL,
                
                -- Maker Execution Tracking
                maker_limit_price REAL,
                maker_filled INTEGER DEFAULT 0,
                maker_time_to_fill_sec REAL,
                adverse_excursion_after_fill_bps REAL,
                
                -- Resolutions
                resolved INTEGER DEFAULT 0,
                exit_price_5m REAL,
                exit_price_15m REAL,
                exit_price_30m REAL,
                exit_price_60m REAL,
                
                -- Mode Outcomes (Net EV Bps)
                mode_a_ev REAL, -- Taker/Taker
                mode_b_ev REAL, -- Maker/Taker
                mode_c_ev REAL, -- Maker/Maker
                
                is_win_mode_a INTEGER DEFAULT 0
            )
        ''')
        conn.commit()
        conn.close()
        
    def log_signal(self, tier, side, reason, probs, entry_price, expected_move_bps):
        """Logs a new signal as soon as the engine triggers it."""
        signal_id = f"sig_{int(time.time() * 1000)}"
        timestamp = datetime.utcnow().isoformat()
        
        # Calculate Maker Limit Price (Assuming we try to get filled exactly at the mid/entry price, or slightly better)
        # We will log the intended limit price
        maker_limit_price = entry_price
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO shadow_signals 
            (signal_id, timestamp, tier, side, reason, p_big_move, p_tradable, p_fail_fast, expected_move_bps, entry_price, maker_limit_price)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            signal_id, timestamp, tier, side, reason, 
            probs.get('p_big_move'), probs.get('p_tradable'), probs.get('p_fail_fast'),
            expected_move_bps, entry_price, maker_limit_price
        ))
        conn.commit()
        conn.close()
        return signal_id
        
    def resolve_signal(self, signal_id, exit_prices, maker_data=None):
        """
        Resolves a signal with extended horizons and maker fill data.
        exit_prices: dict like {'5m': val, '15m': val, '30m': val, '60m': val}
        maker_data: dict like {'filled': 1, 'time_sec': 45, 'adverse_bps': 2.5}
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT side, entry_price, maker_limit_price FROM shadow_signals WHERE signal_id = ?', (signal_id,))
        row = cursor.fetchone()
        if not row:
            return
            
        side_str, entry_price, maker_limit_price = row
        side_mult = 1 if side_str == "LONG" else -1
        
        maker_data = maker_data or {'filled': 0, 'time_sec': None, 'adverse_bps': None}
        
        # Default Exit is 30m for EV evaluation in shadow
        exit_price_eval = exit_prices.get('30m', exit_prices.get('15m', entry_price))
        
        # Taker / Taker = 14 bps slip
        gross_ret_pct = ((exit_price_eval - entry_price) / entry_price) * side_mult
        mode_a_ev = (gross_ret_pct * 10000) - 14.0
        
        # Maker / Taker = 7 bps slip
        mode_b_ev = (gross_ret_pct * 10000) - 7.0 if maker_data['filled'] else 0.0
        
        # Maker / Maker = 0 bps slip
        mode_c_ev = (gross_ret_pct * 10000) - 0.0 if maker_data['filled'] else 0.0
        
        is_win_mode_a = 1 if mode_a_ev > 0 else 0
        
        cursor.execute('''
            UPDATE shadow_signals
            SET resolved = 1,
                exit_price_5m = ?, exit_price_15m = ?, exit_price_30m = ?, exit_price_60m = ?,
                maker_filled = ?, maker_time_to_fill_sec = ?, adverse_excursion_after_fill_bps = ?,
                mode_a_ev = ?, mode_b_ev = ?, mode_c_ev = ?, is_win_mode_a = ?
            WHERE signal_id = ?
        ''', (
            exit_prices.get('5m'), exit_prices.get('15m'), exit_prices.get('30m'), exit_prices.get('60m'),
            maker_data['filled'], maker_data['time_sec'], maker_data['adverse_bps'],
            mode_a_ev, mode_b_ev, mode_c_ev, is_win_mode_a, signal_id
        ))
        
        conn.commit()
        conn.close()

    def generate_calibration_report(self):
        """Generates the required Tier Calibration Report."""
        conn = sqlite3.connect(self.db_path)
        df = pd.read_sql_query("SELECT * FROM shadow_signals WHERE resolved = 1", conn)
        conn.close()
        
        if len(df) == 0:
            return "No resolved signals yet."
            
        report = df.groupby('tier').agg(
            total_signals=('signal_id', 'count'),
            maker_fill_rate=('maker_filled', lambda x: x.mean() * 100),
            win_rate_taker=('is_win_mode_a', lambda x: x.mean() * 100),
            avg_ev_taker_taker=('mode_a_ev', 'mean'),
            avg_ev_maker_taker=('mode_b_ev', 'mean'),
            avg_ev_maker_maker=('mode_c_ev', 'mean')
        ).reset_index()
        
        return report

# Example usage hook
if __name__ == "__main__":
    logger = LiveShadowLogger("test_shadow.db")
    print("Live Shadow Logger Initialized.")
