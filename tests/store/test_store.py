import datetime as dt
import json
import sqlite3
import textwrap

import pytest

from repetita import srs, store
from repetita.content.loader import load_course
from repetita.core.types import Rating
from repetita.store import db

UTC = dt.UTC
AT = dt.datetime(2026, 9, 6, 21, 30, tzinfo=UTC)
DAY = dt.date(2026, 9, 6)

COURSE = """\
format_version: 1
id: t
l2: {code: pt}
l1: {code: pl}
license: {name: CC BY-SA 4.0}
"""


@pytest.fixture
def con(tmp_path):
    c = store.connect(tmp_path / "t.db")
    yield c
    c.close()


@pytest.fixture
def course(tmp_path):
    def build(notes, *, unit_yaml=None, course_yaml=COURSE):
        """One unit. Pass one notes file as a string, or several as {name: yaml}."""
        root = tmp_path / "course"
        directory = root / "units" / "01" / "notes"
        directory.mkdir(parents=True, exist_ok=True)
        (root / "course.yaml").write_text(course_yaml)
        if unit_yaml is not None:
            (root / "units" / "01" / "unit.yaml").write_text(textwrap.dedent(unit_yaml))
        files = {"n.yaml": notes} if isinstance(notes, str) else notes
        for name, text in files.items():
            (directory / name).write_text(textwrap.dedent(text))
        return load_course(root)

    return build


TWO_NOTES = """\
    notetype: vocab
    notes:
      - id: casa
        l2: a casa
        l1: dom
      - id: rua
        l2: a rua
        l1: ulica
    """


ONE_NOTE = """\
    notetype: vocab
    notes:
      - id: casa
        l2: a casa
        l1: dom
    """

EDITED_NOTE = """\
    notetype: vocab
    notes:
      - id: casa
        l2: a casa
        l1: dom (budynek)
      - id: rua
        l2: a rua
        l1: ulica
    """


class TestSync:
    def test_content_lands_in_the_cache(self, con, course):
        report = store.sync(con, course(TWO_NOTES))
        assert (report.notes, report.cards) == (2, 4)
        assert (report.added, report.updated, report.archived) == (2, 0, 0)
        assert sorted(store.card_ids(con)) == [
            "casa#produce",
            "casa#recognize",
            "rua#produce",
            "rua#recognize",
        ]

    def test_syncing_twice_is_idempotent(self, con, course):
        result = course(TWO_NOTES)
        store.sync(con, result)
        store.sync(con, result)
        assert len(store.card_ids(con)) == 4

    def test_sync_never_touches_card_state(self, con, course):
        result = course(TWO_NOTES)
        store.sync(con, result)
        backend = srs.get("sm2")
        store.record_answer(con, "casa#produce", Rating.GOOD, backend=backend, at=AT)
        before = store.get_state(con, "casa#produce")

        store.sync(con, result)

        assert store.get_state(con, "casa#produce") == before

    def test_a_card_whose_note_disappears_keeps_its_history(self, con, course):
        store.sync(con, course(TWO_NOTES))
        backend = srs.get("sm2")
        store.record_answer(con, "rua#produce", Rating.GOOD, backend=backend, at=AT)

        # The author removes the note. Its schedule must survive, so that putting
        # it back does not reset months of work.
        store.sync(
            con,
            course("""\
            notetype: vocab
            notes:
              - id: casa
                l2: a casa
                l1: dom
            """),
        )

        assert "rua#produce" not in store.card_ids(con)
        assert store.get_state(con, "rua#produce") is not None


