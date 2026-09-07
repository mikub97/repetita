"""
FSRS-6 -- the one backend here with an actual model of memory.

SM-2 and Leitner answer "when next?" from a rule about the last few answers.
FSRS keeps two numbers per card -- stability (how long the memory lasts) and
difficulty (how hard this item is for you) -- fitted over ~350M reviews, and can
therefore answer a question the other two structurally cannot: *how likely are
you to recall this right now?* See `retrievability()` below and ADR-0003 for why
that question is worth a dependency.

This module is a thin adapter, deliberately. The scheduling arithmetic belongs
to the `fsrs` package (MIT, `typing-extensions` only at runtime); what lives
here is the four decisions that make it fit this app:

1. **Day granularity.** `learning_steps` and `relearning_steps` are empty, so
   FSRS never returns a ten-minute interval. This is a study tool, not a drill
   sergeant -- a lapsed card comes back through the session queue, not through a
   countdown. Same decision as `sm2.LEARNING_STEPS`, reached the same way.
2. **One timezone boundary.** `Scheduler.review_card()` raises unless it is given
   a timezone-aware UTC datetime; day boundaries in this app are local. `_utc()`
   is the only place the two meet, and `due` is computed from the caller's own
   `at`, never from the UTC instant -- see the note on `review()`.
3. **No uninjected randomness.** FSRS's own fuzzing reads the global `random`
   module, which `srs/CLAUDE.md` rule 1 forbids. It is switched off and the same
   +/-5% fuzz SM-2 uses is applied here through an injectable `Random`.
4. **Opaque state.** FSRS's card is stored as its own dict under one key.
   Nothing outside this module may read it.

The FSRS *optimizer* (which would fit the 21 parameters to a real review log)
pulls torch, about 2GB, and is an optional extra: `pip install "repetita[optimizer]"`.
Nothing here imports it, and nothing in the serving path may. It also returns the
default parameters unchanged below roughly 512 reviews, so there is nothing to
gain from it yet.
"""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

from fsrs import Card, Scheduler
from fsrs import Rating as FSRSRating
from fsrs.card import CardDict

from ..core.types import Rating

NAME = "fsrs6"
VERSION = 1

# --- tuning ---------------------------------------------------------------
# Every knob has a row in docs/tuning.md saying which symptom it treats. If you
# add one here, add it there too, or it may as well not exist.

# The 21 FSRS-6 parameters are the library's defaults and are deliberately not
# repeated here: pinning a copy of them would silently freeze this backend at
# whatever `fsrs` shipped on the day it was written. They are fitted per-learner
# by the optimizer later, on the author's own review log.

DESIRED_RETENTION = 0.9  # probability of recall FSRS aims for at the due date
MAX_INTERVAL = 90  # days -- the same ceiling SM-2 uses, so the two are comparable
FUZZ = 0.05  # +/-5%, so a batch added on one day doesn't all come back on one day

# Empty, on purpose. See point 1 in the module docstring.
LEARNING_STEPS: tuple[timedelta, ...] = ()
RELEARNING_STEPS: tuple[timedelta, ...] = ()

# Our `Rating` is FSRS's 1-4 scale (see core.types.Rating), so the mapping is the
# identity. `test_the_rating_scales_are_the_same_scale` asserts that rather than
# trusting it: if `fsrs` ever renumbered, every interval this backend produced
# would be wrong by one grade and nothing would raise.
_RATINGS = {r: FSRSRating(int(r)) for r in Rating}

# One shared, stateless scheduler. Building it per review would be pure waste:
# it holds only the parameters, and `review_card()` copies the card it is given.
_SCHEDULER = Scheduler(
    desired_retention=DESIRED_RETENTION,
    learning_steps=LEARNING_STEPS,
    relearning_steps=RELEARNING_STEPS,
    maximum_interval=MAX_INTERVAL,
    enable_fuzzing=False,
)

