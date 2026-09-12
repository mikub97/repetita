"""
Deleting material outright.

Archiving is the default and stays the default. What this adds is the ability to
be rid of something, and the thing that makes it safe is not a prohibition but a
count: how much study history is attached, said before anything happens.
"""

from __future__ import annotations

import textwrap
from datetime import UTC, datetime

import pytest

from repetita import store
from repetita.content.loader import load_course
from repetita.store import purge as purging

COURSE = """\
format_version: 1
id: t
l2: {code: pt}
l1: {code: pl}
license: {name: CC BY-SA 4.0}
"""

NOTES = """\
    notetype: vocab
    tags: [vocabulario]
    notes:
      - id: casa
        l2: a casa
        l1: dom
      - id: rua
        l2: a rua
        l1: ulica
    """


@pytest.fixture
def con(tmp_path):
    root = tmp_path / "course"
    (root / "units" / "01" / "notes").mkdir(parents=True)
    (root / "course.yaml").write_text(COURSE)
    (root / "units" / "01" / "notes" / "n.yaml").write_text(textwrap.dedent(NOTES))
    c = store.connect(tmp_path / "t.db")
    store.sync(c, load_course(root))
    c.execute(
        "INSERT INTO review_log(user_id,card_id,rating,review_datetime,day,algo,mode) "
        "VALUES(1,'casa#recognize',3,?,?,'sm2','session')",
        (datetime.now(UTC).isoformat(), "2026-09-11"),
    )
    c.commit()
    yield c
    c.close()


def alive(con, table, column="card_id", like="casa#%"):
    return con.execute(f"SELECT count(*) FROM {table} WHERE {column} LIKE ?", (like,)).fetchone()[0]


class TestSayingWhatItWouldCost:
    def test_it_counts_without_touching_anything(self, con):
        going = purging.what_would_go(con, note_id="casa")
        assert going.notes == ("casa",)
        assert going.cards == 2
        assert going.answers == 1
        assert con.execute("SELECT 1 FROM notes WHERE id = 'casa'").fetchone(), "still there"

    def test_it_says_when_the_material_is_still_live(self, con):
        # Purging something that is still in the course is a different act from
        # tidying away what was archived months ago.
        assert purging.what_would_go(con, note_id="casa").live == ("casa",)

    def test_nothing_matching_is_not_an_error(self, con):
        assert not purging.what_would_go(con, note_id="nao-existe")


class TestPurging:
    def test_material_goes_and_history_stays_by_default(self, con):
        # The answers now point at a card that does not exist. Harmless,
        # countable, and recoverable if the exercise comes back under that id --
        # which is the trade the default makes.
        purging.purge(con, note_id="casa")
        assert not con.execute("SELECT 1 FROM notes WHERE id = 'casa'").fetchone()
        assert alive(con, "cards", "id", "casa%") == 0
        assert alive(con, "review_log") == 1, "history kept"

    def test_with_history_takes_the_answers_too(self, con):
        purging.purge(con, note_id="casa", with_history=True)
        assert alive(con, "review_log") == 0

    def test_a_whole_set_at_once(self, con):
        going = purging.purge(con, unit="01")
        assert set(going.notes) == {"casa", "rua"}
        assert con.execute("SELECT count(*) FROM notes").fetchone()[0] == 0

    def test_only_what_was_archived_long_enough_ago(self, con):
        con.execute("UPDATE notes SET archived_at = '2026-01-01' WHERE id = 'rua'")
        con.commit()
        going = purging.purge(con, archived_before="2026-06-01")
        assert going.notes == ("rua",)
        assert con.execute("SELECT 1 FROM notes WHERE id = 'casa'").fetchone(), "untouched"

    def test_it_leaves_nothing_dangling_in_the_tables_it_owns(self, con):
        purging.purge(con, note_id="casa")
        for table, column in (
            ("distractors", "card_id"),
            ("card_handles", "card_id"),
            ("note_facets", "note_id"),
            ("pending_changes", "note_id"),
        ):
            left = con.execute(
                f"SELECT count(*) FROM {table} WHERE {column} LIKE 'casa%'"
            ).fetchone()[0]
            assert left == 0, f"{table} still refers to it"


class TestWhoseHistoryGoes:
    """
    The material is shared; the history is not.

    Deleting an exercise deletes it for everybody -- that is what deleting an
    exercise means. But `review_log` and `card_state` are one person's, and this
    module deleted every row for the card regardless of whose it was. With one
    account that was invisible. With four it is one person tidying up and three
    people losing a year of study, which is rule 1 broken by omission rather
    than by decision.
    """

    @pytest.fixture
    def shared(self, con):
        """One card, answered by two people."""
        for user in (1, 2):
            con.execute(
                "INSERT INTO review_log(user_id,card_id,rating,review_datetime,day,algo,mode) "
                "VALUES(?,'casa#recognize',3,?,?,'sm2','session')",
                (user, datetime.now(UTC).isoformat(), "2026-09-11"),
            )
            con.execute(
                "INSERT INTO card_state(user_id,card_id,algo,algo_version,state,seen) "
                "VALUES(?,'casa#recognize','sm2',1,'{}',1)",
                (user,),
            )
            con.execute(
                "INSERT INTO pending_changes(user_id,note_id,kind,payload,created_at) "
                "VALUES(?,'casa','tags','[]',?)",
                (user, datetime.now(UTC).isoformat()),
            )
        con.commit()
        return con

    def mine(self, con, table, user):
        return con.execute(
            f"SELECT count(*) FROM {table} WHERE user_id = ? AND card_id LIKE 'casa#%'", (user,)
        ).fetchone()[0]

    def test_it_does_not_delete_another_persons_answers(self, shared):
        purging.purge(shared, note_id="casa", with_history=True, user_id=1)
        assert self.mine(shared, "review_log", 1) == 0, "the caller's answers should go"
        assert self.mine(shared, "review_log", 2) == 1, "somebody else's answers must not"

    def test_it_does_not_delete_another_persons_schedule(self, shared):
        purging.purge(shared, note_id="casa", with_history=True, user_id=1)
        assert self.mine(shared, "card_state", 1) == 0
        assert self.mine(shared, "card_state", 2) == 1

    def test_it_does_not_discard_another_persons_staged_edit(self, shared):
        purging.purge(shared, note_id="casa", user_id=1)
        left = shared.execute("SELECT user_id FROM pending_changes WHERE note_id = 'casa'")
        assert [r["user_id"] for r in left] == [2]

    def test_all_users_is_how_you_mean_everybody(self, shared):
        # Available, deliberate, and not the default -- the same shape as
        # `--with-history` itself.
        purging.purge(shared, note_id="casa", with_history=True, all_users=True)
        assert self.mine(shared, "review_log", 2) == 0
        assert self.mine(shared, "card_state", 2) == 0

    def test_the_count_is_of_what_will_actually_go(self, shared):
        # The number exists to be shown to somebody deciding. Counting rows the
        # operation will not touch is worse than not counting at all.
        assert purging.what_would_go(shared, note_id="casa", user_id=1).answers == 2
        assert purging.what_would_go(shared, note_id="casa", user_id=2).answers == 1
        assert purging.what_would_go(shared, note_id="casa", all_users=True).answers == 3
