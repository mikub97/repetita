"""
SM-2 with learning steps -- the scheduler this project started with.

Kept as a first-class backend, not as a legacy shim: it is fully explainable to
a learner ("you got it right, so we multiplied by your ease"), needs no training
data, and is five scalars of state. When someone wants to understand what a
scheduler *is*, this is the one to read.

Everything here is a pure function over a state dict: no database, no clock, no
randomness that is not injected. That is deliberate -- the scheduler is the one
part of the app where a subtle bug costs months of study before anyone notices.

Day granularity, not minutes: this is a study tool, not a drill sergeant. A card
that lapses comes back inside the same session via the session queue (interval 0
means "due today"), not via a sub-day interval.
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from typing import Any

from ..core.types import Rating

NAME = "sm2"
VERSION = 1

# --- tuning ---------------------------------------------------------------
# All of it lives here so it can be retuned without hunting through the code.
# Every knob has a row in docs/tuning.md saying which symptom it treats -- if you
# add one here, add it there too, or it may as well not exist.

MAX_INTERVAL = 90  # days
EASE_START, EASE_MIN, EASE_MAX = 2.5, 1.3, 2.8
LAPSE_PENALTY = 0.20
FUZZ = 0.05  # ±5%, so a batch added on one day doesn't all come back on one day

# A brand-new card used to jump straight to a one-day interval: seen once, on the
# day you learn it, and not again until tomorrow. For intensive study that wastes
# the best moment there is. With one learning step it comes back later in the
# same session before moving to the day scale.
LEARNING_STEPS = 1

# What a HARD answer does. See ADR-0002: in the app this was extracted from,
# HARD fell below the pass threshold and was therefore *identical* to AGAIN --
# a missing accent reset a 45-day interval to zero. That defeated the entire
# reason the grade exists. Here HARD passes, with a penalty:
HARD_MULTIPLIER = 1.2  # Anki's value; interval grows, but barely
HARD_EASE_PENALTY = 0.15

MATURE_DAYS = 21  # interval at which a card counts as mature
LEECH_LAPSES = 6  # lapses at which a card is flagged for rewriting

# Retirement: a card that reaches the maximum interval with a clean run of
# successes leaves the queue permanently. `reps` resets to 0 on any lapse, so
# reps >= RETIRE_CLEAN_REPS *is* "no lapses in the last N reviews".
RETIRE_AT_INTERVAL = 90
RETIRE_CLEAN_REPS = 5

# SM-2's easiness update was written against a 0-5 quality scale. We speak the
# four-grade scale (see core.types.Rating), so map back for the arithmetic only.
# This reproduces the original formula exactly for the grades it can produce.
_SM2_QUALITY = {Rating.AGAIN: 0, Rating.HARD: 2, Rating.GOOD: 3, Rating.EASY: 5}

# Module-level instance rather than the `random` module itself, so the type is
# one thing and tests can pass a seeded Random in its place.
_DEFAULT_RNG = random.Random()


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def new_state() -> dict[str, Any]:
    return {
        "ease": EASE_START,
        "interval": 0,
        "reps": 0,
        "lapses": 0,
        "due": None,
        "last": None,
    }


def review(
    state: dict[str, Any],
    rating: Rating,
    at: datetime,
    *,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """
    Apply one review outcome and return the new state.

    A lapse sets interval to 0, meaning `due` is today: the card is still due in
    this same session, and the queue reshuffles it back in.
    """
    rng = rng if rng is not None else _DEFAULT_RNG
    today = at.date()
    ease: float = float(state["ease"])
    interval: int = int(state["interval"])
    reps: int = int(state["reps"])
    lapses: int = int(state["lapses"])

    if rating is Rating.AGAIN:
        ease = _clamp(ease - LAPSE_PENALTY, EASE_MIN, EASE_MAX)
        reps, lapses, interval = 0, lapses + 1, 0
    elif rating is Rating.HARD and reps > LEARNING_STEPS:
        # Passed, but only just. The schedule advances slightly rather than
        # resetting; the ease takes the hit instead, so repeated near-misses
        # still slow the card down without erasing its history.
        ease = _clamp(ease - HARD_EASE_PENALTY, EASE_MIN, EASE_MAX)
        reps += 1
        interval = max(1, min(round(interval * HARD_MULTIPLIER), MAX_INTERVAL))
    else:
        reps += 1
        if reps <= LEARNING_STEPS:
            # Same session: due today, so the queue hands it back after a few
            # other cards. Deliberately not clamped up to one day.
            interval = 0
        elif reps == LEARNING_STEPS + 1:
            interval = 1
        elif reps == LEARNING_STEPS + 2:
            interval = 3
        else:
            fuzz = 1.0 + rng.uniform(-FUZZ, FUZZ)
            interval = max(1, min(round(interval * ease * fuzz), MAX_INTERVAL))
        quality = _SM2_QUALITY[rating]
        delta = 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)
        ease = _clamp(ease + delta, EASE_MIN, EASE_MAX)

    return {
        "ease": ease,
        "interval": interval,
        "reps": reps,
        "lapses": lapses,
        "due": (today + timedelta(days=interval)).isoformat(),
        "last": today.isoformat(),
    }


def due_at(state: dict[str, Any]) -> date | None:
    raw = state.get("due")
    return date.fromisoformat(raw) if raw else None


def interval_days(state: dict[str, Any]) -> int:
    return int(state.get("interval", 0))


def retrievability(state: dict[str, Any], at: datetime) -> float | None:
    """
    SM-2 has no memory model, so it cannot answer this.

    Returning None rather than a guess is the point: callers that need a real
    probability (the "shakiest 20 cards" warm-up, honest mastery figures) must
    degrade visibly rather than display a number that means nothing.
    """
    return None


def should_retire(state: dict[str, Any]) -> bool:
    return (
        int(state.get("interval", 0)) >= RETIRE_AT_INTERVAL
        and int(state.get("reps", 0)) >= RETIRE_CLEAN_REPS
    )


def is_leech(state: dict[str, Any]) -> bool:
    return int(state.get("lapses", 0)) >= LEECH_LAPSES


class SM2Scheduler:
    """Object form, satisfying `core.protocols.SchedulerBackend`."""

    name = NAME
    version = VERSION

    new_state = staticmethod(new_state)
    review = staticmethod(review)
    due_at = staticmethod(due_at)
    interval_days = staticmethod(interval_days)
    retrievability = staticmethod(retrievability)
