"""
Material captured before it has been shaped.

Everything else in this package refuses malformed input: the loader quarantines
a note that gives away its answer, the validator refuses a field the note type
does not declare, and `sync` will not import a course it cannot parse. This
module does the opposite on purpose, and ADR-0009 records why.

A lesson is written down in one state of mind -- half-sentences, arrows, a word
you did not catch -- and turned into exercises in another. Making the first wait
for the second means the note is never taken, or is taken somewhere the app
cannot see. So text arrives here exactly as typed, and an agent shapes it later,
when asked.

What a draft is **not**: not material, not studied, not counted in any total, not
validated, and never silently converted. It becomes exercises only when someone
asks an agent to do it and then confirms the result.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

DEFAULT_USER = 1


@dataclass(frozen=True, slots=True)
class Draft:
    id: int
    body: str
    created_at: str
    processed_at: str | None = None
    outcome: str | None = None

    @property
    def queued(self) -> bool:
        return self.processed_at is None

    @property
    def summary(self) -> str:
        """The first line, for a listing. Never a reformatting of the body."""
        first = next((ln.strip() for ln in self.body.splitlines() if ln.strip()), "")
        return first if len(first) <= 60 else first[:59] + "…"


def _row(r: sqlite3.Row) -> Draft:
    return Draft(int(r["id"]), r["body"], r["created_at"], r["processed_at"], r["outcome"])


def capture(
    con: sqlite3.Connection,
    body: str,
    *,
    user_id: int = DEFAULT_USER,
    at: datetime | None = None,
) -> Draft:
    """
    Queue what was typed, unchanged.

    The only validation is that there is something there. Anything stricter
    would be this module deciding what a lesson note may look like, which is the
    judgement it exists to defer.
    """
    if not body.strip():
        raise ValueError("nothing to capture")
    stamp = (at or datetime.now(UTC)).isoformat()
    with con:
        cur = con.execute(
            "INSERT INTO material_drafts(user_id, body, created_at) VALUES(?,?,?)",
            (user_id, body, stamp),
        )
    return Draft(int(cur.lastrowid or 0), body, stamp)


def queued(con: sqlite3.Connection, *, user_id: int = DEFAULT_USER) -> list[Draft]:
    return [
        _row(r)
        for r in con.execute(
            "SELECT * FROM material_drafts WHERE user_id = ? AND processed_at IS NULL ORDER BY id",
            (user_id,),
        )
    ]


def get(con: sqlite3.Connection, draft_id: int, *, user_id: int = DEFAULT_USER) -> Draft | None:
    row = con.execute(
        "SELECT * FROM material_drafts WHERE id = ? AND user_id = ?", (draft_id, user_id)
    ).fetchone()
    return _row(row) if row else None


def all_drafts(con: sqlite3.Connection, *, user_id: int = DEFAULT_USER) -> list[Draft]:
    return [
        _row(r)
        for r in con.execute(
            "SELECT * FROM material_drafts WHERE user_id = ? ORDER BY id DESC", (user_id,)
        )
    ]


def done(
    con: sqlite3.Connection,
    draft_id: int,
    *,
    outcome: str | None = None,
    user_id: int = DEFAULT_USER,
    at: datetime | None = None,
) -> Draft | None:
    """
    Close a draft, recording what came of it.

    The body is kept. It is the provenance of whatever exercises were written
    from it, and the thing to re-read when one of them turns out wrong -- which
    is exactly when deleting it would hurt.
    """
    stamp = (at or datetime.now(UTC)).isoformat()
    with con:
        cur = con.execute(
            "UPDATE material_drafts SET processed_at = ?, outcome = ? "
            "WHERE id = ? AND user_id = ? AND processed_at IS NULL",
            (stamp, outcome, draft_id, user_id),
        )
    return get(con, draft_id, user_id=user_id) if cur.rowcount else None


def discard(con: sqlite3.Connection, draft_id: int, *, user_id: int = DEFAULT_USER) -> bool:
    """
    Throw away a draft that was never going to become anything.

    The one place a body is removed, because a note captured by mistake is not
    provenance for anything. Closing it with an outcome is the ordinary path.
    """
    with con:
        cur = con.execute(
            "DELETE FROM material_drafts WHERE id = ? AND user_id = ?", (draft_id, user_id)
        )
    return bool(cur.rowcount)
