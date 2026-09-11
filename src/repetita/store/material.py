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

from ..content.ids import propose as propose_id
from ..content.labels import derive as derive_label
from ..content.loader import expand_cards
from ..content.models import Facets, FieldSpec, Note, NoteType
from ..content.validate import check
from ..core.forms import FORMS, markable

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
        label=row["label"] or "",
        forms={k: tuple(v) for k, v in json.loads(row["forms"] or "{}").items()},
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

KINDS = ("fields", "tags", "unit", "archive", "restore", "label", "remove_set", "set_name")

#: Changes whose target is a unit rather than a note. `pending_changes.note_id`
#: holds the unit id for these -- a pun on the column name, and cheaper than
#: rebuilding the table for two kinds. Everything that reads a change has to
#: know which list it is on, so there is one list.
SET_KINDS = ("remove_set", "set_name")


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
    #: What to call the thing on screen. A `Diff` never joined back to the note,
    #: which is why the drawer used to name changes by id -- a truncated
    #: directory name that says nothing about what moved.
    label: str = ""


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
    elif kind == "label" and (not isinstance(payload, str) or not payload.strip()):
        raise NotEditable("a name cannot be empty")
    elif kind in SET_KINDS:
        # `note_id` is a unit id here. Checked at the door because a staged
        # change to a set that does not exist cannot be seen in the drawer, and
        # what cannot be seen cannot be discarded.
        found = con.execute("SELECT 1 FROM units WHERE id = ?", (note_id,)).fetchone()
        if found is None:
            raise NotEditable(f"no set called {note_id!r}")
        if kind == "set_name":
            if not isinstance(payload, dict):
                raise NotEditable("naming a set takes an object")
            for name in ("title", "description"):
                value = payload.get(name)
                if value is not None and (
                    not isinstance(value, dict)
                    or not all(isinstance(v, str) for v in value.values())
                ):
                    raise NotEditable(f"a set's {name} is a language-to-text mapping")
            if not any(payload.get(k) is not None for k in ("title", "description", "new_id")):
                raise NotEditable("nothing to change about this set")
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
        if change.kind == "remove_set":
            # A set-scoped change: `note_id` is a unit, and what the drawer has
            # to say before you press Confirm is how much goes with it.
            count = con.execute(
                "SELECT count(*) AS n FROM notes WHERE unit = ? AND archived_at IS NULL",
                (change.note_id,),
            ).fetchone()["n"]
            out.append(Diff(change.note_id, change.kind, count, True, change.note_id))
            continue
        if change.kind == "set_name":
            unit = con.execute(
                "SELECT id, title, description FROM units WHERE id = ?", (change.note_id,)
            ).fetchone()
            if unit is None:
                continue
            was = {
                "title": json.loads(unit["title"] or "{}"),
                "description": json.loads(unit["description"] or "{}"),
                "new_id": unit["id"],
            }
            # Only the parts actually being changed, so the drawer does not
            # claim a description was rewritten when only the name was typed.
            becomes = {k: v for k, v in change.payload.items() if v is not None}
            out.append(
                Diff(
                    change.note_id,
                    change.kind,
                    {k: was[k] for k in becomes if k in was},
                    becomes,
                    change.note_id,
                )
            )
            continue
        row = con.execute(
            "SELECT fields, tags, unit, label, archived_at FROM notes WHERE id = ?",
            (change.note_id,),
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
        elif change.kind == "label":
            before = row["label"] or ""
        else:
            before = row["archived_at"] is not None
        after = (
            change.payload
            if change.kind not in ("archive", "restore")
            else (change.kind == "archive")
        )
        out.append(Diff(change.note_id, change.kind, before, after, row["label"] or ""))
    return out


# --- writing --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ApplyReport:
    """What Confirm did, including what it is unhappy about."""

    notes: int = 0
    #: Sets removed. Counted separately because "0 notes updated" is what
    #: Confirm said after removing an empty set -- true, and not what happened.
    sets: int = 0
    #: Sets given a name or a description. Same reason as `sets`: naming a set
    #: touches no note, so the note count alone reports nothing happened.
    named: int = 0
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
    # Every expected card, not only the new ones. The `ON CONFLICT` clause is
    # what brings an existing card back in line with its note -- and it never ran
    # while this only wrote the cards that were missing, so choosing a different
    # form for an exercise that already existed changed the note and left the
    # card asking the old way.
    fresh = [cid for cid in expected if cid not in live]
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
            for c in expected.values()
        ],
    )
    gone = sorted(live - set(expected))
    con.executemany("UPDATE cards SET archived_at = ? WHERE id = ?", [(stamp, cid) for cid in gone])
    return len(fresh), len(gone)


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
    # Read once. The family rule is course configuration and does not change
    # between two notes in the same batch.
    from .cards import facets_from_db

    # Named for what it is: `row` is reused inside the loop below for a note.
    course_row = con.execute("SELECT id FROM courses LIMIT 1").fetchone()
    facets = facets_from_db(con, course_row["id"]) if course_row else Facets()

    added = archived = touched = named = 0
    quarantined: list[str] = []
    relabel: set[str] = set()

    # Set-scoped changes are taken out first: their target is a unit, not a
    # note, so they cannot go through the loop below. `pending_changes.note_id`
    # holds the unit id for these -- a pun on the column name, and cheaper than
    # rebuilding the table for one kind.
    sets = [c for c in changes if c.kind == "remove_set"]
    namings = [c for c in changes if c.kind == "set_name"]
    course_id = str(course_row["id"]) if course_row else ""

    by_note: dict[str, list[Change]] = {}
    for change in changes:
        if change.kind not in SET_KINDS:
            by_note.setdefault(change.note_id, []).append(change)

    with con:
        # Naming before removal: a set staged for both is being renamed on its
        # way out, and `rename_unit` would not find it the other way round.
        for naming in namings:
            _rename_unit(
                con,
                course_id,
                naming.note_id,
                title=naming.payload.get("title"),
                description=naming.payload.get("description"),
                new_id=naming.payload.get("new_id") or None,
            )
            named += 1
        for removal in sets:
            gone_notes, gone_cards = remove_set(con, course_id, removal.note_id, stamp)
            touched += gone_notes
            archived += gone_cards
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
                    # The name is derived from the answer, so a changed answer
                    # changes it -- which is why this sits beside `csum` and the
                    # card re-expansion rather than somewhere it can be missed.
                    if not row["label_custom"]:
                        relabel.add(note_id)
                elif change.kind == "tags":
                    columns["tags"] = json.dumps(list(change.payload), ensure_ascii=False)
                elif change.kind == "unit":
                    columns["unit"] = str(change.payload)
                elif change.kind == "label":
                    # Written by a person, so it is pinned: a later edit to the
                    # exercise recomputes the answer but not the name.
                    columns["label"] = str(change.payload).strip()
                    columns["label_custom"] = 1
                elif change.kind == "archive":
                    columns["archived_at"] = stamp
                elif change.kind == "restore":
                    columns["archived_at"] = None

            _write_note(con, note_id, stamp, **columns)

            note = get_note(con, note_id)
            nt = notetypes.get(row["notetype"])
            if note is not None and note_id in relabel:
                con.execute(
                    "UPDATE notes SET label = ? WHERE id = ?",
                    (derive_label(note, nt, facets), note_id),
                )
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
        notes=touched + len(by_note),
        sets=len(sets),
        named=named,
        cards_added=added,
        cards_archived=archived,
        quarantined=tuple(quarantined),
    )


