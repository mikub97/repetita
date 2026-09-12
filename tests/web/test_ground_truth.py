"""
The database is the ground truth, and the files are a way in and a way out.

ADR-0006 gave the database ownership of the material; ADR-0015 removed the last
thing that contradicted it, which was that every startup imported the course
files anyway. The tests here pin the properties that change makes available --
principally that a course can be served when its files are somewhere else, or
gone.

The one thing they must never show is study history moving. An import is the
operation most able to do harm, and `card_state` is the thing that cannot be
rebuilt from anything.
"""

from __future__ import annotations

import shutil
import textwrap

import pytest

from repetita import store
from repetita.content.loader import load_course
from repetita.web.app import build_library, seed_if_absent

COURSE = """\
format_version: 1
id: t
l2: {code: pt}
l1: {code: pl}
license: {name: CC BY-SA 4.0}
"""

NOTES = """\
    notetype: gap
    notes:
      - id: a
        prompt: Eu ___ de casa.
        cue: sair
        answers: [saio]
      - id: b
        prompt: Ela ___ cedo.
        cue: sair
        answers: [sai]
    """

TYPES = """\
proverb:
  fields:
    saying: {type: text, visibility: after}
    meaning: {type: text, visibility: before}
  cards:
    recall: {ask: [meaning], expect: saying, grader: typed, forms: [typein]}
"""


def _course(root, notes=NOTES, types=None):
    (root / "units" / "01" / "notes").mkdir(parents=True)
    (root / "course.yaml").write_text(COURSE)
    (root / "units" / "01" / "notes" / "n.yaml").write_text(textwrap.dedent(notes))
    if types:
        (root / "notetypes.yaml").write_text(types)
    return root


@pytest.fixture
def course_dir(tmp_path):
    return _course(tmp_path / "course")


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "study.db"


@pytest.fixture
def con(db_path):
    c = store.connect(db_path)
    yield c
    c.close()


def _without_b():
    """NOTES with its second exercise removed, as an author would delete one."""
    body = textwrap.dedent(NOTES)
    return body[: body.index("  - id: b")]


def _import(db_path, root, **kw):
    con = store.connect(db_path)
    try:
        return store.cards.sync(con, load_course(root), **kw)
    finally:
        con.close()


class TestSeeding:
    def test_an_empty_database_is_seeded_from_the_directory(self, course_dir, db_path):
        assert seed_if_absent(db_path, course_dir) == "t"
        assert sorted(build_library(db_path, "t").notes) == ["a", "b"]

    def test_it_runs_once_and_then_never_again(self, course_dir, db_path):
        seed_if_absent(db_path, course_dir)
        path = course_dir / "units" / "01" / "notes" / "n.yaml"
        path.write_text(
            path.read_text() + "  - id: c\n    prompt: x\n    cue: y\n    answers: [z]\n"
        )

        # The second call finds the course already there and reads nothing.
        assert seed_if_absent(db_path, course_dir) == "t"
        assert "c" not in build_library(db_path, "t").notes

    def test_a_second_course_in_one_database_still_seeds(self, tmp_path, db_path):
        # Keyed on the course, not on the database being empty. "Empty" would
        # mean the second course anybody added never seeded at all.
        seed_if_absent(db_path, _course(tmp_path / "one"))
        # Different note ids: an id is a scheduling key and is unique across the
        # database, not per course.
        other = _course(
            tmp_path / "two", notes=NOTES.replace("id: a", "id: c").replace("id: b", "id: d")
        )
        (other / "course.yaml").write_text(COURSE.replace("id: t", "id: u"))

        assert seed_if_absent(db_path, other) == "u"
        assert sorted(build_library(db_path, "u").notes) == ["c", "d"]
        assert sorted(build_library(db_path, "t").notes) == ["a", "b"]

    def test_a_course_the_database_does_not_hold_is_refused(self, db_path, course_dir):
        seed_if_absent(db_path, course_dir)
        with pytest.raises(LookupError):
            build_library(db_path, "no-such-course")


class TestTheFilesAreNotNeeded:
    def test_a_course_is_served_after_its_directory_is_deleted(self, course_dir, db_path):
        seed_if_absent(db_path, course_dir)
        shutil.rmtree(course_dir)

        library = build_library(db_path, "t")

        assert sorted(library.notes) == ["a", "b"]
        assert library.course.title == {}
        assert [step.unit for step in library.course.path] == ["01"]

    def test_a_course_declared_exercise_type_survives_without_its_file(self, tmp_path, db_path):
        root = _course(
            tmp_path / "c",
            notes=textwrap.dedent("""\
            notetype: proverb
            notes:
              - id: p
                meaning: kto rano wstaje
                saying: Deus ajuda quem cedo madruga
            """),
            types=TYPES,
        )
        seed_if_absent(db_path, root)
        (root / "notetypes.yaml").unlink()

        library = build_library(db_path, "t")

        assert "proverb" in library.notetypes
        assert "p#recall" in library.cards
        assert library.quarantined == 0


