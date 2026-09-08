"""
When a card leaves the queue, and when it should be rewritten instead.

Both questions are **policy over what the store already records** -- the interval,
the lapse count, the recent ratings -- and not properties of any memory model.
That matters for where this lives. Putting `should_retire` on `SchedulerBackend`
would force Leitner and FSRS to hold an opinion neither has: FSRS's own view is
that nothing is ever finished, it just schedules further out, and Leitner has no
notion of a clean run at all. Asking a scheduler "is this card done?" is asking
the wrong object.

Kept in `core/` so `store` can reach it without importing `policies`, which would
invert the layering. Pure functions over plain values: no state dict is read
here, which is the same rule that lets a second backend be added without
touching anything else.
"""

from __future__ import annotations

from collections.abc import Sequence

from .types import Rating

#: A card that has reached the scheduler's ceiling with a clean run has nothing
#: left to prove. Paired with `sm2.MAX_INTERVAL`: retiring below the ceiling
#: would take cards out while they were still being usefully scheduled, and
#: above it is unreachable.
RETIRE_AT_INTERVAL = 90

#: How long that clean run has to be. Read from the review log rather than from a
#: `reps` counter, because `reps` is SM-2's word for it and other backends do not
#: keep one -- but every backend writes to the same log.
RETIRE_CLEAN_REVIEWS = 5

#: Lapses at which a card is not hard but wrong. Lower than Anki's 8 on purpose:
#: by the sixth failure the problem is usually the *item* -- an ambiguous gap, a
#: cue that does not narrow -- and drilling it further teaches the learner to
#: guess rather than teaching them the language.
LEECH_LAPSES = 6


def earned(interval_days: int, recent: Sequence[Rating]) -> bool:
    """
    Has this card earned its way out of the queue?

    `recent` is the card's own ratings, newest first. Too few to judge on means
    no: a card cannot retire before it has been answered enough times to have a
    clean run, however long its interval has grown.
    """
    if interval_days < RETIRE_AT_INTERVAL:
        return False
    window = list(recent[:RETIRE_CLEAN_REVIEWS])
    if len(window) < RETIRE_CLEAN_REVIEWS:
        return False
    return all(rating.passed for rating in window)


def is_leech(lapses: int) -> bool:
    """Has this card failed often enough that it is the card's fault?"""
    return lapses >= LEECH_LAPSES
