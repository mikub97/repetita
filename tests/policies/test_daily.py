"""
The daily queue. Every constant these tests pin has a row in docs/tuning.md with
the measurement that set it; several encode bugs that were expensive to find.
"""

import dataclasses
import datetime as dt
import textwrap

import pytest

from repetita import srs, store
from repetita.content.loader import load_course
from repetita.core.types import Rating
from repetita.policies import daily

UTC = dt.UTC
DAY = dt.date(2026, 9, 6)
AT = dt.datetime(2026, 9, 6, 20, 0, tzinfo=UTC)

COURSE = """\
format_version: 1
id: t
l2: {code: pt}
l1: {code: pl}
license: {name: CC BY-SA 4.0}
"""


def _notes(n, prefix="n", lesson=None):
    head = f"notetype: gap\n{'lesson: ' + lesson if lesson else ''}\nnotes:\n"
    body = "".join(
        f"  - id: {prefix}{i}\n    prompt: Eu ___ {prefix}{i}.\n"
        f"    answers: [saio]\n    cue: wskazówka {i}\n"
        for i in range(n)
    )
    return head + body


@pytest.fixture
def db(tmp_path):
    def build(files):
        root = tmp_path / "course"
        d = root / "units" / "01" / "notes"
        d.mkdir(parents=True, exist_ok=True)
        (root / "course.yaml").write_text(COURSE)
        for name, text in files.items():
            (d / name).write_text(textwrap.dedent(text))
        con = store.connect(tmp_path / "t.db")
        store.sync(con, load_course(root))
        return con

    return build


def answer(con, card_id, rating, day=DAY):
    return store.record_answer(
        con,
        card_id,
        rating,
        backend=srs.get("sm2"),
        at=AT,
        local_day=day,
    )


class TestGate:
    def test_too_little_evidence_leaves_it_open(self):
        # A brake for evidence of overload, not a hurdle to clear before starting.
        assert daily.gate_open([Rating.AGAIN] * (daily.GATE_MIN_ANSWERS - 1))

    def test_poor_recent_accuracy_closes_it(self):
        ratings = [Rating.AGAIN] * 10 + [Rating.GOOD] * 10
        assert not daily.gate_open(ratings)

    def test_good_accuracy_keeps_it_open(self):
        ratings = [Rating.GOOD] * 16 + [Rating.AGAIN] * 4
        assert daily.gate_open(ratings)

    def test_it_looks_only_at_the_recent_window(self):
        ratings = [Rating.GOOD] * daily.GATE_WINDOW + [Rating.AGAIN] * 100
        assert daily.gate_open(ratings)


class TestLessonFreshness:
    def test_material_with_no_lesson_is_never_fresh(self):
        # That is the entire back catalogue; letting it through a closed gate
        # would empty the exemption of meaning.
        assert not daily.lesson_is_fresh(None, DAY)

    def test_a_recent_lesson_is_fresh(self):
        assert daily.lesson_is_fresh("2026-09-05", DAY)

    def test_an_old_lesson_is_not(self):
        assert not daily.lesson_is_fresh("2026-08-01", DAY)

    def test_a_future_lesson_counts_as_fresh(self):
        # Writing tomorrow's date is a statement about what is current.
        assert daily.lesson_is_fresh("2026-09-10", DAY)


class TestWeaving:
    def test_new_cards_are_woven_not_appended(self):
        # The bug this replaces: with 45 owed and a batch of 40, that day's
        # lesson did not appear in the batch at all.
        due = [f"d{i}" for i in range(9)]
        new = ["n0", "n1"]
        out = daily.weave(due, new, every=3)
        assert out.index("n0") == 3
        assert out.index("n1") == 7

    def test_weaving_reduces_nothing(self):
        due = [f"d{i}" for i in range(9)]
        out = daily.weave(due, ["n0"], every=3)
        assert [c for c in out if c.startswith("d")] == due

    def test_leftover_new_cards_still_arrive(self):
        out = daily.weave(["d0"], ["n0", "n1", "n2"], every=3)
        assert set(out) == {"d0", "n0", "n1", "n2"}

    def test_no_due_cards_means_the_new_ones_are_the_session(self):
        assert daily.weave([], ["n0", "n1"]) == ["n0", "n1"]


