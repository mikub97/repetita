"""
Property tests for the schedulers.

These are worth more than a hundred example tests, because the failure mode that
matters -- intervals drifting somewhere in a corner of the state space -- is
invisible until months of study have gone through it.
"""

import datetime as dt

from hypothesis import given, settings
from hypothesis import strategies as st

from roda import srs
from roda.core.types import Rating

AT = dt.datetime(2026, 9, 6, 12, 0, tzinfo=dt.UTC)

ratings = st.sampled_from(list(Rating))
backends = st.sampled_from(srs.names())


@settings(max_examples=200)
@given(name=backends, history=st.lists(ratings, max_size=40))
def test_due_is_never_before_the_review_that_set_it(name, history):
    backend = srs.get(name)
    state = backend.new_state()
    for rating in history:
        state = backend.review(state, rating, AT)
        due = backend.due_at(state)
        assert due is not None and due >= AT.date()


@settings(max_examples=200)
@given(name=backends, history=st.lists(ratings, max_size=40))
def test_interval_stays_within_bounds(name, history):
    backend = srs.get(name)
    state = backend.new_state()
    for rating in history:
        state = backend.review(state, rating, AT)
        assert 0 <= backend.interval_days(state) <= 36500


@settings(max_examples=200)
@given(history=st.lists(ratings, max_size=40))
def test_sm2_ease_stays_between_its_floor_and_ceiling(history):
    from roda.srs import sm2

    state = sm2.new_state()
    for rating in history:
        state = sm2.review(state, rating, AT)
        assert sm2.EASE_MIN <= state["ease"] <= sm2.EASE_MAX


@settings(max_examples=100)
@given(name=backends, history=st.lists(ratings, min_size=1, max_size=20))
def test_failing_never_lengthens_the_interval(name, history):
    """However well it was going, AGAIN must not push the card further away."""
    backend = srs.get(name)
    state = backend.new_state()
    for rating in history:
        state = backend.review(state, rating, AT)
    before = backend.interval_days(state)
    after = backend.interval_days(backend.review(state, Rating.AGAIN, AT))
    assert after <= before
