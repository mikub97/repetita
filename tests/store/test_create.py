"""
Writing material, rather than editing what an import left.

Two of these are why this file exists. `test_an_import_does_not_archive_what_was
_written_here` is the fourth appearance of one rule in this repository --
whatever the app creates must record that it did, or the next import undoes it --
and it failed against the code as it stood. `test_an_archived_id_is_never_reused`
protects the one thing here that cannot be undone.
"""

from __future__ import annotations

import textwrap

import pytest

from repetita import store
from repetita.content.loader import load_course
from repetita.content.models import Facets
from repetita.content.notetypes import BUILTIN
from repetita.store import material
from repetita.store.material import NotEditable

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
    yield c
    c.close()


def gaps(*answers):
    # The prompt must not contain the answer, or every one of these is
    # quarantined for leaking and the tests pass for the wrong reason.
    return [
        {"notetype": "gap", "fields": {"prompt": "Eu ___ em Lisboa.", "answers": [a]}}
        for a in answers
    ]


def save(con, rows, unit="licao-nova", **kw):
    return material.save_set(con, "t", unit, rows, BUILTIN, Facets(), **kw)


class TestWritingASet:
    def test_it_creates_the_set_and_its_exercises(self, con):
        report = save(con, gaps("moro", "trabalho"))
        assert (report.created, report.updated, report.archived) == (2, 0, 0)
        assert report.cards_added == 2
        assert report.quarantined == (), "nothing here gives away its own answer"
        assert {n.unit for n in material.live_notes(con, "t")} == {"01", "licao-nova"}

    def test_an_id_comes_from_the_answer(self, con):
        assert save(con, gaps("moro")).ids == ("licao-nova.moro",)

    def test_two_exercises_with_one_answer_are_numbered(self, con):
        assert save(con, gaps("moro", "moro")).ids == ("licao-nova.moro", "licao-nova.moro-2")

    def test_a_new_exercise_is_named_like_every_other(self, con):
        save(con, gaps("moro"))
        note = material.get_note(con, "licao-nova.moro")
        assert note is not None and note.label == "moro"

    def test_it_is_recorded_as_ours(self, con):
        # The two columns the next import reads. `origin` empty says this was
        # never in a file; `edited_at` says its cards are not the file's to
        # recompute. Asserted directly because nothing in the UI shows them and
        # the failure they prevent is silent.
        save(con, gaps("moro"))
        row = con.execute(
            "SELECT origin, edited_at, content_hash FROM notes WHERE id = 'licao-nova.moro'"
        ).fetchone()
        assert row["origin"] == ""
        assert row["edited_at"]
        assert row["content_hash"] is None

    def test_a_bad_row_writes_nothing_at_all(self, con):
        with pytest.raises(NotEditable):
            save(con, [*gaps("moro"), {"notetype": "gap", "fields": {"nonsense": "x"}}])
        assert not con.execute("SELECT 1 FROM notes WHERE unit = 'licao-nova'").fetchone()


class TestTheImportLeavesItAlone:
    def test_an_import_does_not_archive_what_was_written_here(self, con, course_dir):
        # The regression. An import archives every note it cannot find in the
        # files, and an exercise written in the app is in none of them -- so it
        # survived until the next restart and then vanished, cards and all.
        save(con, gaps("moro"))
        store.sync(con, load_course(course_dir))

        note = material.get_note(con, "licao-nova.moro")
        assert note is not None, "the import archived an exercise written here"
        live = {r["id"] for r in con.execute("SELECT id FROM cards WHERE archived_at IS NULL")}
        assert "licao-nova.moro#fill" in live

    def test_a_file_note_that_has_gone_is_still_archived(self, con, course_dir):
        # The other half, unchanged: material that left the files leaves the
        # course. The exemption is for notes that were never in a file at all,
        # not for every note the app has touched.
        (course_dir / "units" / "01" / "notes" / "n.yaml").write_text(
            textwrap.dedent(NOTES).replace(
                "      - id: casa\n        l2: a casa\n        l1: dom\n", ""
            )
            + "      - id: rua\n        l2: a rua\n        l1: ulica\n"
        )
        store.sync(con, load_course(course_dir))
        assert material.get_note(con, "casa") is None


class TestEditingWhatIsThere:
    def test_saving_again_updates_rather_than_duplicates(self, con):
        first = save(con, gaps("moro"))
        row = {
            "id": first.ids[0],
            "notetype": "gap",
            "fields": {"prompt": "Eu ___ em Lisboa.", "answers": ["morava"]},
        }
        report = save(con, [row])

        assert (report.created, report.updated) == (0, 1)
        note = material.get_note(con, first.ids[0])
        assert note is not None
        assert note.fields["answers"] == ["morava"]
        assert note.label == "morava", "the name follows the answer"

    def test_a_removed_row_is_archived_not_deleted(self, con):
        first = save(con, gaps("moro"))
        report = save(con, [{"id": first.ids[0], "notetype": "gap", "archived": True}])

        assert report.archived == 1
        assert material.get_note(con, first.ids[0]) is None
        assert con.execute("SELECT 1 FROM notes WHERE id = ?", (first.ids[0],)).fetchone()
        assert not con.execute(
            "SELECT 1 FROM cards WHERE note_id = ? AND archived_at IS NULL", (first.ids[0],)
        ).fetchone()

    def test_a_staged_edit_to_the_same_exercise_is_superseded(self, con):
        # Left alone, the next Confirm would put an older version of an exercise
        # back over the one just saved, with nothing said.
        first = save(con, gaps("moro"))
        material.stage(con, first.ids[0], "fields", {"prompt": "stale"})
        report = save(
            con,
            [
                {
                    "id": first.ids[0],
                    "notetype": "gap",
                    "fields": {"prompt": "Eu ___ aqui.", "answers": ["moro"]},
                }
            ],
        )
        assert report.superseded == 1
        assert material.pending(con) == []


