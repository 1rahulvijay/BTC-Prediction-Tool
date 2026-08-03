# PREREG C — OPEN_POSITION_ACTION_VALUE_V1

**Frozen `2026-08-02`, before the forward window opens.** Any edit invalidates every result
scored under it; the hash in `docs/active/PREREG_HASH.txt` is checked in CI.

## Question

Given an **existing position**, can dynamic management capture value that fixed entry/exit
rules miss?

## The gap this targets

Measured (`RESEARCH_LEDGER` §4.6, §4.8, §4.9):

```
perfect-exit ceiling        +0.1005 / share
best fixed rule             -0.0105 / share   (HOLD_TO_SETTLEMENT)
classifier at AUC 0.8731    -0.0107 / share   (loses to doing nothing)
EV rule on magnitude        -0.0215 / share   (loses to random at matched count)
```

The ceiling is real and large; two pre-declared rules captured none of it. The diagnosis was
that **sign is predictable (AUC 0.8731) and magnitude is not (AUC 0.5831)**.

This is the only surviving control problem, and it is now collectable because every action is
recorded against **one causal same-time state**.

## Arms — the same inventory, the same timestamp, every action

```
HOLD        keep the position to settlement
EXIT        sell at the executable bid now
REDUCE_50   sell half
SWITCH      exit and buy the opposite side
LOCK        buy the complement, fixing the payout at $1
```

Every arm is valued from the **same** position, at the **same** instant, from the **same**
recorded ladder. That is what makes them comparable; valuing them from different entry
populations is the error that would make a dynamic policy look better than a fixed one for
reasons unrelated to management.

## Champion

`HOLD` — because it is the best fixed rule measured (−0.0105/share). Not `WAIT`: this protocol
concerns positions already open, so standing aside is not an available action.

## Required result

1. beats `HOLD` on realised net value per share
2. beats a matched-count random action policy
3. positive day-block lower bound on `FORWARD_UNTOUCHED` rows
4. reports **capacity honestly**: exit-side size was never recorded historically
   (`execution_cost.exit_fill` returns `capacity_known = False`). If the forward recorder does
   not capture bid-side depth, the result is reported per share and explicitly **not** sized.

## Hindsight arms are reported and never selectable

`ORACLE_BEST_EXIT` and `ORACLE_PICK_AMONG_TRADEABLE` are computed as **bounds**. They carry
`requires_hindsight = True` and `action_value.select()` excludes them before comparing. They
answer "how much was available", never "what should have been done".

## Population and gate

`FORWARD_UNTOUCHED` rows with an open paper position and a recorded action snapshot. Same data
gate as Preregistration A.

## Stopping rule

Scored once, when the data gate passes.
