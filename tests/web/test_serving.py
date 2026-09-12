"""
What the learner is served comes from the database, and from nothing else.

ADR-0006 made the database the owner of the material, and for a while this was
the half that did not follow: `build_library` merged the course files in and
then served the *file* version, so anything edited in the app was invisible to
the session until it had been exported and reloaded. ADR-0015 removed the merge
from the serving path entirely -- `build_library` now opens no file at all, and
material arrives by an import somebody asked for.

`TestQuarantineFollowsTheMaterial` is the part that matters. The answer-leak
quarantine used to run only in the loader, over notes fresh out of a file.
Serving from the database means an edit reaches a learner without passing it --
and an exercise that gives away its own answer teaches nothing while looking
completely normal in review.
"""

from __future__ import annotations

import json
import shutil
import textwrap

import pytest

from repetita import store
from repetita.content.loader import load_course
from repetita.web.app import build_library, create_app, seed_if_absent

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


@pytest.fixture
def seeded(course_dir, db_path):
    """The course, imported once. Everything after this reads the database."""
    return seed_if_absent(db_path, course_dir)


def _serve(db_path):
    """Rebuild what is served. Opens no file -- that is the point of ADR-0015."""
    return build_library(db_path, "t")


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
    def test_an_edit_made_only_in_the_database_is_what_gets_served(self, seeded, db_path, con):
        _edit(con, "feira", explain="A feira acontece na rua.")
        library = _serve(db_path)

        assert library.notes["feira"].fields["explain"] == "A feira acontece na rua."

    def test_the_course_files_are_not_read_again(self, seeded, course_dir, db_path):
        # The rule ADR-0015 added. A course file that changes reaches nobody
        # until somebody imports it: starting the app is not an import, because
        # an import is the one operation that can archive material in bulk and
        # overwrite an edit, and doing that by restarting is doing it by
        # accident.
        path = course_dir / "units" / "01" / "notes" / "n.yaml"
        path.write_text(
            path.read_text()
            + "  - id: mercado\n"
            + "    prompt: O ___ fecha às seis.\n"
            + "    cue: rynek\n"
            + "    answers: [mercado]\n"
        )

        library = _serve(db_path)

        assert "mercado" not in library.notes

    def test_the_app_serves_a_course_whose_files_have_gone(self, seeded, course_dir, db_path):
        # The property the whole change exists for: the database is the course.
        shutil.rmtree(course_dir)

        library = _serve(db_path)

        assert "feira" in library.notes
        assert [c for c in library.cards if c.startswith("feira")]

    def test_material_still_arrives_from_a_file_when_asked(self, seeded, course_dir, db_path):
        # Ownership did not make the files inert, and `courses/` is still a
        # thing you can send a pull request to. It arrives on an import.
        path = course_dir / "units" / "01" / "notes" / "n.yaml"
        path.write_text(
            path.read_text()
            + "  - id: mercado\n"
            + "    prompt: O ___ fecha às seis.\n"
            + "    cue: rynek\n"
            + "    answers: [mercado]\n"
        )
        con = store.connect(db_path)
        try:
            store.cards.sync(con, load_course(course_dir))
        finally:
            con.close()

        assert "mercado" in _serve(db_path).notes

    def test_a_note_archived_in_the_database_is_not_served(self, seeded, db_path, con):
        # Deliberate: `edited_at` is what marks it as a person's decision rather
        # than material that merely left the files. Without it the next import
        # would put the note straight back, because the file still has it.
        con.execute(
            "UPDATE notes SET archived_at = '2026-09-11', edited_at = '2026-09-11' "
            "WHERE id = 'feira'"
        )
        con.commit()

        library = _serve(db_path)

        assert "feira" not in library.notes
        assert not [c for c in library.cards if c.startswith("feira")]


class TestQuarantineFollowsTheMaterial:
    def test_an_edit_that_gives_away_the_answer_is_refused(self, seeded, db_path, con):
        # The whole reason `build_library` does not simply read the table.
        _edit(con, "feira", hint="feira")
        library = _serve(db_path)

        assert "feira" not in library.notes, "a note that leaks its answer reached the pool"
        assert library.quarantined == 1

    def test_a_good_edit_is_not_refused(self, seeded, db_path, con):
        _edit(con, "feira", hint="o mercado de rua")
        library = _serve(db_path)
        assert "feira" in library.notes
        assert library.quarantined == 0

    def test_the_refusal_is_visible_to_the_learner(self, course_dir, db_path, con):
        # A quarantine nobody can see is a quarantine nobody acts on -- the
        # exercise simply stops appearing and nothing says why.
        app = create_app(course_dir, db_path=db_path)
        _edit(con, "feira", hint="feira")
        app.test_client().post("/api/reload")

        assert app.test_client().get("/api/state").get_json()["quarantined"] == 1

    def test_a_leak_in_a_file_is_recorded_rather_than_dropped(self, tmp_path, db_path):
        # It cannot be practised, and it is still in the database: a note that
        # vanished at the door is one nobody can find to fix, and since ADR-0015
        # the database is the whole record of a course.
        root = tmp_path / "leaky"
        (root / "units" / "01" / "notes").mkdir(parents=True)
        (root / "course.yaml").write_text(COURSE)
        (root / "units" / "01" / "notes" / "n.yaml").write_text(
            textwrap.dedent(NOTES).replace("cue: targ", "cue: feira")
        )
        seed_if_absent(db_path, root)

        library = _serve(db_path)

        assert "feira" not in library.notes
        assert library.quarantined == 1
        con = store.connect(db_path)
        try:
            assert con.execute("SELECT COUNT(*) AS n FROM notes").fetchone()["n"] == 1
            assert con.execute("SELECT COUNT(*) AS n FROM cards").fetchone()["n"] == 0
        finally:
            con.close()

    def test_a_note_whose_type_the_course_dropped_is_refused(self, seeded, db_path, con):
        # Nothing to expand it into and nothing to grade it with.
        con.execute("UPDATE notes SET notetype = 'invented' WHERE id = 'feira'")
        con.commit()

        library = _serve(db_path)

        assert "feira" not in library.notes
        assert library.quarantined == 1
