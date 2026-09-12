"""
A set is a shelf with a name on it (ADR-0013).

Two rules are load-bearing here and both have bitten this repository before.
A name typed in the app must survive the next import -- the fifth appearance of
"whatever the app changes must record that it did" -- and naming a set must wait
for Confirm like every other change on the Manage tab, because removing one
always did and the two used to disagree.
"""

from __future__ import annotations

import json
import textwrap

import pytest

from repetita import store
from repetita.content.loader import load_course
from repetita.content.notetypes import builtin
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


def unit(con, unit_id="01"):
    row = con.execute("SELECT * FROM units WHERE id = ?", (unit_id,)).fetchone()
    return json.loads(row["title"]), json.loads(row["description"])


class TestAFileCanNameASet:
    def test_a_title_and_a_description_are_read_and_stored(self, tmp_path, course_dir):
        (course_dir / "units" / "01" / "unit.yaml").write_text(
            "title: {en: Household}\ndescription: {en: Things in a house.}\n"
        )
        con = store.connect(tmp_path / "n.db")
        store.sync(con, load_course(course_dir))
        title, about = unit(con)
        assert title == {"en": "Household"}
        assert about == {"en": "Things in a house."}
        con.close()

    def test_a_unit_with_neither_is_still_a_unit(self, con):
        assert unit(con) == ({}, {})


class TestNamingWaitsForConfirm:
    def test_staging_writes_nothing_yet(self, con):
        material.stage(con, "01", "set_name", {"title": {"en": "Household"}})
        assert unit(con) == ({}, {}), "a staged name must not reach the units table"
        assert [c.kind for c in material.pending(con)] == ["set_name"]

    def test_the_drawer_says_what_would_change(self, con):
        material.stage(con, "01", "set_name", {"title": {"en": "Household"}})
        (change,) = [d for d in material.diff(con) if d.kind == "set_name"]
        assert change.before == {"title": {}}
        assert change.after == {"title": {"en": "Household"}}

    def test_the_drawer_does_not_claim_the_description_changed(self, con):
        """Only the parts actually being set reach the diff."""
        material.stage(con, "01", "set_name", {"title": {"en": "Household"}})
        (change,) = [d for d in material.diff(con) if d.kind == "set_name"]
        assert "description" not in change.after
        assert "description" not in change.before

    def test_confirm_applies_it_and_counts_it(self, con):
        material.stage(
            con,
            "01",
            "set_name",
            {"title": {"en": "Household"}, "description": {"en": "Things in a house."}},
        )
        report = material.apply_pending(con, builtin())
        assert report.named == 1
        # Naming a set touches no note, so the note count alone would report
        # that nothing happened -- which is what `sets` was added for already.
        assert report.notes == 0
        assert unit(con) == ({"en": "Household"}, {"en": "Things in a house."})

    def test_a_rename_applies_beside_a_note_edit(self, con):
        """
        Both kinds go through one `apply_pending`, which is why the naming calls
        `_rename_unit` rather than `rename_unit`: the public one opens its own
        transaction, and sqlite3's `with con:` commits rather than nesting, so
        it would have committed half a confirmation on the way past.
        """
        material.stage(con, "01", "set_name", {"title": {"en": "Household"}})
        material.stage(con, "01", "set_name", {"new_id": "02"})  # replaces the above
        material.stage(con, "casa", "fields", {"l1": "dom rodzinny"})
        material.apply_pending(con, builtin())
        assert con.execute("SELECT id FROM units WHERE id = '02'").fetchone() is not None
        moved = con.execute("SELECT unit FROM notes WHERE id = 'casa'").fetchone()["unit"]
        assert moved == "02", "every note in a renamed set follows it"
        assert (
            json.loads(
                con.execute("SELECT fields FROM notes WHERE id = 'casa'").fetchone()["fields"]
            )["l1"]
            == "dom rodzinny"
        )


class TestRefusals:
    def test_a_set_that_is_not_there(self, con):
        with pytest.raises(NotEditable, match="no set called"):
            material.stage(con, "nope", "set_name", {"title": {"en": "x"}})

    def test_a_title_that_is_not_a_mapping(self, con):
        with pytest.raises(NotEditable, match="language-to-text"):
            material.stage(con, "01", "set_name", {"title": "Household"})

    def test_a_change_that_changes_nothing(self, con):
        with pytest.raises(NotEditable, match="nothing to change"):
            material.stage(con, "01", "set_name", {})


