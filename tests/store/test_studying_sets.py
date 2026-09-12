"""
Which sets a person's queue draws from.

Every account was served every set in a course, so Karolina's session drew from
Radek's material and Małgosia's. A set belongs to the lessons it came from, and
a queue that ignores that is four people sharing one backlog.

The default is the load-bearing part: **no subscription means the whole
course.** That is what every database predating this table says, what somebody
who has just joined a course wants, and what keeps the author -- who owns no
sets anywhere -- from opening the app one morning to an empty queue.
"""

from __future__ import annotations

import textwrap
from datetime import date

import pytest

from repetita import store
from repetita.content.loader import load_course
from repetita.policies import daily
from repetita.store import users as U

COURSE = """\
format_version: 1
id: t
l2: {code: it}
l1: {code: pl}
license: {name: CC BY-SA 4.0}
"""

NOTES = """\
    notetype: vocab
    tags: [A1]
    notes:
      - id: {p}-one
        l2: uno
        l1: jeden
      - id: {p}-two
        l2: due
        l1: dwa
    """


@pytest.fixture
def con(tmp_path):
    root = tmp_path / "c"
    for unit in ("hers", "his"):
        (root / "units" / unit / "notes").mkdir(parents=True)
        (root / "units" / unit / "notes" / "n.yaml").write_text(
            textwrap.dedent(NOTES).format(p=unit)
        )
    (root / "course.yaml").write_text(COURSE)
    c = store.connect(tmp_path / "t.db")
    store.sync(c, load_course(root))
    U.rename(c, U.DEFAULT_USER, "mikub")
    U.add(c, "karo")
    yield c
    c.close()


def karo(con):
    who = U.by_name(con, "karo")
    assert who is not None
    return who.id


def units_in_queue(con, user_id):
    return sorted({c.unit for c in daily.scheduled_cards(con, "t", user_id=user_id)})


class TestTheDefault:
    def test_subscribed_to_nothing_means_the_whole_course(self, con):
        assert units_in_queue(con, karo(con)) == ["hers", "his"]

    def test_studying_returns_none_rather_than_every_unit(self, con):
        # `None` and not a list of every unit: no subscription must add no
        # clause, or a set added tomorrow would be missing from a queue that
        # nobody changed.
        assert U.studying(con, karo(con), "t") is None

    def test_a_caller_with_no_account_in_hand_still_gets_everything(self, con):
        # The CLI and the parity tests pass no user. That means "unfiltered",
        # not "nobody".
        assert sorted({c.unit for c in daily.scheduled_cards(con, "t")}) == ["hers", "his"]


class TestChoosing:
    """
    The model is a row of checkboxes, all ticked until somebody unticks one.

    So "join a set I have never chosen about" is a no-op -- it was already in
    the queue -- and the meaningful click is the one that takes a set *out*.
    """

    def test_turning_one_off_leaves_the_rest_on(self, con):
        U.leave_set(con, karo(con), "t", "his")
        assert units_in_queue(con, karo(con)) == ["hers"]

    def test_the_first_choice_writes_every_set_down(self, con):
        # Otherwise "no rows" would go on meaning "all of them" and the click
        # would silently do nothing -- which is what the one-set course in
        # `test_two_people.py` caught.
        U.leave_set(con, karo(con), "t", "his")
        rows = dict(
            con.execute("SELECT unit, studying FROM set_enrolments WHERE user_id = ?", (karo(con),))
        )
        assert rows == {"hers": 1, "his": 0}

    def test_turning_it_back_on_restores_it(self, con):
        mine = karo(con)
        U.leave_set(con, mine, "t", "his")
        U.join_set(con, mine, "t", "his")
        assert units_in_queue(con, mine) == ["hers", "his"]

    def test_turning_every_set_off_is_an_empty_queue(self, con):
        # Different from having chosen nothing, and reachable: somebody can
        # untick them all, and the queue should then be empty rather than full.
        mine = karo(con)
        U.leave_set(con, mine, "t", "hers")
        U.leave_set(con, mine, "t", "his")
        assert U.studying(con, mine, "t") == ()
        assert units_in_queue(con, mine) == []

    def test_it_is_one_persons(self, con):
        U.leave_set(con, karo(con), "t", "his")
        assert units_in_queue(con, karo(con)) == ["hers"]
        assert units_in_queue(con, U.DEFAULT_USER) == ["hers", "his"], "his queue is untouched"

    def test_joining_somebody_elses_set_needs_no_permission(self, con):
        con.execute("UPDATE units SET owner = 'mikub' WHERE id = 'his'")
        con.commit()
        U.join_set(con, karo(con), "t", "his")
        assert "his" in units_in_queue(con, karo(con))