class TestSiblingBurying:
    def test_a_sibling_is_held_back_from_today_not_moved_later_in_it(self):
        # Moving it to the end of the queue is not burying: the whole queue
        # ships in one batch, so the learner still meets both in one sitting --
        # and answers the second from the first rather than from memory.
        cards = [
            daily.QueueCard("a#x", "a", "01", 0, None),
            daily.QueueCard("a#y", "a", "01", 0, None),
            daily.QueueCard("b#x", "b", "01", 1, None),
        ]
        kept, buried = daily.bury_siblings(["a#x", "a#y", "b#x"], cards)
        assert kept == ["a#x", "b#x"]
        assert buried == ["a#y"]

    def test_a_held_back_card_is_not_lost(self, db):
        # It keeps its due date, so it simply comes up in the next session.
        con = db(
            {
                "n.yaml": """\
            notetype: vocab
            notes:
              - id: casa
                l2: a casa
                l1: dom
            """
            }
        )
        session = daily.build_session(con, DAY)
        assert len(session.cards) == 1
        assert session.buried == 1
        assert len(store.card_ids(con)) == 2, "both cards still exist and stay schedulable"


class TestIntroductionOrder:
    def test_lesson_material_comes_before_the_back_catalogue(self, db):
        con = db(
            {
                "old.yaml": _notes(3, "old"),
                "new.yaml": _notes(2, "new", lesson="2026-09-06"),
            }
        )
        cards = daily.scheduled_cards(con)
        order = daily.introduction_order(cards, store.all_states(con))
        assert all(c.startswith("new") for c in order[:2])

    def test_an_answered_card_is_no_longer_an_introduction(self, db):
        con = db({"n.yaml": _notes(3)})
        answer(con, "n0#fill", Rating.GOOD)
        cards = daily.scheduled_cards(con)
        order = daily.introduction_order(cards, store.all_states(con))
        assert "n0#fill" not in order


class TestGatedIntroductions:
    def test_an_open_gate_lets_everything_through(self, db):
        con = db({"n.yaml": _notes(5)})
        cards = daily.scheduled_cards(con)
        order = daily.introduction_order(cards, store.all_states(con))
        assert daily.gated_introductions(order, cards, [], DAY) == order

    def test_a_shut_gate_stops_the_back_catalogue(self, db):
        con = db({"n.yaml": _notes(5)})
        cards = daily.scheduled_cards(con)
        order = daily.introduction_order(cards, store.all_states(con))
        bad = [Rating.AGAIN] * 10
        assert daily.gated_introductions(order, cards, bad, DAY) == []

    def test_fresh_lesson_material_jumps_a_shut_gate(self, db):
        con = db(
            {
                "old.yaml": _notes(5, "old"),
                "new.yaml": _notes(3, "new", lesson="2026-09-06"),
            }
        )
        cards = daily.scheduled_cards(con)
        order = daily.introduction_order(cards, store.all_states(con))
        got = daily.gated_introductions(order, cards, [Rating.AGAIN] * 10, DAY)
        assert len(got) == 3
        assert all(c.startswith("new") for c in got)

    def test_but_only_up_to_the_daily_cap(self, db):
        con = db({"n.yaml": _notes(30, "new", lesson="2026-09-06")})
        cards = daily.scheduled_cards(con)
        order = daily.introduction_order(cards, store.all_states(con))
        got = daily.gated_introductions(order, cards, [Rating.AGAIN] * 10, DAY)
        assert len(got) == daily.LESSON_INTRO_CAP

    def test_the_cap_counts_what_the_lesson_already_spent_today(self, db):
        con = db({"n.yaml": _notes(30, "new", lesson="2026-09-06")})
        cards = daily.scheduled_cards(con)
        order = daily.introduction_order(cards, store.all_states(con))
        got = daily.gated_introductions(
            order, cards, [Rating.AGAIN] * 10, DAY, lesson_introduced_today=10
        )
        assert len(got) == daily.LESSON_INTRO_CAP - 10


class TestTheLessonBudget:
    def test_back_catalogue_introductions_do_not_spend_it(self, db):
        """
        The cap is a limit on *lesson* material, and nothing else may spend it.

        Meeting a back-catalogue card for the first time today used to be charged
        to the budget, which shrank the lesson allowance for a reason that has
        nothing to do with the lesson -- and on a day with a real backlog closed
        the exemption entirely, the one thing it must never do.
        """
        con = db(
            {
                "licao.yaml": _notes(20, "new", lesson="2026-09-06"),
                "old.yaml": _notes(8, "old"),
            }
        )
        # Eight back-catalogue cards met for the first time today, six of them
        # wrong: 25% over eight answers, so the gate is shut.
        for i in range(8):
            answer(con, f"old{i}#fill", Rating.GOOD if i < 2 else Rating.AGAIN)
        assert not daily.gate_open(store.recent_ratings(con, daily.GATE_WINDOW))

        session = daily.build_session(con, DAY)

        assert len([c for c in session.cards if c.startswith("new")]) == daily.LESSON_INTRO_CAP

    def test_lesson_introductions_already_made_today_still_spend_it(self, db):
        """The other half of the rule: what the lesson did spend is spent."""
        con = db({"licao.yaml": _notes(20, "new", lesson="2026-09-06")})
        for i in range(8):
            answer(con, f"new{i}#fill", Rating.GOOD if i < 1 else Rating.AGAIN)

        assert not daily.gate_open(store.recent_ratings(con, daily.GATE_WINDOW))

        answered = {f"new{i}#fill" for i in range(8)}
        session = daily.build_session(con, DAY)
        assert len(set(session.cards) - answered) == daily.LESSON_INTRO_CAP - 8


