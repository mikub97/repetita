"""
Behaviour specific to the FSRS-6 backend.

The protocol tests (`test_protocol.py`) already prove this backend is a
scheduler. What is tested here is the part that made it worth a dependency --
retrievability -- and the four things about wiring FSRS into this app that fail
silently if they are wrong: the timezone boundary, the absence of sub-day steps,
the rating scale, and the optimizer staying out of the serving path.
"""

import datetime as dt
import importlib.metadata as md
import itertools
import json
import random
import sys

import fsrs
import pytest

from repetita import srs
from repetita.core.types import Rating
from repetita.srs import fsrs_backend

AT = dt.datetime(2026, 9, 6, 12, 0, tzinfo=dt.UTC)
SAO_PAULO = dt.timezone(dt.timedelta(hours=-3))


def seeded() -> random.Random:
    """Fuzz is +/-5%; a seed keeps assertions about exact intervals meaningful."""
    return random.Random(20260906)


def studied(days: tuple[int, ...] = (0, 1, 3, 7), rating: Rating = Rating.GOOD) -> dict:
    """A card answered on several days, so it has a stability worth asking about."""
    rng = seeded()
    state = fsrs_backend.new_state()
    for offset in days:
        state = fsrs_backend.review(state, rating, AT + dt.timedelta(days=offset), rng=rng)
    return state


# --- the reason this backend exists ---------------------------------------


def test_retrievability_is_a_probability():
    state = studied()
    for offset in (0, 1, 5, 30, 365, 3650):
        r = fsrs_backend.retrievability(state, AT + dt.timedelta(days=7 + offset))
        assert r is not None
        assert 0.0 <= r <= 1.0


def test_retrievability_decreases_as_time_passes_since_the_last_review():
    """
    The one thing SM-2 structurally cannot do, and the whole point of FSRS.

    A forgetting curve is monotonically decreasing: the longer it has been since
    the last review, the less likely recall is. If this ever comes out flat or
    rising, the memory model is not connected to anything and every number the
    app shows a learner about how shaky a card is would be noise.
    """
    state = studied()
    last_review = AT + dt.timedelta(days=7)
    curve = [
        fsrs_backend.retrievability(state, last_review + dt.timedelta(days=d))
        for d in (0, 1, 2, 3, 5, 10, 21, 60, 180, 365)
    ]
    assert all(r is not None for r in curve)
    for earlier, later in itertools.pairwise(curve):
        assert later < earlier, f"retrievability did not decay: {curve}"
    assert curve[0] == pytest.approx(1.0)
    assert curve[-1] < 0.9


def test_a_stronger_card_is_forgotten_more_slowly():
    """Stability has to mean something: EASY answers must outlast AGAIN ones."""
    later = AT + dt.timedelta(days=7, hours=1)
    strong = fsrs_backend.retrievability(studied(rating=Rating.EASY), later + dt.timedelta(days=30))
    weak = fsrs_backend.retrievability(studied(rating=Rating.AGAIN), later + dt.timedelta(days=30))
    assert strong is not None and weak is not None
    assert strong > weak


def test_an_unanswered_card_has_no_retrievability():
    """
    `None`, not 0.0.

    FSRS returns 0.0 for a card with no review history, which reads as "certainly
    forgotten" when the truth is "never learnt". The protocol's `None` says the
    model cannot answer, which is what callers need to hear.
    """
    assert fsrs_backend.retrievability(fsrs_backend.new_state(), AT) is None


def test_the_other_backends_still_refuse_to_guess():
    """Adding a backend that can answer must not tempt the ones that cannot."""
    for name in ("sm2", "leitner"):
        backend = srs.get(name)
        state = backend.review(backend.new_state(), Rating.GOOD, AT)
        assert backend.retrievability(state, AT) is None


# --- the timezone boundary ------------------------------------------------


def test_the_review_day_is_the_local_day_not_the_utc_one():
    """
    Regression, from the predecessor (`hub` commit 5ef755c).

    An evening in Sao Paulo is already tomorrow in UTC. A review at 23:00 on the
    3rd belongs to the 3rd; taking the day from the UTC instant filed it on the
    4th and the learner's streak broke while he was studying.
    """
    late = dt.datetime(2026, 9, 3, 23, 0, tzinfo=SAO_PAULO)
    assert late.astimezone(dt.UTC).date() == dt.date(2026, 9, 4)  # the trap

    state = fsrs_backend.review(fsrs_backend.new_state(), Rating.GOOD, late, rng=seeded())
    due = fsrs_backend.due_at(state)
    assert due is not None
    assert due == dt.date(2026, 9, 3) + dt.timedelta(days=fsrs_backend.interval_days(state))


def test_aware_non_utc_and_naive_datetimes_are_both_accepted():
    """
    `Scheduler.review_card()` raises unless it is handed aware UTC.

    Callers here pass local time, aware or not -- the other backends take
    whatever they are given. If the conversion were missing, this would raise
    `ValueError` rather than schedule anything.
    """
    for at in (
        dt.datetime(2026, 9, 6, 9, 0, tzinfo=SAO_PAULO),
        dt.datetime(2026, 9, 6, 9, 0, tzinfo=dt.timezone(dt.timedelta(hours=2))),
        dt.datetime(2026, 9, 6, 9, 0),
    ):
        state = fsrs_backend.review(fsrs_backend.new_state(), Rating.GOOD, at, rng=seeded())
        assert fsrs_backend.due_at(state) == at.date() + dt.timedelta(
            days=fsrs_backend.interval_days(state)
        )


