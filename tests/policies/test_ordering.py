"""
In what order material arrives.

The bug these pin: `introduction_order` sorted by lesson date and fell back to
the *directory name*, while `course.path` -- parsed since the first course -- was
joined by nothing. With `lesson:` set in 14 of 63 files in one course and in
none at all in the other three, that made alphabetical order the real answer for
almost all material.
"""

import datetime as dt
import random
import textwrap

import pytest

from repetita import srs, store
from repetita.content.loader import load_course
from repetita.core.types import Rating
from repetita.policies import daily, ordering
from repetita.policies.queue import UNPLACED, QueueCard

UTC = dt.UTC
DAY = dt.date(2026, 9, 6)
AT = dt.datetime(2026, 9, 6, 20, 0, tzinfo=UTC)

HEAD = """\
format_version: 1
id: t
l2: {code: pt}
l1: {code: pl}
license: {name: CC BY-SA 4.0}
"""


def _notes(unit):
    return "notetype: gap\nnotes:\n" + "".join(
        f"  - id: {unit}-{i}\n    prompt: Eu ___ {unit}{i}.\n    answers: [saio]\n    cue: w {i}\n"
        for i in range(2)
    )


@pytest.fixture
def db(tmp_path):
    def build(units, path=None):
        root = tmp_path / "course"
        course = HEAD
        if path is not None:
            course += "path:\n" + "".join(f"  - unit: {u}\n" for u in path)
        (root).mkdir(parents=True, exist_ok=True)
        (root / "course.yaml").write_text(course)
        for unit in units:
            d = root / "units" / unit / "notes"
            d.mkdir(parents=True, exist_ok=True)
            (d / "a.yaml").write_text(textwrap.dedent(_notes(unit)))
        con = store.connect(tmp_path / "t.db")
        store.sync(con, load_course(root))
        return con

    return build


def _units(con):
    """The unit of each queued card, in queue order, without repeats."""
    out = []
    for c in daily.scheduled_cards(con, "t"):
        if not out or out[-1] != c.unit:
            out.append(c.unit)
    return out


class TestContentOrder:
    def test_no_path_declared_is_exactly_the_old_key(self, db):
        # The compatibility proof. With no `path:`, `_load_unit` gives every unit
        # ord 0, so the new key collapses to `(unit, ord, id)` -- the old one.
        con = db(["zebra", "alfa", "mid"])
        assert _units(con) == ["alfa", "mid", "zebra"]
        assert {c.unit_ord for c in daily.scheduled_cards(con, "t")} == {0}

    def test_a_declared_path_beats_the_directory_name(self, db):
        # The whole point: `zebra` is last alphabetically and first in the course.
        con = db(["zebra", "alfa", "mid"], path=["zebra", "mid", "alfa"])
        assert _units(con) == ["zebra", "mid", "alfa"]

    def test_a_unit_the_path_does_not_mention_sorts_last(self, db):
        # Material the author has not placed should not jump the sequence they
        # did place -- `content/loader.py:_load_unit` says so in prose.
        con = db(["zebra", "alfa"], path=["zebra"])
        assert _units(con) == ["zebra", "alfa"]

    def test_a_unit_with_no_row_in_units_is_still_in_the_queue(self, db):
        # The LEFT JOIN test. `scheduled_cards` also feeds `owed_count` and
        # `forecast`, so an INNER JOIN here would shrink somebody's debt with no
        # message anywhere.
        con = db(["alfa", "beta"], path=["alfa", "beta"])
        before = len(daily.scheduled_cards(con, "t"))
        with con:
            con.execute("DELETE FROM units WHERE id = 'beta'")
        after = daily.scheduled_cards(con, "t")
        assert len(after) == before
        assert {c.unit_ord for c in after if c.unit == "beta"} == {UNPLACED}
        assert _units(con) == ["alfa", "beta"]

    def test_content_key_is_a_total_order(self, db):
        con = db(["alfa", "beta"], path=["beta", "alfa"])
        cards = daily.scheduled_cards(con, "t")
        keys = [ordering.content_key(c) for c in cards]
        assert len(set(keys)) == len(keys)
        assert keys == sorted(keys)


