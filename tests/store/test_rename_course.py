"""
Moving a course to another id, and removing one entirely.

The property worth the file: a course rename must **not** move history. A note
id is a scheduling key and moving one means moving `card_state` and
`review_log`; a course id is not — nothing in those tables names a course, they
reach one by joining through `notes.course`. So the counts before and after are
the same by construction, and these tests are what keep that true if the schema
grows a column that breaks it.
"""

from __future__ import annotations

import textwrap

import pytest

from repetita import store
from repetita.content.loader import load_course
from repetita.store.rename_course import (
    TABLES,
    CannotRename,
    drop_course,
    history_of,
    rename_course,
)

COURSE = """\
format_version: 1
id: {id}
l2: {{code: pt}}
l1: {{code: pl}}
license: {{name: CC BY-SA 4.0}}
"""

NOTES = """\
    notetype: vocab
    tags: [A1, vocab]
    notes:
      - id: {p}-one
        l2: um
        l1: jeden
      - id: {p}-two
        l2: dois
        l1: dwa
    """


def _course(root, cid, prefix):
    (root / "units" / "01" / "notes").mkdir(parents=True)
    (root / "course.yaml").write_text(COURSE.format(id=cid))
    (root / "units" / "01" / "notes" / "n.yaml").write_text(textwrap.dedent(NOTES).format(p=prefix))
    return root


@pytest.fixture
def con(tmp_path):
    c = store.connect(tmp_path / "db.sqlite")
    store.cards.sync(c, load_course(_course(tmp_path / "a", "old-id", "a")))
    c.execute(
        "INSERT INTO card_state(user_id,card_id,algo,algo_version,state,due,interval,"
        "seen,correct,wrong,lapses) VALUES(1,'a-one#produce','sm2',1,'{}','2026-10-01',21,5,5,0,0)"
    )
    c.execute(
        "INSERT INTO review_log(card_id,rating,review_datetime,day,algo,state_before) "
        "VALUES('a-one#produce',3,'2026-09-12T09:00:00+00:00','2026-09-12','sm2','{}')"
    )
    c.commit()
    yield c
    c.close()


class TestRenaming:
    def test_the_material_moves(self, con):
        moved = rename_course(con, "old-id", "new-id")

        assert moved.rows["courses"] == 1
        assert moved.rows["notes"] == 2
        assert [r["id"] for r in con.execute("SELECT id FROM courses")] == ["new-id"]

    def test_the_history_does_not(self, con):
        before = history_of(con, "old-id")
        assert before == (1, 1), "the fixture has no history, so this proves nothing"

        rename_course(con, "old-id", "new-id")

        assert history_of(con, "new-id") == before
        # And the rows themselves are untouched -- not rewritten to match.
        row = con.execute("SELECT * FROM card_state").fetchone()
        assert row["card_id"] == "a-one#produce" and row["due"] == "2026-10-01"
        assert con.execute("SELECT COUNT(*) AS n FROM review_log").fetchone()["n"] == 1

    def test_every_table_that_names_a_course_is_listed(self, con):
        # `TABLES` is written down rather than discovered, so a table added
        # later without a line in it would silently keep the old id.
        named = {
            r["name"]
            for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            for info in [con.execute(f"PRAGMA table_info({r['name']})").fetchall()]
            if any(c["name"] == "course" for c in info)
        }
        assert named <= {t for t, _ in TABLES}, f"a table names a course and is not moved: {named}"

    def test_it_refuses_to_merge(self, con, tmp_path):
        store.cards.sync(con, load_course(_course(tmp_path / "b", "other", "b")))

        with pytest.raises(CannotRename, match="already a course"):
            rename_course(con, "old-id", "other")

    def test_it_refuses_an_id_that_is_not_there(self, con):
        with pytest.raises(CannotRename, match="no course"):
            rename_course(con, "nope", "new-id")


class TestDropping:
    def test_it_takes_the_material_and_the_configuration(self, con):
        con.execute("DELETE FROM card_state")
        con.execute("DELETE FROM review_log")
        con.commit()

        drop_course(con, "old-id")

        assert con.execute("SELECT COUNT(*) AS n FROM courses").fetchone()["n"] == 0
        assert con.execute("SELECT COUNT(*) AS n FROM notes").fetchone()["n"] == 0
        assert con.execute("SELECT COUNT(*) AS n FROM cards").fetchone()["n"] == 0

    def test_it_refuses_while_the_course_has_been_studied(self, con):
        with pytest.raises(CannotRename, match="schedule"):
            drop_course(con, "old-id")

        assert con.execute("SELECT COUNT(*) AS n FROM notes").fetchone()["n"] == 2

    def test_with_history_says_so_out_loud(self, con):
        drop_course(con, "old-id", with_history=True)

        assert con.execute("SELECT COUNT(*) AS n FROM notes").fetchone()["n"] == 0
        # The schedule rows are deliberately left: they name a card, not a
        # course, and deleting them is `purge --with-history`, which asks twice.
        assert con.execute("SELECT COUNT(*) AS n FROM review_log").fetchone()["n"] == 1
