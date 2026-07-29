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


class KellyAssessment:
    """Kelly evidence, with the three questions kept apart.

    point_estimate          what the sample's log-growth maximiser suggests
    lower_bound_passed      whether a UTC-day-block lower bound cleared zero
    authorized_fraction     what may be risked - ZERO unless the bound passed
    research_probe_fraction paper-only probe to keep evidence flowing; NOT authorization
    """

    __slots__ = ("point_estimate", "lower_bound_passed", "authorized_fraction",
                 "research_probe_fraction", "day_count", "reason")

    def __init__(self, point_estimate, lower_bound_passed, authorized_fraction,
                 research_probe_fraction, day_count, reason):
        self.point_estimate = float(point_estimate)
        self.lower_bound_passed = bool(lower_bound_passed)
        self.authorized_fraction = float(authorized_fraction)
        self.research_probe_fraction = float(research_probe_fraction)
        self.day_count = int(day_count)
        self.reason = str(reason)

    def as_dict(self) -> dict:
        return {name: getattr(self, name) for name in self.__slots__}

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
            "Promotion status": "HEURISTIC_RESEARCH_ONLY / NON_PROMOTABLE",
            "Fill realization": "fill_prob is COMPUTED BUT NOT APPLIED - every accepted signal opens a full position",
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
        self.last_kelly_assessment = None   # most recent KellyAssessment, for reporting
        
    HARD_MAX_KELLY = 0.50          # search ceiling; the 2% cap below is the operative limit
    BOOTSTRAP_DRAWS = 200
    MIN_BOOTSTRAP_DAYS = 5       # fewer distinct days than this cannot support a bound
    RESEARCH_PROBE_FRACTION = 0.005   # paper probe only; never an authorized size
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

    @staticmethod
    def average_fill_price(ask_levels: list, notional: float):
        """Walk the ask ladder. Returns (vwap, shares, fully_filled).

        `ask_levels` is [(price, size_shares), ...] ascending. The AVERAGE fill price across all
        consumed depth is what the position actually costs - not the marginal price at the last
        level touched, which understates the cost of the whole order."""
        if notional <= 0:
            return 0.0, 0.0, True
        spent = 0.0
        shares = 0.0
        for price, size in ask_levels:
            price = float(price)
            if price <= 0.0:
                continue
            level_cost = price * float(size)
            if spent + level_cost >= notional:
                take = (notional - spent) / price
                shares += take
                spent = notional
                return spent / shares, shares, True
            spent += level_cost
            shares += float(size)
        return (spent / shares if shares > 0 else 0.0), shares, False

    def endogenous_kelly(self, p_win: float, ask_levels: list, bankroll: float):
        """Kelly sizing where OUR OWN ORDER moves the price against us.

        Standard Kelly assumes exogenous odds - that the bet does not change the payout. For a
        thin Polymarket book that is false: buying size walks the ask up, so the realised entry
        is worse than the quoted top of book, and the error grows WITH size. Sizing on the quoted
        price therefore overstates the edge exactly where overstating it is most expensive.

            g(f) = p*log(1 + f*b(f)) + (1-p)*log(1 - f)
            b(f) = (1 - q(f)) / q(f)        q(f) = VWAP of the depth consumed by f*bankroll

        b decays as f grows, so g turns over at a finite f even when the top-of-book edge looks
        large. That turning point is the capacity the book actually supports.

        Returns (endogenous_fraction, exogenous_fraction) so the difference is visible."""
        p = min(max(float(p_win), 0.0), 1.0)
        if not ask_levels or bankroll <= 0 or p <= 0.0:
            return 0.0, 0.0

        def growth(fraction: float, price: float) -> float:
            if price <= 0.0 or price >= 1.0 or fraction <= 0.0 or fraction >= 1.0:
                return float("-inf")
            odds = (1.0 - price) / price
            win = 1.0 + fraction * odds
            lose = 1.0 - fraction
            if win <= 0.0 or lose <= 0.0:
                return float("-inf")
            return p * math.log(win) + (1.0 - p) * math.log(lose)

        top_price = float(ask_levels[0][0])
        grid = [i / 500.0 for i in range(1, 500)]

        best_endo, best_endo_g = 0.0, 0.0
        for f in grid:
            vwap, _shares, complete = self.average_fill_price(ask_levels, f * bankroll)
            if not complete:
                # The book cannot absorb this size. Larger f is worse, so stop.
                break
            value = growth(f, vwap)
            if value > best_endo_g:
                best_endo, best_endo_g = f, value

        best_exo, best_exo_g = 0.0, 0.0
        for f in grid:
            value = growth(f, top_price)
            if value > best_exo_g:
                best_exo, best_exo_g = f, value
        return best_endo, best_exo

    def assess_kelly(self, returns_with_time: list, live_mode: bool = False):
        """Return the point estimate, the evidence verdict and the authorized size SEPARATELY.

        A single float conflated three different things: what the sample suggests, whether the
        evidence supports it, and what may actually be risked. That let a de-risked research
        number be read as a statistically authorized size. They are now distinct fields, and
        `authorized_fraction` is the ONLY one any sizing decision may consult.

        `returns_with_time` is [(net_return_fraction, timestamp_ms), ...]."""
        returns = [float(r) for r, _t in returns_with_time]
        point = self.kelly_maximiser(returns)
        if point <= 0.0:
            return KellyAssessment(0.0, False, 0.0, 0.0, 0, "no positive in-sample maximiser")

        lower_bound, day_count = self._day_block_lower_bound(returns_with_time, point)
        passed = lower_bound > 0.0
        if passed:
            return KellyAssessment(point, True, point, 0.0, day_count,
                                   f"lower bound {lower_bound:.6g} > 0 over {day_count} days")
        # Evidence failed. NOTHING is authorized. A research probe may still be run to keep
        # evidence flowing, but it is labelled as a probe and never as an authorized size.
        probe = 0.0 if live_mode else self.RESEARCH_PROBE_FRACTION
        return KellyAssessment(point, False, 0.0, probe, day_count,
                               f"lower bound {lower_bound:.6g} <= 0 over {day_count} days")

    def _day_block_lower_bound(self, returns_with_time: list, fraction: float):
        """Lower confidence bound on growth, resampling whole UTC DAYS.

        Previously this resampled contiguous TRADE-INDEX blocks of len(returns)//10 and called
        itself a day-block bootstrap. Trade-index blocks are not days: a quiet day and a busy
        day contribute different numbers of trades, so a fixed index block spans a varying and
        unknown amount of calendar time. Grouping by UTC day and resampling whole days keeps
        every trade that shares a day together, which is the dependence the method exists to
        respect."""
        import datetime
        import random

        by_day: dict = {}
        for value, timestamp in returns_with_time:
            day = datetime.datetime.fromtimestamp(
                float(timestamp) / 1000.0, datetime.timezone.utc).date()
            by_day.setdefault(day, []).append(float(value))
        days = sorted(by_day)
        if len(days) < self.MIN_BOOTSTRAP_DAYS:
            # Too few distinct days to bootstrap over. Refuse rather than resample noise.
            return -1.0, len(days)

        rng = random.Random(20260728)
        total = sum(len(by_day[d]) for d in days)
        growths = []
        for _ in range(self.BOOTSTRAP_DRAWS):
            sample: list = []
            while len(sample) < total:
                sample.extend(by_day[days[rng.randrange(len(days))]])
            growths.append(self.expected_log_growth(fraction, sample[:total]))
        growths.sort()
        return growths[int(self.LOWER_CONFIDENCE * len(growths))], len(days)

    def _empirical_kelly(self, returns: list, live_mode: bool = False) -> float:
        """Backwards-compatible scalar wrapper. Prefer assess_kelly, which does not conflate
        an authorized size with a research probe."""
        spaced = [(r, 1_700_000_000_000 + i * 86_400_000 // max(1, len(returns) // 10 or 1))
                  for i, r in enumerate(returns)]
        assessment = self.assess_kelly(spaced, live_mode=live_mode)
        return assessment.authorized_fraction or assessment.research_probe_fraction

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
        returns_with_time = []
        for t in recent:
            size = float(t.get("position_size") or 0.0)
            if size > 0:
                returns_with_time.append(
                    (float(t["net_pnl_usd"]) / size, t.get("timestamp") or 0))
        if len(returns_with_time) < 30:
            return 0.02

        assessment = self.assess_kelly(returns_with_time)
        self.last_kelly_assessment = assessment
        if assessment.lower_bound_passed:
            half_kelly = max(0.0, assessment.authorized_fraction / 2.0)
            return min(max(half_kelly, self.RESEARCH_PROBE_FRACTION), 0.02)

        # EVIDENCE FAILED. Nothing is authorized. What is returned here is a PAPER PROBE and
        # must never be described as a Kelly size: a hard 0 meant no trades opened, so history
        # never updated and sizing stayed 0 permanently even after a retrain improved the model.
        # The probe keeps evidence flowing in this research simulator. Live sizing is separate
        # and remains disabled; assess_kelly(live_mode=True) returns 0.0 in this branch.
        return self.RESEARCH_PROBE_FRACTION

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

                # SLIPPAGE IS ALREADY IN pnl_usd. entry_price was marked UP by slippage_factor
                # and exit_price marked DOWN by it, so the price difference above already carries
                # both legs. Subtracting slippage_usd again charged every round trip TWICE, which
                # biased every realized return downward - and those returns are exactly the
                # Kelly inputs, so sizing was fitted to costs that were never paid.
                #
                # Method A (execution-adjusted prices) is what this simulator uses:
                #     net = adjusted-price PnL - fees
                # Method B would use reference prices and subtract explicit slippage. Either is
                # defensible; combining them is not. slippage_usd is still computed and recorded
                # so the cost remains visible in the trade log and in reconciliation.
                slippage_usd = (current_price * slippage_factor * size_btc) * 2

                net_pnl_usd = pnl_usd - fees_usd
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
