"""
Leaving the queue, and being flagged for rewriting.

These moved out of `srs/sm2.py` because they are policy over what the store
records, not properties of a memory model. The move is what makes them work for
every backend: FSRS holds that nothing is ever finished, Leitner has no notion of
a clean run, and neither should have to pretend otherwise.
"""

import pytest

from repetita.core.retirement import (
    LEECH_LAPSES,
    RETIRE_AT_INTERVAL,
    RETIRE_CLEAN_REVIEWS,
    earned,
    is_leech,
)
from repetita.core.types import Rating

CLEAN = [Rating.GOOD] * RETIRE_CLEAN_REVIEWS


class TestEarned:
    def test_a_long_interval_and_a_clean_run_earns_it(self):
        assert earned(RETIRE_AT_INTERVAL, CLEAN)

    def test_a_short_interval_never_does(self):
        assert not earned(RETIRE_AT_INTERVAL - 1, CLEAN)

    def test_nor_does_a_long_interval_with_a_recent_miss(self):
        recent = [Rating.GOOD, Rating.AGAIN, *CLEAN]
        assert not earned(RETIRE_AT_INTERVAL, recent)

    def test_too_few_reviews_to_judge_means_no(self):
        # A card can reach a long interval on an import or a manual pull without
        # ever having proved anything here.
        assert not earned(RETIRE_AT_INTERVAL, CLEAN[:-1])

    def test_it_looks_only_at_the_recent_window(self):
        # Old failures are what the interval already accounts for; the question
        # is whether the card is settled *now*.
        recent = [*CLEAN, Rating.AGAIN, Rating.AGAIN]
        assert earned(RETIRE_AT_INTERVAL, recent)

    def test_hard_still_counts_as_a_pass(self):
        # ADR-0002: a missing accent is a real mistake and not a lapse.
        assert earned(RETIRE_AT_INTERVAL, [Rating.HARD] * RETIRE_CLEAN_REVIEWS)

    def test_no_history_at_all_means_no(self):
        assert not earned(RETIRE_AT_INTERVAL, [])


class TestLeech:
    @pytest.mark.parametrize(
        ("lapses", "flagged"),
        [(0, False), (LEECH_LAPSES - 1, False), (LEECH_LAPSES, True), (LEECH_LAPSES + 9, True)],
    )
    def test_it_is_a_count_of_lapses(self, lapses, flagged):
        assert is_leech(lapses) is flagged
