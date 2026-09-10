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
    def build(notes):
        """One unit. Pass one notes file as a string, or several as {name: yaml}."""
        root = tmp_path / "course"
        directory = root / "units" / "01" / "notes"
        directory.mkdir(parents=True, exist_ok=True)
        (root / "course.yaml").write_text(COURSE)
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


class TestSync:
    def test_content_lands_in_the_cache(self, con, course):
        notes, cards = store.sync(con, course(TWO_NOTES))
        assert (notes, cards) == (2, 4)
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