class TestANameSurvivesTheNextImport:
    def test_it_is_not_overwritten_by_a_course_with_no_unit_yaml(self, con, course_dir):
        """
        The fifth appearance of this rule, after notes, cards, units and drafts.
        Most courses have no `unit.yaml`, so the incoming title is empty -- and
        an unconditional write would wipe the name someone typed.
        """
        material.stage(con, "01", "set_name", {"title": {"en": "Household"}})
        material.apply_pending(con, builtin())
        store.sync(con, load_course(course_dir))
        assert unit(con)[0] == {"en": "Household"}


class TestItRoundTrips:
    def test_export_writes_the_description_back(self, con, tmp_path):
        from repetita.store import export

        material.stage(
            con,
            "01",
            "set_name",
            {"title": {"en": "Household"}, "description": {"en": "Things in a house."}},
        )
        material.apply_pending(con, builtin())
        out = tmp_path / "out"
        export.export_course(con, "t", out)
        written = (out / "units" / "01" / "unit.yaml").read_text()
        assert "Things in a house." in written
        # And the loader reads back what the exporter wrote.
        result = load_course(out)
        assert result.units[0].description == {"en": "Things in a house."}


class TestArchivedMaterialComesBack:
    """
    ADR-0006 chose *archived, never deleted* and then nothing could reach an
    archived note: `restore` was a valid change kind with no way to stage it and
    no screen that could see one. A safety property nobody can use reads as
    deletion.
    """

    def archive_the_set(self, con):
        material.stage(con, "01", "remove_set", True)
        material.apply_pending(con, builtin())

    def test_removing_a_set_archives_it_rather_than_deleting_it(self, con):
        self.archive_the_set(con)
        assert con.execute("SELECT count(*) FROM units WHERE id = '01'").fetchone()[0] == 1
        gone = con.execute("SELECT archived_at FROM notes WHERE id = 'casa'").fetchone()
        assert gone["archived_at"] is not None

    def test_a_set_comes_back_with_everything_archived_with_it(self, con):
        self.archive_the_set(con)
        material.stage(con, "01", "restore_set", True)
        report = material.apply_pending(con, builtin())
        assert report.restored == 1
        unit = con.execute("SELECT archived_at FROM units WHERE id = '01'").fetchone()
        assert unit["archived_at"] is None
        assert [n.id for n in material.live_notes(con)] == ["casa"]
        # The cards come back too, or the exercise is restored and unstudiable.
        live = con.execute(
            "SELECT count(*) FROM cards WHERE note_id = 'casa' AND archived_at IS NULL"
        ).fetchone()[0]
        assert live > 0

    def test_the_drawer_says_how_much_comes_back(self, con):
        self.archive_the_set(con)
        material.stage(con, "01", "restore_set", True)
        (change,) = [d for d in material.diff(con) if d.kind == "restore_set"]
        assert change.before == 1, "one note was archived with the set"

    def test_a_set_that_is_not_archived_cannot_be_restored(self, con):
        with pytest.raises(NotEditable, match="not archived"):
            material.stage(con, "01", "restore_set", True)

    def test_a_note_archived_on_its_own_is_not_swept_back_in(self, con):
        """
        Restoring a shelf says nothing about a note that left for its own
        reason. Only what went with the set comes back with it.
        """
        material.stage(con, "casa", "archive", True)
        material.apply_pending(con, builtin())
        self.archive_the_set(con)

        material.stage(con, "01", "restore_set", True)
        material.apply_pending(con, builtin())
        still = con.execute("SELECT archived_at FROM notes WHERE id = 'casa'").fetchone()
        assert still["archived_at"] is not None, "it was archived before the set was"

    def test_history_is_untouched_by_either_direction(self, con):
        con.execute(
            "INSERT INTO card_state(user_id, card_id, algo, algo_version, state, "
            "interval, seen, correct, wrong, lapses, bucket) "
            "VALUES(1, 'casa#recognize', 'sm2', 1, '{}', 3, 4, 3, 1, 0, 'young')"
        )
        con.commit()
        before = con.execute("SELECT count(*) FROM card_state").fetchone()[0]
        self.archive_the_set(con)
        material.stage(con, "01", "restore_set", True)
        material.apply_pending(con, builtin())
        assert con.execute("SELECT count(*) FROM card_state").fetchone()[0] == before
