"""
SQLite persistence.

The one structural decision worth knowing: content and progress are separate,
and only content is rebuilt. `card_state` is never rebuilt from anything, and
that has not changed. What has changed is `notes` and `cards`: they used to be a
cache wiped and re-derived from the course files on every load, and as of
ADR-0006 the database owns them. An import merges into them and archives what
has gone, rather than deleting and re-inserting.

The property that mattered about the old design still holds, and now holds on
purpose rather than by the absence of a mechanism: a card whose note disappears
keeps its history. That is why there are still no foreign keys here.

Scheduler state is an opaque JSON blob owned by its backend (ADR-0003), with the
columns the queue needs denormalised beside it. Nothing outside `srs/` reads a
key out of `state`, which is what lets a second scheduler be added without
rewriting a single query.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

-- Content cache. Wiped and re-derived from the course files; never a source of truth.
CREATE TABLE IF NOT EXISTS notes (
  id       TEXT PRIMARY KEY,
  course   TEXT NOT NULL,
  unit     TEXT NOT NULL,
  notetype TEXT NOT NULL,
  ord      INTEGER NOT NULL,
  tags     TEXT NOT NULL,          -- JSON array
  lesson   TEXT,                   -- YYYY-MM-DD, or NULL for the back catalogue
  fields   TEXT NOT NULL,          -- JSON
  csum     INTEGER NOT NULL,       -- checksum of the first field, for duplicate hunting
  -- Ownership (ADR-0006). A note is no longer thrown away and rebuilt, so it
  -- needs to say where it came from and whether anyone has touched it since.
  origin       TEXT,               -- authored file, or NULL for a note made here
  content_hash TEXT,               -- of the authored content, as last imported
  created_at   TEXT,
  updated_at   TEXT,
  edited_at    TEXT,               -- non-NULL: changed here, so an import must not clobber it
  archived_at  TEXT                -- gone from the source. NEVER deleted: see ADR-0006
);
CREATE INDEX IF NOT EXISTS ix_notes_csum ON notes(csum);
CREATE INDEX IF NOT EXISTS ix_notes_lesson ON notes(lesson);
CREATE INDEX IF NOT EXISTS ix_notes_archived ON notes(archived_at);

CREATE TABLE IF NOT EXISTS cards (
  id        TEXT PRIMARY KEY,      -- <note_id>#<template>
  note_id   TEXT NOT NULL,
  template  TEXT NOT NULL,
  notetype  TEXT NOT NULL,
  grader    TEXT NOT NULL,
  forms     TEXT NOT NULL,         -- JSON array
  scheduled INTEGER NOT NULL DEFAULT 1,
  archived_at TEXT                 -- as notes.archived_at
);
CREATE INDEX IF NOT EXISTS ix_cards_note ON cards(note_id);
CREATE INDEX IF NOT EXISTS ix_cards_archived ON cards(archived_at);

-- Progress. NEVER rebuilt from content.
CREATE TABLE IF NOT EXISTS card_state (
  user_id      INTEGER NOT NULL DEFAULT 1,
  card_id      TEXT NOT NULL,
  algo         TEXT NOT NULL,
  algo_version INTEGER NOT NULL,
  state        TEXT NOT NULL,      -- JSON, private to the backend named in `algo`
  -- Denormalised so the queue and the counters never parse `state`:
  due      TEXT,
  last     TEXT,
  interval INTEGER NOT NULL DEFAULT 0,
  seen     INTEGER NOT NULL DEFAULT 0,
  correct  INTEGER NOT NULL DEFAULT 0,
  wrong    INTEGER NOT NULL DEFAULT 0,
  lapses   INTEGER NOT NULL DEFAULT 0,
  retired_at     TEXT,
  retired_reason TEXT,
  suspended_at   TEXT,
  PRIMARY KEY (user_id, card_id)
);
CREATE INDEX IF NOT EXISTS ix_card_state_sched
  ON card_state(user_id, suspended_at, due);

-- Append-only. The only thing that makes switching or tuning a scheduler
-- possible later, and it cannot be reconstructed after the fact.
CREATE TABLE IF NOT EXISTS review_log (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL DEFAULT 1,
  card_id TEXT NOT NULL,
  rating  INTEGER NOT NULL,          -- 1..4
  review_datetime TEXT NOT NULL,     -- ISO 8601, aware, UTC
  day     TEXT NOT NULL,             -- LOCAL calendar day; counters and streaks use it
  review_duration_ms INTEGER,
  elapsed_days REAL,                 -- actual time since the previous review
  algo    TEXT NOT NULL,
  state_before TEXT,                 -- JSON snapshot, for replay and optimisation
  mode    TEXT NOT NULL DEFAULT 'session',
  form    TEXT NOT NULL DEFAULT 'typein',
  answer  TEXT                       -- including WRONG answers: tomorrow's distractors
);
CREATE INDEX IF NOT EXISTS ix_review_log_day ON review_log(user_id, day);
CREATE INDEX IF NOT EXISTS ix_review_log_card ON review_log(user_id, card_id);

-- Wrong answers offered beside a right one. Part of the content cache: derived
-- from the course files, rebuilt with them, and deterministic so a rebuild does
-- not churn. Precomputed rather than chosen per request because which options a
-- question offers is a property of the material, and one a reviewer can inspect.
CREATE TABLE IF NOT EXISTS distractors (
  card_id TEXT NOT NULL,
  text    TEXT NOT NULL,
  source  TEXT NOT NULL,          -- curated | same_unit | paradigm | frequency | mined
  rank    INTEGER NOT NULL,
  PRIMARY KEY (card_id, text)
);
CREATE INDEX IF NOT EXISTS ix_distractors_card ON distractors(card_id, rank);

-- The tokens the client sees in place of card ids. Persisted rather than minted
-- per run: an answer queued while offline is posted after the connection comes
-- back, and if the server restarted in between, a per-run handle would resolve to
-- nothing and a real answer would be lost. A stable token gives away nothing --
-- it is random, and it says nothing about the material (ADR-0005).
CREATE TABLE IF NOT EXISTS card_handles (
  card_id TEXT PRIMARY KEY,
  handle  TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS ix_card_handles_handle ON card_handles(handle);

-- A learner's claim that an *exercise* is broken, as opposed to hard.
--
-- Neither content nor progress, which is why it is its own table. It cannot live
-- in `notes`/`cards`: those are wiped and rebuilt from the very files the report
-- is complaining about. It must not live in `review_log`: a report is not an
-- answer, and letting it in would corrupt every accuracy figure computed from
-- there -- including the gate that decides how fast new material arrives.
--
-- Append-only in the same spirit as `review_log`. A card reported twice for two
-- reasons is two facts; `resolved_at` closes one without erasing it.
CREATE TABLE IF NOT EXISTS card_reports (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id     INTEGER NOT NULL DEFAULT 1,
  card_id     TEXT NOT NULL,
  reason      TEXT NOT NULL,      -- a code from reports.REASONS, never prose
  note        TEXT,               -- optional free text from the learner
  reported_at TEXT NOT NULL,      -- ISO 8601, aware, UTC
  day         TEXT NOT NULL,      -- LOCAL calendar day, as review_log
  -- The snapshot. Content is rebuilt on every load, so by the time anyone
  -- triages this the text that provoked it may be gone -- and a report that
  -- cannot say what was on screen says only "something was wrong once".
  note_id     TEXT NOT NULL,
  template    TEXT NOT NULL,
  form        TEXT NOT NULL,
  origin      TEXT,               -- the authored file, from Note.origin
  unit        TEXT,
  fields      TEXT NOT NULL,      -- JSON: the note as authored, at report time
  given       TEXT,               -- what the learner last typed, from review_log
  -- Whether THIS report is what took the card out of the queue. Not derivable
  -- afterwards: a card can already be suspended for another reason (the importer
  -- carries suspensions across), and an undo must put back only what it took.
  suspended   INTEGER NOT NULL DEFAULT 0,
  resolved_at TEXT                -- NULL while open
);
CREATE INDEX IF NOT EXISTS ix_card_reports_open
  ON card_reports(user_id, resolved_at, card_id);

-- Per-scope session preferences and cursor, separate from content and progress.
CREATE TABLE IF NOT EXISTS containers (
  user_id   INTEGER NOT NULL DEFAULT 1,
  scope     TEXT NOT NULL,          -- course:<id> | unit:<id> | chapter:<id>
  viewed_at TEXT,
  settings  TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (user_id, scope)
);
"""

