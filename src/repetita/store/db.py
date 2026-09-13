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

Since ADR-0015 nothing reads the course files unless asked, so these tables are
not merely the owner of the material -- they are the only copy of it until
somebody exports. `repetita snapshot` matters more for that reason, not less.

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

SCHEMA_VERSION = 13

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

-- Who is using this. Nine tables have carried `user_id INTEGER NOT NULL
-- DEFAULT 1` since they were written and nothing ever set it to anything else;
-- this is the row that number finally points at.
--
-- Seeded so that `id = 1` is the author, which is why no existing row moves: a
-- database with a year of history in it becomes a database with a year of that
-- person's history, by adding one row here.
CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  name          TEXT NOT NULL UNIQUE,   -- what you type to sign in
  display       TEXT NOT NULL DEFAULT '',
  -- scrypt, via `werkzeug.security`. Never a password, here or in a log.
  password_hash TEXT NOT NULL DEFAULT '',
  -- Reaches the admin page, which can read every table. Separate from being
  -- able to edit your own material, which every account can do.
  is_admin      INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT,
  -- Deactivated rather than deleted: `card_state` and `review_log` reference
  -- this id, and those rows outlive any decision about an account.
  active        INTEGER NOT NULL DEFAULT 1
);

-- ...and the row that number points at, seeded here rather than in Python so
-- that it arrives with the table on both routes into a database -- created from
-- this script, or migrated into it. Only when there is no account at all: on an
-- existing database this turns a year of anonymous history into a year of the
-- owner's history without moving a single row.
--
-- The name is deliberately not a person's. Whose database this is belongs to the
-- deployment, and `repetita user rename owner <you>` is how it says so.
INSERT INTO users (id, name, display, password_hash, is_admin, created_at, active)
SELECT 1, 'owner', '', '', 1, datetime('now'), 1
WHERE NOT EXISTS (SELECT 1 FROM users);

-- Which *sets* somebody studies, within a course they have joined.
--
-- Enrolment above is about the flag picker; this is about the queue. Everyone
-- was being served every set in a course, so Karolina's session drew from
-- Radek's material and Małgosia's -- fine when there was one account and wrong
-- the moment there were four, because a set belongs to the lessons it came from.
--
-- **Empty means everything.** An account with no row here for a course studies
-- all of it, which is what every database that predates this table says and
-- what somebody who has just joined a course wants. Choosing the first set is
-- what turns the filter on -- the same shape as the login appearing with the
-- first password, and enrolment mattering from the first enrolment.
CREATE TABLE IF NOT EXISTS set_enrolments (
  user_id   INTEGER NOT NULL,
  course    TEXT NOT NULL,
  unit      TEXT NOT NULL,
  -- A row per set once anybody has chosen, and the flag says which way. Storing
  -- only the sets somebody studies cannot tell "has never chosen" from "has
  -- chosen none of them": both are no rows, and with one set in a course,
  -- turning it off left no rows, which read as "study everything" and undid the
  -- click. A toggle that silently does nothing is worse than no toggle.
  studying  INTEGER NOT NULL DEFAULT 1,
  joined_at TEXT,
  PRIMARY KEY (user_id, course, unit)
);
CREATE INDEX IF NOT EXISTS ix_set_enrolments_who
  ON set_enrolments(user_id, course);

-- Which courses somebody has signed up for. Absence is not "cannot see it" --
-- material is shared and visible (ADR-0008) -- it is "not on my flag picker".
CREATE TABLE IF NOT EXISTS enrolments (
  user_id   INTEGER NOT NULL,
  course    TEXT NOT NULL,
  joined_at TEXT,
  PRIMARY KEY (user_id, course)
);

-- The course itself, and its units. Owned like the rest of the material.
-- `units.title` and `cefr` come from `unit.yaml`, and `requires`/`ord` from
-- `course.yaml`'s `path` -- three pieces of structure that were authored and
-- parsed from the start and reached no query until they landed here.
CREATE TABLE IF NOT EXISTS courses (
  id             TEXT PRIMARY KEY,
  title          TEXT NOT NULL DEFAULT '{}',   -- JSON, i18n
  l1             TEXT,
  l2             TEXT,
  variant        TEXT,
  license        TEXT NOT NULL DEFAULT '{}',   -- JSON
  grading        TEXT NOT NULL DEFAULT '{}',   -- JSON
  scheduler      TEXT,
  tag_weights    TEXT NOT NULL DEFAULT '{}',   -- JSON
  family         TEXT,                         -- JSON: how several forms of one word are marked
  format_version INTEGER NOT NULL DEFAULT 1,
  imported_at    TEXT
);

