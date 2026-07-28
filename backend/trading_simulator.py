import os
import uuid
import math
import logging
from collections import deque

import database

logger = logging.getLogger(__name__)

# Round-trip cost lever. Lowering the taker fee lets more (smaller-edge) directional
# signals clear the EV>0 gate, so the tool will surface more long/short calls instead of
# AVOID. Set BTC_TAKER_FEE_BPS (basis points) — default 4.0 bps (0.04%). Maker-only /
# zero-fee venues can set this near 0; conservative live trading can raise it.
TAKER_FEE_BPS = float(os.environ.get("BTC_TAKER_FEE_BPS", "4.0"))

class AlgorithmicExecutionRouter:
    """
    Simulates Institutional Execution Routing (TWAP / VWAP / Maker-Taker Optimization).
    Instead of crossing the spread with a single massive Market Order (Taker), 
    this slices the order and provides limit order Maker Rebate modeling.
    """
    def __init__(self, use_twap: bool = True, slices: int = 5):
        self.use_twap = use_twap
        self.slices = slices
        self.maker_fee = 0.00015  # 1.5 bps maker fee (VIP tier)
        self.taker_fee = TAKER_FEE_BPS / 10000.0  # configurable via BTC_TAKER_FEE_BPS

    def simulate_execution_cost(self, position_size_btc: float, fill_prob: float, 
                                spread_expansion: float = 1.0, avg_volume: float = 1.0, 
                                queue_position_btc: float = 0.5) -> tuple[float, float]:
        """
        Returns (effective_slippage_factor, effective_fee_rate)
        """
        if not self.use_twap or self.slices <= 1:
            slice_size = position_size_btc
        else:
            slice_size = position_size_btc / self.slices

        # Slicing the order exponentially reduces the market volume impact
        volume_impact = 1.0 + math.log1p(slice_size / (avg_volume + 1e-9)) * 0.05
        base_slippage = (1.0 / 10000.0) * spread_expansion * volume_impact

        # Queue Position Penalty: if queue depth is large compared to avg traded volume,
        # our limit order is unlikely to fill before the price moves away, forcing us to cross
        # the spread eventually. This penalty scales up the slippage factor.
        queue_penalty = 1.0 + min(2.0, max(0.0, (queue_position_btc / (avg_volume + 1e-9))))
        base_slippage *= queue_penalty

        # Maker/Taker logic based on Order Book Fill Probability
        # For strict conservatism before live paper-trading calibration,
        # we assume TAKER fees for all fills, as resting limit orders may 
        # suffer adverse selection in fast-moving regimes.
        effective_fee = self.taker_fee
            
        return base_slippage, effective_fee


