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
    tags: [A1, vocab, liczby]
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
  topic: {catch_all: true}
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


class TestTheDesignTabIsOneCourse:
    """
    The Design tab was the one screen PR #71 did not scope, which is how it came
    to show Portuguese topics while the Italian flag was up.
    """

    def _catalogue(self, db_path, course, **args):
        from repetita.web.app import create_app

        client = create_app(course, db_path=db_path).test_client()
        query = "&".join(f"{k}={v}" for k, v in {"group_by": "topic", **args}.items())
        return client.get(f"/api/catalogue?{query}&course={course}").get_json()

    def test_the_topics_are_this_course_s(self, db, con):
        con.execute("UPDATE notes SET tags = '[\"A1\",\"pasta\"]' WHERE course = 'it-x'")
        con.execute("UPDATE notes SET tags = '[\"A1\",\"tapas\"]' WHERE course = 'es-x'")
        con.commit()
        store.cards.reclassify(con, "it-x")
        store.cards.reclassify(con, "es-x")

        topics = [r.get("topic") for r in self._catalogue(db, "it-x")["rows"]]

        assert "pasta" in topics
        assert "tapas" not in topics, "the Design tab is showing another course"

    def test_the_axes_are_this_course_s(self, db, con):
        con.execute("INSERT INTO facet_axes(course, axis, title, ord) VALUES('es-x','solo','{}',9)")
        con.commit()

        axes = [a["axis"] for a in self._catalogue(db, "it-x")["axes"]]

        assert "solo" not in axes, "another course's axis reached this one"


class TestSearchingTheMaterial:
    def test_it_narrows_to_topics_that_hold_the_word(self, db, con):
        con.execute("UPDATE notes SET tags = '[\"A1\",\"pasta\"]' WHERE course = 'it-x'")
        con.commit()
        store.cards.reclassify(con, "it-x")
        from repetita.web.app import create_app

        client = create_app("it-x", db_path=db).test_client()

        hit = client.get("/api/catalogue?group_by=topic&q=due&course=it-x").get_json()
        miss = client.get("/api/catalogue?group_by=topic&q=zzzz&course=it-x").get_json()

        assert [r["topic"] for r in hit["rows"]] == ["pasta"]
        assert hit["rows"][0]["matched"] == 1, "one note has 'due' in it"
        assert hit["rows"][0]["notes"] == 3, "and the topic holds three"
        assert miss["rows"] == []

    def test_it_searches_answers_and_tags_too(self, db, con):
        from repetita.web.app import create_app

        client = create_app("it-x", db_path=db).test_client()

        # `jeden` is an l1 gloss -- an answer on the `produce` card.
        by_answer = client.get("/api/catalogue?group_by=topic&q=jeden&course=it-x").get_json()
        by_tag = client.get("/api/catalogue?group_by=topic&q=vocab&course=it-x").get_json()

        assert by_answer["rows"], "searching an answer found nothing"
        assert by_tag["rows"], "searching a tag found nothing"

    def test_it_never_returns_a_note(self, db):
        # The whole design: this endpoint counts, and must not become a way to
        # read the course (ADR-0008 draws the line elsewhere, and this side of
        # it does not move).
        from repetita.web.app import create_app

        raw = (
            create_app("it-x", db_path=db)
            .test_client()
            .get("/api/catalogue?group_by=topic&q=due&course=it-x")
            .data
        )

        # `due` itself comes back, because the response echoes what was asked.
        # What must not come back is anything the search *found*: the note's
        # other side, its id, or any field it holds.
        assert b"dwa" not in raw, raw
        assert b"it-two" not in raw, raw


class TestTheLibraryIsOneCourse:
    def test_each_course_serves_its_own(self, db):
        assert sorted(build_library(db, "it-x").notes) == ["it-one", "it-three", "it-two"]
        assert sorted(build_library(db, "es-x").notes) == ["es-one", "es-three", "es-two"]