class TestRecordAnswer:
    def test_one_answer_writes_exactly_one_log_row(self, con, course):
        store.sync(con, course(TWO_NOTES))
        store.record_answer(con, "casa#produce", Rating.GOOD, backend=srs.get("sm2"), at=AT)
        rows = con.execute("SELECT * FROM review_log").fetchall()
        assert len(rows) == 1
        assert rows[0]["rating"] == int(Rating.GOOD)
        assert rows[0]["card_id"] == "casa#produce"

    def test_the_state_that_preceded_the_answer_is_logged(self, con, course):
        # Without this, FSRS cannot be tuned and no scheduler can be evaluated.
        # It cannot be reconstructed later, which is why it is written from the
        # first answer rather than added when someone needs it.
        store.sync(con, course(TWO_NOTES))
        backend = srs.get("sm2")
        store.record_answer(con, "casa#produce", Rating.GOOD, backend=backend, at=AT)
        store.record_answer(con, "casa#produce", Rating.GOOD, backend=backend, at=AT)
        rows = con.execute("SELECT state_before FROM review_log ORDER BY id").fetchall()
        assert json.loads(rows[0]["state_before"])["reps"] == 0
        assert json.loads(rows[1]["state_before"])["reps"] == 1

    def test_counters_are_kept_as_columns_not_read_out_of_state(self, con, course):
        store.sync(con, course(TWO_NOTES))
        backend = srs.get("sm2")
        for rating in (Rating.GOOD, Rating.AGAIN, Rating.GOOD):
            store.record_answer(con, "casa#produce", rating, backend=backend, at=AT)
        cs = store.get_state(con, "casa#produce")
        assert (cs.seen, cs.correct, cs.wrong, cs.lapses) == (3, 2, 1, 1)

    def test_due_is_denormalised_so_the_queue_never_parses_state(self, con, course):
        store.sync(con, course(TWO_NOTES))
        cs = store.record_answer(con, "casa#produce", Rating.GOOD, backend=srs.get("sm2"), at=AT)
        assert cs.due == DAY.isoformat()  # first learning step: back this session

    def test_wrong_answers_are_kept(self, con, course):
        # In a year the best distractors are the mistakes real learners made.
        store.sync(con, course(TWO_NOTES))
        store.record_answer(
            con,
            "casa#produce",
            Rating.AGAIN,
            backend=srs.get("sm2"),
            at=AT,
            answer="a rua",
        )
        assert con.execute("SELECT answer FROM review_log").fetchone()["answer"] == "a rua"

    def test_a_naive_datetime_is_refused(self, con, course):
        store.sync(con, course(TWO_NOTES))
        with pytest.raises(ValueError, match="aware"):
            store.record_answer(
                con,
                "casa#produce",
                Rating.GOOD,
                backend=srs.get("sm2"),
                at=dt.datetime(2026, 9, 6, 21, 30),
            )

    def test_the_local_day_is_separate_from_the_utc_instant(self, con, course):
        # 21:30 in Bahia is 00:30 UTC the next day. Deriving the day from the
        # instant files the session under tomorrow; the predecessor shipped
        # exactly that bug.
        store.sync(con, course(TWO_NOTES))
        late = dt.datetime(2026, 9, 7, 0, 30, tzinfo=UTC)
        store.record_answer(
            con,
            "casa#produce",
            Rating.GOOD,
            backend=srs.get("sm2"),
            at=late,
            local_day=DAY,
        )
        assert con.execute("SELECT day FROM review_log").fetchone()["day"] == "2026-09-06"

    def test_elapsed_days_is_measured_not_assumed(self, con, course):
        store.sync(con, course(TWO_NOTES))
        backend = srs.get("sm2")
        store.record_answer(con, "casa#produce", Rating.GOOD, backend=backend, at=AT, local_day=DAY)
        later = dt.date(2026, 9, 20)
        store.record_answer(
            con,
            "casa#produce",
            Rating.GOOD,
            backend=backend,
            at=dt.datetime(2026, 9, 20, 9, 0, tzinfo=UTC),
            local_day=later,
        )
        rows = con.execute("SELECT elapsed_days FROM review_log ORDER BY id").fetchall()
        assert rows[0]["elapsed_days"] is None
        assert rows[1]["elapsed_days"] == 14.0


