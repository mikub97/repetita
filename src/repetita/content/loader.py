"""
YAML -> typed content.

The job here is not parsing, it is refusing to serve broken material with a
message that says how to fix it. Every problem carries the file it came from and
the note id, because "invalid YAML" without a location is a bug report the author
cannot act on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .models import Card, Course, Note, NoteType, Problem
from .notetypes import BUILTIN
from .validate import check

#: Keys that mean something to the engine. Everything else in a note entry is a
#: field of its note type.
RESERVED = frozenset({"id", "notetype", "tags", "lesson"})


@dataclass
class LoadResult:
    course: Course | None = None
    notes: list[Note] = field(default_factory=list)
    cards: list[Card] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    notetypes: dict[str, NoteType] = field(default_factory=dict)

    @property
    def fatal(self) -> list[Problem]:
        return [p for p in self.problems if p.fatal]

    @property
    def warnings(self) -> list[Problem]:
        return [p for p in self.problems if not p.fatal]

    @property
    def ok(self) -> bool:
        return self.course is not None and not self.fatal


def _read_yaml(path: Path) -> tuple[Any, Problem | None]:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")), None
    except yaml.YAMLError as e:
        # A colon inside an unquoted scalar is the usual cause and the message
        # from PyYAML is cryptic, so point at the fix.
        return None, Problem(
            origin=path.name,
            note_id=None,
            kind="schema",
            detail=f"invalid YAML: {e}. A colon in an unquoted value will do this "
            f"-- quote the value.",
        )


def _coerce_fields(
    note_id: str, origin: str, raw: dict[str, Any], nt: NoteType
) -> tuple[dict[str, Any], list[Problem]]:
    """Check field names and types. Refuse, never coerce."""
    problems: list[Problem] = []
    fields: dict[str, Any] = {}

    def bad(detail: str, fatal: bool = True) -> None:
        problems.append(
            Problem(origin=origin, note_id=note_id, kind="shape", detail=detail, fatal=fatal)
        )

    for name, value in raw.items():
        spec = nt.fields.get(name)
        if spec is None:
            known = ", ".join(sorted(nt.fields))
            bad(f"unknown field {name!r} for notetype {nt.name!r}; known: {known}")
            continue

        if spec.type == "text_list":
            items = value if isinstance(value, list) else [value]
            # YAML 1.1 reads no/yes/on/off as booleans, and `no` is an ordinary
            # answer in several languages. Coercing False back to text would
            # silently create a note whose correct answer is the string "False".
            nonstr = [v for v in items if not isinstance(v, str)]
            if nonstr:
                v = nonstr[0]
                bad(
                    f"{name!r} contains {v!r}, parsed as {type(v).__name__} rather "
                    f"than text -- quote it in the YAML "
                    f"(no/yes/on/off are booleans to YAML)"
                )
                continue
            fields[name] = [v for v in items if v.strip()]
        elif spec.type == "number":
            fields[name] = value
        else:
            if isinstance(value, bool):
                bad(f"{name!r} is {value!r}, parsed as a boolean -- quote it")
                continue
            fields[name] = value if value is None else str(value)

    for name, spec in nt.fields.items():
        if spec.required and not fields.get(name):
            bad(f"notetype {nt.name!r} requires {name!r}")

    return fields, problems


def expand_cards(note: Note, nt: NoteType) -> list[Card]:
    """
    One note becomes as many cards as its note type has satisfiable templates.

    A template whose `requires` are not all present yields nothing -- that is how
    a note with no audio simply has no listening card, and gains one the day
    audio is built for it, with no edit to the content.
    """
    out: list[Card] = []
    for name, tpl in nt.cards.items():
        if any(not note.fields.get(r) for r in tpl.requires):
            continue
        if not note.fields.get(tpl.expect):
            continue
        out.append(
            Card(
                id=f"{note.id}#{name}",
                note_id=note.id,
                template=name,
                notetype=nt.name,
                grader=tpl.grader,
                forms=tpl.forms,
            )
        )
    return out


def _load_note_file(
    path: Path, unit: str, notetypes: dict[str, NoteType], seen: dict[str, str]
) -> tuple[list[Note], list[Problem]]:
    raw, err = _read_yaml(path)
    if err:
        return [], [err]
    if not isinstance(raw, dict):
        return [], [
            Problem(
                origin=path.name,
                note_id=None,
                kind="schema",
                detail="file must be a mapping with a 'notes:' list",
            )
        ]

    origin = path.name
    problems: list[Problem] = []
    file_notetype = str(raw.get("notetype", "gap"))
    file_tags = tuple(str(t) for t in (raw.get("tags") or []))

    file_lesson: date | None = None
    if (rawlesson := raw.get("lesson")) is not None:
        try:
            file_lesson = date.fromisoformat(str(rawlesson))
        except ValueError:
            # Refused rather than dropped: silently ignoring it pushes the whole
            # file to the back of the introduction order, which looks exactly
            # like "the app is ignoring today's lesson".
            return [], [
                Problem(
                    origin=origin,
                    note_id=None,
                    kind="shape",
                    detail=f"lesson {rawlesson!r} is not a YYYY-MM-DD date",
                )
            ]

    notes: list[Note] = []
    for i, entry in enumerate(raw.get("notes") or []):
        if not isinstance(entry, dict):
            problems.append(
                Problem(
                    origin=origin,
                    note_id=None,
                    kind="shape",
                    detail=f"note #{i + 1} is not a mapping",
                )
            )
            continue

        nid = str(entry.get("id", i + 1))
        ntname = str(entry.get("notetype", file_notetype))
        nt = notetypes.get(ntname)
        if nt is None:
            known = ", ".join(sorted(notetypes))
            problems.append(
                Problem(
                    origin=origin,
                    note_id=nid,
                    kind="shape",
                    detail=f"unknown notetype {ntname!r}; known: {known}",
                )
            )
            continue

        if nid in seen:
            # The id is a scheduling key. Two notes sharing one would share a
            # schedule, so this is refused rather than renamed.
            problems.append(
                Problem(
                    origin=origin,
                    note_id=nid,
                    kind="duplicate",
                    detail=f"id already used in {seen[nid]}",
                )
            )
            continue

        raw_fields = {k: v for k, v in entry.items() if k not in RESERVED}
        fields, probs = _coerce_fields(nid, origin, raw_fields, nt)
        problems.extend(probs)
        if any(p.fatal for p in probs):
            continue

        note = Note(
            id=nid,
            notetype=ntname,
            fields=fields,
            tags=file_tags + tuple(str(t) for t in (entry.get("tags") or [])),
            lesson=file_lesson,
            unit=unit,
            ord=i,
            origin=origin,
        )

        # Quarantine: a note that gives away its own answer never reaches the
        # pool, so it cannot be practised even if something downstream ignores
        # this report. A warning would not be enough -- see validate.py.
        content_problems = check(note, nt)
        problems.extend(content_problems)
        if any(cp.fatal for cp in content_problems):
            continue

        seen[nid] = origin
        notes.append(note)
    return notes, problems


def load_course(course_dir: Path | str) -> LoadResult:
    """Load one course directory. Never raises on bad content -- it reports."""
    root = Path(course_dir)
    result = LoadResult(notetypes=dict(BUILTIN))

    course_file = root / "course.yaml"
    if not course_file.is_file():
        result.problems.append(
            Problem(origin=str(root), note_id=None, kind="schema", detail="no course.yaml here")
        )
        return result

    raw, err = _read_yaml(course_file)
    if err:
        result.problems.append(err)
        return result
    try:
        result.course = Course.model_validate(raw)
    except ValidationError as e:
        for issue in e.errors():
            loc = ".".join(str(p) for p in issue["loc"]) or "course"
            result.problems.append(
                Problem(
                    origin="course.yaml",
                    note_id=None,
                    kind="schema",
                    detail=f"{loc}: {issue['msg']}",
                )
            )
        return result

    seen: dict[str, str] = {}
    units_dir = root / "units"
    for unit_dir in sorted(p for p in units_dir.glob("*") if p.is_dir()):
        files = sorted(unit_dir.glob("notes/*.yaml")) or sorted(unit_dir.glob("*.yaml"))
        for path in files:
            if path.name == "unit.yaml":
                continue
            notes, problems = _load_note_file(path, unit_dir.name, result.notetypes, seen)
            result.notes.extend(notes)
            result.problems.extend(problems)

    for note in result.notes:
        result.cards.extend(expand_cards(note, result.notetypes[note.notetype]))
    return result