CREATE TABLE IF NOT EXISTS units (
  course   TEXT NOT NULL,
  id       TEXT NOT NULL,               -- the directory name; notes.unit joins on it
  title    TEXT NOT NULL DEFAULT '{}',  -- JSON, i18n
  description TEXT NOT NULL DEFAULT '{}', -- JSON, i18n
  cefr     TEXT,
  ord      INTEGER NOT NULL DEFAULT 0,  -- position in course.path
  requires TEXT NOT NULL DEFAULT '[]',  -- JSON array of unit ids
  -- Made or renamed here rather than read out of a directory. Without it an
  -- import archives every unit it does not find in the files -- which is every
  -- unit the app has ever created. Notes learned this the hard way and so did
  -- cards; this is the same rule, written down once.
  edited_at   TEXT,
  archived_at TEXT,
  -- Whose set this is: the account that may change it. Empty means nobody's in
  -- particular, which is what everything imported before accounts existed is.
  -- On the set rather than the note, because a set is the thing a person makes
  -- and manages, and a note already takes its character from the set it is in.
  owner       TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (course, id)
);
CREATE INDEX IF NOT EXISTS ix_units_ord ON units(course, ord);

-- A course's own exercise types (ADR-0012), as declared in `notetypes.yaml`.
-- Built-in types are code and are never stored: `notetypes.builtin()` is the
-- floor and these are layered over it, exactly as the loader layers them.
--
-- Here because the database has to be able to describe a course without the
-- files. This was the last piece of content an import parsed and then threw
-- away, and a note whose type nothing declares cannot be expanded or graded --
-- so without this row a DB-only start would quarantine every note using one.
CREATE TABLE IF NOT EXISTS notetypes (
  course      TEXT NOT NULL,
  name        TEXT NOT NULL,
  spec        TEXT NOT NULL,          -- JSON: the NoteType as declared
  edited_at   TEXT,                   -- written here rather than imported
  archived_at TEXT,                   -- gone from the source. Never deleted
  PRIMARY KEY (course, name)
);

-- How a course reads its own tags. `note_facets` is the join table that makes
-- GROUP BY possible: `notes.tags` is a JSON array in a TEXT column and cannot be
-- indexed, joined or grouped, which is why it was written by every sync and read
-- by no query at all.
CREATE TABLE IF NOT EXISTS facet_axes (
  course       TEXT NOT NULL,
  axis         TEXT NOT NULL,          -- level | track | topic | source | ...
  title        TEXT NOT NULL DEFAULT '{}',
  ordered      INTEGER NOT NULL DEFAULT 0,
  catch_all    INTEGER NOT NULL DEFAULT 0,
  max_per_note INTEGER,
  ord          INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (course, axis)
);

CREATE TABLE IF NOT EXISTS facet_values (
  course TEXT NOT NULL,
  axis   TEXT NOT NULL,
  value  TEXT NOT NULL,
  title  TEXT NOT NULL DEFAULT '{}',
  ord    INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (course, axis, value)
);

-- A note may sit under several values of one axis. That is the ordinary case,
-- not an edge one: material about ordering food in a market is genuinely both
-- `comida` and `cidade`, and forcing a choice loses real information.
CREATE TABLE IF NOT EXISTS note_facets (
  note_id TEXT NOT NULL,
  axis    TEXT NOT NULL,
  value   TEXT NOT NULL,
  PRIMARY KEY (note_id, axis, value)
);
CREATE INDEX IF NOT EXISTS ix_note_facets_lookup ON note_facets(axis, value, note_id);

-- Material. Owned, merged and archived -- not a cache (ADR-0006).
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
  archived_at  TEXT,               -- gone from the source. NEVER deleted: see ADR-0006
  -- A short name, so three screens can refer to one exercise without falling
  -- back to its id. Derived from the answer; see `content/labels.py` for why
  -- not from the cue. A *name*, not an identifier -- fifteen exercises in one
  -- set legitimately answer `o`, and the id is what tells them apart.
  label        TEXT,
  -- Set when a person writes the name themselves, so editing the exercise does
  -- not quietly overwrite a name someone chose.
  label_custom INTEGER NOT NULL DEFAULT 0,
  -- How this exercise is asked, when its author disagreed with its type.
  -- JSON `{"<template>": ["typein", ...]}`; NULL means "whatever the note type
  -- says", which is the case for everything that came out of a file. Per
  -- template because a form is a property of a card and one note can have
  -- several -- `vocab` has three. See ADR-0010.
  forms        TEXT
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
  -- Which stage of learning this card is at, denormalised so material can be
  -- grouped by it in SQL. Written from `core.buckets.bucket_of`, which is the
  -- single definition -- a threshold that decides policy must not get a second
  -- one in a WHERE clause (ADR-0002).
  bucket   TEXT,
  PRIMARY KEY (user_id, card_id)
);
CREATE INDEX IF NOT EXISTS ix_card_state_sched
  ON card_state(user_id, suspended_at, due);
