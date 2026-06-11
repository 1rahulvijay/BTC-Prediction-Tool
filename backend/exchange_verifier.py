"""
Per-Venue Prediction Verifier
=============================
Our model predicts ONE BTC direction. This checks whether that direction was
confirmed at each exchange (Binance, Coinbase, Bybit, KuCoin, Chainlink) over the
horizon. BTC is ~99% correlated across venues intraday, so:
  - high cross-venue confirmation (5/5) = a clean, broad move (more trustworthy),
  - divergence (e.g. 3/5) = venue-specific dislocation / thin venue (a risk flag),
  - per-venue accuracy reveals which venue our signal tracks best / leads.
Results are stored in DuckDB (`exchange_verifications`) for durable analysis.
"""

import logging
from collections import deque

import database

logger = logging.getLogger(__name__)

VENUES = ["binance", "coinbase", "bybit", "kucoin", "chainlink"]


class PerVenueVerifier:
    def __init__(self, horizons=(5, 15), band=0.0001):
        self.horizons = list(horizons)
        self.band = band
        self.pending: list[dict] = []
        # per-venue per-horizon rolling correctness
        self.history = {v: {h: deque(maxlen=400) for h in self.horizons} for v in VENUES}
        self._last_record_ms = 0

    def _dir(self, p_now, p0):
        if not p0 or not p_now or p0 <= 0:
            return "NONE"
        chg = (p_now - p0) / p0
        return "UP" if chg > self.band else ("DOWN" if chg < -self.band else "FLAT")

    def record(self, direction: str, h: int, venue_prices: dict, now_ms: int):
        """Snapshot venue prices when a directional BTC call is recorded."""
        if direction not in ("UP", "DOWN") or h not in self.horizons:
            return
        prices = {v: venue_prices.get(v) for v in VENUES if venue_prices.get(v)}
        if not prices:
            return
        self.pending.append({
            "direction": direction, "h": h, "prices": prices,
            "verify_at": now_ms + h * 60_000, "ts": now_ms,
        })

    def check(self, venue_prices_now: dict, now_ms: int):
        """Resolve elapsed predictions per venue."""
        still = []
        for p in self.pending:
            if now_ms >= p["verify_at"]:
                row = {"horizon": p["h"], "direction": p["direction"],
                       "timestamp": p["ts"], "venues": {}}
                confirmed = 0
                checked = 0
                for v, p0 in p["prices"].items():
                    pn = venue_prices_now.get(v)
                    actual = self._dir(pn, p0)
                    if actual in ("UP", "DOWN"):
                        hit = (actual == p["direction"])
                        self.history[v][p["h"]].append(1 if hit else 0)
                        row["venues"][v] = {"actual": actual, "hit": hit}
                        confirmed += int(hit)
                        checked += 1
                if checked:
                    row["confirmation_rate"] = round(confirmed / checked, 3)
                    try:
                        database.log_exchange_verification(p["ts"], p["h"], p["direction"],
                                                           confirmed, checked, row["venues"])
                    except Exception as e:
                        logger.debug(f"exchange verify log failed: {e}")
            else:
                still.append(p)
        self.pending = still

    def accuracy(self) -> dict:
        out = {}
        for v in VENUES:
            out[v] = {}
            for h in self.horizons:
                hh = list(self.history[v][h])
                n = len(hh)
                out[v][h] = {"total": n, "accuracy": round(sum(hh) / n, 4) if n else 0.0}
        return out
