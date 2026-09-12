"""
Changing how material is filed.

Tags are the primary way material is grouped, so they have to be editable
deliberately rather than by hand-editing every note file. Since ADR-0006 the
database owns the material, so a tag change is a database write -- and
`repetita export` is what turns it back into a reviewable diff.

**Renaming a tag is safe, and that is worth stating.** CLAUDE.md rule 1 makes
everyone rightly afraid to rename anything under `courses/`: an item id is a
scheduling key, and renaming one silently deletes a learner's progress. A tag is
the exception. Nothing is keyed on it -- no `card_state` row, no review history.
What a rename does touch is `facets.yaml` and any plan that prioritises the old
value, so it leaves a `tag_aliases` row behind and those keep resolving.

Every operation marks the notes it touched as `edited_at`. That is not
bookkeeping: it is what stops the next import from silently overwriting the
change, and what makes a genuine clash surface as a conflict instead.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from .cards import reclassify
from .catalogue import note_ids_for


@dataclass(frozen=True, slots=True)
class TagChange:
    """What an operation did, or would do under `dry_run`."""

    verb: str
    detail: str
    note_ids: tuple[str, ...]

    @property
    def notes(self) -> int:
        return len(self.note_ids)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _tags_of(con: sqlite3.Connection, note_id: str) -> list[str]:
    row = con.execute("SELECT tags FROM notes WHERE id = ?", (note_id,)).fetchone()
    return list(json.loads(row["tags"])) if row else []


def _write(con: sqlite3.Connection, note_id: str, tags: list[str], stamp: str) -> None:
    con.execute(
        "UPDATE notes SET tags = ?, edited_at = ?, updated_at = ? WHERE id = ?",
        (json.dumps(tags, ensure_ascii=False), stamp, stamp, note_id),
    )


def _apply(
    con: sqlite3.Connection,
    note_ids: list[str],
    change: Callable[[list[str]], list[str] | None],
    *,
    dry_run: bool,
) -> tuple[str, ...]:
    """Run `change(tags) -> tags | None` over notes; None means nothing to do."""
    stamp = _now()
    touched: list[str] = []
    for note_id in note_ids:
        before = _tags_of(con, note_id)
        after = change(list(before))
        if after is None or after == before:
            continue
        touched.append(note_id)
        if not dry_run:
            _write(con, note_id, after, stamp)
    if touched and not dry_run:
        con.commit()
        # Re-file immediately. A tag change that leaves `note_facets` describing
        # the old tags is worse than no change: every count would agree with
        # itself and disagree with the material.
        reclassify(con)
    return tuple(touched)


def _live_notes(
    con: sqlite3.Connection, where: dict[str, list[str]] | None, course: str | None = None
) -> list[str]:
    """
    The notes an operation may touch, in one course when one is named.

    The course argument is not decoration. `rename` accepted a course and passed
    it only to the `tag_aliases` row it wrote, so renaming `comida` while looking
    at Italian also renamed it in Portuguese and Spanish -- the same shape of
    bug as the Design tab showing another course's topics (#74), in a function
    that writes rather than reads.
    """
    if where:
        ids = note_ids_for(con, where)
        if not course:
            return ids
        keep = {r["id"] for r in con.execute("SELECT id FROM notes WHERE course = ?", (course,))}
        return [n for n in ids if n in keep]
    sql = "SELECT id FROM notes WHERE archived_at IS NULL"
    params: tuple[object, ...] = ()
    if course:
        sql += " AND course = ?"
        params = (course,)
    return [r["id"] for r in con.execute(sql, params)]


def add(
    con: sqlite3.Connection,
    tag: str,
    *,
    where: dict[str, list[str]] | None = None,
    course: str | None = None,
    dry_run: bool = False,
) -> TagChange:
    notes = _live_notes(con, where, course)
    touched = _apply(con, notes, lambda t: [*t, tag] if tag not in t else None, dry_run=dry_run)
    return TagChange("add", tag, touched)


def remove(
    con: sqlite3.Connection,
    tag: str,
    *,
    where: dict[str, list[str]] | None = None,
    course: str | None = None,
    dry_run: bool = False,
) -> TagChange:
    notes = _live_notes(con, where, course)
    touched = _apply(
        con, notes, lambda t: [x for x in t if x != tag] if tag in t else None, dry_run=dry_run
    )
    return TagChange("remove", tag, touched)


def rename(
    con: sqlite3.Connection,
    old: str,
    new: str,
    *,
    course: str | None = None,
    dry_run: bool = False,
) -> TagChange:
    """
    Rename a tag everywhere, and leave a trail.

    Course-wide by design: a tag renamed on half the material is two tags, which
    is the situation this exists to fix rather than to create. *One* course,
    though -- `course=None` still means every course in the database, which is
    what the CLI does when nobody says otherwise.
    """
    if old == new:
        raise ValueError("old and new are the same tag")

    # Which axes this tag actually occupies, read before the notes change. A
    # plan priority is an (axis, value) pair, and this rewrite matched on the
    # value alone: renaming the tag `vocab` would also rewrite a priority whose
    # axis is `notetype` and whose value happens to be `vocab`, silently
    # re-pointing a plan at material nobody asked for.
    axes = _axes_of(con, old, course)

    def swap(tags: list[str]) -> list[str] | None:
        if old not in tags:
            return None
        out: list[str] = []
        for t in tags:
            t = new if t == old else t
            if t not in out:
                out.append(t)
        return out

    touched = _apply(con, _live_notes(con, None, course), swap, dry_run=dry_run)
    if touched and not dry_run:
        row = con.execute("SELECT id FROM courses LIMIT 1").fetchone()
        with con:
            con.execute(
                "INSERT INTO tag_aliases(course, old, new, renamed_at) VALUES(?,?,?,?) "
                "ON CONFLICT(course, old) DO UPDATE SET new=excluded.new, "
                "renamed_at=excluded.renamed_at",
                (course or (row["id"] if row else ""), old, new, _now()),
            )
            # A plan that prioritised the old value keeps working. Rewriting the
            # row rather than relying on the alias means the plan reads as what
            # the learner would now type.
            #
            # Every plan, deliberately -- including other people's. The material
            # changed for everybody, so a priority naming the old tag now names
            # nothing, and leaving it would break a plan rather than protect it.
            # This is the one write in this package that crosses accounts on
            # purpose, and it is a repair rather than an edit: the axis, the
            # rank, the weight and the plan are all untouched. Scoped to the
            # axes the tag was on and to the plans of this course, which is what
            # it should have been scoped by all along.
            _repoint_plans(con, old, new, axes, course)
    return TagChange("rename", f"{old} -> {new}", touched)


def _axes_of(con: sqlite3.Connection, tag: str, course: str | None) -> tuple[str, ...]:
    """Which facet axes a tag sits on, within a course if one is named."""
    sql = (
        "SELECT DISTINCT f.axis AS axis FROM note_facets f "
        "JOIN notes n ON n.id = f.note_id WHERE f.value = ?"
    )
    params: tuple[object, ...] = (tag,)
    if course:
        sql += " AND n.course = ?"
        params = (tag, course)
    return tuple(r["axis"] for r in con.execute(sql, params))


def _repoint_plans(
    con: sqlite3.Connection,
    old: str,
    new: str,
    axes: tuple[str, ...],
    course: str | None,
) -> None:
    """Move every priority that named the old tag, and nothing else."""
    if not axes:
        # A tag on no declared axis cannot be prioritised: `membership_of` only
        # ever yields facet axes plus unit, notetype and template. Nothing to do
        # is the right answer, not "match on the value and hope".
        return
    marks = ",".join("?" for _ in axes)
    sql = f"UPDATE plan_priorities SET value = ? WHERE value = ? AND axis IN ({marks})"
    params: tuple[object, ...] = (new, old, *axes)
    if course:
        sql += " AND plan_id IN (SELECT id FROM study_plans WHERE course = ?)"
        params = (*params, course)
    con.execute(sql, params)


def merge(
    con: sqlite3.Connection,
    sources: list[str],
    into: str,
    *,
    course: str | None = None,
    dry_run: bool = False,
) -> TagChange:
    """Fold several tags into one. A rename with more than one source."""
    touched: list[str] = []
    for src in sources:
        if src == into:
            continue
        touched.extend(rename(con, src, into, course=course, dry_run=dry_run).note_ids)
    return TagChange("merge", f"{', '.join(sources)} -> {into}", tuple(dict.fromkeys(touched)))


def split(
    con: sqlite3.Connection,
    tag: str,
    new: str,
    *,
    where: dict[str, list[str]],
    course: str | None = None,
    dry_run: bool = False,
) -> TagChange:
    """
    Give part of a tag's material a tag of its own.

    `where` is required and deliberately has no default. A split with no
    selector is a rename, and the two should not be reachable by the same
    command with a typo between them.
    """
    if not where:
        raise ValueError("split needs a selector saying which notes move")
    notes = [n for n in _live_notes(con, where, course) if tag in _tags_of(con, n)]

    def swap(tags: list[str]) -> list[str] | None:
        if tag not in tags:
            return None
        return [new if t == tag else t for t in tags]

    touched = _apply(con, notes, swap, dry_run=dry_run)
    return TagChange("split", f"{tag} -> {new}", touched)


def inventory(con: sqlite3.Connection, course: str | None = None) -> list[tuple[str, int]]:
    """Every tag in use, most-used first. What `--dry-run` is checked against."""
    counts: dict[str, int] = {}
    sql = "SELECT tags FROM notes WHERE archived_at IS NULL"
    params: tuple[object, ...] = ()
    if course:
        sql += " AND course = ?"
        params = (course,)
    for row in con.execute(sql, params):
        for tag in json.loads(row["tags"]):
            counts[tag] = counts.get(tag, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def aliases(con: sqlite3.Connection, course: str | None = None) -> dict[str, str]:
    sql = "SELECT old, new FROM tag_aliases"
    params: tuple[object, ...] = ()
    if course:
        sql += " WHERE course = ?"
        params = (course,)
    return {r["old"]: r["new"] for r in con.execute(sql, params)}
