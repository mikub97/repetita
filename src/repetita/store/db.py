"""
SQLite persistence.

The one structural decision worth knowing: content and progress are separate,
and only content is rebuilt. `notes` and `cards` are a cache re-derived from the
course files on every load; `card_state` is never touched by that rebuild. So
fixing a typo in a sentence costs nothing. An earlier design keyed tracking on
the prompt text itself, which meant editing a sentence silently orphaned months
of history.

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

SCHEMA_VERSION = 1

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
  csum     INTEGER NOT NULL        -- checksum of the first field, for duplicate hunting
);
CREATE INDEX IF NOT EXISTS ix_notes_csum ON notes(csum);
CREATE INDEX IF NOT EXISTS ix_notes_lesson ON notes(lesson);

CREATE TABLE IF NOT EXISTS cards (
  id        TEXT PRIMARY KEY,      -- <note_id>#<template>
  note_id   TEXT NOT NULL,
  template  TEXT NOT NULL,
  notetype  TEXT NOT NULL,
  grader    TEXT NOT NULL,
  forms     TEXT NOT NULL,         -- JSON array
  scheduled INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_cards_note ON cards(note_id);

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
MIGRATIONS: list[tuple[int, str]] = []


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


def _apply_schema(con: sqlite3.Connection) -> None:
    with con:
        con.executescript(SCHEMA)
        row = con.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        current = int(row["value"]) if row else 0
        for version, statement in MIGRATIONS:
            if version > current:
                con.executescript(statement)
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
