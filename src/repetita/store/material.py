"""
Reading and changing the material the database owns.

ADR-0006 made the database the owner of `notes` and `cards`. This is the module
that acts like it: `live_notes` is where served material comes from, and the
write functions are the only way anything other than an import changes it.

Every write here carries four obligations, and each one is silent if forgotten:

* **`edited_at` is set.** `sync()` reads it to decide whether an incoming file
  change is an update or a conflict. An edit that does not set it is silently
  overwritten by the next import.
* **`content_hash` is left alone.** It records what the *file* said at the last
  import. Recomputing it here would make an edit look like an import and disarm
  conflict detection entirely.
* **Cards are re-expanded.** A note type's `requires` means adding `audio` to a
  `vocab` note brings `#listen` into existence; removing it takes that card out
  of circulation -- by archiving, never by deleting, because the scheduling
  history behind it is not recoverable from anything.
* **`reclassify` runs.** A tag change that leaves `note_facets` describing the
  old tags is worse than no change: every count would agree with itself and
  disagree with the material.

Two things are refused outright. A note `id` cannot change -- it is a scheduling
key, and renaming one silently deletes a learner's progress on that item
(CLAUDE.md rule 1). `repetita check-ids` only diffs committed YAML in a pull
request, so it would notice long after the damage. And a `notetype` cannot
change, because it decides which cards exist.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from ..content.loader import expand_cards
from ..content.models import Note, NoteType
from ..content.validate import check

DEFAULT_USER = 1

#: What a note is allowed to carry besides its fields. `id` and `notetype` are
#: deliberately absent: see the module docstring.
EDITABLE = frozenset({"fields", "tags", "unit", "lesson"})


class NotEditable(ValueError):
    """A change that must not be made, named so the refusal can be shown."""


def _row_to_note(row: sqlite3.Row) -> Note:
    lesson = row["lesson"]
    return Note(
        id=row["id"],
        notetype=row["notetype"],
        fields=json.loads(row["fields"]),
        tags=tuple(json.loads(row["tags"])),
        lesson=date.fromisoformat(lesson) if lesson else None,
        unit=row["unit"],
        ord=row["ord"],
        origin=row["origin"] or "",
    )


def live_notes(con: sqlite3.Connection, course_id: str | None = None) -> list[Note]:
    """
    Every note still in the course, in content order.

    The single reader. `export` writes these out and `build_library` serves
    them, so an edit cannot be visible in one and not the other -- which is
    exactly the state the app was in before this existed: the database owned the
    material and the serving path still read the files.
    """
    sql = "SELECT * FROM notes WHERE archived_at IS NULL"
    params: tuple[object, ...] = ()
    if course_id is not None:
        sql += " AND course = ?"
        params = (course_id,)
    sql += " ORDER BY unit, ord, id"
    return [_row_to_note(r) for r in con.execute(sql, params)]


def get_note(con: sqlite3.Connection, note_id: str) -> Note | None:
    row = con.execute(
        "SELECT * FROM notes WHERE id = ? AND archived_at IS NULL", (note_id,)
    ).fetchone()
    return _row_to_note(row) if row else None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _checksum(fields: dict[str, Any]) -> int:
    """The duplicate-hunting checksum, from the one place that defines it."""
    from .cards import _csum

    return _csum(fields)


# --- staging --------------------------------------------------------------
#
# An edit is recorded as an intention and applied later, all at once. The
# alternative -- writing straight through on every keystroke -- would mean a
# half-finished rename is what a learner studies, and would leave no moment at
# which to show what is about to change.

KINDS = ("fields", "tags", "unit", "archive", "restore")


@dataclass(frozen=True, slots=True)
class Change:
    note_id: str
    kind: str
    payload: Any
    created_at: str


@dataclass(frozen=True, slots=True)
class Diff:
    """One note's before and after, for the screen that asks you to confirm."""

    note_id: str
    kind: str
    before: Any
    after: Any