def _card(cid, unit="u", ord_=0, lesson=None, unit_ord=0, template=""):
    return QueueCard(cid, f"note-{cid}", unit, ord_, lesson, unit_ord, template)


POOL = [
    _card("a", unit="b", ord_=1, unit_ord=2, template="produce"),
    _card("b", unit="a", ord_=0, unit_ord=1, lesson="2026-09-05", template="recognise"),
    _card("c", unit="c", ord_=2, unit_ord=0, lesson="2026-09-01", template="produce"),
]


class TestIntroductionOrderings:
    @pytest.mark.parametrize("how", ordering.ORDERINGS)
    def test_every_ordering_is_a_permutation_of_its_input(self, how):
        out = ordering.order_introductions(POOL, how, today=DAY, seed="s")
        assert sorted(out) == sorted(c.card_id for c in POOL)

    @pytest.mark.parametrize("how", ordering.ORDERINGS)
    def test_it_does_not_mutate_its_input(self, how):
        before = list(POOL)
        ordering.order_introductions(POOL, how, today=DAY, seed="s")
        assert before == POOL

    def test_an_unknown_ordering_raises(self):
        # Silently falling back would make a misspelled setting look like one
        # that works, which is the expensive kind of wrong.
        with pytest.raises(ValueError, match="unknown ordering"):
            ordering.order_introductions(POOL, "nonsense", today=DAY)

    def test_lesson_puts_the_freshest_lesson_first(self):
        assert ordering.order_introductions(POOL, "lesson", today=DAY) == ["b", "c", "a"]

    def test_course_ignores_the_lesson_date(self):
        assert ordering.order_introductions(POOL, "course", today=DAY) == ["c", "b", "a"]

    def test_axis_order_follows_the_rank_it_is_given(self):
        ranks = {"a": 0, "b": 2, "c": 1}
        assert ordering.order_introductions(POOL, "axis", today=DAY, axis_rank=ranks) == [
            "a",
            "c",
            "b",
        ]

    def test_an_unranked_card_sorts_after_every_ranked_one(self):
        out = ordering.order_introductions(POOL, "axis", today=DAY, axis_rank={"a": 0})
        assert out[0] == "a"

    def test_plan_weights_lead_and_content_order_breaks_ties(self):
        out = ordering.order_introductions(POOL, "plan", today=DAY, weight={"a": 0.9, "c": 0.1})
        assert out == ["a", "c", "b"]

    def test_template_order_decides_which_sibling_is_introduced(self):
        pair = [
            _card("x", unit="u", ord_=0, template="recognise"),
            _card("y", unit="u", ord_=0, template="produce"),
        ]
        assert ordering.order_introductions(pair, "course", today=DAY) == ["x", "y"]
        out = ordering.order_introductions(
            pair, "course", today=DAY, templates=("produce", "recognise")
        )
        assert out == ["y", "x"]


class TestShuffle:
    def test_it_is_stable_within_a_day_and_differs_across_days(self):
        monday = ordering.order_introductions(POOL, "shuffle", today=DAY, seed="u:t:1")
        again = ordering.order_introductions(POOL, "shuffle", today=DAY, seed="u:t:1")
        tuesday = ordering.order_introductions(POOL, "shuffle", today=DAY, seed="u:t:2")
        assert monday == again
        assert monday != tuesday

    def test_it_does_not_touch_the_global_rng(self):
        # `srs/CLAUDE.md` forbids uninjected randomness for scheduling, and
        # `planned.py` self-imposes the same rule. A seeded hash keeps it true.
        random.seed(0)
        expected = random.random()
        random.seed(0)
        ordering.order_introductions(POOL, "shuffle", today=DAY, seed="s")
        assert random.random() == expected

    def test_adding_a_card_does_not_reshuffle_the_others(self):
        # What `random.shuffle` over the whole list would not give us: a learner
        # who adds one note should not find tomorrow's order rewritten.
        before = ordering.order_introductions(POOL, "shuffle", today=DAY, seed="s")
        after = ordering.order_introductions([*POOL, _card("d")], "shuffle", today=DAY, seed="s")
        assert [c for c in after if c != "d"] == before