class TestQueries:
    def test_recent_ratings_are_newest_first(self, con, course):
        store.sync(con, course(TWO_NOTES))
        backend = srs.get("sm2")
        for rating in (Rating.AGAIN, Rating.GOOD, Rating.EASY):
            store.record_answer(con, "casa#produce", rating, backend=backend, at=AT)
        assert store.recent_ratings(con, 2) == [Rating.EASY, Rating.GOOD]

    def test_first_seen_counts_introductions_not_answers(self, con, course):
        store.sync(con, course(TWO_NOTES))
        backend = srs.get("sm2")
        yesterday = dt.date(2026, 9, 5)
        store.record_answer(
            con,
            "casa#produce",
            Rating.GOOD,
            backend=backend,
            at=AT,
            local_day=yesterday,
        )
        # Reviewed again today -- not a new introduction.
        store.record_answer(con, "casa#produce", Rating.GOOD, backend=backend, at=AT, local_day=DAY)
        store.record_answer(con, "rua#produce", Rating.GOOD, backend=backend, at=AT, local_day=DAY)
        assert store.first_seen_on(con, DAY) == 1
        assert store.count_on(con, DAY) == 2

    def test_the_lesson_introduction_count_ignores_the_back_catalogue(self, con, course):
        # What the lesson introduction cap is charged for: `first_seen_on` counts
        # every card met today, and the budget may only be spent on lesson
        # material recent enough to be exempt from the gate.
        store.sync(
            con,
            course(
                {
                    "old.yaml": TWO_NOTES,
                    "licao.yaml": """\
                    notetype: vocab
                    lesson: 2026-09-05
                    notes:
                      - id: feira
                        l2: a feira
                        l1: targ
                    """,
                }
            ),
        )
        backend = srs.get("sm2")
        for card_id in ("casa#produce", "rua#produce", "feira#produce"):
            store.record_answer(con, card_id, Rating.GOOD, backend=backend, at=AT, local_day=DAY)

        assert store.first_seen_on(con, DAY) == 3
        assert store.lesson_first_seen_on(con, DAY, since=dt.date(2026, 9, 3)) == 1
        # A lesson older than the window is back catalogue as far as the cap goes.
        assert store.lesson_first_seen_on(con, DAY, since=dt.date(2026, 9, 6)) == 0

    def test_a_new_card_is_not_due(self, con, course):
        store.sync(con, course(TWO_NOTES))
        cs = store.CardState(card_id="x", algo="sm2", algo_version=1, state={})
        assert not cs.is_due(DAY), "new cards are introduced under a gate, not owed"


class TestSchema:
    def test_reopening_an_existing_database_is_safe(self, tmp_path, course):
        path = tmp_path / "t.db"
        c1 = store.connect(path)
        store.sync(c1, course(TWO_NOTES))
        store.record_answer(c1, "casa#produce", Rating.GOOD, backend=srs.get("sm2"), at=AT)
        c1.close()

        c2 = store.connect(path)
        assert store.get_state(c2, "casa#produce") is not None
        assert len(store.card_ids(c2)) == 4
        c2.close()

    def test_schema_version_is_recorded(self, con):
        row = con.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        assert int(row["value"]) >= 1


_META = "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
_V1 = _META + "\nCREATE TABLE IF NOT EXISTS widgets (id TEXT PRIMARY KEY);"
# v2 adds a column *and an index on it*, which is the ordinary shape of a
# migration and the one that exposes ordering mistakes: the index names a column
# that only exists after the ALTER has run.
_V2 = (
    _META
    + "\nCREATE TABLE IF NOT EXISTS widgets (id TEXT PRIMARY KEY, colour TEXT);"
    + "\nCREATE INDEX IF NOT EXISTS ix_widgets_colour ON widgets(colour);"
)
_STEP = [
    (
        2,
        "ALTER TABLE widgets ADD COLUMN colour TEXT;"
        "CREATE INDEX IF NOT EXISTS ix_widgets_colour ON widgets(colour);",
    )
]


def _columns(con, table):
    return {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}


def _indexes(con, table):
    return {r["name"] for r in con.execute(f"PRAGMA index_list({table})")}


def _version(con):
    return int(
        con.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()["value"]
    )


