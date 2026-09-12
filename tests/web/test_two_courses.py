"""Two courses in one database: the properties that must hold."""

from __future__ import annotations

import textwrap
from datetime import date

import pytest

from repetita import store
from repetita.content.loader import load_course
from repetita.policies import daily
from repetita.web.app import build_library, seed_if_absent

COURSE = """\
format_version: 1
id: {id}
l2: {{code: {l2}}}
l1: {{code: pl}}
license: {{name: CC BY-SA 4.0}}
"""

NOTES = """\
    notetype: vocab
    tags: [A1, vocab]
    notes:
      - id: {p}-one
        l2: uno
        l1: jeden
      - id: {p}-two
        l2: due
        l1: dwa
      - id: {p}-three
        l2: tre
        l1: trzy
    """

FACETS = """\
axes:
  level: {values: [A1, A2], ordered: true, max_per_note: 1}
  track: {values: [vocab, grammar]}
"""


def _course(root, cid, l2, prefix):
    (root / "units" / "01" / "notes").mkdir(parents=True)
    (root / "course.yaml").write_text(COURSE.format(id=cid, l2=l2))
    (root / "facets.yaml").write_text(FACETS)
    (root / "units" / "01" / "notes" / "n.yaml").write_text(textwrap.dedent(NOTES).format(p=prefix))
    return root


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "two.db"
    seed_if_absent(path, _course(tmp_path / "it", "it-x", "it", "it"))
    seed_if_absent(path, _course(tmp_path / "es", "es-x", "es", "es"))
    return path


@pytest.fixture
def con(db):
    c = store.connect(db)
    yield c
    c.close()


class TestTheQueueIsOneCourse:
    def test_a_session_holds_only_its_own_course(self, con):
        queue = daily.build_session(con, date(2026, 9, 12), course="it-x")
        assert queue.cards, "the session is empty, so this proves nothing"
        assert all(c.startswith("it-") for c in queue.cards), queue.cards

    def test_unscoped_still_sees_both(self, con):
        # The CLI and the parity tests rely on this: None means every course.
        queue = daily.build_session(con, date(2026, 9, 12))
        assert any(c.startswith("es-") for c in queue.cards)
        assert any(c.startswith("it-") for c in queue.cards)

    def test_owed_counts_one_course(self, con):
        day = date(2026, 9, 12)
        assert daily.owed_count(con, day, course="it-x") == 0
        for card in ("it-one#recognize", "es-one#recognize"):
            con.execute(
                "INSERT INTO card_state(user_id,card_id,algo,algo_version,state,due,"
                "interval,seen,correct,wrong,lapses) "
                "VALUES(1,?,'sm2',1,'{}','2026-09-01',3,1,1,0,0)",
                (card,),
            )
        con.commit()
        assert daily.owed_count(con, day, course="it-x") == 1
        assert daily.owed_count(con, day, course="es-x") == 1
        assert daily.owed_count(con, day) == 2


class TestImportingOneCourseLeavesTheOtherAlone:
    def test_distractors_survive(self, con, tmp_path, db):
        before = con.execute(
            "SELECT COUNT(*) AS n FROM distractors WHERE card_id LIKE 'it-%'"
        ).fetchone()["n"]
        assert before, "no Italian distractors were built, so this proves nothing"

        store.cards.sync(con, load_course(tmp_path / "es"))

        after = con.execute(
            "SELECT COUNT(*) AS n FROM distractors WHERE card_id LIKE 'it-%'"
        ).fetchone()["n"]
        assert after == before, "importing Spanish emptied Italian's distractors"

    def test_note_facets_survive(self, con, tmp_path):
        before = con.execute(
            "SELECT COUNT(*) AS n FROM note_facets WHERE note_id LIKE 'it-%'"
        ).fetchone()["n"]
        assert before, "no Italian facet rows, so this proves nothing"

        store.cards.sync(con, load_course(tmp_path / "es"))

        after = con.execute(
            "SELECT COUNT(*) AS n FROM note_facets WHERE note_id LIKE 'it-%'"
        ).fetchone()["n"]
        assert after == before, "importing Spanish emptied Italian's facet rows"


class TestAnIdBelongsToOneCourse:
    def test_a_clash_is_refused_by_name(self, con, tmp_path):
        # `notes.id` is a database-wide primary key and a card id is built from
        # it, so two courses cannot share one. This used to surface as
        # `IntegrityError: UNIQUE constraint failed` part-way through the insert,
        # naming neither the id nor the course that already had it.
        other = _course(tmp_path / "three", "fr-x", "fr", "it")  # same note prefix

        with pytest.raises(ValueError, match="already belong to another course"):
            store.cards.sync(con, load_course(other), course="fr-x")

    def test_nothing_is_written_when_it_is_refused(self, con, tmp_path):
        before = con.execute("SELECT COUNT(*) AS n FROM notes").fetchone()["n"]
        other = _course(tmp_path / "four", "fr-y", "fr", "it")

        with pytest.raises(ValueError):
            store.cards.sync(con, load_course(other), course="fr-y")

        assert con.execute("SELECT COUNT(*) AS n FROM notes").fetchone()["n"] == before


class TestTheLibraryIsOneCourse:
    def test_each_course_serves_its_own(self, db):
        assert sorted(build_library(db, "it-x").notes) == ["it-one", "it-three", "it-two"]
        assert sorted(build_library(db, "es-x").notes) == ["es-one", "es-three", "es-two"]
