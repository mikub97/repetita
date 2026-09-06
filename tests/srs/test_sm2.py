import datetime as dt
import random

import pytest

from roda.core.types import Rating
from roda.srs import sm2

AT = dt.datetime(2026, 9, 6, 12, 0, tzinfo=dt.UTC)
NO_FUZZ = random.Random(0)


def mature(interval=45, reps=6, ease=2.5, lapses=0):
    return {
        "ease": ease,
        "interval": interval,
        "reps": reps,
        "lapses": lapses,
        "due": "2026-09-06",
        "last": "2026-08-01",
    }


def test_new_card_returns_within_the_same_session():
    # interval 0 means "due today": the queue hands it back a few cards later,
    # which is the whole point of a learning step.
    s = sm2.review(sm2.new_state(), Rating.GOOD, AT)
    assert s["interval"] == 0
    assert s["due"] == "2026-09-06"


def test_ladder_climbs_zero_one_three():
    s = sm2.new_state()
    intervals = []
    for _ in range(3):
        s = sm2.review(s, Rating.GOOD, AT, rng=NO_FUZZ)
        intervals.append(s["interval"])
    assert intervals == [0, 1, 3]


def test_again_resets_the_schedule_and_records_a_lapse():
    s = sm2.review(mature(), Rating.AGAIN, AT)
    assert s["interval"] == 0
    assert s["reps"] == 0
    assert s["lapses"] == 1
    assert s["ease"] == pytest.approx(2.30)


def test_hard_does_not_reset_a_mature_card():
    """
    Regression, and the reason this backend is not a byte-for-byte port.

    In the app this was extracted from, HARD sat below the pass threshold and so
    was handled identically to AGAIN: a missing accent on a card with a 45-day
    interval reset it to zero and cost a lapse. That defeats the entire purpose
    of having a middle grade -- and the grading code's own docstring said the
    opposite of what the scheduler did. See ADR-0002.
    """
    before = mature(interval=45)
    after = sm2.review(before, Rating.HARD, AT)

    assert after["interval"] > 0, "a missing accent must not erase the schedule"
    assert after["lapses"] == before["lapses"], "HARD is a pass, not a lapse"
    assert after["reps"] == before["reps"] + 1
    # It still costs something -- the ease takes the hit instead of the interval.
    assert after["ease"] < before["ease"]
    assert after["interval"] < 45 * before["ease"], "but it advances far less than GOOD"


def test_hard_differs_from_again():
    h = sm2.review(mature(), Rating.HARD, AT)
    a = sm2.review(mature(), Rating.AGAIN, AT)
    assert (h["interval"], h["lapses"]) != (a["interval"], a["lapses"])


def test_ease_has_a_floor():
    s = mature(ease=sm2.EASE_MIN)
    for _ in range(10):
        s = sm2.review(s, Rating.AGAIN, AT)
    assert s["ease"] == pytest.approx(sm2.EASE_MIN)


def test_interval_is_capped():
    s = mature(interval=sm2.MAX_INTERVAL, reps=9)
    s = sm2.review(s, Rating.EASY, AT)
    assert s["interval"] <= sm2.MAX_INTERVAL


def test_retires_after_a_clean_run_at_the_maximum_interval():
    s = mature(interval=sm2.MAX_INTERVAL, reps=sm2.RETIRE_CLEAN_REPS)
    assert sm2.should_retire(sm2.review(s, Rating.GOOD, AT))


def test_leech_is_flagged_by_lapses():
    assert not sm2.is_leech(mature(lapses=sm2.LEECH_LAPSES - 1))
    assert sm2.is_leech(mature(lapses=sm2.LEECH_LAPSES))


def test_review_is_pure():
    before = mature()
    snapshot = dict(before)
    sm2.review(before, Rating.GOOD, AT, rng=NO_FUZZ)
    assert before == snapshot, "review() must not mutate the state it is given"


def test_sm2_cannot_report_retrievability():
    # Not an oversight: a scheduler with no memory model must say so rather than
    # return a number that looks like a probability and is not one.
    assert sm2.retrievability(mature(), AT) is None