class TestMigrations:
    """
    `MIGRATIONS` is empty and has never had an entry, so nothing has exercised
    this path yet. These tests exist to make the first entry safe to add: the
    mechanism was wrong in a way that only shows up on a database created after
    the migration is written, which is the worst possible failure mode -- green
    on the machine of whoever wrote it, red on a fresh clone and in CI.

    A toy schema rather than the real one, because the point is the *mechanism*,
    and pinning it to whatever `SCHEMA` happens to contain would make these fail
    for unrelated reasons every time a table is added.
    """

    def _at_v2(self, monkeypatch):
        monkeypatch.setattr(db, "SCHEMA", _V2)
        monkeypatch.setattr(db, "SCHEMA_VERSION", 2)
        monkeypatch.setattr(db, "MIGRATIONS", _STEP)

    def test_a_fresh_database_does_not_replay_migrations(self, tmp_path, monkeypatch):
        """
        The bug this closes. `SCHEMA` builds every table in its final shape, so a
        brand-new database already has `colour` -- and having no `meta` row, it
        used to read as version 0 and replay the ALTER anyway, failing with
        "duplicate column name".
        """
        self._at_v2(monkeypatch)
        con = db.connect(tmp_path / "fresh.db")
        assert "colour" in _columns(con, "widgets")
        assert _version(con) == 2
        con.close()

    def test_an_existing_database_is_migrated(self, tmp_path, monkeypatch):
        path = tmp_path / "old.db"
        monkeypatch.setattr(db, "SCHEMA", _V1)
        monkeypatch.setattr(db, "SCHEMA_VERSION", 1)
        monkeypatch.setattr(db, "MIGRATIONS", [])
        old = db.connect(path)
        old.execute("INSERT INTO widgets(id) VALUES('w1')")
        old.commit()
        old.close()

        self._at_v2(monkeypatch)
        con = db.connect(path)
        assert "colour" in _columns(con, "widgets"), "the ALTER did not run"
        assert _version(con) == 2
        assert con.execute("SELECT COUNT(*) AS n FROM widgets").fetchone()["n"] == 1
        con.close()

    def test_both_routes_reach_the_same_schema(self, tmp_path, monkeypatch):
        """
        The property that makes `SCHEMA`-plus-`MIGRATIONS` coherent: it must not
        matter whether a database was created at v2 or upgraded to it.
        """
        path = tmp_path / "upgraded.db"
        monkeypatch.setattr(db, "SCHEMA", _V1)
        monkeypatch.setattr(db, "SCHEMA_VERSION", 1)
        monkeypatch.setattr(db, "MIGRATIONS", [])
        db.connect(path).close()

        self._at_v2(monkeypatch)
        upgraded = db.connect(path)
        created = db.connect(tmp_path / "created.db")
        assert _columns(upgraded, "widgets") == _columns(created, "widgets")
        assert _version(upgraded) == _version(created)
        upgraded.close()
        created.close()

    def test_an_index_on_a_newly_added_column_is_created(self, tmp_path, monkeypatch):
        """
        The second ordering bug. `SCHEMA` describes the tables as they are now,
        so it names `colour` -- a column an existing database only gains once the
        migration has run. Executing `SCHEMA` first therefore died with
        "no such column: colour" on exactly the databases the migration existed
        for, while every fresh one passed.
        """
        path = tmp_path / "indexed.db"
        monkeypatch.setattr(db, "SCHEMA", _V1)
        monkeypatch.setattr(db, "SCHEMA_VERSION", 1)
        monkeypatch.setattr(db, "MIGRATIONS", [])
        db.connect(path).close()

        self._at_v2(monkeypatch)
        con = db.connect(path)
        fresh = db.connect(tmp_path / "new.db")

        assert "ix_widgets_colour" in _indexes(con, "widgets")
        assert _indexes(con, "widgets") == _indexes(fresh, "widgets")
        con.close()
        fresh.close()

    def test_a_database_older_than_the_meta_table_is_migrated(self, tmp_path, monkeypatch):
        """
        A database from before versioning has no `meta` at all, so the read that
        decides which steps are still owed must not assume the table is there.
        """
        path = tmp_path / "ancient.db"
        raw = sqlite3.connect(path)
        raw.executescript("CREATE TABLE widgets (id TEXT PRIMARY KEY);")
        raw.commit()
        raw.close()

        self._at_v2(monkeypatch)
        con = db.connect(path)

        assert "colour" in _columns(con, "widgets")
        assert _version(con) == 2
        con.close()

    def test_reconnecting_does_not_rerun_a_migration(self, tmp_path, monkeypatch):
        """An ALTER is not idempotent, so a second connect must skip it."""
        self._at_v2(monkeypatch)
        path = tmp_path / "twice.db"
        db.connect(path).close()
        con = db.connect(path)
        assert _version(con) == 2
        con.close()


