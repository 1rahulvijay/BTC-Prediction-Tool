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
import target_contract as _tc

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

    #: Every map whose contents belong to ONE release and must not outlive it. Named once, so
    #: `bind_release` and the test agree by construction instead of by hand - the previous list
    #: and the real attribute set had no overlap at all on the inference path.
    RELEASE_SCOPED_MAPS = ("calibrators", "calib_n", "bins", "global_rate")

    def bind_release(self, bundle_id: str, target_contract: str = "") -> dict:
        """Point the calibrator at a release, CLEARING every map first.

        Switching `active_bundle_id` alone left the previous release's maps serving: the
        refresh timer could wait hours, and a release with no rows yet left the old map in
        place entirely. A calibrator fitted on one model's behaviour is not a prior for a
        different model - it is a stale opinion wearing the new model's name.

        Clearing makes the calibrator UNAVAILABLE until it has been refitted on rows from the
        new release, which is the honest state. Callers must handle unavailability; the
        previous behaviour handled it by quietly answering with the old map.

        THE MAPS THIS MUST CLEAR ARE THE ONES INFERENCE READS.

        It cleared `isotonic`, `regime_rate` and `conviction_rate` - none of which exist on this
        class. `getattr(self, attr, None)` returned None for all three and the loop skipped
        them, so the clear was a no-op on the only state that matters: `calibrated()` reads
        `self.calibrators`, and that survived every release change.

        It also reset `last_fit_ts`, an attribute written twice and READ NOWHERE. The refresh
        timer is `_last_fit`, which was left untouched - so `refresh_if_stale` saw a recent fit
        and declined to refit for up to six hours. The new release inherited the previous
        model's isotonic maps AND its refresh age.

        The test passed because it invented `e.isotonic = {...}` and asserted that fabricated
        attribute was cleared. A check that verifies state the subject does not have is the
        same defect class as the code it was guarding.
        """
        cleared = {name: len(getattr(self, name, {}) or {})
                   for name in self.RELEASE_SCOPED_MAPS}
        for attr in self.RELEASE_SCOPED_MAPS:
            current = getattr(self, attr, None)
            if not isinstance(current, dict):
                # Refuse silently skipping a name that does not exist - that is exactly how the
                # previous version cleared nothing while reporting success.
                raise AttributeError(
                    f"PrecisionEngine.RELEASE_SCOPED_MAPS names {attr!r}, which is not a dict "
                    f"on this instance; a release-scoped map that cannot be cleared would "
                    f"survive the swap")
            current.clear()
        self.active_bundle_id = (bundle_id or "").strip()
        if target_contract:
            self.fitted_under_contract = target_contract
        # PROVENANCE IS CLEARED WITH THE MAPS IT DESCRIBES.
        #
        # This line only became necessary when a fit started EARNING "RECORDED": before
        # that the flag was a constant and a swap could not stale it. Now a release change
        # that cleared every calibrator would have left the engine still declaring RECORDED,
        # so `is_admissible_for` would answer True about a map that no longer exists - the
        # return value below already says `available: False`, and the flag must not
        # contradict it.
        self.contract_provenance = "UNRECORDED"
        # THE refresh timer. Zeroing it forces the next refresh_if_stale to actually refit
        # instead of waiting out the remainder of a six-hour window it never started.
        self._last_fit = 0.0
        self.last_fit_ts = 0.0
        return {"bundle_id": self.active_bundle_id, "cleared": cleared,
                "available": False,
                "reason": "cleared on release change; unavailable until refitted"}

    def is_admissible_for(self, target_contract: str) -> bool:
        """False when this map was not fitted under the contract being asked about.

        Returns False while provenance is UNRECORDED, because "we do not know" must not read
        as "yes". That is the whole lesson of the contract layer applied to calibration.
        """
        if self.contract_provenance != "RECORDED":
            return False
        return self.fitted_under_contract == target_contract

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

    #: P0-14. WHICH CONTRACT THIS MAP WAS FITTED UNDER.
    #:
    #: The fit below defines correct as `raw_direction=UP AND actual_move > 0` - ENDPOINT
    #: sign - while the ensemble trains on FIRST_TOUCH_TRIPLE_BARRIER_V1. So first-touch
    #: confidence is calibrated by a different rule than the model was trained on.
    #:
    #: RESOLVED 2026-08-08. That column now exists, along with `release_id`, and
    #: `log_prediction` REQUIRES both - so the rows CAN be separated and the fit no longer
    #: has to guess which question it is answering. `fit_from_db` filters on it and grades
    #: with the contract's own outcome (`raw_direction == actual_direction`) rather than the
    #: endpoint sign of `actual_move`, which is a different rule that disagrees on roughly a
    #: quarter of paths.
    #:
    #: These two attributes are now SET BY THE FIT rather than declared as constants. They
    #: stay at the pessimistic values below until a fit actually succeeds, because a map that
    #: has not been fitted must not claim provenance it has not earned - the refusal in
    #: `is_admissible_for` is the point, not a placeholder.
    fitted_under_contract: str = "endpoint_move_sign"
    contract_provenance: str = "UNRECORDED"

    #: Rows written before the column existed carry this sentinel. They are EXCLUDED rather
    #: than pooled in: a row that cannot say which question it answers cannot calibrate an
    #: answer to a specific one.
    UNKNOWN_CONTRACT_SENTINEL = "UNKNOWN_LEGACY"

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

    def fit_from_db(self, target_contract: str | None = None):
        """Fit the map on rows that answer ONE question, and record which.

        Two things changed here when `target_contract` became a stored column.

        SELECTION. Rows are filtered to the contract being fitted for. Pooling first-touch
        and endpoint rows produced a map that answered neither: they disagree on roughly a
        quarter of paths, and both are floats in [0, 1] so nothing about the value revealed
        the mixture.

        THE LABEL. Correctness is now the CONTRACT'S own outcome - `raw_direction ==
        actual_direction`, written by the verifier through `target_contract.grade()`. It
        used to be `actual_move > 0`, which is ENDPOINT SIGN: under first touch a lean can
        be right by the contract (it touched the upper barrier first) while the endpoint
        closed lower, and vice versa. A NEUTRAL outcome counts as a miss for a directional
        lean, which is what the contract says and is the conservative direction.

        `hit` is still not used, for the reason below - that part was right.
        """
        want_contract = str(target_contract or _tc.TRAINING_CONTRACT)
        era_clause, era_mode = self._era_clause()
        self.era_mode = era_mode
        conn = database._connect()
        fitted_any = False
        try:
            for h in HORIZONS:
                try:
                    # LABEL != the `hit` column. `hit` is dual-semantic: for gated rows
                    # (signal NEUTRAL, raw lean UP/DOWN - the majority) hit = avoid_success,
                    # which is TRUE when the lean was WRONG. Fitting on `hit` would teach the
                    # calibrator an inverted map on exactly the rows it must learn from.
                    rows = conn.execute(f"""
                        SELECT confidence, regime, conviction,
                               CASE WHEN raw_direction = actual_direction
                                    THEN 1 ELSE 0 END
                        FROM predictions_{h}m
                        WHERE resolved AND raw_direction IN ('UP','DOWN')
                          AND actual_direction IS NOT NULL AND actual_direction <> ''
                          AND confidence IS NOT NULL AND confidence > 0
                          AND COALESCE(target_contract, ?) = ?
                          AND {era_clause}
                    """, (self.UNKNOWN_CONTRACT_SENTINEL, want_contract)).fetchall()
                except Exception as exc:
                    # A SWALLOWED QUERY FAILURE IS INDISTINGUISHABLE FROM NO EVIDENCE, and
                    # that is not a hypothetical: this fix first shipped referencing a column
                    # that did not exist yet, the exception was caught here, and the fit
                    # reported "waiting for samples" on every horizon forever. Silence is the
                    # one outcome a data-availability path must never produce.
                    logger.error(
                        "[PRECISION] %sm calibration query FAILED (not 'no rows'): %s",
                        h, exc)
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
                        fitted_any = True
                        logger.info(f"[PRECISION] {h}m calibrator ACTIVE (n={len(rows)}, "
                                    f"base rate {hit.mean():.3f})")
                    except Exception as e:
                        logger.warning(f"[PRECISION] {h}m isotonic fit failed: {e}")
                else:
                    logger.info(f"[PRECISION] {h}m calibrator waiting: "
                                f"{len(rows)}/{MIN_CALIB_SAMPLES} resolved leans "
                                f"under {want_contract}")
        finally:
            conn.close()
        # Provenance is EARNED by a fit that happened, not asserted by the code path that
        # intended one. A run that selected no admissible rows leaves the map declaring
        # UNRECORDED, and `is_admissible_for` keeps refusing - which is the correct answer.
        if fitted_any:
            self.fitted_under_contract = want_contract
            self.contract_provenance = "RECORDED"

    # ── inference-time API (cheap, no DB) ────────────────────────────────
    def calibrated(self, h: int, raw_conf: float, *, required_contract: str | None = None):
        """Calibrated P(correct) for this confidence, or None until active AND admissible.

        `required_contract` is enforced HERE, not by the caller. `is_admissible_for` existed and
        was correct, and had exactly zero production callers - only a test - so the serving path
        attached this map to every prediction while provenance was UNRECORDED. A guard every
        caller must remember to invoke is not a guard; it is documentation.

        Pass the contract the consumer actually needs. None preserves the old behaviour for
        consumers that genuinely do not care which question the map answers, and there should
        not be many.
        """
        if required_contract is not None and not self.is_admissible_for(required_contract):
            return None
        iso = self.calibrators.get(h)
        if iso is None or raw_conf <= 0:
            return None
        try:
            return float(iso.predict([raw_conf])[0])
        except Exception:
            return None

    def expected_precision(self, h: int, regime: str, conviction: float,
                           *, required_contract: str | None = None):
        """Shrunk empirical P(hit) for this (horizon, regime, conviction-bin).

        Same contract enforcement as `calibrated`: these bins are fitted by the same rule and
        carry the same provenance, so a consumer that may not use one may not use the other.
        """
        if required_contract is not None and not self.is_admissible_for(required_contract):
            return None
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
