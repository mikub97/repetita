"""
Changing how material is filed, and writing it back out.

The two belong together: since ADR-0006 the database owns the material, so a tag
change is a database write, and `export` is the only thing that turns it into
something a person can review.
"""

from __future__ import annotations

import textwrap

import pytest

from repetita import store
from repetita.content.loader import load_course
from repetita.store import material
from repetita.store import tags as T
from repetita.store.catalogue import catalogue, parse_selector
from repetita.store.export import export_course

COURSE = """\
format_version: 1
id: t
l2: {code: pt}
l1: {code: pl}
license: {name: CC BY-SA 4.0}
"""

FACETS = """\
    axes:
      track: {values: [vocabulario, gramatica]}
      topic: {catch_all: true}
    """

NOTES = """\
    notetype: vocab
    tags: [vocabulario, comida]
    notes:
      - id: casa
        l2: a casa
        l1: dom
      - id: rua
        l2: a rua
        l1: ulica
        tags: [cidade]
    """


@pytest.fixture
def course_dir(tmp_path):
    root = tmp_path / "course"
    (root / "units" / "01" / "notes").mkdir(parents=True)
    (root / "course.yaml").write_text(COURSE)
    (root / "facets.yaml").write_text(textwrap.dedent(FACETS))
    (root / "units" / "01" / "notes" / "n.yaml").write_text(textwrap.dedent(NOTES))
    return root


@pytest.fixture
def con(tmp_path, course_dir):
    c = store.connect(tmp_path / "t.db")
    store.sync(c, load_course(course_dir))
    yield c
    c.close()


class TestTagging:
    def test_a_dry_run_writes_nothing(self, con):
        before = T.inventory(con)
        change = T.rename(con, "comida", "jedzenie", dry_run=True)
        assert change.notes == 2
        assert T.inventory(con) == before

    def test_a_rename_moves_every_note(self, con):
        T.rename(con, "comida", "jedzenie")
        assert dict(T.inventory(con))["jedzenie"] == 2
        assert "comida" not in dict(T.inventory(con))

    def test_a_rename_leaves_an_alias(self, con):
        # A tag carries no scheduling state, so renaming it is safe -- but plans
        # and facets.yaml refer to it by value, so the old name has to keep
        # meaning something.
        T.rename(con, "comida", "jedzenie")
        assert T.aliases(con) == {"comida": "jedzenie"}

    def test_the_catalogue_follows_immediately(self, con):
        # A tag change that left `note_facets` describing the old tags would be
        # worse than no change: every count would agree with itself and disagree
        # with the material.
        T.rename(con, "comida", "jedzenie")
        rows = {r.keys["topic"] for r in catalogue(con, group_by=["topic"])}
        assert "jedzenie" in rows and "comida" not in rows

    def test_adding_a_tag_to_a_slice_touches_only_that_slice(self, con):
        change = T.add(con, "pilne", where=parse_selector("topic=cidade"))
        assert change.note_ids == ("rua",)

    def test_a_split_needs_a_selector(self, con):
        # A split with no selector is a rename, and the two must not be reachable
        # by the same command with a typo between them.
        with pytest.raises(ValueError):
            T.split(con, "comida", "jedzenie", where={})

    def test_a_merge_folds_several_into_one(self, con):
        T.merge(con, ["comida", "cidade"], "miejsca")
        assert dict(T.inventory(con))["miejsca"] == 2

    def test_a_tag_change_marks_the_note_as_edited(self, con):
        # Which is what stops the next import silently overwriting it, and makes
        # a genuine clash surface as a conflict.
        T.rename(con, "comida", "jedzenie")
        rows = con.execute("SELECT edited_at FROM notes WHERE id = 'casa'").fetchone()
        assert rows["edited_at"] is not None


class TestExport:
    def test_the_round_trip_keeps_the_change(self, con, tmp_path):
        T.rename(con, "comida", "jedzenie")
        out = tmp_path / "exported"
        export_course(con, "t", out)

        reloaded = load_course(out)

        assert reloaded.ok
        assert {t for n in reloaded.notes for t in n.tags} == {
            "vocabulario",
            "jedzenie",
            "cidade",
        }

    def test_the_alias_survives_the_round_trip(self, con, tmp_path):
        T.rename(con, "comida", "jedzenie")
        out = tmp_path / "exported"
        export_course(con, "t", out)
        assert load_course(out).facets.aliases == {"comida": "jedzenie"}

    def test_note_ids_go_out_verbatim(self, con, tmp_path):
        # Rule 1. An id that changed on the way through here would silently
        # orphan the history behind it.
        out = tmp_path / "exported"
        export_course(con, "t", out)
        assert sorted(n.id for n in load_course(out).notes) == ["casa", "rua"]

    def test_archived_material_is_not_written_back(self, con, tmp_path):
        # It has left the course; exporting it would put it straight back on the
        # next import, which would make removing a note impossible to express.
        con.execute("UPDATE notes SET archived_at = '2026-09-10' WHERE id = 'rua'")
        con.commit()
        out = tmp_path / "exported"
        export_course(con, "t", out)
        assert [n.id for n in load_course(out).notes] == ["casa"]

    def test_a_derived_name_is_not_written_to_the_files(self, con, tmp_path):
        # It would come back identical from the rule on the next import, so it
        # is not content -- and writing it would add a line to every note in the
        # course that nobody authored and everybody would have to review.
        out = tmp_path / "exported"
        export_course(con, "t", out)
        assert "label:" not in (out / "units" / "01" / "notes" / "01.yaml").read_text()

    def test_a_name_somebody_typed_goes_out_and_comes_back(self, con, course_dir, tmp_path):
        material.stage(con, "casa", "label", "dom rodzinny")
        material.apply_pending(con, load_course(course_dir).notetypes)

        out = tmp_path / "exported"
        export_course(con, "t", out)
        reloaded = load_course(out)

        assert reloaded.ok
        assert {n.id: n.label for n in reloaded.notes}["casa"] == "dom rodzinny"

    def test_it_reports_an_unknown_course(self, con, tmp_path):
        with pytest.raises(LookupError):
            export_course(con, "not-a-course", tmp_path / "x")