class TestRetirement:
    """
    A card that has earned its way out must actually leave the queue.

    `should_retire` existed in `srs/sm2.py` with its own unit test, and nothing
    ever called it: `record_answer` only carried `retired_at` forward. Every card
    would have been reviewed forever. The unit test passed the whole time, which
    is why this one drives the real path instead.
    """

    def _answer_until_ceiling(self, con, card_id, backend, rating=Rating.GOOD, limit=20):
        cs = None
        for i in range(limit):
            cs = store.record_answer(
                con, card_id, rating, backend=backend, at=AT, local_day=DAY + dt.timedelta(days=i)
            )
            if cs.retired_at:
                return cs, i + 1
        return cs, limit

    def test_a_clean_run_at_the_ceiling_retires_the_card(self, con, course):
        store.sync(con, course(TWO_NOTES))
        cs, answers = self._answer_until_ceiling(con, "casa#produce", srs.get("sm2"))
        assert cs.retired_at is not None, f"still not retired after {answers} clean answers"
        assert cs.retired_reason == "earned"
        assert cs.interval >= 90

    def test_a_recent_miss_keeps_it_in_the_queue(self, con, course):
        store.sync(con, course(TWO_NOTES))
        backend = srs.get("sm2")
        # Reach the ceiling, then fail once. The interval collapses, so the card
        # is nowhere near retirement -- but the point is that nothing retires it
        # on the way back up until the run is clean again.
        self._answer_until_ceiling(con, "casa#produce", backend)
        after = store.record_answer(
            con, "casa#produce", Rating.AGAIN, backend=backend, at=AT, local_day=DAY
        )
        assert after.interval == 0

    def test_a_declared_retirement_is_not_overwritten(self, con, course):
        # "I already know this" is a claim, and answering the card again must not
        # quietly relabel it as evidence.
        import dataclasses

        store.sync(con, course(TWO_NOTES))
        backend = srs.get("sm2")
        store.record_answer(con, "casa#produce", Rating.GOOD, backend=backend, at=AT)
        cs = store.get_state(con, "casa#produce")
        store.save_state(
            con, dataclasses.replace(cs, retired_at="2026-09-01", retired_reason="declared")
        )
        after = store.record_answer(con, "casa#produce", Rating.GOOD, backend=backend, at=AT)
        assert after.retired_reason == "declared"

    def test_retirement_survives_a_reload(self, con, course):
        result = course(TWO_NOTES)
        store.sync(con, result)
        cs, _ = self._answer_until_ceiling(con, "casa#produce", srs.get("sm2"))
        assert cs.retired_at is not None
        store.sync(con, result)
        assert store.get_state(con, "casa#produce").retired_at == cs.retired_at


class TestFacade:
    """
    `store/__init__.py` is a flat re-export surface: everything is used as
    `store.record_answer(...)`. That style hides a whole class of mistake --
    every internal caller reaches through the module object, so a name promised
    in `__all__` but never imported goes unnoticed until someone writes a star
    import, which nothing in this repo does.
    """

    def test_every_exported_name_is_importable(self):
        missing = sorted(n for n in store.__all__ if not hasattr(store, n))
        assert not missing, f"listed in __all__ but never imported: {missing}"

    def test_a_star_import_succeeds(self):
        namespace: dict[str, object] = {}
        exec("from repetita.store import *", namespace)
        assert "record_answer" in namespace