# --- sets -----------------------------------------------------------------


def remove_set(
    con: sqlite3.Connection,
    course: str,
    unit_id: str,
    stamp: str,
) -> tuple[int, int]:
    """
    Archive a set and everything in it, in one go.

    Archived, never deleted -- the same rule as every other removal here, so
    every schedule behind those exercises stays reachable and a mistake is undone
    by restoring rather than by re-authoring.

    Returns how many notes and cards went with it, so the confirmation can say
    what it actually did rather than "done".
    """
    notes = [
        r["id"]
        for r in con.execute(
            "SELECT id FROM notes WHERE course = ? AND unit = ? AND archived_at IS NULL",
            (course, unit_id),
        )
    ]
    con.executemany(
        "UPDATE notes SET archived_at = ?, edited_at = ?, updated_at = ? WHERE id = ?",
        [(stamp, stamp, stamp, i) for i in notes],
    )
    cards = 0
    if notes:
        marks = ",".join("?" for _ in notes)
        cur = con.execute(
            f"UPDATE cards SET archived_at = ? WHERE archived_at IS NULL AND note_id IN ({marks})",
            (stamp, *notes),
        )
        cards = cur.rowcount
    # `edited_at` on the unit too, or the next import finds the directory still
    # there and puts the set straight back.
    con.execute(
        "UPDATE units SET archived_at = ?, edited_at = ? WHERE course = ? AND id = ?",
        (stamp, stamp, course, unit_id),
    )
    return len(notes), cards


