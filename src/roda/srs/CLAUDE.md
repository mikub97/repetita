# CLAUDE.md — src/roda/srs/

Scheduler backends. **The highest-risk directory in the repository.**

A bug here does not crash and does not show up in a screenshot. It quietly
lengthens or shortens intervals, and by the time anyone notices, months of study
have gone through the wrong schedule and cannot be redone. Treat every change
here as you would a migration.

## Rules

1. **Pure functions only.** No database, no `datetime.now()`, no `random` that is
   not injected. Everything takes the current time as an argument. This is what
   makes the whole directory testable and it is not negotiable.
2. **`review()` must not mutate its input.** Return a new dict.
   `test_review_is_pure` pins this.
3. **State is private to its backend.** Nothing outside `srs/` may read a key out
   of a state dict. If a caller needs a value, add a method to the protocol.
   `tests/srs/test_protocol.py` is written to enforce this by example: it drives
   every backend through the protocol alone.
4. **State must be JSON-serialisable.** It is stored in one column.
5. **If you change the protocol, `leitner.py` must still satisfy it in ~15
   lines.** That backend has no other purpose. If satisfying the new protocol
   requires giving Leitner a memory model it does not have, the protocol has
   grown a dependency on FSRS and the change is wrong.

## Before changing tuning constants

Read [ADR-0002](../../../docs/architecture/decisions/0002-hard-is-a-pass.md) and
`docs/tuning.md`. Every constant is meant to have a recorded reason and, where
possible, the measurement that set it. `HARD_MULTIPLIER` and `HARD_EASE_PENALTY`
are currently conventional rather than measured, and are labelled as such —
measuring them against real review data is a legitimate task, guessing at them is
not.

## What does not belong here

* Which cards go into today's session — that is `policies/`.
* How hard a question should be — that is `difficulty/`.
* Whether an answer was correct — that is `graders/`.

`srs/` answers exactly one question: given a rating and a state, when next?
