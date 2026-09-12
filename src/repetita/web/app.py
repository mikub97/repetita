"""
Application factory.

Starting reads the database and nothing else. The course, its exercise types and
its material are all rows, indexed once into a `Library` rather than looked up
per request.

This used to import the course files on every start -- `load_course`, then
`sync`, then serve. ADR-0006 had already made the database the owner; what
survived was the habit of proving it against the files each time, which meant a
restart quietly ran the one operation that archives material in bulk and raises
conflicts. ADR-0015 removed it. Material arrives by `repetita import` or through
the app, both of which say what they changed; the only file this module opens is
the one that seeds a database with nothing in it.

The leak rule does not weaken for any of that: `_servable` runs `validate.check`
over whatever the tables hold, however it got there.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from flask import Flask

from ..content.loader import expand_cards, load_course
from ..content.models import Card, Course, Note, NoteType
from ..content.validate import check
from ..store import cards as store_cards
from ..store import db as store_db
from ..store.material import live_notes
from .api import bp
from .handles import Handles

#: A course zip is measured in hundreds of kilobytes. This is generous enough
#: that nobody meets it by accident and small enough that nothing has to be
#: streamed to handle it.
MAX_UPLOAD_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Library:
    """Everything the serialiser needs, indexed once instead of per request."""

    course: Course
    notes: dict[str, Note]
    cards: dict[str, Card]
    notetypes: dict[str, NoteType]
    #: Notes dropped for giving away their own answer. Surfaced as a count so a
    #: broken course is visible in the running app, not only in `validate`.
    quarantined: int
    #: Opaque tokens the client sees in place of card ids, which are authored
    #: from the material and therefore leak answers. See handles.py.
    handles: Handles


def build_library(db_path: Path | str, course_id: str) -> Library:
    """
    Everything the app serves, read from the database and from nothing else.

    Used at startup and again by `POST /api/reload`. One function rather than
    two, because two would eventually disagree about what a load means -- and the
    thing they would disagree about is which material is safe to serve.

    **No file is opened here.** ADR-0006 made the database the owner of the
    material and ADR-0015 finished the job: an import is an operation somebody
    asks for, not a side effect of starting a process. This function used to call
    `load_course` and merge the course files in, which meant every restart
    silently performed the one operation that can archive material in bulk and
    raise conflicts -- against files that may be a branch behind, or edited half
    way through, or gone.

    Seeding an empty database is the single exception and it lives in
    `seed_if_empty`, deliberately outside this function.
    """
    con = store_db.connect(db_path)
    try:
        course = store_cards.course_from_db(con, course_id)
        notetypes = store_cards.notetypes_from_db(con, course_id)
        notes, cards, refused = _servable(con, course_id, notetypes)
        # Handles are read from the same connection and persist, so an answer
        # queued while offline can still be posted after a restart or a reload.
        handles = Handles((c.id for c in cards), con=con)
    finally:
        con.close()

    return Library(
        course=course,
        notes={n.id: n for n in notes},
        cards={c.id: c for c in cards},
        notetypes=notetypes,
        # Material that cannot be practised, whatever made it so: a note that
        # gives away its own answer, or one whose type nothing declares. Counting
        # the two apart would mean two numbers for one idea.
        quarantined=len(refused),
        handles=handles,
    )


def seed_if_absent(db_path: Path | str, course_dir: Path | str) -> str:
    """
    Import `course_dir` if the database has never heard of that course. Its id.

    The one place startup may read YAML, and it is less an exception to the rule
    than the absence of anything for the rule to protect: a course the database
    does not hold has no material to conflict with, nothing to archive and no
    edit to lose. Once it is there it is the truth, and it is never compared
    against the files again -- that comparison is `repetita import`, and it
    belongs to whoever runs it.

    Keyed on *this* course rather than on the database being empty. One database
    may hold several courses, and "empty" would mean the second course anybody
    added never seeded at all.
    """
    course_id = _id_of(Path(course_dir))
    con = store_db.connect(db_path)
    try:
        if course_id in store_cards.courses_in_db(con):
            return course_id
    finally:
        con.close()

    result = load_course(course_dir)
    if result.course is None:
        # Refusing beats seeding half a course and serving an empty queue that
        # looks like "nothing is due today".
        why = "; ".join(str(p) for p in result.problems)
        raise ValueError(f"no usable course at {course_dir}: {why}")

    con = store_db.connect(db_path)
    try:
        store_cards.sync(con, result)
    finally:
        con.close()
    return result.course.id


def _servable(
    con: sqlite3.Connection, course_id: str, notetypes: dict[str, NoteType]
) -> tuple[list[Note], list[Card], set[str]]:
    """
    The material in the database that is safe to put in front of a learner.

    The quarantine is the reason this is not a plain read. `validate.check`
    refuses a note that gives away its own answer -- a hint reading
    `fim de semana = weekend` for the answer `fim de semana` -- and it used to
    run only in the loader, over notes that had just come out of a file. Once
    the database is what gets served, an edit made in the app reaches a learner
    without passing it, and an exercise that teaches nothing is invisible in
    review. So it runs here too, over whatever the database holds and however it
    got there.

    A note whose type the course no longer declares is refused for the same
    reason: there is nothing to expand it into and nothing to grade it with.
    """
    notes: list[Note] = []
    cards: list[Card] = []
    refused: set[str] = set()
    for note in live_notes(con, course_id):
        nt = notetypes.get(note.notetype)
        if nt is None or any(p.fatal for p in check(note, nt)):
            refused.add(note.id)
            continue
        notes.append(note)
        cards.extend(expand_cards(note, nt))
    return notes, cards, refused


def init_app(
    app: Flask,
    course: Path | str,
    *,
    db_path: Path | str | None = None,
    url_prefix: str | None = None,
) -> Flask:
    """
    Mount repetita on an application someone else owns.

    Everything the blueprint needs is read through `current_app`, so a host only
    has to supply it: the database path, the course, and the library. This is the
    same work `create_app` does -- factored out rather than duplicated, because
    two versions would drift and the thing they would drift about is which
    material is served.

    `course` is either a course directory or the id of a course the database
    already holds. A directory is what seeds an empty database and is remembered
    as the default target for `repetita import`; it is read once, on a database
    with nothing in it, and never again.

    A host gets the engine and keeps its own shell: authentication, navigation,
    a launcher. Repetita stays a complete application on its own, and neither
    arrangement is the special case.
    """
    app.config.setdefault("REPETITA_DB", Path(db_path) if db_path else store_db.default_path())
    # The ceiling on an uploaded course, enforced by Flask before a byte reaches
    # any of our code. `setdefault`, because a host that mounts repetita may
    # already have an opinion about what it will accept and this must not raise
    # it -- only supply one where there was none.
    app.config.setdefault("MAX_CONTENT_LENGTH", MAX_UPLOAD_BYTES)
    db = app.config["REPETITA_DB"]

    directory = Path(course) if (Path(course) / "course.yaml").is_file() else None
    if directory is not None:
        app.config["REPETITA_COURSE"] = directory
        course_id = seed_if_absent(db, directory)
    else:
        app.config.setdefault("REPETITA_COURSE", None)
        course_id = str(course)

    # The course served when nothing says otherwise -- a default, not the answer.
    # Which course a request is about is the request's to say (see `api._course`).
    app.config["REPETITA_COURSE_ID"] = course_id
    app.extensions["repetita"] = Shelf(db)
    app.extensions["repetita"].get(course_id)

    app.register_blueprint(bp, url_prefix=url_prefix)
    app.teardown_appcontext(_close_db)
    return app


class Shelf:
    """
    The libraries, one per course, built when first asked for.

    There used to be exactly one `Library` in the process, which is why the app
    could serve exactly one course. Lazily rather than all at once, because
    `en-from-pl` alone is 2415 notes to expand and validate, and somebody who
    only ever studies Italian should not pay for it on every start.

    Not a cache in the sense of something that may be stale: `drop` is called by
    everything that writes material, so a library is either current or absent.
    """

    __slots__ = ("_built", "_db")

    def __init__(self, db_path: Path | str) -> None:
        self._db = db_path
        self._built: dict[str, Library] = {}

    def get(self, course_id: str) -> Library:
        library = self._built.get(course_id)
        if library is None:
            library = build_library(self._db, course_id)
            self._built[course_id] = library
        return library

    def drop(self, course_id: str) -> None:
        """Forget one course, so the next request rebuilds it."""
        self._built.pop(course_id, None)

    def built(self) -> list[str]:
        return sorted(self._built)


def _id_of(course_dir: Path) -> str:
    """
    The id a course directory declares, read without loading the material.

    Needed only to answer "which course did you mean" when the database is
    already seeded, so parsing the notes would be work done to throw away.
    """
    raw = yaml.safe_load((course_dir / "course.yaml").read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not raw.get("id"):
        raise ValueError(f"no course id in {course_dir / 'course.yaml'}")
    return str(raw["id"])


def create_app(
    course: Path | str,
    *,
    db_path: Path | str | None = None,
    config: dict[str, Any] | None = None,
) -> Flask:
    """Repetita as its own application, which is how it runs by default."""
    app = Flask(__name__)
    app.config["REPETITA_DB"] = Path(db_path) if db_path else store_db.default_path()
    app.config.update(config or {})
    return init_app(app, course)


def _close_db(_: BaseException | None) -> None:
    from flask import g

    con: sqlite3.Connection | None = g.pop("repetita_db", None)
    if con is not None:
        con.close()
