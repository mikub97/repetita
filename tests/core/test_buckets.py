"""
Which stage of learning a card is at.

The bucket is denormalised onto `card_state` and grouped on in SQL, so it has to
be right at the moment it is written -- there is no query that recomputes it.
That is deliberate (one definition, `core.buckets`), and it is why these are
worth pinning rather than leaving to the caller.
"""

from __future__ import annotations

import dataclasses

import pytest

from repetita.core.buckets import LEARNING, MATURE, NEW, RETIRED, SUSPENDED, YOUNG, bucket_of
from repetita.store.cards import CardState


def a_state(**over):
    return dataclasses.replace(CardState(card_id="c", algo="sm2", algo_version=1, state={}), **over)


class TestBuckets:
    def test_a_card_with_no_row_is_new(self):
        # The commonest case by far, and the one a LEFT JOIN produces.
        assert bucket_of(None) == NEW

    def test_a_card_never_answered_is_new(self):
        assert bucket_of(a_state(seen=0)) == NEW

    @pytest.mark.parametrize(
        ("interval", "expected"), [(1, YOUNG), (20, YOUNG), (21, MATURE), (400, MATURE)]
    )
    def test_the_threshold_is_where_it_says_it_is(self, interval, expected):
        assert bucket_of(a_state(seen=5, interval=interval)) == expected

    def test_a_card_being_relearned_is_not_young(self):
        # A relapse is not progress. Counting a leech as "young going on mature"
        # is how it hides from anyone looking at where the effort is going.
        assert bucket_of(a_state(seen=9, interval=1, lapses=3)) == LEARNING

    def test_a_reset_card_is_learning(self):
        assert bucket_of(a_state(seen=4, interval=0)) == LEARNING

    def test_retirement_outranks_everything(self):
        # A card that has left the queue has left it. Reporting it as `mature`
        # would put it in counts of material still being worked through.
        state = a_state(seen=9, interval=90, retired_at="2026-01-01", suspended_at="2026-01-01")
        assert bucket_of(state) == RETIRED

    def test_suspension_outranks_progress(self):
        assert bucket_of(a_state(seen=9, interval=90, suspended_at="2026-01-01")) == SUSPENDED

    def test_it_never_reads_the_scheduler_blob(self):
        # ADR-0003: `state` belongs to the backend. A bucket that parsed it would
        # break the moment a second backend wrote a different shape.
        weird = a_state(seen=5, interval=30, state={"stability": "nonsense", "difficulty": None})
        assert bucket_of(weird) == MATURE