class TradingSimulator:
    """
    Simulated Execution Engine with institutional-grade features:
    - Kelly-criterion position sizing (half-Kelly, capped)
    - Dynamic slippage model (spread + volume aware)
    - Fill probability estimation from order book depth
    - Rolling risk metrics (Sharpe, Sortino, VaR, consecutive losses)
    """
    def __init__(self, starting_capital=10000.0, taker_fee_rate=0.0005, base_slippage_bps=2.0):
        self.equity = starting_capital
        self.starting_capital = starting_capital
        self.taker_fee_rate = taker_fee_rate
        self.base_slippage_bps = base_slippage_bps
        
        self.calibration_status = {
            "Maker/Taker fees": "Configured (Exchange Schedule)",
            "Spread": "Live observed",
            "Slippage": "Theoretical until calibrated",
            "Fill probability": "Heuristic until paper/live fills exist",
            "Partial fills": "Simulated",
            "Latency": "Estimated unless measured end-to-end",
            "Queue position": "Approximate L2, not true L3",
            "Kelly sizing": "Research only; capped conservatively",
        }
        
        self.router = AlgorithmicExecutionRouter(use_twap=True, slices=5)
        
        self.active_trades = {}
        self.trade_history = []
        
        self.max_equity = starting_capital
        self.max_drawdown_usd = 0.0
        self.max_drawdown_pct = 0.0

        # Rolling risk tracking (times kept aligned with pnls for honest annualization)
        self._pnl_history: deque = deque(maxlen=200)
        self._pnl_times: deque = deque(maxlen=200)
        self._consecutive_losses = 0
        self._max_consecutive_losses = 0
        
    HARD_MAX_KELLY = 0.50          # search ceiling; the 2% cap below is the operative limit
    BOOTSTRAP_DRAWS = 200
    LOWER_CONFIDENCE = 0.05        # 5th percentile of bootstrapped growth at the chosen fraction

    @staticmethod
    def expected_log_growth(fraction: float, returns: list) -> float:
        """Mean log wealth multiplier at `fraction`. -inf if any outcome would wipe the stake."""
        total = 0.0
        for r in returns:
            multiplier = 1.0 + fraction * r
            if multiplier <= 0.0:
                return float("-inf")
            total += math.log(multiplier)
        return total / len(returns)

    def kelly_maximiser(self, returns: list) -> float:
        """The fraction that maximises in-sample expected log growth. NOT a size on its own.

        Kept separate from `_empirical_kelly` because the two answer different questions: this is
        the optimum, that is what may be risked given the strength of the evidence. Returning a
        de-risked number from a function named for the maximiser makes the optimality property
        untestable and invites the two from being confused at the call site."""
        if not returns:
            return 0.0
        grid = [i / 200.0 * self.HARD_MAX_KELLY for i in range(201)]
        best_f, best_growth = 0.0, 0.0
        for f in grid:
            growth = self.expected_log_growth(f, returns)
            if growth > best_growth:
                best_f, best_growth = f, growth
        return best_f

    def _empirical_kelly(self, returns: list, live_mode: bool = False) -> float:
        """Kelly fraction gated by a day-block bootstrap lower confidence bound.

        The point estimate alone overfits the sample: the maximiser of in-sample log growth is
        positive for most return series, including ones with no real edge. The lower bound is
        what distinguishes edge from noise, and if it is not positive the honest size is ZERO."""
        best_f = self.kelly_maximiser(returns)
        if best_f <= 0.0:
            return 0.0

        # Day-block bootstrap: resample CONTIGUOUS blocks, because consecutive trades share a
        # regime and an i.i.d. resample would understate the true variance.
        import random

        rng = random.Random(20260728)
        block = max(1, len(returns) // 10)
        growths = []
        for _ in range(self.BOOTSTRAP_DRAWS):
            sample = []
            while len(sample) < len(returns):
                start = rng.randrange(0, max(1, len(returns) - block + 1))
                sample.extend(returns[start:start + block])
            growths.append(self.expected_log_growth(best_f, sample[:len(returns)]))
        growths.sort()
        lower_bound = growths[int(self.LOWER_CONFIDENCE * len(growths))]
        if lower_bound <= 0.0:
            # No demonstrable edge at the chosen fraction. In live mode that means no position.
            return 0.0 if live_mode else best_f * 0.5
        return best_f

    def _compute_kelly_fraction(self) -> float:
        """
        Half-Kelly position sizing based on historical trade outcomes.
        Requires ≥30 historical trades; returns flat 1% until then.
        Heavily capped at 2% max risk per trade pending empirical calibration.
        """
        # Evaluate on RECENT trades only: trade_history grows unbounded, so a stale
        # losing era (e.g. the pre-retrain model) would otherwise pin sizing down
        # forever no matter how the current model performs.
        recent = self.trade_history[-100:]
        if len(recent) < 30:
            return 0.01  # highly conservative default

        # EMPIRICAL LOG-GROWTH KELLY over actual after-cost returns.
        #
        # The previous binary form mis-priced SCRATCHES. It set
        #     win_rate = len(wins) / len(recent)          # denominator includes scratches
        #     kelly    = (p*b - (1-p)) / b                # so (1-p) carries the scratches
        # while b = avg_win/avg_loss was built only from REAL losses. A trade that returned
        # exactly zero was therefore charged the average LOSING magnitude, and sizing was
        # understated whenever scratches were common.
        #
        # Simply removing scratches from the denominator - p = wins/(wins+losses) - is not the
        # repair either: it discards the fact that capital was committed for no return, which
        # genuinely lowers growth, and it OVERSTATES size.
        #
        # Maximising empirical expected log growth handles it with no special case: a zero
        # return contributes log(1 + f*0) = 0 to the sum while still counting in the mean, so
        # it dilutes growth without being charged as a loss - which is exactly what it is.
        returns = []
        for t in recent:
            size = float(t.get("position_size") or 0.0)
            if size > 0:
                returns.append(float(t["net_pnl_usd"]) / size)
        if len(returns) < 30:
            return 0.02

        kelly = self._empirical_kelly(returns)
        half_kelly = max(0.0, kelly / 2.0)
        # PROBE FLOOR (recovery path): a hard 0 here meant no trades opened, so the
        # history never updated and Kelly stayed 0 PERMANENTLY — even after a retrain
        # improved the model. A 0.5% paper-probe keeps evidence flowing so sizing can
        # recover; this is a research simulator, not live money. Cap stays 2%.
        return min(max(half_kelly, 0.005), 0.02)

    def _compute_dynamic_slippage(self, current_price: float, position_size_btc: float,
                                   spread_expansion: float = 1.0, avg_volume: float = 1.0) -> float:
        """
        Dynamic slippage model: base × spread factor × volume impact.
        Slippage worsens in thin markets and with larger positions.
        """
        base = self.base_slippage_bps / 10000.0
        spread_factor = 1.0 + max(0, (spread_expansion - 1.0)) * 0.5
        volume_impact = 1.0 + math.log1p(position_size_btc / (avg_volume + 1e-9)) * 0.1
        return base * spread_factor * volume_impact

    def _estimate_fill_probability(self, direction: str, order_book_data: dict = None) -> float:
        """
        Estimate fill probability based on queue depth at the target price level.
        Thin book = lower fill probability.
        """
        if not order_book_data:
            return 0.95  # default: assume fills
        
        bid_depth = order_book_data.get("bid_depth", 0)
        ask_depth = order_book_data.get("ask_depth", 0)
        total = bid_depth + ask_depth + 1e-9
        
        if direction == "UP":
            # Buying: need asks available
            fill_ratio = ask_depth / total
        else:
            # Selling: need bids available
            fill_ratio = bid_depth / total
        
        # Map to 0.5-1.0 range
        return max(0.5, min(1.0, 0.5 + fill_ratio))

    def _compute_rolling_metrics(self) -> dict:
        """Compute rolling Sharpe, Sortino, VaR from recent PnL history."""
        pnls = list(self._pnl_history)
        n = len(pnls)
        if n < 5:
            return {
                "rolling_sharpe": 0.0,
                "rolling_sortino": 0.0,
                "daily_var_95": 0.0,
                "max_consecutive_losses": self._max_consecutive_losses,
            }
        
        import numpy as np
        arr = np.array(pnls)
        mean_pnl = float(np.mean(arr))
        std_pnl = float(np.std(arr))
        
        # Annualize by MEASURED trade frequency, not sqrt(min(252, n)): the old factor
        # treated n TRADES as n DAYS, so 30 trades in 2 hours got the same multiplier
        # as 30 trading days — wildly inflating/deflating the ratio. Proper scaling for
        # per-trade returns is sqrt(trades_per_year), estimated from the actual time
        # span of the recent trades; falls back to the un-annualized per-trade ratio
        # when the span is too short to estimate frequency.
        times = list(self._pnl_times)
        ann_factor = 1.0
        if len(times) >= 5 and times[-1] > times[0]:
            span_days = (times[-1] - times[0]) / 86400000.0
            if span_days > 0.01:
                trades_per_year = ((len(times) - 1) / span_days) * 365.0
                ann_factor = math.sqrt(trades_per_year)
        sharpe = (mean_pnl / std_pnl) * ann_factor if std_pnl > 0 else 0.0

        # Sortino (downside deviation only)
        downside = arr[arr < 0]
        downside_std = float(np.std(downside)) if len(downside) > 1 else std_pnl
        sortino = (mean_pnl / downside_std) * ann_factor if downside_std > 0 else 0.0
        
        # VaR (95th percentile loss)
        var_95 = float(np.percentile(arr, 5)) if n >= 20 else 0.0
        
        return {
            "rolling_sharpe": round(sharpe, 3),
            "rolling_sortino": round(sortino, 3),
            "daily_var_95": round(var_95, 2),
            "max_consecutive_losses": self._max_consecutive_losses,
        }

    def calculate_signal_expectancy(self, prediction: dict, current_price: float, order_book_data: dict = None, spread_expansion: float = 1.0) -> dict:
        """
        Calculates the expected value (Expectancy) in USD of a given signal.
        Expectancy = (Prob(Win) * Expected_Net_Win) - (Prob(Loss) * Expected_Net_Loss)
        """
        direction = prediction.get("direction", "NEUTRAL")
        if direction not in ["UP", "DOWN"] or current_price <= 0:
            return {"expectancy_usd": 0.0, "expected_slippage_usd": 0.0, "effective_fee": 0.0, "is_valid": False}
        
        kelly_frac = self._compute_kelly_fraction()
        position_size_usd = self.equity * kelly_frac
        position_size_btc = position_size_usd / current_price
        
        fill_prob = self._estimate_fill_probability(direction, order_book_data)
        slippage_factor, effective_fee = self.router.simulate_execution_cost(position_size_btc, fill_prob, spread_expansion, avg_volume=1.0)
        
        prob_win = prediction.get("confidence", 0.0)
        prob_loss = 1.0 - prob_win
        
        expected_move_gross = prediction.get("expectedMove", 0.0)
        
        # Uncertainty penalty (VRP approximation)
        move_range = prediction.get("expectedMoveRange")
        uncertainty_penalty = 1.0
        if move_range and move_range.get("median", 0) > 0:
            quantile_spread = (move_range.get("high", 0) - move_range.get("low", 0)) / move_range["median"]
            uncertainty_penalty = max(0.2, 1.0 - (quantile_spread * 0.2))
            
        expected_move_gross *= uncertainty_penalty
        
        stop_loss_dist = expected_move_gross * 1.5 if expected_move_gross > 0 else current_price * 0.005
        
        fees_usd = (position_size_btc * current_price * effective_fee) * 2  # Entry and exit
        slippage_usd = (position_size_btc * current_price * slippage_factor) * 2
        
        expected_net_win = (position_size_btc * expected_move_gross) - fees_usd - slippage_usd
        expected_net_loss = (position_size_btc * stop_loss_dist) + fees_usd + slippage_usd
        
        expectancy_usd = (prob_win * expected_net_win) - (prob_loss * expected_net_loss)
        
        return {
            "expectancy_usd": expectancy_usd,
            "expected_slippage_usd": slippage_usd,
            "effective_fee": effective_fee,
            "slippage_factor": slippage_factor,
            "is_valid": True,
            "position_size_btc": position_size_btc,
            "position_size_usd": position_size_usd,
            "fill_prob": fill_prob,
            "kelly_frac": kelly_frac
        }

    def process_signal(self, prediction: dict, current_price: float, now_ms: int,
                       order_book_data: dict = None, spread_expansion: float = 1.0):
        """Processes a new prediction signal and opens a virtual position if valid."""
        direction = prediction.get("direction", "NEUTRAL")
        if direction not in ["UP", "DOWN"]:
            return
            
        horizon = prediction["horizon"]
        pred_id = prediction.get("id", str(uuid.uuid4()))
        exp_calc = self.calculate_signal_expectancy(prediction, current_price, order_book_data, spread_expansion)
        if not exp_calc["is_valid"]:
            return
            
        position_size_usd = exp_calc["position_size_usd"]
        position_size_btc = exp_calc["position_size_btc"]
        fill_prob = exp_calc["fill_prob"]
        slippage_factor = exp_calc["slippage_factor"]
        effective_fee = exp_calc["effective_fee"]
        kelly_frac = exp_calc["kelly_frac"]

        # Degenerate-size guard: never open a ~$0 position (recording $0-PnL trades
        # once froze the simulator — neither win nor loss, diluting win_rate forever).
        # Kelly itself now has a 0.5% probe floor, so this only fires on tiny equity.
        if position_size_usd <= 1.0 or position_size_btc <= 0:
            return

        entry_price = current_price * (1 + slippage_factor) if direction == "UP" else current_price * (1 - slippage_factor)
        
        trade_id = str(uuid.uuid4())
        
        trade = {
            "trade_id": trade_id,
            "prediction_id": pred_id,
            "horizon": horizon,
            "direction": direction,
            "entry_price": entry_price,
            "position_size_btc": position_size_btc,
            "position_size_usd": position_size_usd,
            "entry_time_ms": now_ms + 50, # 50ms simulated latency
            "exit_time_ms": now_ms + (horizon * 60 * 1000),
            "status": "OPEN",
            "effective_fee": effective_fee,
            "slippage_factor": slippage_factor,
            "kelly_fraction": kelly_frac,
            "fill_probability": fill_prob,
            "dynamic_slippage_bps": round(slippage_factor * 10000, 2),
        }
        
        self.active_trades[trade_id] = trade
        logger.info(f"[SIMULATOR] Entered {direction} trade for {horizon}m at {entry_price:.2f} "
                     f"(Kelly={kelly_frac:.3f}, Fill={fill_prob:.2f}, Slip={slippage_factor*10000:.1f}bps)")

    def update(self, current_price: float, now_ms: int):
        """Checks if any active trades have hit their horizon timeout and closes them."""
        closed_trades = []
        for tid, trade in list(self.active_trades.items()):
            if now_ms >= trade["exit_time_ms"]:
                direction = trade["direction"]
                entry_price = trade["entry_price"]
                size_btc = trade["position_size_btc"]
                
                # Apply dynamic slippage on exit (same model)
                eff_fee = trade.get("effective_fee", self.taker_fee_rate)
                slippage_factor = trade.get("slippage_factor", 0.0)
                exit_price = current_price * (1 - slippage_factor) if direction == "UP" else current_price * (1 + slippage_factor)
                
                # Calculate Gross PnL
                if direction == "UP":
                    pnl_usd = (exit_price - entry_price) * size_btc
                else:
                    pnl_usd = (entry_price - exit_price) * size_btc
                    
                # Fees (entry + exit)
                fees_usd = (size_btc * entry_price * eff_fee) + (size_btc * current_price * eff_fee)
                
                slippage_usd = (current_price * slippage_factor * size_btc) * 2 
                
                net_pnl_usd = pnl_usd - fees_usd - slippage_usd
                self.equity += net_pnl_usd
                
                # Track PnL for rolling metrics
                self._pnl_history.append(net_pnl_usd)
                self._pnl_times.append(now_ms)
                
                # Consecutive losses tracking
                if net_pnl_usd < 0:
                    self._consecutive_losses += 1
                    self._max_consecutive_losses = max(self._max_consecutive_losses, self._consecutive_losses)
                else:
                    self._consecutive_losses = 0
                
                # Drawdown tracking
                if self.equity > self.max_equity:
                    self.max_equity = self.equity
                dd_usd = self.max_equity - self.equity
                dd_pct = dd_usd / self.max_equity
                if dd_usd > self.max_drawdown_usd:
                    self.max_drawdown_usd = dd_usd
                if dd_pct > self.max_drawdown_pct:
                    self.max_drawdown_pct = dd_pct
                
                finished_trade = {
                    "trade_id": tid,
                    "prediction_id": trade["prediction_id"],
                    "timestamp": now_ms,
                    "horizon": trade["horizon"],
                    "direction": direction,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "position_size": trade["position_size_usd"],
                    "slippage_usd": float(slippage_usd),
                    "fees_usd": float(fees_usd),
                    "pnl_usd": float(pnl_usd),
                    "net_pnl_usd": float(net_pnl_usd),
                    "hold_time_ms": now_ms - trade["entry_time_ms"],
                    "kelly_fraction": trade["kelly_fraction"],
                    "fill_probability": trade["fill_probability"],
                    "dynamic_slippage_bps": trade["dynamic_slippage_bps"],
                }
                
                self.trade_history.append(finished_trade)
                database.log_simulated_trade(finished_trade)
                closed_trades.append(tid)
                
                logger.info(f"[SIMULATOR] Closed {direction} trade {tid}. Net PnL: ${net_pnl_usd:.2f}. Equity: ${self.equity:.2f}")
                
        for tid in closed_trades:
            del self.active_trades[tid]
            
    def get_metrics(self):
        trades = len(self.trade_history)
        if trades == 0:
            return {"equity": self.equity, "trades": 0}
            
        wins = len([t for t in self.trade_history if t["net_pnl_usd"] > 0])
        gross_profit = sum(t["net_pnl_usd"] for t in self.trade_history if t["net_pnl_usd"] > 0)
        gross_loss = abs(sum(t["net_pnl_usd"] for t in self.trade_history if t["net_pnl_usd"] < 0))
        
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float('inf')
        win_rate = wins / trades
        
        risk_metrics = self._compute_rolling_metrics()
        
        return {
            "equity": round(self.equity, 2),
            "net_profit_pct": round(((self.equity - self.starting_capital) / self.starting_capital) * 100, 2),
            "trades": trades,
            "win_rate": round(win_rate * 100, 1),
            "profit_factor": round(profit_factor, 3),
            "max_drawdown_pct": round(self.max_drawdown_pct * 100, 2),
            "kelly_fraction": round(self._compute_kelly_fraction(), 4),
            **risk_metrics,
        }