def test_the_same_moment_in_two_zones_schedules_the_same_number_of_days():
    """The conversion must not smuggle the offset into the interval itself."""
    utc = dt.datetime(2026, 9, 6, 12, 0, tzinfo=dt.UTC)
    same = utc.astimezone(SAO_PAULO)
    a = fsrs_backend.review(fsrs_backend.new_state(), Rating.GOOD, utc, rng=seeded())
    b = fsrs_backend.review(fsrs_backend.new_state(), Rating.GOOD, same, rng=seeded())
    assert fsrs_backend.interval_days(a) == fsrs_backend.interval_days(b)


# --- day granularity ------------------------------------------------------


def test_there_are_no_sub_day_steps():
    """
    Configuration assertion, because the default is not what we want.

    `fsrs.Scheduler` ships with one-minute and ten-minute learning steps. Day
    granularity is a product decision -- a study tool, not a drill sergeant -- and
    a card that lapses comes back inside the same session through the queue, not
    through a countdown.
    """
    assert fsrs_backend._SCHEDULER.learning_steps == ()
    assert fsrs_backend._SCHEDULER.relearning_steps == ()


@pytest.mark.parametrize("rating", list(Rating))
def test_every_interval_is_at_least_a_whole_day(rating):
    state = fsrs_backend.new_state()
    rng = seeded()
    for offset in range(6):
        state = fsrs_backend.review(state, rating, AT + dt.timedelta(days=offset), rng=rng)
        assert fsrs_backend.interval_days(state) >= 1


def test_a_lapse_shortens_the_schedule_and_never_lengthens_it():
    state = studied(days=(0, 1, 3, 7, 21))
    before = fsrs_backend.interval_days(state)
    after = fsrs_backend.interval_days(
        fsrs_backend.review(state, Rating.AGAIN, AT + dt.timedelta(days=28), rng=seeded())
    )
    assert after < before


def test_intervals_stay_under_the_ceiling():
    """The same 90-day ceiling SM-2 uses, so the two backends can be compared."""
    state = studied(days=tuple(range(0, 900, 30)), rating=Rating.EASY)
    assert 1 <= fsrs_backend.interval_days(state) <= fsrs_backend.MAX_INTERVAL


# --- the rating scale -----------------------------------------------------


def test_the_rating_scales_are_the_same_scale():
    """
    Asserted, not assumed.

    Our `Rating` was defined as FSRS's 1-4 scale, so the mapping is the identity.
    If `fsrs` ever renumbered, every interval this backend produced would be off
    by one grade and nothing anywhere would raise.
    """
    assert [(r.name.upper(), int(r)) for r in fsrs.Rating] == [
        (r.name.upper(), int(r)) for r in Rating
    ]
    for rating in Rating:
        assert int(fsrs_backend._RATINGS[rating]) == int(rating)


def test_better_ratings_schedule_further_out():
    """Sanity on the mapping's direction: AGAIN < HARD <= GOOD <= EASY."""
    intervals = [
        fsrs_backend.interval_days(
            fsrs_backend.review(studied(), rating, AT + dt.timedelta(days=7), rng=seeded())
        )
        for rating in Rating
    ]
    assert intervals == sorted(intervals)
    assert intervals[0] < intervals[-1]


# --- keeping the dependency where it belongs ------------------------------


def test_the_optimizer_is_not_in_the_serving_path():
    """
    `fsrs[optimizer]` pulls torch, about 2GB.

    It is an optional extra, it does nothing below roughly 512 reviews, and
    importing it anywhere reachable from a request would make the app unshippable.
    """
    assert "torch" not in sys.modules
    assert "fsrs.optimizer" not in sys.modules


def test_the_major_version_pin_still_holds():
    """
    FSRS-7 changes behaviour: fractional intervals, 34 parameters.

    That has to be an explicit, tested migration rather than a transitive
    upgrade, which is what `fsrs>=6.3,<7` in pyproject.toml is for. This test is
    the one that notices if the pin is ever widened by accident.
    """
    assert md.version("fsrs").startswith("6.")


# --- state ----------------------------------------------------------------


def test_review_does_not_mutate_its_input():
    state = studied()
    before = json.dumps(state, sort_keys=True)
    fsrs_backend.review(state, Rating.AGAIN, AT + dt.timedelta(days=8), rng=seeded())
    assert json.dumps(state, sort_keys=True) == before


def test_state_survives_a_round_trip_through_json():
    """It is stored as one column, and a resumed card must schedule identically."""
    state = json.loads(json.dumps(studied()))
    resumed = fsrs_backend.review(state, Rating.GOOD, AT + dt.timedelta(days=14), rng=seeded())
    direct = fsrs_backend.review(studied(), Rating.GOOD, AT + dt.timedelta(days=14), rng=seeded())
    assert resumed == direct


def test_scheduling_is_deterministic_given_its_inputs():
    """
    Rule 1 of `srs/CLAUDE.md`: no randomness that is not injected.

    FSRS's own fuzzing reads the global `random` module, so it is switched off and
    the fuzz is applied here through a `Random` the caller can supply.
    """
    assert fsrs_backend._SCHEDULER.enable_fuzzing is False
    a = fsrs_backend.review(studied(), Rating.GOOD, AT + dt.timedelta(days=9), rng=random.Random(7))
    b = fsrs_backend.review(studied(), Rating.GOOD, AT + dt.timedelta(days=9), rng=random.Random(7))
    assert a == b


# --- scope ----------------------------------------------------------------


def test_the_backend_is_registered_but_nothing_switched_to_it():
    """
    Adding FSRS does not adopt it.

    Which scheduler a course uses is configuration; the choice for real study is
    made on a real review log (ADR-0003), not by an import landing in the tree.
    """
    assert srs.get("fsrs6") is not None
    assert "fsrs6" in srs.names()
    assert srs.DEFAULT == "sm2"
    assert srs.get().name == "sm2"
