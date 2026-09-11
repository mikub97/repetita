"""
Writing imported material back out as a course directory.

The database owns `notes` and `cards` as of ADR-0006, so material written there
is no longer discarded by the next startup. This module still exists, and the
reason has changed rather than gone away: a course that lives only in one
person's database is not a course anyone can fork, review or send a pull request
against, and `courses/` is what makes it CC BY-SA content rather than a private
file.

So an importer writes both. The database is where the material is used; the
course directory is how it leaves this machine.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from ..content.models import Course, Note

#: Field order inside a note, so a diff of regenerated content stays readable.
#: Anything not listed keeps its original order after these.
FIELD_ORDER = (
    "prompt",
    "instruction",
    "situation",
    "l2",
    "l1",
    "image",
    "cue",
    "hint",
    "translation",
    "options",
    "answers",
    "target",
    "explain",
    "distractors",
    "example_l2",
    "example_l1",
    "pos",
    "audio",
)


def _ordered(fields: dict[str, Any]) -> dict[str, Any]:
    known = [k for k in FIELD_ORDER if k in fields]
    rest = [k for k in fields if k not in FIELD_ORDER]
    return {k: fields[k] for k in known + rest}


class _Dumper(yaml.SafeDumper):
    """Block style everywhere, so one exercise is one readable diff hunk."""


def _str_representer(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    # Multi-line values as literal blocks; everything else as a plain or quoted
    # scalar, letting SafeDumper decide. It quotes `no`, `yes`, `on`, `off` and
    # anything else that would come back as a non-string, which is the trap this
    # project refuses to coerce its way out of at load time.
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_Dumper.add_representer(str, _str_representer)


def _dump(payload: dict[str, Any]) -> str:
    return yaml.dump(
        payload,
        Dumper=_Dumper,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=100,
    )


def _course_payload(course: Course) -> dict[str, Any]:
    out: dict[str, Any] = {
        "format_version": course.format_version,
        "id": course.id,
        "l2": {"code": course.l2.code},
        "l1": {"code": course.l1.code},
        "license": {"name": course.license.name},
    }
    if course.l2.variant:
        out["l2"]["variant"] = course.l2.variant
    if course.license.link:
        out["license"]["link"] = course.license.link
    if course.title:
        out["title"] = dict(course.title)
    out["grading"] = {
        "fold_accents": course.grading.fold_accents,
        "sentence_slack": course.grading.sentence_slack,
    }
    out["scheduler"] = course.scheduler
    return out


def _note_payload(note: Note, *, omit_notetype: bool, omit_tags: tuple[str, ...]) -> dict[str, Any]:
    entry: dict[str, Any] = {"id": note.id}
    if not omit_notetype:
        entry["notetype"] = note.notetype
    extra = [t for t in note.tags if t not in omit_tags]
    if extra:
        entry["tags"] = extra
    # Only a name somebody wrote. A derived one is re-derived identically on the
    # way back in, so writing it would add a line to every note in the course
    # and put a value into the file that nothing authored.
    if note.label:
        entry["label"] = note.label
    # Always authored -- there is no derived value for this one, so anything
    # here is somebody's decision about how the exercise is asked.
    if note.forms:
        entry["forms"] = {k: list(v) for k, v in note.forms.items()}
    entry.update(_ordered(note.fields))
    return entry


def emit_course(course: Course, notes: list[Note], dest: Path | str) -> list[Path]:
    """
    Write a course directory. Returns the files written.

    One file per unit. Note ids are written verbatim: they are scheduling keys,
    and an id that changes on the way through here silently orphans the history
    that was just imported.
    """
    root = Path(dest)
    (root / "units").mkdir(parents=True, exist_ok=True)
    written = [root / "course.yaml"]
    (root / "course.yaml").write_text(_dump(_course_payload(course)), encoding="utf-8")

    by_unit: dict[str, list[Note]] = defaultdict(list)
    for note in notes:
        by_unit[note.unit or "misc"].append(note)

    for unit, unit_notes in sorted(by_unit.items()):
        unit_notes.sort(key=lambda n: n.ord)
        notes_dir = root / "units" / unit / "notes"
        notes_dir.mkdir(parents=True, exist_ok=True)

        # Hoist what the whole file agrees on, so the per-note entries stay short
        # enough to read. Anything not unanimous stays on each note.
        types = {n.notetype for n in unit_notes}
        one_type = types.pop() if len(types) == 1 else None
        lessons = {n.lesson for n in unit_notes}
        one_lesson = lessons.pop() if len(lessons) == 1 else None
        shared_tags = (
            tuple(sorted(set.intersection(*(set(n.tags) for n in unit_notes))))
            if unit_notes
            else ()
        )

        head: dict[str, Any] = {}
        if one_type:
            head["notetype"] = one_type
        if shared_tags:
            head["tags"] = list(shared_tags)
        if one_lesson:
            head["lesson"] = one_lesson.isoformat()
        head["notes"] = [
            _note_payload(n, omit_notetype=bool(one_type), omit_tags=shared_tags)
            for n in unit_notes
        ]

        path = notes_dir / f"{unit}.yaml"
        path.write_text(_dump(head), encoding="utf-8")
        written.append(path)

    return written