class TestOwnership:
    """
    ADR-0006: the database owns the material, so an import merges rather than
    wipes. These pin the four outcomes and, more importantly, the thing that
    used to be true by accident and is now true on purpose -- study history is
    never reachable by an import.
    """

    def test_a_note_removed_from_the_source_is_archived_not_deleted(self, con, course):
        store.sync(con, course(TWO_NOTES))
        store.record_answer(con, "rua#produce", Rating.GOOD, backend=srs.get("sm2"), at=AT)

        report = store.sync(con, course(ONE_NOTE))

        assert report.archived == 1
        row = con.execute("SELECT archived_at FROM notes WHERE id = 'rua'").fetchone()
        assert row is not None, "the note row was deleted; it must be archived"
        assert row["archived_at"] is not None
        # The point of archiving rather than deleting.
        assert store.get_state(con, "rua#produce") is not None

    def test_archived_material_leaves_the_queue(self, con, course):
        store.sync(con, course(TWO_NOTES))
        assert len(store.card_ids(con)) == 4

        store.sync(con, course(ONE_NOTE))

        assert sorted(store.card_ids(con)) == ["casa#produce", "casa#recognize"]

    def test_material_that_comes_back_returns_on_its_old_schedule(self, con, course):
        store.sync(con, course(TWO_NOTES))
        store.record_answer(con, "rua#produce", Rating.GOOD, backend=srs.get("sm2"), at=AT)
        before = store.get_state(con, "rua#produce")
        store.sync(con, course(ONE_NOTE))

        store.sync(con, course(TWO_NOTES))

        after = store.get_state(con, "rua#produce")
        assert "rua#produce" in store.card_ids(con)
        assert (after.due, after.interval, after.seen) == (before.due, before.interval, before.seen)

    def test_a_changed_note_is_updated(self, con, course):
        store.sync(con, course(TWO_NOTES))

        report = store.sync(con, course(EDITED_NOTE))

        assert (report.added, report.updated, report.archived) == (0, 1, 0)
        fields = json.loads(
            con.execute("SELECT fields FROM notes WHERE id = 'casa'").fetchone()["fields"]
        )
        assert fields["l1"] == "dom (budynek)"

    def test_a_local_edit_is_not_clobbered_and_the_clash_is_reported(self, con, course):
        # The behaviour the whole design turns on. Neither version is lost and
        # neither is silently chosen: the local note stays put and the id is
        # handed back so a person can decide.
        store.sync(con, course(TWO_NOTES))
        con.execute(
            "UPDATE notes SET fields = ?, edited_at = ? WHERE id = 'casa'",
            (json.dumps({"l2": "a casa", "l1": "moje dom"}), "2026-09-10T00:00:00+00:00"),
        )
        con.commit()

        report = store.sync(con, course(EDITED_NOTE))

        assert report.conflicted == ("casa",)
        assert report.updated == 0
        fields = json.loads(
            con.execute("SELECT fields FROM notes WHERE id = 'casa'").fetchone()["fields"]
        )
        assert fields["l1"] == "moje dom", "the local edit was overwritten"

    def test_an_unchanged_local_edit_is_not_a_conflict(self, con, course):
        # Edited here, untouched at the source: there is nothing to disagree
        # about, so this must stay quiet rather than nag on every reload.
        store.sync(con, course(TWO_NOTES))
        con.execute("UPDATE notes SET edited_at = ? WHERE id = 'casa'", ("2026-09-10T00:00:00Z",))
        con.commit()

        report = store.sync(con, course(TWO_NOTES))

        assert report.conflicted == ()

    def test_an_import_writes_nothing_the_second_time(self, con, course):
        store.sync(con, course(TWO_NOTES))

        report = store.sync(con, course(TWO_NOTES))

        assert (report.added, report.updated, report.archived) == (0, 0, 0)
        assert report.conflicted == ()

    def test_moving_a_note_to_another_file_is_not_an_edit(self, con, course):
        # `origin` is out of the content hash on purpose: reorganising a course
        # must not read as an edit of every note in it.
        store.sync(con, course({"n.yaml": TWO_NOTES}))

        report = store.sync(con, course({"renamed.yaml": TWO_NOTES, "n.yaml": "notes: []"}))

        assert (report.updated, report.archived, report.conflicted) == (0, 0, ())

    def test_the_review_log_is_never_touched_by_an_import(self, con, course):
        store.sync(con, course(TWO_NOTES))
        store.record_answer(con, "rua#produce", Rating.GOOD, backend=srs.get("sm2"), at=AT)
        before = con.execute("SELECT COUNT(*) AS n FROM review_log").fetchone()["n"]

        store.sync(con, course(ONE_NOTE))
        store.sync(con, course(TWO_NOTES))
        store.sync(con, course(EDITED_NOTE))

        assert con.execute("SELECT COUNT(*) AS n FROM review_log").fetchone()["n"] == before