class TestWhatAnImportMayArchive:
    def test_a_whole_course_archives_what_it_does_not_contain(self, course_dir, db_path, con):
        seed_if_absent(db_path, course_dir)
        path = course_dir / "units" / "01" / "notes" / "n.yaml"
        path.write_text(_without_b())

        report = _import(db_path, course_dir)

        assert report.archived == 1
        assert report.archived_ids == ("b",)
        assert "b" not in build_library(db_path, "t").notes

    def test_a_fragment_archives_nothing(self, course_dir, db_path, tmp_path):
        # The rule that makes a partial import safe. Without it, importing one
        # unit's file would empty the course it belongs to.
        seed_if_absent(db_path, course_dir)
        fragment = tmp_path / "frag"
        (fragment / "units" / "01" / "notes").mkdir(parents=True)
        (fragment / "units" / "01" / "notes" / "extra.yaml").write_text(
            textwrap.dedent("""\
                notetype: gap
                notes:
                  - id: c
                    prompt: Nós ___ juntos.
                    cue: sair
                    answers: [saímos]
                """)
        )

        con = store.connect(db_path)
        try:
            from repetita.content.loader import load_fragment
            from repetita.store.cards import facets_from_db, notetypes_from_db

            result = load_fragment(
                fragment,
                notetypes=notetypes_from_db(con, "t"),
                facets=facets_from_db(con, "t"),
            )
            report = store.cards.sync(con, result, archive_missing=False, course="t")
        finally:
            con.close()

        assert report.archived == 0
        assert sorted(build_library(db_path, "t").notes) == ["a", "b", "c"]

    def test_add_only_downgrades_a_whole_course(self, course_dir, db_path):
        seed_if_absent(db_path, course_dir)
        path = course_dir / "units" / "01" / "notes" / "n.yaml"
        path.write_text(_without_b())

        report = _import(db_path, course_dir, archive_missing=False)

        assert report.archived == 0
        assert "b" in build_library(db_path, "t").notes

    def test_the_preview_says_exactly_what_the_run_does(self, course_dir, db_path, con):
        # A preview that disagrees with the run is worse than no preview: it
        # teaches whoever reads it to skip the next one.
        seed_if_absent(db_path, course_dir)
        path = course_dir / "units" / "01" / "notes" / "n.yaml"
        path.write_text(_without_b())

        result = load_course(course_dir)
        preview, _ = store.cards.preview_import(con, result)
        done = store.cards.sync(con, result)

        assert preview.archived == done.archived
        assert preview.archived_ids == done.archived_ids
        assert (preview.added, preview.updated) == (done.added, done.updated)

    def test_a_note_written_in_the_app_is_never_archived_by_an_import(
        self, course_dir, db_path, con
    ):
        # It was never in a file, so its absence from one says nothing (ADR-0010).
        seed_if_absent(db_path, course_dir)
        con.execute(
            "INSERT INTO notes(id,course,unit,notetype,ord,tags,fields,csum,origin,"
            "created_at,updated_at,edited_at,label_custom) "
            "VALUES('mine','t','01','gap',9,'[]','{}',0,'','x','x','x',0)"
        )
        con.commit()

        report = _import(db_path, course_dir)

        assert "mine" not in report.archived_ids
        row = con.execute("SELECT archived_at FROM notes WHERE id = 'mine'").fetchone()
        assert row["archived_at"] is None


class TestHistoryDoesNotMove:
    def test_importing_repeatedly_never_touches_the_schedule(self, course_dir, db_path, con):
        seed_if_absent(db_path, course_dir)
        con.execute(
            "INSERT INTO card_state(user_id,card_id,algo,algo_version,state,due,interval,"
            "seen,correct,wrong,lapses) VALUES(1,'a#fill','sm2',1,'{}','2026-09-20',7,3,3,0,0)"
        )
        con.execute(
            "INSERT INTO review_log(card_id,rating,review_datetime,day,algo,state_before) "
            "VALUES('a#fill',3,'2026-09-12T10:00:00+00:00','2026-09-12','sm2','{}')"
        )
        con.commit()

        for _ in range(3):
            _import(db_path, course_dir)

        state = con.execute("SELECT * FROM card_state WHERE card_id = 'a#fill'").fetchone()
        assert state["due"] == "2026-09-20" and state["interval"] == 7 and state["seen"] == 3
        assert con.execute("SELECT COUNT(*) AS n FROM review_log").fetchone()["n"] == 1
