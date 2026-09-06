# ADR-0002: HARD is a pass, not a lapse

**Status:** accepted, 2026-09-06
**Supersedes:** the behaviour of `hub/pt/srs.py` in the app this was extracted from

## Context

Grading produces four outcomes. Two are unambiguous: the answer was right, or it
was wrong. The middle one exists for the cases that are neither —

* a typed word differing from the expected one only by an accent
  (`amanha` for `amanhã`), and
* a whole-sentence answer with one word off out of ten.

The original code named this `GRADE_HARD = 2` and documented its purpose
clearly. From `grade_typed`:

> The middle grade exists because "hoje" vs "hojé" is a real but minor error --
> counting it as a full failure would reset months of scheduling over a missing
> tilde, and counting it as correct would let accent mistakes ossify.

and from the sentence-slack constant:

> Up to SENTENCE_SLACK differing tokens still counts as GRADE_HARD rather than a
> miss: retyping ten words and losing three months of schedule to one article
> would make the type unusable.

**The scheduler did not implement any of that.** `schedule()` computed
`ok = grade >= PASS_THRESHOLD` with `PASS_THRESHOLD = 3` and `GRADE_HARD = 2`,
so HARD fell into the failure branch — the *same* branch as AGAIN, with no
distinction between them whatsoever:

```
card: interval 45, reps 6, lapses 0, ease 2.5

  GOOD    -> interval 90, ease 2.36, reps 7, lapses 0
  HARD    -> interval  0, ease 2.30, reps 0, lapses 1     <- identical to AGAIN
  AGAIN   -> interval  0, ease 2.30, reps 0, lapses 1
```

A missing accent erased a 45-day interval. Every consequence the two docstrings
said must not happen, happened on every such answer. No test covered it: the
suite asserted that `grade_typed` *returns* HARD, never what the scheduler then
did with it.

The distinction was therefore real in the grading layer, real in the verdict
shown to the learner, and inert everywhere it mattered.

## Decision

HARD passes.

* The interval advances, but barely — `× 1.2` (Anki's value) rather than
  `× ease`.
* `reps` is not reset and no lapse is recorded.
* The cost lands on the ease instead: `−0.15`. Repeated near-misses still slow a
  card down, without erasing its history.

`Rating.HARD.passed` is `True`, and `tests/srs/test_sm2.py::test_hard_does_not_reset_a_mature_card`
pins it.

## Consequences

* This backend is **not** a byte-for-byte port of the original, and the phase-2
  parity test must exclude schedules that pass through a HARD answer. That is the
  intended divergence, not a failure of the port.
* Cards in the imported history that were reset by a HARD answer stay reset. The
  information needed to undo it — which of the 454 imported reviews were HARD
  rather than AGAIN — is in the review log, so a corrective re-schedule is
  possible later; it is not done automatically, because silently rewriting
  someone's study history is worse than the bug.
* Anyone tuning `HARD_MULTIPLIER` or `HARD_EASE_PENALTY` should read this file
  first: the values are conventional, not measured. Measuring them against real
  review data is a good issue for later.

## Notes

The general lesson is worth recording separately from the fix: **a constant that
encodes a policy needs a test on the policy, not on the constant.** The original
suite tested that a function returned the value `2`. It never tested that
returning `2` meant anything.