class TestCourseAndUnits:
    """
    `unit.yaml` was skipped outright by the loader, and `course.path` and
    `tag_weights` were parsed and read by nothing. All three were authored
    structure that never reached a query.
    """

    def test_the_course_row_carries_what_was_dead_configuration(self, con, course):
        store.sync(con, course(TWO_NOTES))
        row = con.execute("SELECT * FROM courses").fetchone()
        assert row["id"] == "t"
        assert row["scheduler"] == "sm2"
        # `tag_weights` is documented as the relative share of each tag when new
        # material is introduced, and had no reader at all.
        assert json.loads(row["tag_weights"]) == {}

    def test_a_unit_carries_its_title_and_level(self, con, course):
        store.sync(
            con,
            course(
                TWO_NOTES,
                unit_yaml="""\
                title:
                  pl: Powitania
                  en: Greetings
                cefr: A1
                """,
            ),
        )
        row = con.execute("SELECT * FROM units").fetchone()
        assert json.loads(row["title"]) == {"pl": "Powitania", "en": "Greetings"}
        assert row["cefr"] == "A1"

    def test_a_unit_without_a_file_still_exists(self, con, course):
        # A unit exists because its directory does. A missing `unit.yaml` is a
        # unit with no title yet, which is the normal state of a course in
        # progress -- not an error.
        store.sync(con, course(TWO_NOTES))
        row = con.execute("SELECT * FROM units").fetchone()
        assert row["id"] == "01"
        assert json.loads(row["title"]) == {}
        assert row["cefr"] is None

    def test_the_path_supplies_order_and_prerequisites(self, con, course):
        store.sync(
            con,
            course(
                TWO_NOTES,
                course_yaml=COURSE + "path:\n  - {unit: '01', requires: ['00']}\n",
            ),
        )
        row = con.execute("SELECT ord, requires FROM units WHERE id = '01'").fetchone()
        assert row["ord"] == 0
        assert json.loads(row["requires"]) == ["00"]

    def test_a_unit_is_archived_rather_than_deleted(self, con, course):
        # Same reasoning as notes: `notes.unit` joins on this id, and a unit that
        # leaves the files still names the unit its notes were studied under.
        store.sync(con, course(TWO_NOTES))
        con.execute("INSERT INTO units(course,id) VALUES('t','99')")
        con.commit()

        store.sync(con, course(TWO_NOTES))

        row = con.execute("SELECT archived_at FROM units WHERE id = '99'").fetchone()
        assert row is not None, "the unit row was deleted"
        assert row["archived_at"] is not None

    def test_syncing_twice_leaves_one_course_row(self, con, course):
        store.sync(con, course(TWO_NOTES))
        store.sync(con, course(TWO_NOTES))
        assert con.execute("SELECT COUNT(*) AS n FROM courses").fetchone()["n"] == 1
        assert con.execute("SELECT COUNT(*) AS n FROM units").fetchone()["n"] == 1