def stage(
    con: sqlite3.Connection,
    note_id: str,
    kind: str,
    payload: Any,
    *,
    user_id: int = DEFAULT_USER,
) -> Change:
    """
    Record an intended change. Replaces any previous one of the same kind.

    Two edits to the same field are not two changes; they are one change made
    twice, and keeping both would make the diff a history rather than a
    statement of what will happen.
    """
    if kind not in KINDS:
        raise NotEditable(f"unknown change {kind!r}; expected one of {', '.join(KINDS)}")

    # The shape is checked here, at the only door in, because a malformed change
    # that reaches the table cannot be got rid of from the screen: `pending` and
    # `confirm` both fail on it, and Discard lives inside the drawer that
    # `pending` can no longer draw. One bad request and the tab is bricked.
    if kind == "fields":
        if not isinstance(payload, dict):
            raise NotEditable("a field change must be an object of field names to values")
        for name in ("id", "notetype"):
            if name in payload:
                raise NotEditable(_why_fixed(name))
    elif kind == "tags":
        if not isinstance(payload, list) or not all(isinstance(t, str) for t in payload):
            raise NotEditable("tags must be a list of strings")
    elif kind == "unit" and (not isinstance(payload, str) or not payload.strip()):
        raise NotEditable("a note has to move to a named set")
    stamp = _now()
    body = json.dumps(payload, ensure_ascii=False)
    with con:
        con.execute(
            "INSERT INTO pending_changes(user_id,note_id,kind,payload,created_at) "
            "VALUES(?,?,?,?,?) ON CONFLICT(user_id,note_id,kind) DO UPDATE SET "
            "payload=excluded.payload, created_at=excluded.created_at",
            (user_id, note_id, kind, body, stamp),
        )
    return Change(note_id, kind, payload, stamp)


def _why_fixed(name: str) -> str:
    if name == "id":
        return (
            "a note id cannot change: it is the key a learner's progress is stored "
            "under, and renaming one deletes that progress with nothing in the app "
            "to show for it"
        )
    return (
        "a note type cannot change: it decides which cards exist, and dropping one "
        "leaves the history behind it unreachable"
    )


def pending(con: sqlite3.Connection, *, user_id: int = DEFAULT_USER) -> list[Change]:
    return [
        Change(r["note_id"], r["kind"], json.loads(r["payload"]), r["created_at"])
        for r in con.execute(
            "SELECT * FROM pending_changes WHERE user_id = ? ORDER BY created_at, note_id",
            (user_id,),
        )
    ]


def discard(
    con: sqlite3.Connection,
    note_id: str | None = None,
    kind: str | None = None,
    *,
    user_id: int = DEFAULT_USER,
) -> int:
    sql = "DELETE FROM pending_changes WHERE user_id = ?"
    params: list[object] = [user_id]
    if note_id:
        sql += " AND note_id = ?"
        params.append(note_id)
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    with con:
        cur = con.execute(sql, params)
    return cur.rowcount


def diff(con: sqlite3.Connection, *, user_id: int = DEFAULT_USER) -> list[Diff]:
    """What Confirm would actually do, read against the material as it stands."""
    out: list[Diff] = []
    for change in pending(con, user_id=user_id):
        row = con.execute(
            "SELECT fields, tags, unit, archived_at FROM notes WHERE id = ?", (change.note_id,)
        ).fetchone()
        if row is None:
            continue
        before: Any
        if change.kind == "fields":
            current = json.loads(row["fields"])
            before = {k: current.get(k) for k in change.payload}
        elif change.kind == "tags":
            before = json.loads(row["tags"])
        elif change.kind == "unit":
            before = row["unit"]
        else:
            before = row["archived_at"] is not None
        after = (
            change.payload
            if change.kind not in ("archive", "restore")
            else (change.kind == "archive")
        )
        out.append(Diff(change.note_id, change.kind, before, after))
    return out


# --- writing --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ApplyReport:
    """What Confirm did, including what it is unhappy about."""

    notes: int = 0
    cards_added: int = 0
    cards_archived: int = 0
    #: Notes that will not be served because of what the edit did to them --
    #: almost always an answer showing in a field the learner sees while the
    #: question is open. Applied anyway: the quarantine means they cannot reach
    #: a learner, and refusing would strand a half-finished edit with nowhere to
    #: live. Reported loudly instead, because a note that silently stops
    #: appearing is the worst of the three outcomes.
    quarantined: tuple[str, ...] = ()


def _write_note(
    con: sqlite3.Connection,
    note_id: str,
    stamp: str,
    **columns: object,
) -> None:
    """
    Update a note and mark it as edited here.

    `edited_at` is the hinge, and `content_hash` is deliberately untouched: it
    records what the *file* said at the last import, so leaving it alone is what
    lets `sync()` tell an edit from an import and raise a conflict instead of
    silently overwriting one with the other.
    """
    assignments = ", ".join(f"{name} = ?" for name in columns)
    con.execute(
        f"UPDATE notes SET {assignments}, edited_at = ?, updated_at = ? WHERE id = ?",
        (*columns.values(), stamp, stamp, note_id),
    )