#: Additive steps applied in order to a database older than SCHEMA_VERSION.
#: Never rewrite history here -- append.
#:
#: The standing contract, which `_apply_schema` depends on: `SCHEMA` above is
#: always the *cumulative* result of applying every step below to an empty
#: database. Adding a column means editing `SCHEMA` **and** appending here, and
#: bumping SCHEMA_VERSION. Adding a whole table needs only `SCHEMA`, since
#: `CREATE TABLE IF NOT EXISTS` reaches an existing database on the next connect.
#:
#: On an existing database a step runs *before* `SCHEMA`, so it may alter tables
#: that are already there and index columns it has just added -- but it must not
#: assume a table introduced in the same version exists yet.
MIGRATIONS: list[tuple[int, str]] = [
    # ADR-0006: the database owns the material, so a note has to survive an
    # import and say what happened to it.
    (
        2,
        """
        ALTER TABLE notes ADD COLUMN origin TEXT;
        ALTER TABLE notes ADD COLUMN content_hash TEXT;
        ALTER TABLE notes ADD COLUMN created_at TEXT;
        ALTER TABLE notes ADD COLUMN updated_at TEXT;
        ALTER TABLE notes ADD COLUMN edited_at TEXT;
        ALTER TABLE notes ADD COLUMN archived_at TEXT;
        ALTER TABLE cards ADD COLUMN archived_at TEXT;
        CREATE INDEX IF NOT EXISTS ix_notes_archived ON notes(archived_at);
        CREATE INDEX IF NOT EXISTS ix_cards_archived ON cards(archived_at);
        """,
    ),
]


