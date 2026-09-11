"""
What the learner is served comes from the database.

ADR-0006 made the database the owner of the material, and for a while this was
the half that did not follow: `build_library` merged the course files in and
then served the *file* version, so anything edited in the app was invisible to
the session until it had been exported and reloaded.

The second test is the one that matters. The answer-leak quarantine used to run
only in the loader, over notes fresh out of a file. Serving from the database
means an edit reaches a learner without passing it -- and an exercise that gives
away its own answer teaches nothing while looking completely normal in review.
"""

from __future__ import annotations

import json
import textwrap

import pytest

from repetita import store
from repetita.web.app import build_library, create_app

COURSE = """\
format_version: 1
id: t
l2: {code: pt}
l1: {code: pl}
license: {name: CC BY-SA 4.0}
"""

NOTES = """\
    notetype: gap
    tags: [A2, comida]
    notes:
      - id: feira
        prompt: Eu vou à ___ no sábado.
        cue: targ
        answers: [feira]
        explain: A feira é o mercado de rua.
    """


@pytest.fixture
def course_dir(tmp_path):
    root = tmp_path / "course"
    (root / "units" / "01" / "notes").mkdir(parents=True)
    (root / "course.yaml").write_text(COURSE)
    (root / "units" / "01" / "notes" / "n.yaml").write_text(textwrap.dedent(NOTES))
    return root


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "study.db"


@pytest.fixture
def con(db_path):
    c = store.connect(db_path)
    yield c
    c.close()


def _edit(con, note_id, **fields):
    """Change a note the way the management write path will."""
    row = con.execute("SELECT fields FROM notes WHERE id = ?", (note_id,)).fetchone()
    merged = {**json.loads(row["fields"]), **fields}
    con.execute(
        "UPDATE notes SET fields = ?, edited_at = ? WHERE id = ?",
        (json.dumps(merged, ensure_ascii=False), "2026-09-11T00:00:00+00:00", note_id),
    )
    con.commit()


class TestServedFromTheDatabase:
    def test_an_edit_made_only_in_the_database_is_what_gets_served(self, course_dir, db_path, con):
        build_library(course_dir, db_path)

        _edit(con, "feira", explain="A feira acontece na rua.")
        library = build_library(course_dir, db_path)

        assert library.notes["feira"].fields["explain"] == "A feira acontece na rua."

    def test_the_course_files_are_still_how_material_gets_in(self, course_dir, db_path):
        # Ownership did not make the files inert. A note added to a file still
        # arrives, which is what keeps `courses/` a thing you can send a pull
        # request to.
        build_library(course_dir, db_path)
        path = course_dir / "units" / "01" / "notes" / "n.yaml"
        path.write_text(
            path.read_text()
            + "  - id: mercado\n"
            + "    prompt: O ___ fecha às seis.\n"
            + "    cue: rynek\n"
            + "    answers: [mercado]\n"
        )

        library = build_library(course_dir, db_path)

        assert "mercado" in library.notes

    def test_a_note_archived_in_the_database_is_not_served(self, course_dir, db_path, con):
        # Deliberate: `edited_at` is what marks it as a person's decision rather
        # than material that merely left the files. Without it the next import
        # would put the note straight back, because the file still has it.
        build_library(course_dir, db_path)
        con.execute(
            "UPDATE notes SET archived_at = '2026-09-11', edited_at = '2026-09-11' "
            "WHERE id = 'feira'"
        )
        con.commit()

        library = build_library(course_dir, db_path)

        assert "feira" not in library.notes
        assert not [c for c in library.cards if c.startswith("feira")]


class TestQuarantineFollowsTheMaterial:
    def test_an_edit_that_gives_away_the_answer_is_refused(self, course_dir, db_path, con):
        # The whole reason `build_library` does not simply read the table.
        build_library(course_dir, db_path)

        _edit(con, "feira", hint="feira")
        library = build_library(course_dir, db_path)

        assert "feira" not in library.notes, "a note that leaks its answer reached the pool"
        assert library.quarantined == 1

    def test_a_good_edit_is_not_refused(self, course_dir, db_path, con):
        build_library(course_dir, db_path)
        _edit(con, "feira", hint="o mercado de rua")
        library = build_library(course_dir, db_path)
        assert "feira" in library.notes
        assert library.quarantined == 0

    def test_the_refusal_is_visible_to_the_learner(self, course_dir, db_path, con):
        # A quarantine nobody can see is a quarantine nobody acts on -- the
        # exercise simply stops appearing and nothing says why.
        app = create_app(course_dir, db_path=db_path)
        _edit(con, "feira", hint="feira")
        app.test_client().post("/api/reload")

        assert app.test_client().get("/api/state").get_json()["quarantined"] == 1

    def test_a_note_whose_type_the_course_dropped_is_refused(self, course_dir, db_path, con):
        # Nothing to expand it into and nothing to grade it with.
        build_library(course_dir, db_path)
        con.execute("UPDATE notes SET notetype = 'invented' WHERE id = 'feira'")
        con.commit()

        library = build_library(course_dir, db_path)

        assert "feira" not in library.notes
        assert library.quarantined == 1