CARDS = {c.card_id: c for c in POOL}
DUE_ON = {"a": "2026-09-01", "b": "2026-09-03", "c": "2026-09-02"}
INTERVAL = {"a": 10, "b": 2, "c": 5}
LAPSES = {"a": 0, "b": 3, "c": 1}


class TestDebtOrder:
    def _order(self, how, **kw):
        return ordering.order_debt(
            ["a", "b", "c"],
            how,
            cards=CARDS,
            due_on=DUE_ON,
            interval=INTERVAL,
            lapses=LAPSES,
            **kw,
        )

    @pytest.mark.parametrize("how", ordering.DEBT_ORDERINGS)
    def test_the_debt_is_never_filtered_only_reordered(self, how):
        # The guardrail, asserted rather than trusted: whatever the learner
        # chooses, every owed card is still in the list.
        assert sorted(self._order(how)) == ["a", "b", "c"]

    def test_overdue_is_the_default_and_puts_the_oldest_first(self):
        assert self._order("overdue") == ["a", "c", "b"]

    def test_weakest_is_the_most_lapsed_then_the_shortest_interval(self):
        assert self._order("weakest") == ["b", "c", "a"]

    def test_course_follows_the_path(self):
        assert self._order("course") == ["c", "b", "a"]

    def test_an_unknown_debt_ordering_raises(self):
        with pytest.raises(ValueError, match="unknown debt ordering"):
            self._order("nonsense")


class TestBuildSession:
    def test_the_defaults_are_the_old_behaviour(self, db):
        con = db(["alfa", "beta"], path=["beta", "alfa"])
        plain = daily.build_session(con, DAY, user_id=1)
        spelled = daily.build_session(
            con,
            DAY,
            user_id=1,
            introductions="lesson",
            debt="overdue",
            every=daily.NEW_EVERY,
            threshold=daily.GATE_THRESHOLD,
            consolidation=True,
        )
        assert plain.cards == spelled.cards

    def test_the_queue_follows_the_course_path(self, db):
        con = db(["zebra", "alfa"], path=["zebra", "alfa"])
        session = daily.build_session(con, DAY, user_id=1)
        assert session.cards, "a fresh course should introduce something"
        first = daily.scheduled_cards(con, "t")[0]
        assert first.unit == "zebra"

    def test_consolidation_can_be_turned_off(self, db):
        con = db(["alfa"], path=["alfa"])
        # Twice: sm2's first learning step leaves a card due the same day, so one
        # answer moves it out of "new" without moving it out of the debt.
        for card in daily.scheduled_cards(con, "t") * 2:
            store.record_answer(
                con,
                card.card_id,
                Rating.GOOD,
                backend=srs.get("sm2"),
                at=AT,
                local_day=DAY,
            )
        # Same day, everything answered: nothing is owed and nothing is new, so
        # the only thing left to serve is reinforcement.
        assert daily.build_session(con, DAY, user_id=1).consolidating
        assert daily.build_session(con, DAY, user_id=1, consolidation=False).cards == []


class TestLadderSteps:
    """
    The presenter is resolved once per request, and the reason is the review log.

    `served_form` is called twice for one answer -- when the question is served
    and again when the answer is recorded as "what was actually served". Resolve
    it separately in the two places and the log quietly stops describing the
    screen the learner saw.
    """

    def test_zero_steps_serves_the_declared_form(self):
        from repetita import presenters
        from repetita.core.protocols import PresentationContext

        ctx = PresentationContext(
            seen=0, lapses=0, available_forms=("typein", "choice"), answer_tokens=1
        )
        assert presenters.get(steps=1).choose("typein", ctx) == "choice"
        assert presenters.get(steps=0).choose("typein", ctx) == "typein"

    def test_the_registry_default_is_unchanged(self):
        from repetita import presenters

        assert presenters.get().steps == presenters.get(steps=None).steps
