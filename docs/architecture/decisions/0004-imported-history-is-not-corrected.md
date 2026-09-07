# ADR-0004: Imported history is not corrected

**Status:** accepted, 2026-09-07
**Context:** [ADR-0002](0002-hard-is-a-pass.md), issue #6

## Context

ADR-0002 fixed a scheduling bug in the predecessor: `HARD` fell below the pass
threshold and was handled identically to `AGAIN`, so an accent-only mistake
erased a card's interval and recorded a lapse.

Measured on the real data before the rewrite:

```
454 reviews total
  AGAIN (0)  141   31.1%
  HARD  (2)    7    1.5%   <- the affected answers
  GOOD  (3)  306   67.4%

6 distinct cards, 7 events
```

The review log records which answers were `HARD` rather than `AGAIN`, so those
six cards' schedules could be recomputed under the corrected rule at import time.

Three options were on the table: leave the history intact; replay those six cards
under the new rule; or import unchanged but flag them for inspection.

## Decision

**Leave the history intact.** The importer copies what happened, faithfully,
including the six schedules the bug reset.

## Why

* **The blast radius is small and self-healing.** Seven answers out of 454. All
  six cards are still in circulation, so each is re-answered within days and the
  scheduler corrects itself over a few reviews. The cost of doing nothing is a
  handful of cards coming up sooner than they strictly needed to — which is the
  cheap direction to be wrong in.

* **Silently rewriting someone's study history is worse than the bug it
  corrects.** A review log that has been retroactively adjusted is no longer a
  record of what happened; it is a record of what the current rules say should
  have happened. Every later question asked of that data — how accuracy moved,
  when the gate closed, whether FSRS predicts better than SM-2 on this learner —
  gets a subtly wrong answer, and nothing in the data says so.

* **It cannot be verified afterwards.** Once the schedules are rewritten, the
  original is gone and there is nothing left to compare against. A correction
  that cannot be checked is a claim, not a fix.

## Consequences

* `import-hub` performs no corrective rescheduling. Imported `HARD` answers keep
  the lapse they caused at the time; the new rules apply only from the first
  answer given in this engine.
* This is the general policy, not a one-off: **the review log is append-only and
  is never edited to match a later understanding of the rules.** A future bug of
  this shape gets the same treatment, and a corrective pass, if one is ever
  genuinely warranted, is a new command that writes new rows rather than an
  importer that quietly changes old ones.
* If the six cards are ever worth inspecting, they are findable:
  `SELECT card_id FROM review_log WHERE rating = 2 AND algo = 'sm2-legacy'`.