@dataclass(frozen=True, slots=True)
class SaveReport:
    """What Save did, in the words the footer uses."""

    created: int = 0
    updated: int = 0
    archived: int = 0
    cards_added: int = 0
    cards_archived: int = 0
    #: Staged changes dropped because this write superseded them. Reported
    #: rather than done quietly: a Confirm afterwards would otherwise reapply an
    #: older version of an exercise that was just saved.
    superseded: int = 0
    #: Applied, and not servable -- the same convention `apply_pending` uses.
    quarantined: tuple[str, ...] = ()
    #: The id of every row, in the order they were sent, so the client can match
    #: what it has on screen to what now exists without guessing.
    ids: tuple[str, ...] = ()


def row_note(
    row: dict[str, Any],
    note_id: str,
    unit_id: str,
    ord_: int,
    nt: NoteType,
) -> Note:
    """One row of the Create tab as a `Note`, refusing what must not be set."""
    fields = row.get("fields")
    if not isinstance(fields, dict):
        raise NotEditable("an exercise needs its fields as an object")
    for fixed in ("id", "notetype"):
        if fixed in fields:
            raise NotEditable(_why_fixed(fixed))
    unknown = sorted(set(fields) - set(nt.fields))
    if unknown:
        known = ", ".join(sorted(nt.fields))
        raise NotEditable(f"{nt.name} has no field {unknown[0]!r}; it has: {known}")

    tags = row.get("tags") or []
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise NotEditable("tags must be a list of strings")

    lesson = row.get("lesson")
    try:
        when = date.fromisoformat(lesson) if lesson else None
    except (TypeError, ValueError):
        # Refused, not ignored. A dropped lesson date pushes a whole set to the
        # back of the introduction order, which looks exactly like the app
        # ignoring today's lesson -- the loader learned this one the same way.
        raise NotEditable(f"{lesson!r} is not a date; write it as YYYY-MM-DD") from None

    return Note(
        id=note_id,
        notetype=nt.name,
        # Empty values are dropped rather than stored, so clearing a hint in the
        # editor means the same thing as never having written one -- the rule
        # `apply_pending` already applies to an edited field.
        fields={k: _typed(v, nt.fields[k]) for k, v in fields.items() if v not in (None, "", [])},
        tags=tuple(str(t) for t in tags),
        lesson=when,
        unit=unit_id,
        ord=ord_,
        label=str(row.get("label") or "").strip(),
        forms=_checked_forms(row.get("forms"), nt),
    )


def _typed(value: Any, spec: FieldSpec) -> Any:
    """
    A field as its type says it is.

    The loader coerces on the way in from YAML and this is the same job on the
    way in from a browser: a `text_list` that arrived as one string would be
    stored as a string, exported as a string and read back as a list on the next
    import -- a difference that shows up later as a note changing by itself.
    """
    if spec.type == "text_list":
        if isinstance(value, str):
            return [value] if value.strip() else []
        return [str(v) for v in value] if isinstance(value, list) else [str(value)]
    return value if isinstance(value, str) else str(value)