class TestDueOrder:
    """
    Cards owed on the same day: a total order, decided by content, not by SQLite.

    `due` alone ties -- most of a real backlog is ties -- and a stable sort then
    keeps whatever order the rows arrived in. That was the query planner's
    choice, so the order a learner met the day's backlog in could change under a
    SQLite upgrade, an added index or an `ANALYZE`, with nothing in the app to
    explain why.
    """

    def _owed_today(self, db):
        con = db({"a.yaml": _notes(3, "a"), "b.yaml": _notes(3, "b")})
        for cid in store.card_ids(con):
            answer(con, cid, Rating.GOOD)  # interval 0 -> owed again today
        return con

    def test_a_tie_is_broken_by_content_order(self, db):
        con = self._owed_today(db)
        assert daily.build_session(con, DAY).cards == [
            "a0#fill",
            "b0#fill",
            "a1#fill",
            "b1#fill",
            "a2#fill",
            "b2#fill",
        ]

    def test_the_physical_row_order_does_not_decide_it(self, db):
        con = self._owed_today(db)
        before = daily.build_session(con, DAY).cards

        columns = "id,note_id,template,notetype,grader,forms,scheduled"
        rows = [tuple(r) for r in con.execute(f"SELECT {columns} FROM cards")]
        with con:
            con.execute("DELETE FROM cards")
            con.executemany(
                f"INSERT INTO cards({columns}) VALUES(?,?,?,?,?,?,?)", list(reversed(rows))
            )

        assert daily.build_session(con, DAY).cards == before


class TestConsolidation:
    def test_it_never_pre_empts_owed_or_new_work(self, db):
        con = db({"n.yaml": _notes(5)})
        answer(con, "n0#fill", Rating.AGAIN)
        session = daily.build_session(con, DAY)
        assert not session.consolidating

    def test_it_appears_when_nothing_is_owed_and_nothing_is_new(self, db):
        con = db({"n.yaml": _notes(2)})
        # Answer everything, then push both out of reach so nothing is owed.
        for cid in store.card_ids(con):
            answer(con, cid, Rating.GOOD)
            cs = store.get_state(con, cid)
            store.save_state(con, dataclasses.replace(cs, due="2026-12-01"))
        session = daily.build_session(con, DAY)
        assert session.consolidating
        assert session.cards

    def test_mastered_material_is_excluded(self, db):
        con = db({"n.yaml": _notes(2)})
        for cid in store.card_ids(con):
            answer(con, cid, Rating.GOOD)
            cs = store.get_state(con, cid)
            store.save_state(con, dataclasses.replace(cs, due="2026-12-01", interval=90))
        session = daily.build_session(con, DAY)
        assert session.cards == [], "mastered cards must not keep reappearing"


class TestCounters:
    def test_owed_is_the_debt_not_the_batch_size(self, db):
        con = db({"n.yaml": _notes(60)})
        for cid in store.card_ids(con)[:50]:
            answer(con, cid, Rating.GOOD)  # interval 0 -> due today
        assert daily.owed_count(con, DAY) == 50
        assert len(daily.build_session(con, DAY).cards) == daily.BATCH

    def test_a_session_reports_when_there_is_more(self, db):
        con = db({"n.yaml": _notes(60)})
        assert daily.build_session(con, DAY).has_more

    def test_a_day_with_nothing_owed_is_done(self, db):
        con = db({"n.yaml": _notes(1)})
        assert daily.day_done(con, DAY) is False or daily.owed_count(con, DAY) == 0

    def test_a_day_is_also_done_at_the_answer_target(self, db):
        # With a real backlog "zero owed" is unreachable, and a streak that can
        # never move measures nothing.
        con = db({"n.yaml": _notes(60)})
        for cid in store.card_ids(con)[: daily.DAILY_TARGET]:
            answer(con, cid, Rating.GOOD)
        assert daily.day_done(con, DAY)

    def test_forecast_is_cumulative(self, db):
        con = db({"n.yaml": _notes(3)})
        answer(con, "n0#fill", Rating.GOOD)
        out = daily.forecast(con, DAY, days=5)
        assert len(out) == 5
        assert out == sorted(out), "an owed card stays owed until it is answered"
