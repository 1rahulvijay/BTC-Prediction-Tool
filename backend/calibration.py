"""
Precision Engine — Stages 1+2 of the 80%-precision plan
========================================================
Implements the machinery NOW; each piece AUTO-ACTIVATES when its data threshold is met,
so nothing waits on a human and nothing overfits a tiny sample.

Stage 1 — CONFIDENCE CALIBRATION (isotonic), per horizon.
    Fit on LIVE RESOLVED outcomes (genuinely out-of-sample by construction — every row
    was predicted before its outcome existed). Activates at >=150 resolved directional
    leans per horizon. Why it matters: the DB proved raw confidence is ANTI-correlated
    with success at 5m+ (gate-passed subset won 50% vs 64% for all leans). Calibration
    re-maps confidence so the gate selects the right predictions.

Stage 2 — EMPIRICAL PRECISION lookup (a robust, shrunk "meta-model v0").
    P(hit | horizon, regime, conviction-bin) from the DB with Laplace shrinkage toward
    the horizon's global rate, so small cells don't produce wild estimates. Works from
    the first day of data and sharpens as n grows; the full ML meta-model replaces it
    once >=300 leans/horizon exist.

Refit cadence: refresh_if_stale() — at most every REFIT_SEC, called from the server's
maintenance loop off the event loop. Fitting is a cheap aggregate query (<100ms).
"""

import logging
import time

import numpy as np

import database

logger = logging.getLogger(__name__)

HORIZONS = [5, 15]   # pruned 2026-06-21: dropped 3/7/10/30 (no market, coin-flip direction)
MIN_CALIB_SAMPLES = 150       # isotonic below this overfits
REFIT_SEC = 6 * 3600
SHRINK = 8.0                  # Laplace prior weight for the precision bins

# conviction bins: evidence showed mid-conviction outperforming high (inversion),
# so bins must be granular enough for calibration to SEE that.
def _conv_bin(c: float) -> str:
    return "high" if c >= 65 else "mid" if c >= 40 else "low"