CREATE INDEX IF NOT EXISTS ix_card_state_bucket ON card_state(user_id, bucket);

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
  answer  TEXT,                      -- including WRONG answers: tomorrow's distractors
  -- Which revision of which study plan produced this answer. ADR-0003 exists
  -- because the predecessor kept aggregates and threw the sequence away, and
  -- that is the one decision that cannot be undone later. "Did making it harder
  -- help?" is the same shape of question, so this is recorded from day one.
  plan_revision_id INTEGER,
  -- The same, for an answer given on the Study tab, where there is no plan.
  -- Two columns rather than one: an answer from Study is not evidence about a
  -- plan, and filing it under whichever plan happened to be active would make
  -- every later comparison wrong (ADR-0007). At most one is ever set.
  style_revision_id INTEGER
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
  -- The snapshot. The exercise can be edited, archived or reworded between the
  -- report and the triage, so by the time anyone reads this the text that
  -- provoked it may be gone -- and a report that cannot say what was on screen
  -- says only "something was wrong once". (The reasoning used to be "content is
  -- rebuilt on every load", which stopped being true at ADR-0006; the column
  -- earns its place either way, for a better reason.)
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

-- Renamed tags keep resolving. A tag carries no scheduling state, so unlike an
-- item id -- which is renamed with `repetita rename-id`, so that the history
-- comes too -- it can simply be renamed. But plans and facets.yaml refer to a
-- tag by value, so the old name has to keep meaning something.
CREATE TABLE IF NOT EXISTS tag_aliases (
  course     TEXT NOT NULL,
  old        TEXT NOT NULL,
  new        TEXT NOT NULL,
  renamed_at TEXT NOT NULL,
  PRIMARY KEY (course, old)
);

-- "This grouping is wrong." An observation about how material is *organised*,
-- as opposed to `card_reports`, which says one exercise is broken and suspends
-- it. This suspends nothing. Same reasoning that keeps reports out of
-- `review_log`: a report is not an answer, and an issue is not a report.
CREATE TABLE IF NOT EXISTS material_issues (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id     INTEGER NOT NULL DEFAULT 1,
  -- Which course this is about. Empty means "not about any one course", and
  -- such an issue is shown under every course rather than hidden under none.
  course      TEXT NOT NULL DEFAULT '',
  kind        TEXT NOT NULL,      -- taxonomy | coverage | balance | duplicate | other
  body        TEXT NOT NULL,      -- the learner's own words
  selector    TEXT,               -- what they were looking at, e.g. "topic=tempo"
  raised_at   TEXT NOT NULL,
  resolved_at TEXT,
  resolution  TEXT
);
CREATE INDEX IF NOT EXISTS ix_material_issues_open
  ON material_issues(user_id, resolved_at);

