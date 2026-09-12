"""
Per-scope preferences and cursor: which course somebody was last on.

The `containers` table has been in the schema since it was written and read by
nothing. Its `scope` vocabulary -- `course:<id>` -- and its `viewed_at` column
are exactly the slot "the course you were last studying" needs, so this is the
table finally being used rather than a new one being added beside it.

Separate from content and from progress, and it is neither: losing every row
here costs somebody one click on a flag.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from .cards import DEFAULT_USER

COURSE = "course:"


def touch(
    con: sqlite3.Connection,
    course_id: str,
    *,
    user_id: int = DEFAULT_USER,
    at: datetime | None = None,
) -> None:
    """Record that this course is the one being looked at."""
    stamp = (at or datetime.now(UTC)).isoformat()
    with con:
        con.execute(
            "INSERT INTO containers(user_id, scope, viewed_at) VALUES(?,?,?) "
            "ON CONFLICT(user_id, scope) DO UPDATE SET viewed_at = excluded.viewed_at",
            (user_id, f"{COURSE}{course_id}", stamp),
        )


def last_course(con: sqlite3.Connection, *, user_id: int = DEFAULT_USER) -> str | None:
    """
    The course most recently looked at, or `None` if nobody has chosen one.

    `None` rather than a guess: the caller has a configured default and knows
    more about what to fall back to than this module does.
    """
    row = con.execute(
        "SELECT scope FROM containers WHERE user_id = ? AND scope LIKE ? "
        "AND viewed_at IS NOT NULL ORDER BY viewed_at DESC LIMIT 1",
        (user_id, f"{COURSE}%"),
    ).fetchone()
    return str(row["scope"])[len(COURSE) :] if row else None


def settings(con: sqlite3.Connection, scope: str, *, user_id: int = DEFAULT_USER) -> dict[str, Any]:
    row = con.execute(
        "SELECT settings FROM containers WHERE user_id = ? AND scope = ?", (user_id, scope)
    ).fetchone()
    return dict(json.loads(row["settings"])) if row else {}


def remember(
    con: sqlite3.Connection, scope: str, values: dict[str, Any], *, user_id: int = DEFAULT_USER
) -> dict[str, Any]:
    """Merge `values` into this scope's settings and return the result."""
    merged = {**settings(con, scope, user_id=user_id), **values}
    with con:
        con.execute(
            "INSERT INTO containers(user_id, scope, settings) VALUES(?,?,?) "
            "ON CONFLICT(user_id, scope) DO UPDATE SET settings = excluded.settings",
            (user_id, scope, json.dumps(merged, ensure_ascii=False)),
        )
    return merged