class PrecisionEngine:
    def __init__(self):
        self.calibrators: dict = {}     # h -> IsotonicRegression
        self.calib_n: dict = {}         # h -> sample count used
        self.bins: dict = {}            # h -> {(regime, conv_bin): (n, hits)}
        self.global_rate: dict = {}     # h -> overall directional winrate
        self._last_fit = 0.0

    # ── fitting ──────────────────────────────────────────────────────────
    def refresh_if_stale(self, max_age_s: float = REFIT_SEC) -> bool:
        if time.time() - self._last_fit < max_age_s:
            return False
        try:
            self.fit_from_db()
            self._last_fit = time.time()
            return True
        except Exception as e:
            logger.warning(f"[PRECISION] refresh failed (will retry): {e}")
            self._last_fit = time.time() - max_age_s + 600  # retry in 10 min
            return False

    #: Set by the server to the live bundle id. When present, calibration selects rows by
    #: EXACT bundle identity and the mtime era is not used at all.
    active_bundle_id: str = ""

    @staticmethod
    def _model_era_ms() -> int:
        """Timestamp (ms) of the CURRENT model bundle (architecture_version.pkl mtime).

        P1-9. This is a FALLBACK only, for rows written before `model_version` was populated.
        It is not a correct era boundary on its own:

            challenger trained Monday        -> artifact mtime = Monday
            incumbent keeps predicting Mon-Fri
            challenger promoted Friday       -> mtime STILL Monday (the file was not rewritten)

        Every incumbent prediction from Monday to Friday then satisfies `timestamp >= mtime`,
        so the newly promoted challenger's calibrator is fitted on five days of a DIFFERENT
        model's confidence distribution - which is precisely the skew the era filter exists to
        prevent. A file's modification time is a fact about the filesystem, not about which
        model produced a row."""
        import os
        try:
            data_dir = os.environ.get("BTC_DATA_DIR") or os.path.join(
                os.path.dirname(os.path.dirname(__file__)), "data")
            vp = os.path.join(data_dir, "saved_models", "architecture_version.pkl")
            return int(os.path.getmtime(vp) * 1000) if os.path.exists(vp) else 0
        except Exception:
            return 0

    def _era_clause(self) -> tuple[str, str]:
        """(SQL predicate, mode). Exact bundle identity when we know it."""
        bundle = (self.active_bundle_id or "").strip()
        if bundle:
            safe = bundle.replace("'", "''")
            return f"model_version = '{safe}'", f"bundle:{bundle}"
        return f"timestamp >= {int(self._model_era_ms())}", "mtime_fallback"

    def fit_from_db(self):
        era_clause, era_mode = self._era_clause()
        self.era_mode = era_mode
        conn = database._connect()
        try:
            for h in HORIZONS:
                try:
                    # LABEL = LEAN-CORRECTNESS by realized move sign — NOT the `hit` column.
                    # `hit` is dual-semantic: for gated rows (signal NEUTRAL, raw lean
                    # UP/DOWN — the majority) hit = avoid_success, which is TRUE when the
                    # lean was WRONG. Fitting on `hit` would teach the calibrator an
                    # inverted map on exactly the rows it must learn from. Sign-of-move vs
                    # raw_direction is the unambiguous betting truth.
                    rows = conn.execute(f"""
                        SELECT confidence, regime, conviction,
                               CASE WHEN (raw_direction='UP'   AND actual_move > 0)
                                      OR (raw_direction='DOWN' AND actual_move < 0)
                                    THEN 1 ELSE 0 END
                        FROM predictions_{h}m
                        WHERE resolved AND raw_direction IN ('UP','DOWN')
                          AND actual_move IS NOT NULL
                          AND confidence IS NOT NULL AND confidence > 0
                          AND {era_clause}
                    """).fetchall()
                except Exception:
                    continue
                if not rows:
                    continue
                conf = np.array([r[0] for r in rows], dtype=float)
                hit = np.array([r[3] for r in rows], dtype=float)
                self.global_rate[h] = float(hit.mean())

                # Stage 2: shrunk precision bins (works at any n)
                b: dict = {}
                for c_, rg, cv, ht in rows:
                    key = (str(rg or "UNKNOWN"), _conv_bin(float(cv or 0.0)))
                    n0, h0 = b.get(key, (0, 0))
                    b[key] = (n0 + 1, h0 + int(ht))
                self.bins[h] = b

                # Stage 1: isotonic calibration (auto-activates at threshold)
                if len(rows) >= MIN_CALIB_SAMPLES:
                    try:
                        from sklearn.isotonic import IsotonicRegression
                        iso = IsotonicRegression(y_min=0.05, y_max=0.95,
                                                 out_of_bounds="clip")
                        iso.fit(conf, hit)
                        self.calibrators[h] = iso
                        self.calib_n[h] = len(rows)
                        logger.info(f"[PRECISION] {h}m calibrator ACTIVE (n={len(rows)}, "
                                    f"base rate {hit.mean():.3f})")
                    except Exception as e:
                        logger.warning(f"[PRECISION] {h}m isotonic fit failed: {e}")
                else:
                    logger.info(f"[PRECISION] {h}m calibrator waiting: "
                                f"{len(rows)}/{MIN_CALIB_SAMPLES} resolved leans")
        finally:
            conn.close()

    # ── inference-time API (cheap, no DB) ────────────────────────────────
    def calibrated(self, h: int, raw_conf: float):
        """Calibrated P(correct) for this confidence, or None until active."""
        iso = self.calibrators.get(h)
        if iso is None or raw_conf <= 0:
            return None
        try:
            return float(iso.predict([raw_conf])[0])
        except Exception:
            return None

    def expected_precision(self, h: int, regime: str, conviction: float):
        """Shrunk empirical P(hit) for this (horizon, regime, conviction-bin)."""
        g = self.global_rate.get(h)
        if g is None:
            return None
        n, hits = (self.bins.get(h) or {}).get(
            (str(regime or "UNKNOWN"), _conv_bin(float(conviction or 0.0))), (0, 0))
        return round((hits + SHRINK * g) / (n + SHRINK), 4)

    def status(self) -> dict:
        return {
            "calibrators_active": sorted(self.calibrators.keys()),
            "samples": dict(self.calib_n),
            "global_rates": {k: round(v, 4) for k, v in self.global_rate.items()},
            "last_fit_age_s": round(time.time() - self._last_fit) if self._last_fit else None,
        }
