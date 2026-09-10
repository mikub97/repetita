"""
Sessions built from a priority list.

Everything here is a pure function, which is the point: a plan's effect can be
reasoned about by reading it, and a preview of tomorrow costs nothing. These
pin the arithmetic, because a mix that is quietly wrong looks exactly like a mix
that is right.
"""

from __future__ import annotations

from repetita.policies.planned import (
    allocate,
    bucket_cards,
    order_by_priority,
    planned_introductions,
    weights_from_ranks,
)
from repetita.store.plans import Priority

THREE = [
    Priority(0, "topic", "numeros"),
    Priority(1, "topic", "comida"),
    Priority(2, "track", "gramatica"),
]


class TestWeights:
    def test_the_top_of_the_list_dominates(self):
        w = weights_from_ranks(THREE)
        assert w[("topic", "numeros")] > w[("topic", "comida")] > w[("track", "gramatica")]
        # Zipf, not linear. Dragging something to the top is a strong statement,
        # and a linear ramp would make it a weak one.
        assert round(w[("topic", "numeros")], 3) == 0.545

    def test_the_shares_sum_to_one(self):
        assert round(sum(weights_from_ranks(THREE).values()), 6) == 1.0

    def test_an_explicit_weight_is_honoured_and_the_rest_divide_what_is_left(self):
        pinned = [
            Priority(0, "topic", "numeros", 0.5),
            Priority(1, "topic", "comida"),
            Priority(2, "track", "gramatica"),
        ]
        w = weights_from_ranks(pinned)
        assert w[("topic", "numeros")] == 0.5
        assert round(sum(w.values()), 6) == 1.0
        assert w[("topic", "comida")] > w[("track", "gramatica")]

    def test_no_priorities_is_no_weights(self):
        assert weights_from_ranks([]) == {}


class TestAllocate:
    def test_the_budget_is_spent_exactly(self):
        # Largest remainder rather than independent rounding: a session that
        # asked for 20 and produced 19 is an off-by-one nobody investigates.
        w = weights_from_ranks(THREE)
        shares = allocate({k: 100 for k in w}, w, 20)
        assert sum(shares.values()) == 20

    def test_a_bucket_never_gets_more_than_it_holds(self):
        w = weights_from_ranks(THREE)
        sizes = {("topic", "numeros"): 2, ("topic", "comida"): 100, ("track", "gramatica"): 100}
        shares = allocate(sizes, w, 20)
        assert shares[("topic", "numeros")] == 2
        # And what it could not take flows down the list rather than shrinking
        # the session.
        assert sum(shares.values()) == 20

    def test_it_stops_when_there_is_nothing_left_to_give(self):
        w = weights_from_ranks(THREE)
        shares = allocate({k: 1 for k in w}, w, 50)
        assert sum(shares.values()) == 3

    def test_an_empty_budget_allocates_nothing(self):
        assert allocate({("topic", "x"): 5}, {("topic", "x"): 1.0}, 0) == {}


class TestBucketing:
    def test_a_card_counts_for_one_priority_only(self):
        # Counting it twice would let one card satisfy two shares, and the mix
        # would quietly stop matching the list.
        w = weights_from_ranks(THREE)
        membership = {"a": {("topic", "numeros"), ("topic", "comida")}}
        buckets, rest = bucket_cards(["a"], membership, w)
        assert buckets[("topic", "numeros")] == ["a"]
        assert buckets[("topic", "comida")] == []
        assert rest == []

    def test_material_matching_nothing_is_kept_aside_not_dropped(self):
        w = weights_from_ranks(THREE)
        _buckets, rest = bucket_cards(["a"], {"a": {("topic", "other")}}, w)
        assert rest == ["a"]


class TestIntroductions:
    def _membership(self):
        return {
            **{f"n{i}": {("topic", "numeros")} for i in range(10)},
            **{f"c{i}": {("topic", "comida")} for i in range(10)},
            **{f"g{i}": {("track", "gramatica")} for i in range(10)},
        }

    def test_the_mix_follows_the_list(self):
        ordered = [
            *(f"n{i}" for i in range(10)),
            *(f"c{i}" for i in range(10)),
            *(f"g{i}" for i in range(10)),
        ]
        picked = planned_introductions(ordered, self._membership(), tuple(THREE), budget=10)
        assert len(picked) == 10
        assert sum(1 for c in picked if c.startswith("n")) > sum(
            1 for c in picked if c.startswith("g")
        )

    def test_content_order_is_preserved_within_the_session(self):
        # A plan changes *which* material arrives, not the order the course lays
        # it out in.
        ordered = [*(f"n{i}" for i in range(5)), *(f"c{i}" for i in range(5))]
        picked = planned_introductions(ordered, self._membership(), tuple(THREE), budget=6)
        assert picked == sorted(picked, key=ordered.index)

    def test_unplanned_material_fills_what_the_list_cannot(self):
        # A plan narrows what comes first without walling off the rest.
        ordered = ["n0", "x0", "x1"]
        picked = planned_introductions(
            ordered, {"n0": {("topic", "numeros")}}, tuple(THREE), budget=3
        )
        assert set(picked) == {"n0", "x0", "x1"}

    def test_no_plan_means_plain_content_order(self):
        ordered = ["a", "b", "c"]
        assert planned_introductions(ordered, {}, (), budget=2) == ["a", "b"]


class TestOrderOfPractice:
    """
    A priority list is an order of practice, not only a mix of new material --
    it applies to the debt as well. What it must never do is *shrink* the debt.
    """

    def _membership(self):
        return {
            "n1": {("topic", "numeros")},
            "c1": {("topic", "comida")},
            "g1": {("track", "gramatica")},
            "x1": {("topic", "something-else")},
        }

    def test_the_plans_material_comes_first(self):
        due = ["x1", "g1", "c1", "n1"]
        assert order_by_priority(due, self._membership(), weights_from_ranks(THREE)) == [
            "n1",
            "c1",
            "g1",
            "x1",
        ]

    def test_nothing_owed_is_dropped(self):
        # The one property that matters. A plan decides what you meet first, not
        # what you get out of.
        due = ["x1", "g1", "c1", "n1"]
        assert sorted(
            order_by_priority(due, self._membership(), weights_from_ranks(THREE))
        ) == sorted(due)

    def test_equal_priority_keeps_the_order_it_arrived_in(self):
        # For the debt that order is most-overdue-first, and it becomes the
        # tie-break rather than being discarded.
        membership = {"a": {("topic", "comida")}, "b": {("topic", "comida")}}
        assert order_by_priority(["a", "b"], membership, weights_from_ranks(THREE)) == ["a", "b"]
        assert order_by_priority(["b", "a"], membership, weights_from_ranks(THREE)) == ["b", "a"]

    def test_no_plan_leaves_the_order_alone(self):
        due = ["x1", "g1", "c1"]
        assert order_by_priority(due, self._membership(), {}) == due
