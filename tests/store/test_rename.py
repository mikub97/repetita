"""
Renaming an exercise, with its history.

The rule was *never rename an id*, and the reason was real: scheduling state is
keyed on the card id, which is keyed on the note id. This is the operation that
makes the rule unnecessary, so the test that matters is the arithmetic one --
every answer and every card state is still attached afterwards.
"""

from __future__ import annotations

import textwrap
from datetime import UTC, datetime

import pytest

from repetita import store
from repetita.content.loader import load_course
from repetita.store import rename as renaming
from repetita.store.rename import CannotRename

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
def course_dir(tmp_path):
    root = tmp_path / "course"
    (root / "units" / "01" / "notes").mkdir(parents=True)
    (root / "course.yaml").write_text(COURSE)
    (root / "units" / "01" / "notes" / "n.yaml").write_text(textwrap.dedent(NOTES))
    return root


@pytest.fixture
def con(tmp_path, course_dir):
    c = store.connect(tmp_path / "t.db")
    store.sync(c, load_course(course_dir))
    # Give `casa` a history worth losing: two answers and a card state.
    for card in ("casa#recognize", "casa#produce"):
        c.execute(
            "INSERT INTO review_log(user_id,card_id,rating,review_datetime,day,algo,mode) "
            "VALUES(1,?,3,?,?,'sm2','session')",
            (card, datetime.now(UTC).isoformat(), "2026-09-11"),
        )
    c.execute(
        "INSERT INTO card_state(user_id,card_id,algo,algo_version,state,due,interval,seen) "
        "VALUES(1,'casa#recognize','sm2',1,'{}','2026-09-20',7,2)"
    )
    c.commit()
    yield c
    c.close()


def counts(con, like):
    return (
        con.execute("SELECT count(*) FROM review_log WHERE card_id LIKE ?", (like,)).fetchone()[0],
        con.execute("SELECT count(*) FROM card_state WHERE card_id LIKE ?", (like,)).fetchone()[0],
    )


class TestTheHistoryComesToo:
    def test_every_answer_and_state_moves(self, con):
        before = counts(con, "casa#%")
        assert before == (2, 1), "the fixture gave it a history"

        moved = renaming.rename(con, "casa", "vocab-casa.casa")

        assert counts(con, "casa#%") == (0, 0)
        assert counts(con, "vocab-casa.casa#%") == before
        assert (moved.answers, moved.states, moved.cards) == (2, 1, 2)

    def test_the_totals_do_not_change(self, con):
        # The arithmetic that the old prohibition existed to protect.
        total = lambda t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]  # noqa: E731
        before = (total("review_log"), total("card_state"), total("cards"), total("notes"))
        renaming.rename(con, "casa", "outra")
        assert (total("review_log"), total("card_state"), total("cards"), total("notes")) == before

    def test_the_cards_are_renamed_with_it(self, con):
        renaming.rename(con, "casa", "outra")
        ids = {r["id"] for r in con.execute("SELECT id FROM cards WHERE note_id = 'outra'")}
        assert ids == {"outra#recognize", "outra#produce"}

    def test_a_handle_for_the_old_card_is_dropped(self, con):
        # Handles are regenerated on every start; one left pointing at an id
        # that no longer exists is worse than none.
        con.execute("INSERT INTO card_handles(card_id, handle) VALUES('casa#produce','abc')")
        con.commit()
        renaming.rename(con, "casa", "outra")
        assert not con.execute("SELECT 1 FROM card_handles WHERE card_id LIKE 'casa#%'").fetchone()

    def test_it_is_marked_as_ours_so_an_import_leaves_it_alone(self, con, course_dir):
        # Without `edited_at` the next import finds an id it does not recognise
        # in the files and archives the exercise that was just renamed.
        renaming.rename(con, "casa", "outra")
        store.sync(con, load_course(course_dir))
        row = con.execute("SELECT archived_at FROM notes WHERE id = 'outra'").fetchone()
        assert row is not None and row["archived_at"] is None


class TestRefusals:
    def test_an_id_that_is_not_there(self, con):
        with pytest.raises(CannotRename, match="no exercise called"):
            renaming.rename(con, "nao-existe", "outra")

    def test_a_name_that_is_taken(self, con):
        with pytest.raises(CannotRename, match="already taken"):
            renaming.rename(con, "casa", "rua")

    def test_a_name_taken_by_an_archived_exercise(self, con):
        # It still owns the history stored under that id.
        con.execute("UPDATE notes SET archived_at = '2026-09-01' WHERE id = 'rua'")
        con.commit()
        with pytest.raises(CannotRename, match="archived"):
            renaming.rename(con, "casa", "rua")

    def test_a_shape_that_cannot_be_an_id(self, con):
        for bad in ("", "   ", "with space", "has#hash", "has/slash"):
            with pytest.raises(CannotRename):
                renaming.rename(con, "casa", bad)

    def test_renaming_to_itself(self, con):
        with pytest.raises(CannotRename, match="already its id"):
            renaming.rename(con, "casa", "casa")

    def test_a_refusal_changes_nothing(self, con):
        with pytest.raises(CannotRename):
            renaming.rename(con, "casa", "rua")
        assert counts(con, "casa#%") == (2, 1)