def _checked_forms(raw: Any, nt: NoteType) -> dict[str, tuple[str, ...]]:
    """
    A form preference, refused when it names something that cannot happen.

    Two rules, both of which produce an exercise that looks fine and cannot be
    answered if they are skipped: the template has to exist on this note type,
    and the form has to be one its grader can mark. A flashcard asks the learner
    for a self-rating, so a `typed` grader would score every one of them AGAIN.
    """
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict):
        raise NotEditable("how an exercise is asked has to be given per card")
    out: dict[str, tuple[str, ...]] = {}
    for template, forms in raw.items():
        tpl = nt.cards.get(str(template))
        if tpl is None:
            known = ", ".join(nt.cards)
            raise NotEditable(f"{nt.name} has no card {template!r}; it has: {known}")
        if isinstance(forms, str):
            forms = [forms]
        if not isinstance(forms, list) or not all(isinstance(f, str) for f in forms):
            raise NotEditable("the forms of a card are a list of names")
        chosen = tuple(f for f in forms if f)
        if not chosen:
            continue
        gradeable = markable(tpl.grader)
        for form in chosen:
            if form not in FORMS:
                raise NotEditable(f"there is no {form!r} exercise; there is: {', '.join(FORMS)}")
            if form not in gradeable:
                raise NotEditable(
                    f"{form!r} cannot be marked by the {tpl.grader!r} grader this exercise uses; "
                    f"it can be asked as: {', '.join(gradeable)}"
                )
        out[str(template)] = chosen
    return out


def lang_of(con: sqlite3.Connection, course: str) -> str | None:
    """The course's target language, for the frequency ranking of distractors."""
    row = con.execute("SELECT l2 FROM courses WHERE id = ?", (course,)).fetchone()
    return row["l2"] if row and row["l2"] else None


