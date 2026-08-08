# The directional tilt is a distribution shift, not a boundary effect — `2026-08-08`

Follow-up to `LIVE_ROUND_EDGE_AUDIT_2026-08-08.md`, which found the served lean is UP 68.8%
of the time against a market that settles UP 50.3%, at the same strength in an uptrend
(78.0%) as in a downtrend (77.4%).

This locates the mechanism, and **refutes the remedy the code recommends for it**.

---

## The mechanism: the probabilities themselves are shifted

Measured on 2,514 served 5m predictions carrying `prob_up` and `prob_down`:

```text
mean prob_up      0.3430
mean prob_down    0.2920      a +5.1pp gap

prob_up - prob_down
   5th pct   -0.1303
  25th pct   -0.0206
  median     +0.0581
  75th pct   +0.1093
  95th pct   +0.2425
```

`prob_up > prob_down` on **69.5%** of rows — which is the 68.8% UP-lean seen on the recorded
rounds. The lean is not being distorted downstream; it is faithfully reporting a tilted
probability.

---

## The remedy in the code is counterproductive

`model.py` carries a `BTC_DIR_MARGIN` dead zone with this instruction:

> *Default 0.0 == prior behavior; set BTC_DIR_MARGIN (~0.015) to neutralize the tilt, then
> re-check with probe_direction_tilt before trusting it (measure-before-gate).*

The margin assumes the tilt is a **small offset concentrated near the boundary**, so
excluding near-ties would remove it. It is not. It is a uniform shift, and excluding
near-ties therefore selects *more* of the biased side:

```text
margin 0.000  -> 100.0% of rows survive   UP share 69.5%
margin 0.015  ->  91.1%                   UP share 71.1%
margin 0.050  ->  71.3%                   UP share 74.8%
margin 0.100  ->  36.9%                   UP share 76.8%
```

**Setting `BTC_DIR_MARGIN` would make the skew worse while shrinking the sample.** The 0.0
default is correct. The measure-before-gate step the comment demanded is now done, and the
answer is *don't*. The knob is kept only so this record has something to attach to; the
refutation is written at the knob itself.

That is the sixth stated remedy in this audit series whose direction was wrong. The pattern
is consistent: the remedy encodes a hypothesis about the mechanism, and the hypothesis is
what needed checking.

---

## A separate, real defect: the margin was bypassed

`price_to_beat._bet_lean` decides the displayed side. When the head returns NEUTRAL it fell
through to:

```python
return "UP" if pu >= pd else "DOWN"
```

That is **exactly the bare comparison the dead zone exists to prevent**, performed on
precisely the rows the head had just refused to call. A margin applied at one site and
bypassed at the next is not a margin. It now applies the same per-horizon margin, and inside
it returns NEUTRAL — which is what this function's own last line already calls "no
probability signal at all".

**This changes nothing at the shipped default of 0.0, and it does not fix the tilt.** It is
a consistency fix. Saying otherwise would be the same error as the margin remedy itself.

It is consistent with the measured split, though: fallback leans (the ones this branch
produces) are **71.9%** UP, against **65.9%** for committed model leans — the bypassed branch
is the more biased of the two.

---

## Where the tilt has to be fixed

Not at the serving margin, and not in the display. In the probabilities, which means one of:

1. **The class-balanced training.** `MODEL_ARCH_VERSION` contains `classbal`, so the
   tempered-prior correction at `model.py` is deliberately switched off on the grounds that
   "the loss-level fix already removes the prior". These numbers say the loss-level fix did
   not remove it. That claim is checkable directly against the training labels and the
   holdout predictions, and it is the first thing to check.
2. **The ensemble blend.** Seven seats are averaged; if the tilt is concentrated in a subset,
   the per-seat `model_probabilities` already logged would show it.
3. **A feature asymmetry.** Least likely to be diagnosed cheaply, and only worth reaching for
   after the first two are excluded.

Note the sign check that makes (1) the leading candidate: the 5m train-slice priors are
near symmetric (`DOWN 0.269 / NEUTRAL 0.460 / UP 0.270`), so the *labels* are not tilted.
A +5.1pp gap in the outputs from near-symmetric labels is a property the fitting introduced.

---

## What this does not claim

The tilt is not, by itself, the reason there is no edge. A perfectly balanced lean on this
head would still be a coin flip — the bakeoff ceiling and the microstructure probe both say
the directional information is not there. Removing the tilt would make the lean *honest*, not
*profitable*. It matters because a systematically tilted output makes every downstream
measurement harder to read, not because de-tilting is expected to produce a signal.

---

# Found while chasing this: seat votes had two graders

`database.update_outcome` carries this docstring:

> *`actual_direction` is the graded result, not the sign of `actual_move`. Under first touch
> a lean can be right by the contract — it reached the upper barrier first — while the
> endpoint closed lower. Consumers that re-derived direction from the move were answering the
> endpoint question about a first-touch model.*

Three lines below it:

```python
strict_direction = "UP" if float(actual_move or 0.0) >= 0.0 else "DOWN"
conn.execute("UPDATE model_predictions SET actual_direction = ?, "
             "hit = CASE WHEN direction IN ('UP','DOWN') THEN (direction = ?) ... ")
```

**A function whose docstring names the defect and then commits it, on the next table.** And
`actual_move` is `resolution_price - entry`, which under first touch is the *barrier* — so
that "endpoint sign" was the barrier side on touching rows and the closing residual on
timeouts. The same mixture found in `lean_hit`.

Worse, it was the **second** writer. `model_verifier.check_and_verify` already grades those
rows through `tc.grade()` under each vote's own declared contract, refusing when it cannot,
and writes them via `resolve_model_prediction`. Two writers on one column means the later one
wins and they can disagree.

Now: the contract's direction or nothing, and never over a row the contract grader has
already resolved.

```python
graded_direction = str(actual_direction or "")
if graded_direction in ("UP", "DOWN", "NEUTRAL"):
    ... WHERE starts_with(id, ?) AND resolved = FALSE
```

`test_model_metrics_integrity.py` encoded the old rule and failed — correctly. Its stated
intent was the NEUTRAL-abstention rule, not the grading rule, so both are pinned now: without
a contract direction nothing is graded; with one, committed votes are graded and NEUTRAL
stays NULL; and a second pass with the opposite direction cannot overwrite a resolved row.
2/2 mutation.

## A note on the data

`actual_direction` is empty on all 2,512 resolved `predictions_5m` rows, because they were
resolved between 2026-06-12 and **2026-07-04**, before the column was populated. The
contract-aware `lean_hit` backfill therefore does nothing on this database — which is the
correct fail-closed behaviour (it refuses rather than inventing an answer), but it means the
1,230 NULL `lean_hit` rows stay NULL and cannot be recovered.

Also worth noting on its own: the newest resolved prediction is over a month old, while
`price_to_beat` rounds run to 2026-08-07. The fast round ticker has been recording; the heavy
prediction loop's resolution path has not.
