import time
import logging
import database

logger = logging.getLogger(__name__)

class PolymarketSimulator:
    def __init__(self, min_edge_threshold=0.04):
        self.min_edge_threshold = min_edge_threshold
        
    def layer_4_trade_filter(self, fair_prob: float, executable_ask: float, executable_bid: float, features: dict) -> dict:
        """
        Layer 4 cost-aware trading filter.
        Decides BUY YES, BUY NO, EXIT, or AVOID.
        """
        spread = features.get('yes_spread', 0.10)
        
        # Penalize edge based on spread width and a fixed minimum barrier
        uncertainty_buffer = spread * 0.5
        
        yes_edge = fair_prob - executable_ask - uncertainty_buffer
        # NO probability is (1 - fair_prob)
        # Executing a NO means buying NO shares at NO's ask price
        # NO's ask price is (1 - YES bid price)
        no_executable_ask = 1.0 - executable_bid
        no_fair_prob = 1.0 - fair_prob
        
        no_edge = no_fair_prob - no_executable_ask - uncertainty_buffer
        
        action = 'AVOID'
        reason = 'No edge exceeds the safety threshold after costs.'
        
        if yes_edge > self.min_edge_threshold and yes_edge > no_edge:
            action = 'BUY YES'
            reason = f'YES edge ({yes_edge:.3f}) > threshold ({self.min_edge_threshold})'
        elif no_edge > self.min_edge_threshold:
            action = 'BUY NO'
            reason = f'NO edge ({no_edge:.3f}) > threshold ({self.min_edge_threshold})'
            
        return {
            'action': action,
            'yes_edge': yes_edge,
            'no_edge': no_edge,
            'reason': reason
        }

    def simulate_paper_trade(self, market_id: str, prediction_id: str, action_dict: dict, quote: dict):
        action = action_dict['action']
        if action == 'AVOID':
            return None
            
        executable_ask = quote.get('yes_best_ask', 1.0)
        executable_bid = quote.get('yes_best_bid', 0.0)
        
        fill_price = executable_ask if action == 'BUY YES' else (1.0 - executable_bid)
        
        trade = {
            'trade_id': f"pm_trade_{int(time.time()*1000)}",
            'prediction_id': prediction_id,
            'market_id': market_id,
            'action': action,
            'fill_price': fill_price,
            'size': 100.0,
            'fees': fill_price * 100.0 * 0.01, # Example 1% fee on Polymarket
            'slippage': 0.005, # Assumed 0.5c slippage
            'timestamp': int(time.time() * 1000)
        }
        
        database.log_polymarket_paper_trade(trade)
        return trade
