"""
Application factory.

The course is loaded once, at startup, and kept in memory as typed objects; the
database gets the same content synced into it so the queue can be built in SQL.
Those are two representations of one load, never two loads -- the content
package's rule 5 is that the CLI and the running app must not be able to
disagree about what is safe to serve.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from flask import Flask

from ..content.loader import expand_cards, load_course
from ..content.models import Card, Course, Note, NoteType
from ..content.validate import check
from ..store import cards as store_cards
from ..store import db as store_db
from ..store.material import live_notes
from .api import bp
from .handles import Handles


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


def build_library(course_dir: Path | str, db_path: Path | str) -> Library:
    """
    Import the course files, then serve what the database holds.

    Used at startup and again by `POST /api/reload`. One function rather than
    two, because two would eventually disagree about what a load means -- and the
    thing they would disagree about is which material is safe to serve.

    The order matters and is the whole point. Files are how material gets *in*;
    the database is what the learner studies. ADR-0006 made the database the
    owner and this function did not follow: it merged the files in and then
    served `result.notes` -- the file version -- so anything edited in the app
    was invisible to the session until it had been exported and reloaded.
    """
    result = load_course(course_dir)
    if result.course is None:
        # No course.yaml, or one the model refuses. Refusing beats serving an
        # empty queue that looks like "nothing is due today".
        why = "; ".join(str(p) for p in result.problems)
        raise ValueError(f"no usable course at {course_dir}: {why}")

    # The database owns the material (ADR-0006), so this merges the course files
    # into it rather than rebuilding: a note edited here survives the import, and
    # one that has left the files is archived rather than deleted. `card_state`
    # is untouched either way, which is what makes fixing a typo free. Handles
    # are read from the same connection and persist, so an answer queued while
    # offline can still be posted after a restart or a reload.
    con = store_db.connect(db_path)
    try:
        store_cards.sync(con, result)
        notes, cards, refused = _servable(con, result.course.id, result.notetypes)
        handles = Handles((c.id for c in cards), con=con)
    finally:
        con.close()

    return Library(
        course=result.course,
        notes={n.id: n for n in notes},
        cards={c.id: c for c in cards},
        notetypes=result.notetypes,
        # Notes refused by the loader *or* by the check below. Both are the same
        # failure -- material that cannot be practised -- and counting them apart
        # would mean two numbers for one idea.
        quarantined=len({p.note_id for p in result.fatal if p.note_id} | refused),
        handles=handles,
    )


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
    course_dir: Path | str,
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

    A host gets the engine and keeps its own shell: authentication, navigation,
    a launcher. Repetita stays a complete application on its own, and neither
    arrangement is the special case.
    """
    app.config.setdefault("REPETITA_DB", Path(db_path) if db_path else store_db.default_path())
    app.config["REPETITA_COURSE"] = Path(course_dir)
    app.extensions["repetita"] = build_library(course_dir, app.config["REPETITA_DB"])

    app.register_blueprint(bp, url_prefix=url_prefix)
    app.teardown_appcontext(_close_db)
    return app


def create_app(
    course_dir: Path | str,
    *,
    db_path: Path | str | None = None,
    config: dict[str, Any] | None = None,
) -> Flask:
    """Repetita as its own application, which is how it runs by default."""
    app = Flask(__name__)
    app.config["REPETITA_DB"] = Path(db_path) if db_path else store_db.default_path()
    app.config.update(config or {})
    return init_app(app, course_dir)


def _close_db(_: BaseException | None) -> None:
    from flask import g

    con: sqlite3.Connection | None = g.pop("repetita_db", None)
    if con is not None:
        con.close()
