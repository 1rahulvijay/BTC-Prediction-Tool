import asyncio
import json
import logging
import requests
import time
import websockets
from datetime import datetime

logger = logging.getLogger(__name__)

GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events?active=true&closed=false&limit=100"
CLOB_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

class PolymarketClient:
    def __init__(self):
        self.markets = {} # token_id -> market_info
        self.orderbooks = {} # token_id -> orderbook_state
        self.market_stats = {} # market_id -> stats
        self.ws = None

    def discover_markets(self):
        """Fetch active BTC markets, especially hourly/daily short-term ones."""
        try:
            res = requests.get(GAMMA_EVENTS_URL, timeout=10)
            res.raise_for_status()
            events = res.json()
            btc_markets = []
            
            for event in events:
                if 'bitcoin' not in event.get('title', '').lower() and 'btc' not in event.get('title', '').lower():
                    continue
                for market in event.get('markets', []):
                    if not market.get('active') or market.get('closed'):
                        continue
                    
                    # Parse token IDs
                    try:
                        tokens = json.loads(market.get('clobTokenIds', '[]'))
                        outcomes = json.loads(market.get('outcomes', '[]'))
                    except Exception:
                        continue
                        
                    if len(tokens) >= 2 and outcomes[0] == 'Yes' and outcomes[1] == 'No':
                        market_data = {
                            'id': market['id'],
                            'question': market['question'],
                            'slug': market['slug'],
                            'end_date': market.get('endDate'),
                            'yes_token': tokens[0],
                            'no_token': tokens[1],
                            'reference_price': self._extract_reference_price(market['question']),
                            'spread': market.get('spread', 0),
                        }
                        btc_markets.append(market_data)
                        
            # Sort by end date (closest first)
            def parse_date(date_str):
                if not date_str:
                    return float('inf')
                try:
                    return datetime.fromisoformat(date_str.replace('Z', '+00:00')).timestamp()
                except Exception:
                    return float('inf')
                    
            btc_markets.sort(key=lambda m: parse_date(m['end_date']))
            
            # Keep top 10 closest markets
            self.markets = {}
            for m in btc_markets[:10]:
                self.markets[m['yes_token']] = m
                self.markets[m['no_token']] = m
                import database
                database.log_polymarket_market(m)
            
            logger.info(f"Discovered {len(btc_markets)} BTC markets, tracking top {len(self.markets)//2}")
            return btc_markets[:10]
            
        except Exception as e:
            logger.error(f"Failed to discover Polymarket markets: {e}")
            return []

    def _extract_reference_price(self, question: str) -> float:
        """Naive extraction of price from question like 'Will Bitcoin hit $150k...'"""
        import re
        match = re.search(r'\$?(\d{2,3}(?:,\d{3})*(?:\.\d+)?|\d{2,3}k)', question, re.IGNORECASE)
        if match:
            val = match.group(1).replace(',', '')
            if val.lower().endswith('k'):
                return float(val[:-1]) * 1000
            return float(val)
        return 0.0

    async def connect_ws(self):
        """Connect to public CLOB websocket and subscribe to discovered markets."""
        if not self.markets:
            logger.warning("No Polymarket markets to subscribe to.")
            return
            
        token_ids = list(self.markets.keys())
        
        while True:
            try:
                async with websockets.connect(
                    CLOB_WS_URL, ping_interval=None, ping_timeout=None
                ) as ws:
                    self.ws = ws
                    subscribe_msg = {
                        "assets_ids": token_ids,
                        "type": "market"
                    }
                    await ws.send(json.dumps(subscribe_msg))
                    logger.info("Subscribed to Polymarket CLOB WS")
                    
                    async for message in ws:
                        self.handle_ws_message(json.loads(message))
            except Exception as e:
                logger.error(f"Polymarket WS error: {e}, reconnecting in 5s...")
                await asyncio.sleep(5)

    def handle_ws_message(self, msg: dict):
        if isinstance(msg, list):
            for update in msg:
                self._update_orderbook(update)
        else:
            self._update_orderbook(msg)
            
    def _update_orderbook(self, update: dict):
        asset_id = update.get("asset_id")
        if not asset_id or asset_id not in self.markets:
            return
            
        if asset_id not in self.orderbooks:
            self.orderbooks[asset_id] = {'bids': [], 'asks': []}
            
        if 'bids' in update:
            self.orderbooks[asset_id]['bids'] = update['bids']
        if 'asks' in update:
            self.orderbooks[asset_id]['asks'] = update['asks']
            
        self._calculate_stats(asset_id)
            
    def _calculate_stats(self, asset_id: str):
        market = self.markets[asset_id]
        market_id = market['id']
        is_yes = asset_id == market['yes_token']
        
        ob = self.orderbooks[asset_id]
        bids = [float(b['price']) for b in ob['bids']] if ob.get('bids') else []
        asks = [float(a['price']) for a in ob['asks']] if ob.get('asks') else []
        bid_vols = [float(b['size']) for b in ob['bids']] if ob.get('bids') else []
        ask_vols = [float(a['size']) for a in ob['asks']] if ob.get('asks') else []
        
        best_bid = max(bids) if bids else 0
        best_ask = min(asks) if asks else 1
        spread = best_ask - best_bid
        
        bid_depth = sum(bid_vols)
        ask_depth = sum(ask_vols)
        total_depth = bid_depth + ask_depth
        imbalance = (bid_depth - ask_depth) / total_depth if total_depth > 0 else 0
        
        if market_id not in self.market_stats:
            self.market_stats[market_id] = {'id': market_id, 'slug': market['slug']}
            
        prefix = 'yes' if is_yes else 'no'
        self.market_stats[market_id].update({
            f'{prefix}_best_bid': best_bid,
            f'{prefix}_best_ask': best_ask,
            f'{prefix}_spread': spread,
            f'{prefix}_imbalance': imbalance,
            f'{prefix}_depth': bid_depth + ask_depth,
            'timestamp': time.time(),
            'question': market['question'],
            'reference_price': market['reference_price'],
            'end_date': market['end_date']
        })
        
        # Log to DuckDB if we have both yes/no populated reasonably
        stats = self.market_stats[market_id]
        if 'yes_best_bid' in stats and 'no_best_bid' in stats:
            quote_data = {
                "market_id": market_id,
                "yes_best_bid": stats.get("yes_best_bid", 0.0),
                "yes_best_ask": stats.get("yes_best_ask", 1.0),
                "no_best_bid": stats.get("no_best_bid", 0.0),
                "no_best_ask": stats.get("no_best_ask", 1.0),
                "yes_spread": stats.get("yes_spread", 1.0),
                "yes_imbalance": stats.get("yes_imbalance", 0.0)
            }
            import database
            database.log_polymarket_quote(quote_data)

    def get_summary(self):
        import copy
        return copy.deepcopy(list(self.market_stats.values()))