def default_path() -> Path:
    if env := os.environ.get("REPETITA_DB"):
        return Path(env)
    data = os.environ.get("REPETITA_DATA")
    return (Path(data) if data else Path.cwd() / "data") / "repetita.db"


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    """Open a database, creating and migrating it if needed."""
    target = Path(path) if path is not None else default_path()
    if target.parent and str(target) != ":memory:":
        target.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(target))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    _apply_schema(con)
    return con


def _is_fresh(con: sqlite3.Connection) -> bool:
    """
    Is this a database that did not exist until a moment ago?

    The question has to be asked *before* `SCHEMA` runs, and that is the whole
    subtlety. `SCHEMA` creates every table in its current shape, so afterwards a
    brand-new database is indistinguishable from a fully migrated one -- while
    still having no `meta` row, which reads as version 0, which replays every
    step in `MIGRATIONS` against tables that already have the columns those
    steps add.

    The first real `ALTER TABLE ... ADD COLUMN` anyone appends would therefore
    fail with "duplicate column name" on every *newly created* database and on
    no existing one: green on the machine of whoever wrote it, red on a fresh
    clone and in CI.
    """
    row = con.execute(
        "SELECT COUNT(*) AS n FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchone()
    return int(row["n"]) == 0


def _apply_schema(con: sqlite3.Connection) -> None:
    with con:
        if _is_fresh(con):
            # Nothing to replay: `SCHEMA` is the cumulative result of every step,
            # so a database built from it is at SCHEMA_VERSION by construction.
            con.executescript(SCHEMA)
        else:
            # `meta` may predate versioning, or not exist at all.
            con.execute(
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            row = con.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
            current = int(row["value"]) if row else 0
            for version, statement in MIGRATIONS:
                if version > current:
                    con.executescript(statement)
            # After the migrations, not before. `SCHEMA` describes the tables as
            # they are *now*, so it may name a column that only exists once a
            # migration has added it -- an index on a newly added column is the
            # ordinary case, not an exotic one. Running `SCHEMA` first fails with
            # "no such column" on precisely the databases the migration exists
            # for, while passing on every fresh one.
            con.executescript(SCHEMA)
        con.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )


@contextmanager
def session(path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    con = connect(path)
    try:
        yield con
    finally:
        con.close()