def save_set(
    con: sqlite3.Connection,
    course: str,
    unit_id: str,
    rows: list[dict[str, Any]],
    notetypes: dict[str, NoteType],
    facets: Facets,
    *,
    title: dict[str, str] | None = None,
    user_id: int = DEFAULT_USER,
) -> SaveReport:
    """
    Write a whole set: exercises made here, edits to ones already here, removals.

    The first path in this package that *creates* material rather than editing
    what an import left -- see ADR-0010. Two columns carry that fact and both are
    load-bearing:

    * `origin` stays empty, which is what tells an import this note was never in
      a file. Without it the next startup archives everything written here.
    * `edited_at` is set, which is what keeps its cards from being recomputed
      from a file that does not mention it.

    All of it in one transaction, for the reason `apply_pending` gives: a
    half-saved set is the state nobody can reason about.
    """
    from .cards import rebuild_distractors, reclassify

    known = set(notetypes)
    for row in rows:
        name = str(row.get("notetype") or "")
        if name not in known:
            raise NotEditable(
                f"there is no {name!r} exercise; there is: {', '.join(sorted(known))}"
            )

    # Before the transaction, deliberately: `create_unit` commits on its own, and
    # nesting two `with con:` blocks would commit half of this one early. A set
    # that exists with nothing in it is not a broken state -- the "+ New set"
    # button has always been able to make one.
    here = con.execute(
        "SELECT archived_at FROM units WHERE course = ? AND id = ?", (course, unit_id)
    ).fetchone()
    if here is None or here["archived_at"]:
        # `create_unit` also un-archives, so saving into a set that was removed
        # brings it back -- which is what pressing Save on it means.
        create_unit(con, course, unit_id, title=title)
    if title is not None:
        rename_unit(con, course, unit_id, title=title)

    stamp = _now()
    # Every id the database has ever held, archived ones included: an archived
    # note still owns the history behind it, and reusing its id would hand that
    # history to a different exercise.
    taken = {r["id"] for r in con.execute("SELECT id FROM notes")}

    created = updated = archived = added = gone = superseded = 0
    quarantined: list[str] = []
    ids: list[str] = []

    with con:
        for position, row in enumerate(rows):
            nt = notetypes[str(row["notetype"])]
            given = str(row.get("id") or "")
            existing = (
                con.execute("SELECT id FROM notes WHERE id = ?", (given,)).fetchone()
                if given
                else None
            )

            if existing is None:
                answers = row.get("fields", {}).get(next(iter(nt.cards.values())).expect) or []
                first = answers[0] if isinstance(answers, list) and answers else str(answers or "")
                note_id = propose_id(unit_id, first, taken)
                taken.add(note_id)
            else:
                note_id = given

            ids.append(note_id)

            # Before the row is read as an exercise: a removal carries an id and
            # nothing else, because there is nothing left to say about it.
            if row.get("archived"):
                if existing is not None:
                    con.execute(
                        "UPDATE notes SET archived_at = ?, edited_at = ?, updated_at = ? "
                        "WHERE id = ? AND archived_at IS NULL",
                        (stamp, stamp, stamp, note_id),
                    )
                    cur = con.execute(
                        "UPDATE cards SET archived_at = ? "
                        "WHERE note_id = ? AND archived_at IS NULL",
                        (stamp, note_id),
                    )
                    gone += cur.rowcount
                    archived += 1
                continue

            note = row_note(row, note_id, unit_id, position, nt)
            label = note.label or derive_label(note, nt, facets)
            forms = json.dumps({k: list(v) for k, v in note.forms.items()}, ensure_ascii=False)

            if existing is None:
                con.execute(
                    "INSERT INTO notes(id,course,unit,notetype,ord,tags,lesson,fields,csum,"
                    "origin,content_hash,created_at,updated_at,edited_at,label,label_custom,forms) "
                    "VALUES(?,?,?,?,?,?,?,?,?,'',NULL,?,?,?,?,?,?)",
                    (
                        note_id,
                        course,
                        unit_id,
                        note.notetype,
                        position,
                        json.dumps(list(note.tags), ensure_ascii=False),
                        note.lesson.isoformat() if note.lesson else None,
                        json.dumps(note.fields, ensure_ascii=False),
                        _checksum(note.fields),
                        stamp,
                        stamp,
                        stamp,
                        label,
                        int(bool(note.label)),
                        forms,
                    ),
                )
                created += 1
            else:
                _write_note(
                    con,
                    note_id,
                    stamp,
                    unit=unit_id,
                    ord=position,
                    tags=json.dumps(list(note.tags), ensure_ascii=False),
                    lesson=note.lesson.isoformat() if note.lesson else None,
                    fields=json.dumps(note.fields, ensure_ascii=False),
                    csum=_checksum(note.fields),
                    label=label,
                    label_custom=int(bool(note.label)),
                    forms=forms,
                    archived_at=None,
                )
                updated += 1

            a, g = _reexpand(con, note, nt, stamp)
            added += a
            gone += g
            if any(p.fatal for p in check(note, nt)):
                quarantined.append(note_id)

            # A staged edit to something just written is a description of a
            # version that no longer exists. Left alone, the next Confirm would
            # quietly put it back.
            cur = con.execute(
                "DELETE FROM pending_changes WHERE user_id = ? AND note_id = ?",
                (user_id, note_id),
            )
            superseded += cur.rowcount

    reclassify(con)
    # An answer that has just been written is a wrong answer for everything else
    # in the course, and everything else is a wrong answer for it. Without this
    # a multiple choice chosen in the editor is accepted and then not served.
    rebuild_distractors(con, notetypes, course, lang=lang_of(con, course))
    return SaveReport(
        created=created,
        updated=updated,
        archived=archived,
        cards_added=added,
        cards_archived=gone,
        superseded=superseded,
        quarantined=tuple(quarantined),
        ids=tuple(ids),
    )


