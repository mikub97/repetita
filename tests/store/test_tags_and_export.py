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


class TestWhatARenameIsAllowedToReach:
    """
    `rename` accepted a `course` and used it for one thing only: the row it
    wrote to `tag_aliases`. The rename itself ran over every note in the
    database, and the plan repair that follows it matched on the value alone --
    no axis, no course, no plan.
    """

    @pytest.fixture
    def two_courses(self, con, tmp_path):
        """A second course using the same tag, which is the ordinary case."""
        root = tmp_path / "other"
        (root / "units" / "01" / "notes").mkdir(parents=True)
        (root / "course.yaml").write_text(COURSE.replace("id: t", "id: u"))
        (root / "facets.yaml").write_text(textwrap.dedent(FACETS))
        (root / "units" / "01" / "notes" / "n.yaml").write_text(
            textwrap.dedent(
                """\
                notetype: vocab
                tags: [comida]
                notes:
                  - id: casa-it
                    l2: la casa
                    l1: dom
                """
            )
        )
        store.sync(con, load_course(root))
        return con

    def tags_of(self, con, note_id):
        import json

        row = con.execute("SELECT tags FROM notes WHERE id = ?", (note_id,)).fetchone()
        return json.loads(row["tags"])

    def test_a_rename_in_one_course_leaves_the_others_alone(self, two_courses):
        T.rename(two_courses, "comida", "jedzenie", course="t")
        assert "jedzenie" in self.tags_of(two_courses, "casa")
        assert self.tags_of(two_courses, "casa-it") == ["comida"], "another course was renamed"

    def test_without_a_course_it_still_means_everywhere(self, two_courses):
        # The old behaviour, kept and now said out loud rather than arrived at
        # by dropping an argument on the floor.
        T.rename(two_courses, "comida", "jedzenie")
        assert self.tags_of(two_courses, "casa-it") == ["jedzenie"]

    def test_a_priority_on_another_axis_is_not_rewritten(self, con):
        # A plan priority is an (axis, value) pair. Matching on the value alone
        # re-points a priority at material nobody asked for -- here, a plan that
        # wanted the `comida` *unit* would silently start meaning `jedzenie`.
        plan = con.execute(
            "INSERT INTO study_plans(user_id,name,course,active,created_at,updated_at) "
            "VALUES(1,'p','t',1,'2026-09-11','2026-09-11')"
        ).lastrowid
        con.executemany(
            "INSERT INTO plan_priorities(plan_id,rank,axis,value) VALUES(?,?,?,?)",
            [(plan, 0, "topic", "comida"), (plan, 1, "unit", "comida")],
        )
        con.commit()
        T.rename(con, "comida", "jedzenie", course="t")
        rows = dict(
            con.execute("SELECT axis, value FROM plan_priorities WHERE plan_id = ?", (plan,))
        )
        assert rows == {"topic": "jedzenie", "unit": "comida"}

    def test_everybody_who_prioritised_the_tag_keeps_a_working_plan(self, con):
        # The one write here that crosses accounts on purpose. The material
        # changed for everybody, so a priority naming the old tag now names
        # nothing: repairing only the caller's plan would break the others'.
        plans = []
        for user in (1, 2):
            plans.append(
                con.execute(
                    "INSERT INTO study_plans(user_id,name,course,active,created_at,updated_at) "
                    "VALUES(?,'p','t',1,'2026-09-11','2026-09-11')",
                    (user,),
                ).lastrowid
            )
        con.executemany(
            "INSERT INTO plan_priorities(plan_id,rank,axis,value) VALUES(?,0,'topic','comida')",
            [(p,) for p in plans],
        )
        con.commit()
        T.rename(con, "comida", "jedzenie", course="t")
        values = [r["value"] for r in con.execute("SELECT value FROM plan_priorities")]
        assert values == ["jedzenie", "jedzenie"]


class TestReBucketing:
    """
    `card_state.bucket` is a denormalisation, not a decision: `bucket_of` derives
    it from the row it sits in and nothing else (ADR-0002). `reclassify` read
    `all_states(con)`, which means user 1, and then wrote buckets keyed on
    `(user_id, card_id)` -- re-filing one person and leaving the rest describing
    the world before the change.
    """

    def test_it_re_buckets_every_account(self, con):
        for user in (1, 2):
            con.execute(
                "INSERT INTO card_state(user_id,card_id,algo,algo_version,state,"
                "seen,correct,bucket) VALUES(?,'casa#recognize','sm2',1,'{}',9,9,'nonsense')",
                (user,),
            )
        con.commit()
        T.rename(con, "comida", "jedzenie", course="t")
        left = dict(
            con.execute("SELECT user_id, bucket FROM card_state WHERE card_id = 'casa#recognize'")
        )
        assert left[1] != "nonsense", "the caller's bucket should be recomputed"
        assert left[2] != "nonsense", "and so should everybody else's"
        assert left[1] == left[2], "same row, same rule, same answer"


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