class TestStudyOnly:
    """`study_only` is what "by default, their own" is made of."""

    def test_it_names_exactly_what_is_studied(self, con):
        assert U.study_only(con, karo(con), "t", ["hers"]) == ("hers",)
        assert units_in_queue(con, karo(con)) == ["hers"]

    def test_the_rest_are_written_down_as_not_studied(self, con):
        # Not left absent: absent means "never chose", which means all of them.
        U.study_only(con, karo(con), "t", ["hers"])
        rows = dict(
            con.execute("SELECT unit, studying FROM set_enrolments WHERE user_id = ?", (karo(con),))
        )
        assert rows == {"hers": 1, "his": 0}

    def test_naming_none_is_an_empty_queue(self, con):
        assert U.study_only(con, karo(con), "t", []) == ()
        assert units_in_queue(con, karo(con)) == []

    def test_it_replaces_rather_than_adds(self, con):
        mine = karo(con)
        U.study_only(con, mine, "t", ["his"])
        U.study_only(con, mine, "t", ["hers"])
        assert units_in_queue(con, mine) == ["hers"]


class TestItHidesRatherThanDestroys:
    def test_leaving_a_set_keeps_every_answer_and_every_schedule(self, con):
        # Rule 1. Leaving a set takes it out of the queue and touches nothing
        # else, so rejoining is rejoining rather than starting again.
        mine = karo(con)
        card = con.execute("SELECT id FROM cards WHERE note_id LIKE 'his-%' LIMIT 1").fetchone()[
            "id"
        ]
        con.execute(
            "INSERT INTO card_state(user_id,card_id,algo,algo_version,state,seen) "
            "VALUES(?,?,'sm2',1,'{}',7)",
            (mine, card),
        )
        con.commit()

        U.leave_set(con, mine, "t", "his")
        assert "his" not in units_in_queue(con, mine)
        kept = con.execute(
            "SELECT seen FROM card_state WHERE user_id = ? AND card_id = ?", (mine, card)
        ).fetchone()
        assert kept["seen"] == 7, "the schedule survived being filtered out"

        U.join_set(con, mine, "t", "his")
        assert "his" in units_in_queue(con, mine), "and it comes straight back"


class TestTheCountersAgreeWithTheQueue:
    def test_owed_counts_only_what_you_study(self, con):
        mine = karo(con)
        for row in con.execute("SELECT id FROM cards"):
            con.execute(
                "INSERT INTO card_state(user_id,card_id,algo,algo_version,state,due,seen) "
                "VALUES(?,?,'sm2',1,'{}','2020-01-01',3)",
                (mine, row["id"]),
            )
        con.commit()
        today = date(2026, 9, 12)
        everything = daily.owed_count(con, today, course="t", user_id=mine)
        U.study_only(con, mine, "t", ["hers"])
        mine_only = daily.owed_count(con, today, course="t", user_id=mine)
        assert 0 < mine_only < everything, (everything, mine_only)

    def test_the_session_serves_only_what_you_study(self, con):
        mine = karo(con)
        U.study_only(con, mine, "t", ["hers"])
        session = daily.build_session(con, date(2026, 9, 12), course="t", user_id=mine)
        units = {
            con.execute(
                "SELECT n.unit AS unit FROM cards c JOIN notes n ON n.id = c.note_id "
                "WHERE c.id = ?",
                (cid,),
            ).fetchone()["unit"]
            for cid in session.cards
        }
        assert units == {"hers"}, units