def create_unit(
    con: sqlite3.Connection,
    course: str,
    unit_id: str,
    *,
    title: dict[str, str] | None = None,
    description: dict[str, str] | None = None,
) -> str:
    """
    A set that exists here and in no course file yet.

    `edited_at` is what keeps it: an import archives every unit it cannot find
    in the files, and a set made in the app is in none of them until
    `repetita export` writes one. Third time this rule has been needed, after
    notes and cards.
    """
    unit_id = unit_id.strip()
    if not unit_id:
        raise NotEditable("a set needs a name")
    if any(c in unit_id for c in "/\\"):
        # It becomes a directory name on export, so it has to be one.
        raise NotEditable("a set name cannot contain a slash")
    row = con.execute(
        "SELECT archived_at FROM units WHERE course = ? AND id = ?", (course, unit_id)
    ).fetchone()
    stamp = _now()
    with con:
        if row is None:
            con.execute(
                "INSERT INTO units(course,id,title,description,ord,edited_at) VALUES(?,?,?,?,?,?)",
                (
                    course,
                    unit_id,
                    json.dumps(title or {}, ensure_ascii=False),
                    json.dumps(description or {}, ensure_ascii=False),
                    999,
                    stamp,
                ),
            )
        elif row["archived_at"]:
            con.execute(
                "UPDATE units SET archived_at = NULL, edited_at = ? WHERE course = ? AND id = ?",
                (stamp, course, unit_id),
            )
        else:
            raise NotEditable(f"there is already a set called {unit_id!r}")
    return unit_id


def rename_unit(
    con: sqlite3.Connection,
    course: str,
    unit_id: str,
    *,
    title: dict[str, str] | None = None,
    description: dict[str, str] | None = None,
    new_id: str | None = None,
) -> str:
    """
    Give a set a readable name, a description, and optionally a new id.

    Three different weights of change, which is why they are separate arguments.
    A **title** is a display name and moves nothing; a **description** is prose
    about the shelf and moves less. A **new id** is the directory the set is
    exported to, and every note in it has to follow.

    Renaming the id is safe in a way renaming a note id is not: nothing in
    `card_state` references a unit. `notes.unit` does, and is updated in the same
    transaction, so there is no moment where a note points at a set that is not
    there.
    """
    with con:
        return _rename_unit(
            con, course, unit_id, title=title, description=description, new_id=new_id
        )


def _rename_unit(
    con: sqlite3.Connection,
    course: str,
    unit_id: str,
    *,
    title: dict[str, str] | None = None,
    description: dict[str, str] | None = None,
    new_id: str | None = None,
) -> str:
    """
    `rename_unit` without the transaction.

    Confirm applies a whole drawer of changes in one transaction, and sqlite3's
    `with con:` commits on exit rather than nesting -- so calling the public
    function from inside `apply_pending` would commit half a confirmation. Two
    callers, one body, one place that opens a transaction.
    """
    stamp = _now()
    if title is not None:
        con.execute(
            "UPDATE units SET title = ?, edited_at = ? WHERE course = ? AND id = ?",
            (json.dumps(title, ensure_ascii=False), stamp, course, unit_id),
        )
    if description is not None:
        con.execute(
            "UPDATE units SET description = ?, edited_at = ? WHERE course = ? AND id = ?",
            (json.dumps(description, ensure_ascii=False), stamp, course, unit_id),
        )
    if new_id and new_id != unit_id:
        new_id = new_id.strip()
        if not new_id or any(c in new_id for c in "/\\"):
            raise NotEditable("a set name cannot be empty or contain a slash")
        clash = con.execute(
            "SELECT 1 FROM units WHERE course = ? AND id = ?", (course, new_id)
        ).fetchone()
        if clash:
            raise NotEditable(f"there is already a set called {new_id!r}")
        con.execute(
            "UPDATE units SET id = ?, edited_at = ? WHERE course = ? AND id = ?",
            (new_id, stamp, course, unit_id),
        )
        con.execute(
            "UPDATE notes SET unit = ?, edited_at = ?, updated_at = ? "
            "WHERE course = ? AND unit = ?",
            (new_id, stamp, stamp, course, unit_id),
        )
        return new_id
    return unit_id