def _reexpand(con: sqlite3.Connection, note: Note, nt: NoteType, stamp: str) -> tuple[int, int]:
    """
    Bring a note's cards back in line with its fields.

    A note type's `requires` makes this real rather than bookkeeping: giving a
    `vocab` note an `audio` field brings `#listen` into existence, and taking it
    away takes that card out of circulation. Out is by **archiving**. A card
    carries a learner's history and a `DELETE` here would make it unreachable,
    which is the one thing this schema has never done -- it is why there are no
    foreign keys either.
    """
    expected = {c.id: c for c in expand_cards(note, nt)}
    live = {
        r["id"]
        for r in con.execute(
            "SELECT id FROM cards WHERE note_id = ? AND archived_at IS NULL", (note.id,)
        )
    }
    added = [c for cid, c in expected.items() if cid not in live]
    con.executemany(
        "INSERT INTO cards(id,note_id,template,notetype,grader,forms,scheduled,archived_at) "
        "VALUES(?,?,?,?,?,?,1,NULL) "
        "ON CONFLICT(id) DO UPDATE SET grader=excluded.grader, forms=excluded.forms, "
        "notetype=excluded.notetype, archived_at=NULL",
        [
            (
                c.id,
                c.note_id,
                c.template,
                c.notetype,
                c.grader,
                json.dumps(list(c.forms), ensure_ascii=False),
            )
            for c in added
        ],
    )
    gone = sorted(live - set(expected))
    con.executemany("UPDATE cards SET archived_at = ? WHERE id = ?", [(stamp, cid) for cid in gone])
    return len(added), len(gone)


def apply_pending(
    con: sqlite3.Connection,
    notetypes: dict[str, NoteType],
    *,
    user_id: int = DEFAULT_USER,
) -> ApplyReport:
    """
    Apply every staged change, in one transaction, or none of them.

    All-or-nothing because a half-applied batch is the state nobody can reason
    about: the learner sees material that matches neither what they had nor what
    they asked for, and the pending list no longer describes the difference.
    """
    changes = pending(con, user_id=user_id)
    if not changes:
        return ApplyReport()

    stamp = _now()
    by_note: dict[str, list[Change]] = {}
    for change in changes:
        by_note.setdefault(change.note_id, []).append(change)

    added = archived = 0
    quarantined: list[str] = []
    with con:
        for note_id, note_changes in by_note.items():
            row = con.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
            if row is None:
                continue
            fields = json.loads(row["fields"])
            columns: dict[str, object] = {}

            for change in note_changes:
                if change.kind == "fields":
                    # `None` removes a field rather than storing a null, so
                    # clearing `hint` in the editor means the same thing as
                    # never having written one.
                    fields = {
                        k: v
                        for k, v in {**fields, **change.payload}.items()
                        if v not in (None, "", [])
                    }
                    columns["fields"] = json.dumps(fields, ensure_ascii=False)
                    columns["csum"] = _checksum(fields)
                elif change.kind == "tags":
                    columns["tags"] = json.dumps(list(change.payload), ensure_ascii=False)
                elif change.kind == "unit":
                    columns["unit"] = str(change.payload)
                elif change.kind == "archive":
                    columns["archived_at"] = stamp
                elif change.kind == "restore":
                    columns["archived_at"] = None

            _write_note(con, note_id, stamp, **columns)

            note = get_note(con, note_id)
            nt = notetypes.get(row["notetype"])
            if note is not None and nt is not None:
                a, g = _reexpand(con, note, nt, stamp)
                added += a
                archived += g
                if any(p.fatal for p in check(note, nt)):
                    quarantined.append(note_id)
            elif note is None:
                # The note was archived, so its cards go with it -- archived,
                # not deleted, so every schedule behind them stays reachable.
                cur = con.execute(
                    "UPDATE cards SET archived_at = ? WHERE note_id = ? AND archived_at IS NULL",
                    (stamp, note_id),
                )
                archived += cur.rowcount

        con.execute("DELETE FROM pending_changes WHERE user_id = ?", (user_id,))

    # After the transaction: `note_facets` describes tags that may have just
    # changed, and a card's bucket may have just come into existence.
    from .cards import reclassify

    reclassify(con)
    return ApplyReport(
        notes=len(by_note),
        cards_added=added,
        cards_archived=archived,
        quarantined=tuple(quarantined),
    )
