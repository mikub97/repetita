"""
Giving a course a different id, and taking its material with it.

The sibling of `rename.py`, and the easier half of the problem. A *note* id is a
scheduling key — `card_state` and `review_log` are keyed on a card id built from
it — so moving one means moving history. A **course** id is not: no row in
`cards`, `card_state` or `review_log` mentions a course at all. They reach one
by joining through `notes.course`, which is the column this moves.

So the history does not move, is not rewritten, and is not at risk. That is
worth stating rather than assuming, because "rename" reads like the dangerous
operation next door and this one is not it — every schedule and every answer
still points at the same card id afterwards.

What moves is the material and the configuration: ten columns across nine
tables, in one transaction.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

#: A course id is a directory name, a flag-picker key and part of a URL query.
#: Same shape rule as a note id, for the same reason.
_BAD = re.compile(r"[\s#/\\]")

#: Every table that names a course, and the column that does it. Listed rather
#: than discovered, so a table added later without a line here fails a test
#: instead of silently keeping the old id.
TABLES: tuple[tuple[str, str], ...] = (
    ("courses", "id"),
    ("notes", "course"),
    ("units", "course"),
    ("notetypes", "course"),
    ("facet_axes", "course"),
    ("facet_values", "course"),
    ("tag_aliases", "course"),
    ("study_plans", "course"),
    ("material_drafts", "course"),
    ("material_issues", "course"),
)


class CannotRename(ValueError):
    """Said out loud, because every refusal here has a specific reason."""


@dataclass(frozen=True, slots=True)
class MovedCourse:
    old: str
    new: str
    rows: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.rows.values())


def rename_course(con: sqlite3.Connection, old: str, new: str) -> MovedCourse:
    """
    Move every row naming `old` to `new`, or none of them.

    Refuses a target that already exists. Merging two courses is a different
    operation with different questions — which facet axes win, what happens to
    two units of the same name — and doing it by accident under this name would
    answer them all badly.
    """
    old, new = old.strip(), new.strip()
    if not old or not new:
        raise CannotRename("both the old and the new course id are needed")
    if old == new:
        raise CannotRename(f"{old!r} is already its own id")
    if _BAD.search(new):
        raise CannotRename(f"{new!r} cannot be a course id: no spaces, '#', '/' or '\\\\'")
    if not con.execute("SELECT 1 FROM courses WHERE id = ?", (old,)).fetchone():
        raise CannotRename(f"no course {old!r} in this database")
    if con.execute("SELECT 1 FROM courses WHERE id = ?", (new,)).fetchone():
        raise CannotRename(
            f"{new!r} is already a course here. Renaming onto it would merge two "
            f"courses, which is not what this does."
        )

    rows: dict[str, int] = {}
    with con:
        for table, column in TABLES:
            cur = con.execute(f"UPDATE {table} SET {column} = ? WHERE {column} = ?", (new, old))
            if cur.rowcount:
                rows[table] = cur.rowcount
    return MovedCourse(old, new, rows)


def history_of(con: sqlite3.Connection, course: str) -> tuple[int, int]:
    """
    How much schedule and how many answers hang off a course, by the join.

    Only ever read — this is what a rename prints before and after so that
    "nothing moved" is a number somebody can check rather than a promise.
    """
    in_course = (
        "card_id IN (SELECT c.id FROM cards c JOIN notes n ON n.id = c.note_id WHERE n.course = ?)"
    )
    states = con.execute(
        f"SELECT COUNT(*) AS n FROM card_state WHERE {in_course}", (course,)
    ).fetchone()["n"]
    answers = con.execute(
        f"SELECT COUNT(*) AS n FROM review_log WHERE {in_course}", (course,)
    ).fetchone()["n"]
    return int(states), int(answers)


def drop_course(
    con: sqlite3.Connection, course: str, *, with_history: bool = False
) -> dict[str, int]:
    """
    Remove a course from a database entirely: its material and its configuration.

    There was no way to do this. `purge` removes notes by id, by set or by age,
    which leaves the `courses` row and its facet axes behind — a course with no
    exercises, still in the flag picker. Deleting a course is a real thing to
    want (a demo you never studied; a course that moved to another id) and it
    needed a name.

    **Refuses while any of it has been studied**, unless `with_history` says so.
    The rows in `card_state` and `review_log` do not name a course and so are not
    deleted here — they would be left pointing at cards whose notes are gone,
    which is survivable (there are no foreign keys, by design) and is still not
    something to do by accident. `repetita purge --with-history` is the operation
    that deletes those, and it asks twice.
    """
    states, answers = history_of(con, course)
    if (states or answers) and not with_history:
        raise CannotRename(
            f"{course!r} has {states} schedule(s) and {answers} answer(s) behind it. "
            f"Removing the material would leave them pointing at nothing. Use "
            f"`--with-history` if that is really what you want."
        )

    gone: dict[str, int] = {}
    with con:
        for table in ("cards",):
            cur = con.execute(
                f"DELETE FROM {table} WHERE note_id IN (SELECT id FROM notes WHERE course = ?)",
                (course,),
            )
            if cur.rowcount:
                gone[table] = cur.rowcount
        for table in ("distractors",):
            cur = con.execute(f"DELETE FROM {table} WHERE card_id NOT IN (SELECT id FROM cards)")
            if cur.rowcount:
                gone[table] = cur.rowcount
        cur = con.execute(
            "DELETE FROM note_facets WHERE note_id IN (SELECT id FROM notes WHERE course = ?)",
            (course,),
        )
        if cur.rowcount:
            gone["note_facets"] = cur.rowcount
        for table, column in TABLES:
            cur = con.execute(f"DELETE FROM {table} WHERE {column} = ?", (course,))
            if cur.rowcount:
                gone[table] = cur.rowcount
    return gone