class TestIds:
    def test_an_archived_id_is_never_reused(self, con):
        # An archived note still owns the history behind it. Handing its id to a
        # new exercise would hand over the history with it -- rule 1 by another
        # route, and the reason `taken` reads every row rather than the live ones.
        first = save(con, gaps("moro"))
        save(con, [{"id": first.ids[0], "notetype": "gap", "archived": True}])
        again = save(con, gaps("moro"))
        assert again.ids[0] != first.ids[0]
        assert again.ids == ("licao-nova.moro-2",)


class TestHowItIsAsked:
    def test_a_chosen_form_reaches_the_card(self, con):
        save(con, [{**gaps("moro")[0], "forms": {"fill": ["wordbank", "typein"]}}])
        row = con.execute("SELECT forms FROM cards WHERE id = 'licao-nova.moro#fill'").fetchone()
        assert row["forms"] == '["wordbank", "typein"]'

    def test_it_can_be_changed_on_an_exercise_that_already_exists(self, con):
        # This is where it broke: `_reexpand` wrote only the cards that were
        # missing, so the `ON CONFLICT` clause that brings an existing card back
        # in line with its note never ran. The note said wordbank and the card
        # went on asking the old way, with nothing to see.
        first = save(con, gaps("moro"))
        save(
            con,
            [
                {
                    "id": first.ids[0],
                    "notetype": "gap",
                    "fields": {"prompt": "Eu ___ em Lisboa.", "answers": ["moro"]},
                    "forms": {"fill": ["wordbank"]},
                }
            ],
        )
        row = con.execute(
            "SELECT forms FROM cards WHERE id = ?", (f"{first.ids[0]}#fill",)
        ).fetchone()
        assert row["forms"] == '["wordbank"]'

    def test_it_survives_being_saved_again(self, con):
        first = save(con, [{**gaps("moro")[0], "forms": {"fill": ["wordbank"]}}])
        save(
            con,
            [
                {
                    "id": first.ids[0],
                    "notetype": "gap",
                    "fields": {"prompt": "Eu ___ aqui.", "answers": ["moro"]},
                    "forms": {"fill": ["wordbank"]},
                }
            ],
        )
        row = con.execute(
            "SELECT forms FROM cards WHERE id = ?", (f"{first.ids[0]}#fill",)
        ).fetchone()
        assert row["forms"] == '["wordbank"]'

    def test_a_form_its_grader_cannot_mark_is_refused(self, con):
        # A flashcard asks the learner for a rating; the `typed` grader would
        # score every one of them as the string it was handed. Nothing about the
        # result would look wrong from the outside.
        with pytest.raises(NotEditable, match="cannot be marked"):
            save(con, [{**gaps("moro")[0], "forms": {"fill": ["flashcard"]}}])

    def test_a_card_the_type_does_not_have_is_refused(self, con):
        with pytest.raises(NotEditable, match="no card"):
            save(con, [{**gaps("moro")[0], "forms": {"recite": ["typein"]}}])


class TestRefusals:
    def test_an_unknown_type_is_refused(self, con):
        with pytest.raises(NotEditable, match="no 'quiz' exercise"):
            save(con, [{"notetype": "quiz", "fields": {}}])

    def test_a_field_the_type_does_not_have_is_refused(self, con):
        with pytest.raises(NotEditable, match="no field"):
            save(con, [{"notetype": "gap", "fields": {"situation": "x"}}])

    def test_an_id_cannot_be_smuggled_in_as_a_field(self, con):
        with pytest.raises(NotEditable, match="note id cannot change"):
            save(con, [{"notetype": "gap", "fields": {"id": "mine", "prompt": "x"}}])


class TestWrongAnswersToChooseBetween:
    def test_a_new_exercise_gets_distractors(self, con):
        # Built from the database, not from the course files. Built from the
        # files, an exercise written in the app contributed none and received
        # none -- so a multiple choice chosen in the editor was accepted and
        # then quietly never served.
        save(con, gaps("moro", "moras", "mora"))
        rows = con.execute(
            "SELECT count(*) AS n FROM distractors WHERE card_id = 'licao-nova.moro#fill'"
        ).fetchone()
        assert rows["n"] >= 2, "two is the minimum a multiple choice can be built from"

    def test_they_survive_an_import(self, con, course_dir):
        # `sync` rebuilds them wholesale, and building from the files would wipe
        # what the app's material contributed on the next restart.
        save(con, gaps("moro", "moras", "mora"))
        store.sync(con, load_course(course_dir))
        rows = con.execute(
            "SELECT count(*) AS n FROM distractors WHERE card_id = 'licao-nova.moro#fill'"
        ).fetchone()
        assert rows["n"] >= 2