# FSRS stamps each card with a creation-time id we have no use for -- our cards
# are keyed by their own ids in `store/`. Passing a fixed one keeps `review()`
# pure: `Card()` with no id calls `datetime.now()` *and* sleeps a millisecond.
_CARD_ID = 0

# Module-level instance rather than the `random` module itself, so the type is
# one thing and tests can pass a seeded Random in its place.
_DEFAULT_RNG = random.Random()


def _utc(at: datetime) -> datetime:
    """
    The single timezone boundary in this backend.

    `Scheduler.review_card()` raises `ValueError` unless its datetime is aware
    *and* UTC, while the rest of the app works in local days. Everything that
    crosses into `fsrs` goes through here; nothing that comes back out is used to
    decide a calendar day.

    A naive datetime is read as local wall-clock time. FSRS only ever looks at
    differences between review times, so labelling them all UTC gives the same
    elapsed days as labelling them all with the real local zone -- and the day a
    review is *recorded on* is never taken from this value.
    """
    return at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)


def _card(state: dict[str, Any], now: datetime) -> Card:
    blob = state.get("card")
    if blob:
        return Card.from_dict(cast(CardDict, blob))
    return Card(card_id=_CARD_ID, due=now)


def new_state() -> dict[str, Any]:
    """
    A card nobody has answered yet.

    No FSRS card is built here: an unseen card has no stability and no due date,
    and `Card()` would have to invent one from the clock.
    """
    return {"card": None, "interval": 0, "due": None, "last": None}


def review(
    state: dict[str, Any],
    rating: Rating,
    at: datetime,
    *,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """
    Apply one review outcome and return the new state.

    `at` is the moment of the review in the learner's own time. It is used twice,
    and the distinction is the bug this codebase has already shipped once (`hub`
    commit 5ef755c, an activity filed on the wrong day because UTC was used for a
    Brazilian evening):

    * converted to UTC for FSRS, which measures elapsed time and needs an
      unambiguous instant;
    * kept as-is for `due` and `last`, which are calendar days in the learner's
      own zone. A review at 23:00 in Sao Paulo belongs to that day, not to the
      next one in UTC.
    """
    rng = rng if rng is not None else _DEFAULT_RNG
    now = _utc(at)

    reviewed, _log = _SCHEDULER.review_card(_card(state, now), _RATINGS[rating], now)

    # With no learning or relearning steps every interval FSRS returns is a whole
    # number of days, so this loses nothing.
    interval = (reviewed.due - now).days
    interval = max(1, min(round(interval * (1.0 + rng.uniform(-FUZZ, FUZZ))), MAX_INTERVAL))
    # Keep FSRS's own copy of the due date consistent with the fuzzed one, so the
    # blob and the denormalised column can never disagree.
    reviewed.due = now + timedelta(days=interval)

    today = at.date()
    return {
        "card": reviewed.to_dict(),
        "interval": interval,
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
    Probability that the card would be recalled right now.

    This is the whole reason the backend exists: SM-2 and Leitner return `None`
    here because they have no memory model to ask, and callers that need a real
    probability -- the "shakiest twenty cards" warm-up, an honest mastery figure,
    a forecast -- have to degrade visibly rather than print a number that means
    nothing.

    `None` for a card that has never been answered: FSRS would return 0.0, which
    reads as "certainly forgotten" when the truth is "never learnt".
    """
    if not state.get("card"):
        return None
    r = _SCHEDULER.get_card_retrievability(_card(state, _utc(at)), _utc(at))
    return max(0.0, min(1.0, float(r)))


class FSRSScheduler:
    """Object form, satisfying `core.protocols.SchedulerBackend`."""

    name = NAME
    version = VERSION

    new_state = staticmethod(new_state)
    review = staticmethod(review)
    due_at = staticmethod(due_at)
    interval_days = staticmethod(interval_days)
    retrievability = staticmethod(retrievability)
