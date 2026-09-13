"""
Upgrading a database that already exists.

This file exists because of an outage. `store/db.py` states the rule in prose --
"a step runs *before* `SCHEMA`, so it must not assume a table introduced in the
same version exists yet" -- and the person who wrote that rule down then added a
step that altered `study_styles`, a table `SCHEMA` creates. On a real database
the sequence was:

  13  adds `review_log.style_revision_id`, and commits
  14  raises "no such table: study_styles"
      -> the connect aborts before the version is stamped
      next connect: 13 replays -> "duplicate column name: style_revision_id"

on a database that had been working a minute earlier. CI was green throughout,
because every test here builds a database from `SCHEMA` and no test upgraded one.
A rule with no test is a decoration, which is the same lesson `plans.KNOBS`
taught two commits earlier.
"""

from __future__ import annotations

import sqlite3

import pytest

from repetita import store
from repetita.store import db


def _version(con):
    row = con.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    return int(row["value"]) if row else 0


def _without_column(con, table, column):
    """
    Rebuild `table` without `column`, keeping its rows.

    Not `ALTER TABLE ... DROP COLUMN`: SQLite re-parses the stored CREATE
    statement to do that, and `review_log`'s carries a trailing `--` comment
    after its last column, so it fails with "incomplete input". Copying is what
    this codebase would have to do anyway, and it keeps the rows, which is the
    part these tests are about.
    """
    kept = [r["name"] for r in con.execute(f"PRAGMA table_info({table})") if r["name"] != column]
    cols = ", ".join(kept)
    con.execute(f"CREATE TABLE _old AS SELECT {cols} FROM {table}")
    con.execute(f"DROP TABLE {table}")
    con.execute(f"ALTER TABLE _old RENAME TO {table}")


def _at_version(path, version, drop=(), drop_columns=()):
    """A database in its current shape, wound back to look like an older one."""
    con = store.connect(path)
    with con:
        for table in drop:
            con.execute(f"DROP TABLE IF EXISTS {table}")
        for table, column in drop_columns:
            _without_column(con, table, column)
        con.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(version),))
    con.close()


class TestReplaying:
    def test_every_migration_survives_being_replayed(self, tmp_path):
        # Version 0 against a database that already has every column. A step is a
        # statement about what the schema contains at a version, not a command
        # that has never been run, and the two differ the moment one half-applies.
        path = tmp_path / "t.db"
        _at_version(path, 0)
        con = store.connect(path)
        assert _version(con) == db.SCHEMA_VERSION
        con.close()

    def test_connecting_twice_changes_nothing(self, tmp_path):
        path = tmp_path / "t.db"
        store.connect(path).close()
        store.connect(path).close()
        con = store.connect(path)
        assert _version(con) == db.SCHEMA_VERSION
        con.close()


class TestTheOutage:
    """The exact two states that took the engine down, reproduced."""

    def test_a_database_at_twelve_upgrades(self, tmp_path):
        # What every real database looked like before this feature: no style
        # tables, no `style_revision_id`.
        path = tmp_path / "t.db"
        _at_version(
            path,
            12,
            drop=("study_styles", "style_revisions"),
            drop_columns=(("review_log", "style_revision_id"),),
        )
        con = store.connect(path)
        assert _version(con) == db.SCHEMA_VERSION
        cols = {r[1] for r in con.execute("PRAGMA table_info(study_styles)")}
        assert {"focus", "focus_until"} <= cols
        assert "style_revision_id" in {r[1] for r in con.execute("PRAGMA table_info(review_log)")}
        con.close()

    def test_a_database_left_half_upgraded_recovers(self, tmp_path):
        # The state the outage actually left behind: 13 had committed its column,
        # 14 had raised, and the version was never stamped. Before the fix this
        # is the connect that failed with "duplicate column name".
        path = tmp_path / "t.db"
        _at_version(path, 12, drop=("study_styles", "style_revisions"))
        con = store.connect(path)
        assert _version(con) == db.SCHEMA_VERSION
        con.close()

    def test_study_history_is_untouched_by_the_upgrade(self, tmp_path):
        # The only thing here that cannot be rebuilt (CLAUDE.md rule 1). An
        # upgrade may add tables and columns; it may not cost somebody a day.
        path = tmp_path / "t.db"
        con = store.connect(path)
        with con:
            con.execute(
                "INSERT INTO review_log(user_id,card_id,rating,review_datetime,day,algo) "
                "VALUES(1,'a#produce',3,'2026-09-06T20:00:00+00:00','2026-09-06','sm2')"
            )
        con.close()
        _at_version(path, 12, drop=("study_styles", "style_revisions"))

        con = store.connect(path)
        row = con.execute("SELECT card_id, day FROM review_log").fetchone()
        assert row["card_id"] == "a#produce"
        assert row["day"] == "2026-09-06"
        con.close()


class TestTheContract:
    def test_a_step_never_alters_a_table_it_did_not_bring(self, tmp_path):
        """
        The rule, as a test rather than a paragraph.

        Each step is run against a database holding *only* what `SCHEMA` builds
        minus the tables no step before it created -- which is the situation a
        step actually meets, since `SCHEMA` runs after all of them. A step that
        needs a table must create it.
        """
        for version, script in db.MIGRATIONS:
            statements = db._statements(script)
            altered = {
                s.split()[2].strip('"`[]')
                for s in statements
                if s.upper().startswith("ALTER TABLE")
            }
            created = {
                s.split("EXISTS")[1].split("(")[0].strip().strip('"`[]')
                for s in statements
                if "CREATE TABLE IF NOT EXISTS" in s.upper()
            }
            for table in altered - created:
                # It must already exist at that point -- i.e. no step may alter a
                # table that only `SCHEMA` knows about.
                assert table in _TABLES_BEFORE_MIGRATIONS, (
                    f"migration {version} alters {table!r}, which nothing before it creates; "
                    f"`SCHEMA` runs after every step, so this raises 'no such table' on "
                    f"exactly the databases the step exists for"
                )

    def test_the_splitter_respects_string_literals(self):
        # `script.split(";")` would cut this in half and corrupt the schema
        # rather than raise, which is the worse of the two failures.
        out = db._statements("ALTER TABLE t ADD COLUMN c TEXT DEFAULT 'a;b';")
        assert len(out) == 1


#: Tables that exist before any migration runs, on a database old enough to need
#: one. Anything a step alters must be in here or be created by the step itself.
_TABLES_BEFORE_MIGRATIONS = {
    "meta",
    "courses",
    "notes",
    "cards",
    "card_state",
    "review_log",
    "units",
    "notetypes",
    "facet_axes",
    "facet_values",
    "tag_aliases",
    "note_facets",
    "distractors",
    "study_plans",
    "plan_priorities",
    "plan_knobs",
    "plan_revisions",
    "material_drafts",
    "material_issues",
    "card_reports",
    "containers",
    "users",
    "enrolments",
    "set_enrolments",
    "study_styles",
    "style_revisions",
}


def test_sqlite_supports_drop_column(tmp_path):
    """These tests wind a schema back with DROP COLUMN; say so if it is missing."""
    if sqlite3.sqlite_version_info < (3, 35):
        pytest.skip("DROP COLUMN needs SQLite 3.35+")
