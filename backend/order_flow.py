"""
Order Flow Analysis
CVD, Order Book Imbalance, Trade Classification, Whale Detection.
"""

import time
from collections import deque


class OrderFlowAnalyzer:
    """Tracks real-time order flow from aggTrade and depth streams."""

    def __init__(self, whale_threshold_btc: float = 0.5):
        self.cvd = 0.0
        self.cvd_history: deque = deque(maxlen=5000)
        # 20000 trades comfortably covers the 5-minute (cvd_5m) and large-trade windows even
        # during high-intensity bursts, so the LIVE time-window sums match the full-window
        # BACKFILL (train/serve consistency). At ~900 trades/min that's ~22 min of history;
        # ~6 MB of dicts. (Was 3000 ≈ 3.4 min — it silently truncated cvd_5m's 5-min window.)
        self.recent_trades: deque = deque(maxlen=20000)
        self.imbalance_history: deque = deque(maxlen=3000)
        self.spread_history: deque = deque(maxlen=1000)
        self.whale_threshold = whale_threshold_btc
        self.last_depth = None
        
        # Microstructure tracking
        self.wall_state = {
            "bid": {"price": 0, "size": 0, "first_seen": 0, "initial_size": 0}, 
            "ask": {"price": 0, "size": 0, "first_seen": 0, "initial_size": 0}
        }
        self.last_bid_volume = 0
        self.last_ask_volume = 0
        self.queue_stats = {
            "bids_added": 0, "bids_canceled": 0, "bids_executed": 0,
            "asks_added": 0, "asks_canceled": 0, "asks_executed": 0
        }
        self.pending_executions = {"bid": 0.0, "ask": 0.0}
        self.local_extremes = {"high": 0.0, "low": float('inf'), "last_break": 0}
        # Last-sweep timestamps (ms); 0 = none. Stored as timestamps (not consume-on-read
        # booleans) so the per-candle feature snapshot reliably sees a sweep that occurred
        # during the candle instead of one that a prior get_summary() call already erased.
        self.liquidity_sweeps = {"bullish": 0.0, "bearish": 0.0}
        self.wall_migrations = {"bids_moved_up": 0, "asks_moved_down": 0}

        # Spoofing detection: track depth snapshots for appearing/disappearing walls
        self.depth_snapshots: deque = deque(maxlen=10)
        self.spoof_score = 0.0  # exponential-decay spoof indicator (0-1)

        # Absorption detection
        self.absorption_ratio = 0.0  # high volume + flat price = institutional accumulation

        # Queue consumption dynamics (per-second rates)
        self._last_queue_time = time.time()
        self.bid_consume_rate = 0.0
        self.ask_consume_rate = 0.0

        # Large-trade flagging — streaming EWMA of trade notional, SAME rule/constants as the
        # historical backfill (trade_features.large_trade_per_bar), so live == backfill.
        import trade_features as _tf
        self._lt_alpha = _tf.LARGE_TRADE_EWMA_ALPHA
        self._lt_mult = _tf.LARGE_TRADE_MULT
        self._lt_ewma = 0.0

        # Streaming VPIN — SAME algorithm/constants as the backfill (trade_features.
        # vpin_buckets): equal-volume buckets, trades split across boundaries, vpin =
        # rolling-sum(|buy−sell|, N) / (N × bucket_volume). 0 until N buckets exist
        # (matches the backfill's NaN→0 bar mapping). Warms up ~1h after boot.
        self._vpin_bucket_vol = _tf.DEFAULT_BUCKET_VOLUME_BTC
        self._vpin_n = _tf.DEFAULT_ROLLING_BUCKETS
        self._vpin_imb: deque = deque(maxlen=_tf.DEFAULT_ROLLING_BUCKETS)
        self._vpin_buy = 0.0
        self._vpin_sell = 0.0
        self._vpin_fill = 0.0
        self.vpin = 0.0

        # get_summary() THROTTLE — it is called on EVERY trade + depth update (~25-110/s) but
        # each call scans recent_trades (20000) ~7x (the time-window CVDs). Its consumers (the
        # main loop ~every 3s, the per-candle snapshot) only need it periodically, so cache the
        # result for a short TTL → the heavy scan runs ~4x/s instead of ~100x/s, keeping the
        # event loop free. (Without this, the 20000-deep scans saturate a core at high volume.)
        self._summary_cache = None
        self._summary_cache_ms = 0.0
        self._summary_ttl_ms = 250.0

        # Microprice + best-level OFI (research #1/#3), computed from the EXISTING depth20
        # stream. RECORDED into signal_history (not yet a model feature) so they accrue for a
        # future L2 ("V2") retrain — they are live-only / not cheaply backfillable on Binance.
        self._prev_bb = self._prev_bb_q = self._prev_ba = self._prev_ba_q = None
        self._ofi_ewma = 0.0
        self.microprice_edge_bps = 0.0
        self.ofi_best = 0.0

    def process_trade(self, trade: dict) -> dict:
        """
        Process an aggTrade event.
        is_buyer_maker=True means taker was SELLING.
        """
        is_buy = not trade["is_buyer_maker"]
        signed_qty = trade["quantity"] if is_buy else -trade["quantity"]

        self.cvd += signed_qty
        self.cvd_history.append({
            "time": trade["time"],
            "cvd": self.cvd,
            "delta": signed_qty,
        })

        notional = trade["price"] * trade["quantity"]
        # Large-trade flag: notional > MULT × EWMA-of-PRIOR-notional (causal). EWMA updated
        # after the check. Identical recursion to the backfill's pandas ewm(adjust=False).
        prior_ewma = self._lt_ewma if self._lt_ewma > 0 else notional
        is_large = notional > (self._lt_mult * prior_ewma)
        self._lt_ewma = self._lt_alpha * notional + (1.0 - self._lt_alpha) * prior_ewma
        enriched = {
            **trade,
            "is_buy": is_buy,
            "value": notional,
            "is_whale": trade["quantity"] >= self.whale_threshold,
            "is_large": is_large,
            "large_signed_notional": (notional if is_buy else -notional) if is_large else 0.0,
        }
        self.recent_trades.append(enriched)

        # Track execution volume to split from cancels
        if is_buy:
            self.pending_executions["ask"] += trade["quantity"]
            self.queue_stats["asks_executed"] += trade["quantity"]
        else:
            self.pending_executions["bid"] += trade["quantity"]
            self.queue_stats["bids_executed"] += trade["quantity"]

        # Streaming VPIN: fill equal-volume buckets (split across boundaries, identical to
        # trade_features.vpin_buckets); on each completed bucket update the rolling VPIN.
        _rem = trade["quantity"]
        while _rem > 1e-15:
            _cap = self._vpin_bucket_vol - self._vpin_fill
            _take = _rem if _rem < _cap else _cap
            if is_buy:
                self._vpin_buy += _take
            else:
                self._vpin_sell += _take
            self._vpin_fill += _take
            _rem -= _take
            if self._vpin_fill >= self._vpin_bucket_vol - 1e-12:
                self._vpin_imb.append(abs(self._vpin_buy - self._vpin_sell))
                self._vpin_buy = self._vpin_sell = self._vpin_fill = 0.0
                if len(self._vpin_imb) == self._vpin_n:
                    self.vpin = sum(self._vpin_imb) / (self._vpin_n * self._vpin_bucket_vol)
        
        # Liquidity Sweep Detection
        price = float(trade["price"])
        if price > self.local_extremes["high"]:
            self.local_extremes["high"] = price
            self.local_extremes["last_break"] = trade["time"]
        if price < self.local_extremes["low"]:
            self.local_extremes["low"] = price
            self.local_extremes["last_break"] = trade["time"]
            
        # If we broke a local low recently (within 5 seconds), and we see massive buying (reversal)
        if (trade["time"] - self.local_extremes["last_break"]) < 5000:
            if price > self.local_extremes["low"] * 1.0005 and is_buy and enriched["is_whale"]:
                self.liquidity_sweeps["bullish"] = trade["time"]
            elif price < self.local_extremes["high"] * 0.9995 and not is_buy and enriched["is_whale"]:
                self.liquidity_sweeps["bearish"] = trade["time"]
        
        return enriched

    def process_depth(self, depth: dict) -> dict:
        """Process a depth20 snapshot."""
        self.last_depth = depth

        bid_depth = sum(q for _, q in depth["bids"])
        ask_depth = sum(q for _, q in depth["asks"])
        total = bid_depth + ask_depth
        imbalance = (bid_depth - ask_depth) / total if total > 0 else 0.0

        # Queue Dynamics
        now = time.time()
        dt = max(0.01, now - self._last_queue_time)
        if self.last_bid_volume > 0:
            bid_diff = bid_depth - self.last_bid_volume
            ask_diff = ask_depth - self.last_ask_volume
            if bid_diff > 0:
                self.queue_stats["bids_added"] += bid_diff
            else:
                removed = abs(bid_diff)
                executed = min(removed, self.pending_executions["bid"])
                self.pending_executions["bid"] -= executed
                canceled = removed - executed
                self.queue_stats["bids_canceled"] += canceled
                
            if ask_diff > 0:
                self.queue_stats["asks_added"] += ask_diff
            else:
                removed = abs(ask_diff)
                executed = min(removed, self.pending_executions["ask"])
                self.pending_executions["ask"] -= executed
                canceled = removed - executed
                self.queue_stats["asks_canceled"] += canceled
            
            # Per-second consumption rates (EWMA smoothed)
            alpha_q = 0.3
            raw_bid_rate = abs(min(0, bid_diff)) / dt
            raw_ask_rate = abs(min(0, ask_diff)) / dt
            self.bid_consume_rate = self.bid_consume_rate * (1 - alpha_q) + raw_bid_rate * alpha_q
            self.ask_consume_rate = self.ask_consume_rate * (1 - alpha_q) + raw_ask_rate * alpha_q
            
            # Decay the rolling queue stats slightly to act like a leaky bucket
            self.queue_stats["bids_added"] *= 0.95
            self.queue_stats["bids_canceled"] *= 0.95
            self.queue_stats["bids_executed"] *= 0.95
            self.queue_stats["asks_added"] *= 0.95
            self.queue_stats["asks_canceled"] *= 0.95
            self.queue_stats["asks_executed"] *= 0.95

        self.last_bid_volume = bid_depth
        self.last_ask_volume = ask_depth
        self._last_queue_time = now

        # Calculate multi-level OBI
        def calc_obi(levels: int) -> float:
            b_vol = sum(q for _, q in depth["bids"][:levels])
            a_vol = sum(q for _, q in depth["asks"][:levels])
            tot = b_vol + a_vol
            return (b_vol - a_vol) / tot if tot > 0 else 0.0

        obi_5 = calc_obi(5)
        obi_10 = calc_obi(10)
        obi_20 = calc_obi(20)

        self.imbalance_history.append({
            "time": time.time(),
            "imbalance": imbalance,
            "obi_5": obi_5,
            "obi_10": obi_10,
            "obi_20": obi_20,
            "bid_depth": bid_depth,
            "ask_depth": ask_depth,
        })

        # Spread
        if depth["bids"] and depth["asks"]:
            best_bid = float(depth["bids"][0][0])
            best_ask = float(depth["asks"][0][0])
            bb_q = float(depth["bids"][0][1])
            ba_q = float(depth["asks"][0][1])
            spread = best_ask - best_bid
            mid = (best_bid + best_ask) / 2.0
            spread_bps = (spread / mid) * 10000 if mid > 0 else 0

            self.spread_history.append({
                "time": time.time(),
                "spread": spread,
                "spread_bps": spread_bps,
                "mid_price": mid,
            })

            # ── Microprice edge + best-level OFI (research #1/#3) ──────────────
            # Microprice weights the mid toward the side with LESS size (where price is
            # likelier to move). Edge in bps = directional pressure.
            denom_mp = bb_q + ba_q
            if denom_mp > 0 and mid > 0:
                microprice = (best_ask * bb_q + best_bid * ba_q) / denom_mp
                self.microprice_edge_bps = (microprice - mid) / mid * 10000.0
            # Top-of-book OFI (Cont et al.): contribution of best-bid/ask changes between
            # consecutive snapshots, normalized by best depth, EWMA-smoothed to a flow.
            if self._prev_bb is not None:
                if best_bid > self._prev_bb:
                    bc = bb_q
                elif best_bid == self._prev_bb:
                    bc = bb_q - self._prev_bb_q
                else:
                    bc = -self._prev_bb_q
                if best_ask < self._prev_ba:
                    ac = -ba_q
                elif best_ask == self._prev_ba:
                    ac = self._prev_ba_q - ba_q
                else:
                    ac = self._prev_ba_q
                ofi = bc + ac
                norm = denom_mp if denom_mp > 0 else 1.0
                self._ofi_ewma = 0.1 * (ofi / norm) + 0.9 * self._ofi_ewma
                self.ofi_best = max(-1.0, min(1.0, self._ofi_ewma))
            self._prev_bb, self._prev_bb_q = best_bid, bb_q
            self._prev_ba, self._prev_ba_q = best_ask, ba_q

        # ── Spoofing Detection ──
        # Track large orders across depth snapshots. If a wall > 2x median
        # appears and vanishes within 3 snapshots without price crossing it, flag it.
        self.spoof_score *= 0.92  # exponential decay
        bid_walls = [(float(p), float(q)) for p, q in depth["bids"] if float(q) > bid_depth * 0.15]
        ask_walls = [(float(p), float(q)) for p, q in depth["asks"] if float(q) > ask_depth * 0.15]
        snap = {"time": now, "bid_walls": set(round(p, 1) for p, _ in bid_walls),
                "ask_walls": set(round(p, 1) for p, _ in ask_walls)}
        if len(self.depth_snapshots) >= 3:
            old = self.depth_snapshots[-3]
            # Walls that appeared and then vanished (never traded through)
            mid_price = self.get_mid_price() or 0
            for side, old_walls, new_walls in [
                ("bid", old["bid_walls"], snap["bid_walls"]),
                ("ask", old["ask_walls"], snap["ask_walls"])]:
                vanished = old_walls - new_walls
                appeared = new_walls - old_walls
                for price_level in vanished:
                    # If price never reached this wall, it was pulled — likely spoof
                    if mid_price and ((side == "bid" and mid_price > price_level) or
                                      (side == "ask" and mid_price < price_level)):
                        self.spoof_score = min(1.0, self.spoof_score + 0.25)
                        
                        # Detect Wall Migration (canceled far, placed near)
                        for new_p in appeared:
                            if side == "bid" and new_p > price_level and new_p < mid_price:
                                self.wall_migrations["bids_moved_up"] += 1
                            elif side == "ask" and new_p < price_level and new_p > mid_price:
                                self.wall_migrations["asks_moved_down"] += 1
                                
        self.depth_snapshots.append(snap)
        
        # Decay wall migrations
        self.wall_migrations["bids_moved_up"] *= 0.98
        self.wall_migrations["asks_moved_down"] *= 0.98

        # ── Absorption Detection ──
        # High volume on one side, but price barely moves → institutional accumulation
        if len(self.recent_trades) >= 10 and len(self.spread_history) >= 2:
            window = list(self.recent_trades)[-20:]
            buy_vol = sum(t["quantity"] for t in window if t["is_buy"])
            sell_vol = sum(t["quantity"] for t in window if not t["is_buy"])
            vol_imbalance = abs(buy_vol - sell_vol) / (buy_vol + sell_vol + 1e-9)
            prices = [t["price"] for t in window]
            price_range = (max(prices) - min(prices)) / (max(prices) + 1e-9)
            # High volume imbalance with minimal price movement = absorption
            if price_range > 0:
                self.absorption_ratio = min(5.0, vol_imbalance / (price_range * 100 + 1e-9))
            else:
                self.absorption_ratio = vol_imbalance * 5.0  # flat price = strong absorption

        return {"imbalance": imbalance, "bid_depth": bid_depth, "ask_depth": ask_depth}

    def get_trade_intensity(self, window_seconds: float = 5.0) -> float:
        now = time.time() * 1000  # trades use ms
        cutoff = now - window_seconds * 1000
        recent = [t for t in self.recent_trades if t["time"] > cutoff]
        return len(recent) / window_seconds if window_seconds > 0 else 0

    def get_buy_sell_ratio(self, n: int = 50) -> dict:
        trades = list(self.recent_trades)[-n:]
        if not trades:
            return {"buy_ratio": 0.5, "sell_ratio": 0.5}
        buys = sum(1 for t in trades if t["is_buy"])
        return {
            "buy_ratio": buys / len(trades),
            "sell_ratio": (len(trades) - buys) / len(trades),
        }

    def get_cvd_change(self, n: int = 30) -> float:
        hist = list(self.cvd_history)
        if len(hist) < 2:
            return 0.0
        recent = hist[-n:]
        return recent[-1]["cvd"] - recent[0]["cvd"]

    def get_time_based_cvd(self, window_seconds: float) -> float:
        """Calculate CVD over a precise time window."""
        now = time.time() * 1000
        cutoff = now - (window_seconds * 1000)
        
        # Filter trades within the window
        valid_trades = [t for t in self.recent_trades if t["time"] >= cutoff]
        if not valid_trades:
            return 0.0
            
        cvd_change = 0.0
        for t in valid_trades:
            signed_qty = t["quantity"] if t["is_buy"] else -t["quantity"]
            cvd_change += signed_qty
            
        return cvd_change

    def get_current_imbalance(self) -> float:
        if not self.imbalance_history:
            return 0.0
        return list(self.imbalance_history)[-1]["imbalance"]
        
    def get_time_based_obi(self, window_seconds: float) -> float:
        now = time.time()
        cutoff = now - window_seconds
        valid_obi = [h["imbalance"] for h in self.imbalance_history if h["time"] >= cutoff]
        if not valid_obi:
            return self.get_current_imbalance()
        return sum(valid_obi) / len(valid_obi)

    def get_imbalance_momentum(self, window: int = 10) -> float:
        hist = list(self.imbalance_history)
        if len(hist) < window:
            return 0.0
        return hist[-1]["imbalance"] - hist[-window]["imbalance"]

    def get_current_spread_bps(self) -> float:
        if not self.spread_history:
            return 0.0
        return list(self.spread_history)[-1]["spread_bps"]

    def get_mid_price(self) -> float | None:
        if not self.spread_history:
            return None
        return list(self.spread_history)[-1]["mid_price"]

    def get_whale_trades(self, n: int = 10) -> list[dict]:
        return [t for t in self.recent_trades if t["is_whale"]][-n:]

    def compute_liquidity_walls(self) -> dict:
        current_price = self.get_mid_price() or 60000.0
        if not self.last_depth:
            return {
                'largest_bid_wall_qty': 0,
                'largest_ask_wall_qty': 0,
                'distance_to_bid_wall': 0,
                'distance_to_ask_wall': 0,
                'wall_imbalance': 0,
                'bid_wall_price': current_price,
                'ask_wall_price': current_price,
            }

        bids = self.last_depth.get('bids', [])
        asks = self.last_depth.get('asks', [])
        price_range = current_price * 0.005

        relevant_bids = [(float(p), float(q)) for p, q in bids if current_price - price_range <= float(p) <= current_price]
        relevant_asks = [(float(p), float(q)) for p, q in asks if current_price <= float(p) <= current_price + price_range]

        largest_bid = max(relevant_bids, key=lambda x: x[1]) if relevant_bids else (current_price, 0)
        largest_ask = max(relevant_asks, key=lambda x: x[1]) if relevant_asks else (current_price, 0)

        bid_wall_qty = largest_bid[1]
        ask_wall_qty = largest_ask[1]
        total_wall = bid_wall_qty + ask_wall_qty + 1e-9
        
        # Track Wall Persistence
        now = time.time()
        for side, wall_price, wall_size in [("bid", largest_bid[0], bid_wall_qty), ("ask", largest_ask[0], ask_wall_qty)]:
            state = self.wall_state[side]
            if state["price"] == wall_price and wall_size > 0:
                state["size"] = wall_size
            else:
                state["price"] = wall_price
                state["size"] = wall_size
                state["first_seen"] = now
                state["initial_size"] = wall_size
                
        bid_persistence = now - self.wall_state["bid"]["first_seen"]
        ask_persistence = now - self.wall_state["ask"]["first_seen"]
        
        bid_growth = (bid_wall_qty / self.wall_state["bid"]["initial_size"]) if self.wall_state["bid"]["initial_size"] > 0 else 1.0
        ask_growth = (ask_wall_qty / self.wall_state["ask"]["initial_size"]) if self.wall_state["ask"]["initial_size"] > 0 else 1.0

        return {
            'largest_bid_wall_qty': bid_wall_qty,
            'largest_ask_wall_qty': ask_wall_qty,
            'distance_to_bid_wall': current_price - largest_bid[0],
            'distance_to_ask_wall': largest_ask[0] - current_price,
            'wall_imbalance': (bid_wall_qty - ask_wall_qty) / total_wall,
            'bid_wall_price': largest_bid[0],
            'ask_wall_price': largest_ask[0],
            'bid_wall_persistence': bid_persistence,
            'ask_wall_persistence': ask_persistence,
            'bid_wall_growth': bid_growth,
            'ask_wall_growth': ask_growth,
        }

    def detect_liquidity_vacuum(self) -> dict:
        if not self.last_depth or len(self.spread_history) < 20:
            return {'spread_expansion_ratio': 1.0, 'vacuum_detected': False}
            
        bids = self.last_depth.get('bids', [])[:5]
        asks = self.last_depth.get('asks', [])[:5]

        avg_spread = sum(x["spread"] for x in list(self.spread_history)[-20:]) / 20.0
        current_spread = (float(asks[0][0]) - float(bids[0][0])) if bids and asks else 0

        spread_expansion = (current_spread / avg_spread) if avg_spread > 0 else 1.0

        return {
            'spread_expansion_ratio': spread_expansion,
            'vacuum_detected': spread_expansion > 2.0
        }

    def get_summary(self) -> dict:
        # Throttle: return the cached summary if computed within the TTL. get_summary is called
        # on every trade/depth (~25-110/s); the heavy time-window scans below are O(20000), so
        # without this they saturate a core. Consumers only need ~per-second freshness.
        _now_ms = time.time() * 1000.0
        if self._summary_cache is not None and (_now_ms - self._summary_cache_ms) < self._summary_ttl_ms:
            return self._summary_cache

        bs = self.get_buy_sell_ratio()

        last_imb = {}
        if self.imbalance_history:
            last_imb = list(self.imbalance_history)[-1]
            
        # A sweep is a transient event; report it as "active" for the candle it occurred
        # in rather than consuming it on read. The old code reset these flags on EVERY
        # get_summary() call (~every 2s), so the per-candle feature snapshot almost always
        # saw False — making liquidity_sweep_* dead, zero-variance columns the models
        # could never learn from. Now: 1.0 while a sweep is within the last 60s (≈ one 1m
        # candle), else 0.0. No reset, so concurrent readers don't erase each other's view.
        _SWEEP_TTL_MS = 60_000.0
        _now_ms = time.time() * 1000.0
        sweep_bull = 1.0 if (_now_ms - self.liquidity_sweeps["bullish"]) < _SWEEP_TTL_MS else 0.0
        sweep_bear = 1.0 if (_now_ms - self.liquidity_sweeps["bearish"]) < _SWEEP_TTL_MS else 0.0

        # Queue pressure: ratio of bid-to-ask consumption rate
        total_consume = self.bid_consume_rate + self.ask_consume_rate + 1e-9
        queue_pressure = (self.bid_consume_rate - self.ask_consume_rate) / total_consume

        # Large-trade flow over the last 60s — SAME definition as the backfill
        # (trade_features.large_trade_per_bar): big-trade signed notional as a fraction of
        # total notional, and the large-trade buy/sell imbalance.
        _cut = time.time() * 1000.0 - 60_000.0
        _lw = [t for t in self.recent_trades if t["time"] >= _cut]
        _tot_n = sum(t.get("value", 0.0) for t in _lw)
        _ls = sum(t.get("large_signed_notional", 0.0) for t in _lw)
        _lb = sum(t["value"] for t in _lw if t.get("is_large") and t.get("is_buy"))
        _lsell = sum(t["value"] for t in _lw if t.get("is_large") and not t.get("is_buy"))
        large_trade_delta = (_ls / _tot_n) if _tot_n > 0 else 0.0
        large_trade_imbalance = ((_lb - _lsell) / (_lb + _lsell)) if (_lb + _lsell) > 0 else 0.0

        result = {
            "cvd": self.cvd,
            "cvd_change": self.get_cvd_change(),
            "cvd_1s": self.get_time_based_cvd(1),
            "cvd_5s": self.get_time_based_cvd(5),
            "cvd_15s": self.get_time_based_cvd(15),
            "cvd_30s": self.get_time_based_cvd(30),
            "cvd_1m": self.get_time_based_cvd(60),
            "cvd_5m": self.get_time_based_cvd(300),
            "imbalance": self.get_current_imbalance(),
            "obi_5": last_imb.get("obi_5", 0.0),
            "obi_10": last_imb.get("obi_10", 0.0),
            "obi_20": last_imb.get("obi_20", 0.0),
            "obi_1s": self.get_time_based_obi(1),
            "obi_5s": self.get_time_based_obi(5),
            "obi_15s": self.get_time_based_obi(15),
            "imbalance_momentum": self.get_imbalance_momentum(),
            "trade_intensity": self.get_trade_intensity(),
            "buy_ratio": bs["buy_ratio"],
            "sell_ratio": bs["sell_ratio"],
            "spread_bps": self.get_current_spread_bps(),
            "mid_price": self.get_mid_price(),
            "whale_trades": self.get_whale_trades(),
            "liquidity_walls": self.compute_liquidity_walls(),
            "liquidity_vacuum": self.detect_liquidity_vacuum(),
            "queue_depletion_rate": (self.queue_stats["bids_canceled"] + self.queue_stats["bids_executed"] + 
                                     self.queue_stats["asks_canceled"] + self.queue_stats["asks_executed"]) / 
                                    (self.queue_stats["bids_added"] + self.queue_stats["asks_added"] + 1e-9),
            "bids_added": self.queue_stats["bids_added"],
            "bids_canceled": self.queue_stats["bids_canceled"],
            "bids_executed": self.queue_stats["bids_executed"],
            "asks_added": self.queue_stats["asks_added"],
            "asks_canceled": self.queue_stats["asks_canceled"],
            "asks_executed": self.queue_stats["asks_executed"],
            "book_replenishment_rate": (self.queue_stats["bids_added"] + self.queue_stats["asks_added"]) / 
                                       (self.queue_stats["bids_canceled"] + self.queue_stats["asks_canceled"] + 1e-9),
            "liquidity_sweep_bullish": sweep_bull,
            "liquidity_sweep_bearish": sweep_bear,
            # New institutional microstructure signals
            "spoof_score": round(self.spoof_score, 4),
            "absorption_ratio": round(self.absorption_ratio, 4),
            "bid_consume_rate": round(self.bid_consume_rate, 4),
            "ask_consume_rate": round(self.ask_consume_rate, 4),
            "queue_pressure": round(queue_pressure, 4),
            "large_trade_delta": round(max(-1.0, min(1.0, large_trade_delta)), 4),
            "large_trade_imbalance": round(max(-1.0, min(1.0, large_trade_imbalance)), 4),
            # Streaming VPIN (order-flow toxicity) — same constants as the backfill, so the
            # live feature matches the historical training distribution (feature 112).
            "vpin": round(self.vpin, 4),
            # Recorded for a future L2 ("V2") retrain — not yet a model feature.
            "microprice_edge_bps": round(self.microprice_edge_bps, 4),
            "ofi_best": round(self.ofi_best, 4),
        }
        self._summary_cache = result
        self._summary_cache_ms = _now_ms
        return result

    def get_last_trades_for_tape(self, n: int = 50) -> list[dict]:
        """Return last N trades formatted for the UI tape."""
        trades = list(self.recent_trades)[-n:]
        return [
            {
                "time": t["time"],
                "price": round(t["price"], 2),
                "quantity": round(t["quantity"], 5),
                "is_buy": t["is_buy"],
                "is_whale": t["is_whale"],
                "value": round(t["value"], 2),
            }
            for t in trades
        ]
