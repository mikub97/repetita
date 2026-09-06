"""
Every backend must satisfy the protocol, and the protocol must not have grown a
dependency on any one backend's memory model.
"""

import datetime as dt

import pytest

from roda import srs
from roda.core.protocols import SchedulerBackend
from roda.core.types import Rating

AT = dt.datetime(2026, 9, 6, 12, 0, tzinfo=dt.UTC)


@pytest.mark.parametrize("name", srs.names())
def test_backend_satisfies_protocol(name):
    assert isinstance(srs.get(name), SchedulerBackend)


@pytest.mark.parametrize("name", srs.names())
def test_full_cycle_through_the_protocol_only(name):
    """
    Drive a backend using nothing but the protocol.

    No test here may read a key out of `state`: that dict is the backend's
    private business. If this test needs to peek, the protocol is missing a
    method.
    """
    backend = srs.get(name)
    state = backend.new_state()
    for rating in (Rating.GOOD, Rating.GOOD, Rating.AGAIN, Rating.GOOD, Rating.EASY):
        state = backend.review(state, rating, AT)
        assert backend.interval_days(state) >= 0
        due = backend.due_at(state)
        assert due is not None and due >= AT.date()
        r = backend.retrievability(state, AT)
        assert r is None or 0.0 <= r <= 1.0


@pytest.mark.parametrize("name", srs.names())
def test_state_is_json_serialisable(name):
    """It is stored as a JSON blob in one column, so it has to survive the trip."""
    import json

    backend = srs.get(name)
    state = backend.review(backend.new_state(), Rating.GOOD, AT)
    assert json.loads(json.dumps(state)) == state


def test_unknown_scheduler_names_itself_in_the_error():
    with pytest.raises(LookupError, match="sm2"):
        srs.get("no-such-scheduler")
