"""
Writing the material back out as a course directory.

The database owns the material (ADR-0006), which makes this not optional. The
README promises that "adding a lesson is a pull request a non-programmer can
make", and once notes and tags can be changed in the app that promise depends
entirely on being able to write them back to `courses/`. It is also what makes a
tag change reviewable: a normal diff on normal files, in a normal pull request,
rather than a row that changed in someone's SQLite file.

Note emission is `importers.emit.emit_course`, unchanged -- the same writer the
hub import already used, so exported material and imported material come out in
one format rather than two that drift.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import yaml

from ..content.models import Note
from ..importers.emit import _dump, emit_course
from .cards import course_from_db
from .material import live_notes


def _notes(con: sqlite3.Connection, course_id: str) -> list[Note]:
    """
    Live notes only, from the one reader.

    Archived material is deliberately not written back. It has left the course;
    exporting it would put it straight back in on the next import, which would
    make removing a note impossible to express -- and `live_notes` already
    excludes it, for the same reason the serving path does.
    """
    notes = live_notes(con, course_id)
    # A derived name is not content: it comes back identical from the rule on
    # the next import, so it has no business in a file a person reads or
    # reviews. A name somebody typed is content, and goes out.
    typed = {
        r["id"]
        for r in con.execute(
            "SELECT id FROM notes WHERE course = ? AND label_custom = 1", (course_id,)
        )
    }
    return [n if n.id in typed else n.model_copy(update={"label": ""}) for n in notes]


def _facets_payload(con: sqlite3.Connection, course_id: str) -> dict[str, Any] | None:
    axes: dict[str, Any] = {}
    values: dict[str, list[str]] = {}
    for row in con.execute(
        "SELECT axis, value FROM facet_values WHERE course = ? ORDER BY axis, ord", (course_id,)
    ):
        values.setdefault(row["axis"], []).append(row["value"])

    for row in con.execute("SELECT * FROM facet_axes WHERE course = ? ORDER BY ord", (course_id,)):
        spec: dict[str, Any] = {}
        title = json.loads(row["title"])
        if title:
            spec["title"] = title
        if values.get(row["axis"]):
            spec["values"] = values[row["axis"]]
        if row["ordered"]:
            spec["ordered"] = True
        if row["catch_all"]:
            spec["catch_all"] = True
        if row["max_per_note"] is not None:
            spec["max_per_note"] = row["max_per_note"]
        axes[row["axis"]] = spec

    aliases = {
        r["old"]: r["new"]
        for r in con.execute(
            "SELECT old, new FROM tag_aliases WHERE course = ? ORDER BY old", (course_id,)
        )
    }
    if not axes and not aliases:
        return None
    payload: dict[str, Any] = {}
    if axes:
        payload["axes"] = axes
    if aliases:
        payload["aliases"] = aliases
    return payload


def _notetypes_payload(con: sqlite3.Connection, course_id: str) -> dict[str, Any]:
    """
    A course's own exercise types, in the shape `notetypes.yaml` declares them.

    Only its own: the built-in six are code, and writing them into a course file
    would mean a course could disagree with the engine about what a `vocab` note
    is -- and the file would win on the next import.

    This was missing, and it was a hole in the round trip rather than an
    omission: a course that declares a type could be exported and re-imported
    into a database that then quarantined every note using it, for the
    perfectly correct reason that nothing declared the type any more.
    """
    out: dict[str, Any] = {}
    for row in con.execute(
        "SELECT name, spec FROM notetypes WHERE course = ? AND archived_at IS NULL ORDER BY name",
        (course_id,),
    ):
        spec = json.loads(row["spec"])
        # `name` is the key, so repeating it inside is noise in a file a person
        # reads, and `declared()` supplies it from the key on the way back in.
        spec.pop("name", None)
        out[row["name"]] = spec
    return out


def export_course(con: sqlite3.Connection, course_id: str, dest: Path | str) -> list[Path]:
    """
    Write `course_id` to `dest` as a course directory. Returns the files written.

    Note ids go out verbatim. They are scheduling keys, and an id that changed on
    the way through here would silently orphan the history behind it
    (CLAUDE.md rule 1) -- the one thing this must never do.
    """
    root = Path(dest)
    course = course_from_db(con, course_id)
    written = emit_course(course, _notes(con, course_id), root)

    facets = _facets_payload(con, course_id)
    if facets is not None:
        path = root / "facets.yaml"
        path.write_text(_dump(facets), encoding="utf-8")
        written.append(path)

    types = _notetypes_payload(con, course_id)
    if types:
        path = root / "notetypes.yaml"
        path.write_text(_dump(types), encoding="utf-8")
        written.append(path)

    for row in con.execute(
        "SELECT * FROM units WHERE course = ? AND archived_at IS NULL ORDER BY ord, id",
        (course_id,),
    ):
        title = json.loads(row["title"])
        description = json.loads(row["description"] or "{}")
        if not title and not description and not row["cefr"]:
            continue  # nothing to say about it; a bare directory is enough
        payload: dict[str, Any] = {}
        if title:
            payload["title"] = title
        if description:
            payload["description"] = description
        if row["cefr"]:
            payload["cefr"] = row["cefr"]
        directory = root / "units" / row["id"]
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "unit.yaml"
        path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), "utf-8")
        written.append(path)

    return written
