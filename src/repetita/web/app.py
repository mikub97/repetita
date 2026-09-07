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

from ..content.loader import load_course
from ..content.models import Card, Course, Note, NoteType
from ..store import cards as store_cards
from ..store import db as store_db
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


def create_app(
    course_dir: Path | str,
    *,
    db_path: Path | str | None = None,
    config: dict[str, Any] | None = None,
) -> Flask:
    app = Flask(__name__)
    app.config["REPETITA_DB"] = Path(db_path) if db_path else store_db.default_path()
    app.config.update(config or {})

    result = load_course(course_dir)
    if result.course is None:
        # No course.yaml, or one the model refuses. Refusing to start beats
        # serving an empty queue that looks like "nothing is due today".
        why = "; ".join(str(p) for p in result.problems)
        raise ValueError(f"no usable course at {course_dir}: {why}")

    app.extensions["repetita"] = Library(
        course=result.course,
        notes={n.id: n for n in result.notes},
        cards={c.id: c for c in result.cards},
        notetypes=result.notetypes,
        quarantined=len({p.note_id for p in result.fatal if p.note_id}),
        handles=Handles(c.id for c in result.cards),
    )

    # Content is a cache and is rebuilt here on every start. `card_state` is not
    # touched by that, which is what makes fixing a typo in a sentence free.
    con = store_db.connect(app.config["REPETITA_DB"])
    try:
        store_cards.sync(con, result)
    finally:
        con.close()

    app.register_blueprint(bp)
    app.teardown_appcontext(_close_db)
    return app


def _close_db(_: BaseException | None) -> None:
    from flask import g

    con: sqlite3.Connection | None = g.pop("repetita_db", None)
    if con is not None:
        con.close()
