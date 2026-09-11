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

from ..content.models import Course, GradingSpec, LanguageSpec, LicenseSpec, Note, PathStep
from ..importers.emit import _dump, emit_course
from .material import live_notes


def _course(con: sqlite3.Connection, course_id: str) -> Course:
    row = con.execute("SELECT * FROM courses WHERE id = ?", (course_id,)).fetchone()
    if row is None:
        raise LookupError(f"no course {course_id!r} in this database")
    return Course(
        format_version=row["format_version"],
        id=row["id"],
        title=json.loads(row["title"]),
        l2=LanguageSpec(code=row["l2"], variant=row["variant"]),
        l1=LanguageSpec(code=row["l1"]),
        license=LicenseSpec(**json.loads(row["license"])),
        grading=GradingSpec(**json.loads(row["grading"])),
        scheduler=row["scheduler"] or "sm2",
        tag_weights=json.loads(row["tag_weights"]),
        path=[
            PathStep(unit=u["id"], requires=tuple(json.loads(u["requires"])))
            for u in con.execute(
                "SELECT id, requires FROM units WHERE course = ? AND archived_at IS NULL "
                "ORDER BY ord, id",
                (course_id,),
            )
        ],
    )


def _notes(con: sqlite3.Connection, course_id: str) -> list[Note]:
    """
    Live notes only, from the one reader.

    Archived material is deliberately not written back. It has left the course;
    exporting it would put it straight back in on the next import, which would
    make removing a note impossible to express -- and `live_notes` already
    excludes it, for the same reason the serving path does.
    """
    return live_notes(con, course_id)


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


def export_course(con: sqlite3.Connection, course_id: str, dest: Path | str) -> list[Path]:
    """
    Write `course_id` to `dest` as a course directory. Returns the files written.

    Note ids go out verbatim. They are scheduling keys, and an id that changed on
    the way through here would silently orphan the history behind it
    (CLAUDE.md rule 1) -- the one thing this must never do.
    """
    root = Path(dest)
    course = _course(con, course_id)
    written = emit_course(course, _notes(con, course_id), root)

    facets = _facets_payload(con, course_id)
    if facets is not None:
        path = root / "facets.yaml"
        path.write_text(_dump(facets), encoding="utf-8")
        written.append(path)

    for row in con.execute(
        "SELECT * FROM units WHERE course = ? AND archived_at IS NULL ORDER BY ord, id",
        (course_id,),
    ):
        title = json.loads(row["title"])
        if not title and not row["cefr"]:
            continue  # nothing to say about it; a bare directory is enough
        payload: dict[str, Any] = {}
        if title:
            payload["title"] = title
        if row["cefr"]:
            payload["cefr"] = row["cefr"]
        directory = root / "units" / row["id"]
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "unit.yaml"
        path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), "utf-8")
        written.append(path)

    return written
