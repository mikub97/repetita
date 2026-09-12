"""
Observations about how the material is organised.

A learner looking at their own catalogue notices things: *"too much grammar and
no listening"*, *"`tempo` and `tempo-adverbios` are the same subject"*,
*"numbers are scattered across three topics"*. None of those is a bug in an
exercise, and none of them should take a card out of the queue -- but they are
exactly the observations that are lost if there is nowhere to put them.

Deliberately **not** `card_reports`. That table says *this exercise is broken*
and suspends the card; this says *this grouping is wrong* and suspends nothing.
Same reasoning that keeps reports out of `review_log`: a report is not an answer,
and an issue is not a report. Three verbs, three tables, none of them corrupting
the counts of the others.

Append-only in the same spirit: `resolved_at` closes an issue without erasing it,
and `resolution` records what was actually done -- so a tag change six months
later can still be traced back to the thing that prompted it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from .users import DEFAULT_USER

#: What kind of observation this is. Prose in `body`, never in `kind`.
KINDS = ("taxonomy", "coverage", "balance", "duplicate", "other")


@dataclass(frozen=True, slots=True)
class Issue:
    id: int
    kind: str
    body: str
    selector: str | None
    raised_at: str
    resolved_at: str | None = None
    resolution: str | None = None

    @property
    def open(self) -> bool:
        return self.resolved_at is None


def _row(r: sqlite3.Row) -> Issue:
    return Issue(
        id=int(r["id"]),
        kind=r["kind"],
        body=r["body"],
        selector=r["selector"],
        raised_at=r["raised_at"],
        resolved_at=r["resolved_at"],
        resolution=r["resolution"],
    )


def raise_issue(
    con: sqlite3.Connection,
    *,
    body: str,
    kind: str = "other",
    selector: str | None = None,
    user_id: int = DEFAULT_USER,
    at: datetime | None = None,
    course: str = "",
) -> Issue:
    """
    Record an observation.

    `selector` is what the person was looking at when they raised it --
    `topic=tempo`, say. Without it, "these two are the same" is unactionable a
    week later, because nobody remembers which two.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}; expected one of {', '.join(KINDS)}")
    if not body.strip():
        raise ValueError("an issue with no body says nothing")
    stamp = (at or datetime.now(UTC)).isoformat()
    with con:
        cur = con.execute(
            "INSERT INTO material_issues(user_id,kind,body,selector,raised_at,course) "
            "VALUES(?,?,?,?,?,?)",
            (user_id, kind, body.strip(), selector, stamp, course),
        )
    return Issue(int(cur.lastrowid or 0), kind, body.strip(), selector, stamp)


def resolve(
    con: sqlite3.Connection,
    issue_id: int,
    *,
    note: str | None = None,
    user_id: int = DEFAULT_USER,
    at: datetime | None = None,
) -> Issue | None:
    """
    Close an issue, recording what was done about it.

    Only an open one. Re-resolving would overwrite the record of what actually
    fixed it with whatever the second person assumed.
    """
    stamp = (at or datetime.now(UTC)).isoformat()
    with con:
        cur = con.execute(
            "UPDATE material_issues SET resolved_at = ?, resolution = ? "
            "WHERE id = ? AND user_id = ? AND resolved_at IS NULL",
            (stamp, note, issue_id, user_id),
        )
    if not cur.rowcount:
        return None
    row = con.execute("SELECT * FROM material_issues WHERE id = ?", (issue_id,)).fetchone()
    return _row(row) if row else None


#: This course's, plus anything not tied to one -- which shows everywhere
#: rather than nowhere. See `drafts._SCOPE`, which says the same thing.
_SCOPE = "(course = ? OR course = '')"


def open_issues(
    con: sqlite3.Connection, *, user_id: int = DEFAULT_USER, course: str | None = None
) -> list[Issue]:
    sql = "SELECT * FROM material_issues WHERE user_id = ? AND resolved_at IS NULL"
    args: tuple[object, ...] = (user_id,)
    if course:
        sql += f" AND {_SCOPE}"
        args += (course,)
    return [_row(r) for r in con.execute(sql + " ORDER BY id", args)]


def all_issues(con: sqlite3.Connection, *, user_id: int = DEFAULT_USER) -> list[Issue]:
    return [
        _row(r)
        for r in con.execute(
            "SELECT * FROM material_issues WHERE user_id = ? ORDER BY id", (user_id,)
        )
    ]