-- Intent: what the learner wants studied, as opposed to what they have studied.
-- Never rebuilt from content.
CREATE TABLE IF NOT EXISTS study_plans (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id    INTEGER NOT NULL DEFAULT 1,
  name       TEXT NOT NULL,
  course     TEXT NOT NULL,
  active     INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_study_plans_active ON study_plans(user_id, active);

-- The draggable list. `weight` NULL means derive it from `rank`.
CREATE TABLE IF NOT EXISTS plan_priorities (
  plan_id INTEGER NOT NULL,
  rank    INTEGER NOT NULL,
  axis    TEXT NOT NULL,          -- topic | track | level | unit | notetype
  value   TEXT NOT NULL,
  weight  REAL,
  PRIMARY KEY (plan_id, axis, value)
);

-- One row per knob so a change is diffable rather than a rewritten blob.
CREATE TABLE IF NOT EXISTS plan_knobs (
  plan_id INTEGER NOT NULL,
  key     TEXT NOT NULL,
  value   TEXT NOT NULL,          -- JSON scalar
  PRIMARY KEY (plan_id, key)
);

-- Append-only. What the plan looked like when a session was built under it.
CREATE TABLE IF NOT EXISTS plan_revisions (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  plan_id    INTEGER NOT NULL,
  changed_at TEXT NOT NULL,
  snapshot   TEXT NOT NULL        -- JSON: priorities + knobs at this moment
);
CREATE INDEX IF NOT EXISTS ix_plan_revisions_plan ON plan_revisions(plan_id, id);

-- How one person wants their own queue built, in one course. "Jak sie ucze".
--
-- Progress-side data, like a study plan: never rebuilt from content, never
-- derived from anything, and the only record of a preference. Distinct from a
-- plan, and deliberately a separate table rather than a flag on one (ADR-0017):
-- a plan is an *additional* path through the material and is asked for per
-- request, while this configures the one path everybody already has. Merging
-- them is what ADR-0007 reverted once already.
--
-- Absence is the default, not a missing row to be repaired: `styles.get`
-- answers with `styles.DEFAULT`, so a database that predates this table behaves
-- exactly as it did.
CREATE TABLE IF NOT EXISTS study_styles (
  user_id       INTEGER NOT NULL DEFAULT 1,
  course        TEXT NOT NULL,
  mode          TEXT NOT NULL DEFAULT 'kurs',      -- the named preset it came from
  introductions TEXT NOT NULL DEFAULT 'lesson',    -- policies/ordering.ORDERINGS
  intro_axis    TEXT NOT NULL DEFAULT '',          -- an axis with ordered = 1
  debt          TEXT NOT NULL DEFAULT 'overdue',   -- policies/ordering.DEBT_ORDERINGS
  plan_id       INTEGER,                           -- only when an ordering says 'plan'
  knobs         TEXT NOT NULL DEFAULT '{}',        -- JSON, validated against plans.KNOBS
  updated_at    TEXT,
  PRIMARY KEY (user_id, course)
);

-- Append-only, exactly as `plan_revisions` and for exactly the same reason.
-- ADR-0003 applied a third time: an answer has to say which settings produced
-- it, recorded from the first day rather than added once somebody wants the
-- answer, because a column added later leaves every earlier answer
-- unattributable. The Study tab is where nearly every answer is given, so this
-- is the copy that matters most.
CREATE TABLE IF NOT EXISTS style_revisions (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id    INTEGER NOT NULL DEFAULT 1,
  course     TEXT NOT NULL,
  changed_at TEXT NOT NULL,
  snapshot   TEXT NOT NULL        -- JSON: the whole style at this moment
);
CREATE INDEX IF NOT EXISTS ix_style_revisions_who ON style_revisions(user_id, course, id);

-- Edits made in the app and not yet applied.
--
-- Server-side rather than held in the page, so that a refresh, a second tab or
-- a crash does not lose work, and so Confirm can show what will actually change
-- rather than a count. One row per (note, kind): the newest statement of an
-- intention replaces the previous one, because two edits to the same field are
-- not two changes, they are one change made twice.
CREATE TABLE IF NOT EXISTS pending_changes (
  user_id    INTEGER NOT NULL DEFAULT 1,
  note_id    TEXT NOT NULL,
  kind       TEXT NOT NULL,      -- fields | tags | unit | archive | restore
  payload    TEXT NOT NULL,      -- JSON: the proposed value
  created_at TEXT NOT NULL,
  PRIMARY KEY (user_id, note_id, kind)
);
CREATE INDEX IF NOT EXISTS ix_pending_changes_note ON pending_changes(user_id, note_id);

-- Material captured before it has been shaped into exercises.
--
-- Raw text, on purpose, which is the opposite of everything else in this schema
-- (ADR-0009). A lesson is written down in one state of mind and turned into
-- exercises in another, and making the first wait for the second loses the note.
-- Nothing here is studied, counted or validated until an agent has shaped it and
-- a person has confirmed the result.
--
-- Kept after processing rather than deleted: the note is the provenance of the
-- exercises that came out of it, and the thing to re-read when one is wrong.
CREATE TABLE IF NOT EXISTS material_drafts (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id      INTEGER NOT NULL DEFAULT 1,
  -- The course it was captured under. A draft is raw text and which course it
  -- becomes exercises in is decided later, so this is a hint, not a claim --
  -- empty shows everywhere.
  course       TEXT NOT NULL DEFAULT '',
  body         TEXT NOT NULL,      -- exactly what was pasted, never reformatted
  created_at   TEXT NOT NULL,
  processed_at TEXT,
  outcome      TEXT                -- what was made from it, written by whoever did
);
CREATE INDEX IF NOT EXISTS ix_material_drafts_open
  ON material_drafts(user_id, processed_at);

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
    # Grouping by learning stage, and recording which plan produced an answer.
    # Only columns on tables that already exist need a step; the new tables in
    # `SCHEMA` reach an existing database on their own.
    (
        3,
        """
        ALTER TABLE card_state ADD COLUMN bucket TEXT;
        ALTER TABLE review_log ADD COLUMN plan_revision_id INTEGER;
        CREATE INDEX IF NOT EXISTS ix_card_state_bucket ON card_state(user_id, bucket);
        """,
    ),
    # A unit can now be made or renamed in the app, so it has to be able to say
    # so -- otherwise the next import archives every set the app ever created.
    (4, "ALTER TABLE units ADD COLUMN edited_at TEXT;"),
    # The family rule is course configuration and belongs with the rest of it,
    # so the database can reconstruct a course's facets without the files.
    (5, "ALTER TABLE courses ADD COLUMN family TEXT;"),
    # A short name per exercise. Backfilled in Python rather than here: the rule
    # reads note types and answer fields, which SQL cannot.
    (
        6,
        """
        ALTER TABLE notes ADD COLUMN label TEXT;
        ALTER TABLE notes ADD COLUMN label_custom INTEGER NOT NULL DEFAULT 0;
        """,
    ),
    # How an exercise is asked, where its author disagreed with its type. NULL
    # everywhere until someone says otherwise, so there is nothing to backfill.
    (7, "ALTER TABLE notes ADD COLUMN forms TEXT;"),
    # A set is a shelf with a name on it, and the name has no room for what the
    # shelf is for. Empty everywhere until someone writes one (ADR-0013).
    (8, "ALTER TABLE units ADD COLUMN description TEXT NOT NULL DEFAULT '{}';"),
    # Which course a draft or an issue is about. Empty on everything that
    # already exists, which reads as "not about any one course" and shows under
    # all of them -- the honest answer for rows recorded before anyone could say.
    (
        10,
        """
        ALTER TABLE material_drafts ADD COLUMN course TEXT NOT NULL DEFAULT '';
        ALTER TABLE material_issues ADD COLUMN course TEXT NOT NULL DEFAULT '';
        """,
    ),
    # 11 adds `users` and `enrolments`, and `units.owner`. The two tables reach
    # an existing database through `SCHEMA` on their own; the column does not.
    (11, "ALTER TABLE units ADD COLUMN owner TEXT NOT NULL DEFAULT '';"),
    # 12 adds `set_enrolments` and needs no step for the same reason: `SCHEMA`
    # creates it with IF NOT EXISTS on both paths. Recorded so the version
    # moving is an answer rather than a question -- and it has to move, because
    # an older build opening this database must not stamp it back down.
    # 9 adds the `notetypes` table and needs no step: `SCHEMA` creates it with
    # IF NOT EXISTS on both paths, so it reaches an existing database on its
    # own. Recorded here so the gap in the numbering is an answer rather than a
    # question -- the version still moves, because the shape did.
    #
    # 13 adds `study_styles` and `style_revisions`, which reach an existing
    # database through `SCHEMA` -- but the column below does not.
    #
    # A second column beside `plan_revision_id` rather than reusing it. ADR-0007:
    # "an answer from the Study tab is not evidence about any plan, and filing it
    # under the active one would make every later comparison wrong". Overloading
    # the column is that same failure arriving by a different door. At most one
    # of the two is ever non-NULL, and a test pins it.
    #
    # Nothing in `card_state` is read or written by this step. `review_log` is
    # append-only and never rewritten, so adding a nullable column to it cannot
    # disturb a schedule -- which is the question CLAUDE.md says to answer
    # before writing anything under `store/`, not after.
    (13, "ALTER TABLE review_log ADD COLUMN style_revision_id INTEGER;"),
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
        # Never *lower* the recorded version. An older build opening a newer
        # database would otherwise stamp it back down, and the next time the
        # newer build ran it would replay a migration against tables that
        # already have the columns -- "duplicate column name", on a database
        # that was fine until something read it. Switching branches over one
        # study database is an ordinary thing to do here, so this is a real
        # path rather than a theoretical one.
        con.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = "
            "  CASE WHEN CAST(excluded.value AS INTEGER) > CAST(meta.value AS INTEGER) "
            "       THEN excluded.value ELSE meta.value END",
            (str(SCHEMA_VERSION),),
        )


@contextmanager
def session(path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    con = connect(path)
    try:
        yield con
    finally:
        con.close()
