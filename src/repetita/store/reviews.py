"""
Recording answers.

Every answer produces exactly one `review_log` row and one updated `card_state`.
The log is append-only and never edited: it is the record of what happened, not
of what the current rules would have done.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime

from ..core.protocols import SchedulerBackend
from ..core.types import Rating
from .cards import DEFAULT_USER, CardState, get_state, save_state


def _elapsed_days(last: str | None, today: date) -> float | None:
    if not last:
        return None
    try:
        return float((today - date.fromisoformat(last)).days)
    except ValueError:
        return None


def record_answer(
    con: sqlite3.Connection,
    card_id: str,
    rating: Rating,
    *,
    backend: SchedulerBackend,
    at: datetime,
    local_day: date | None = None,
    mode: str = "session",
    form: str = "typein",
    answer: str | None = None,
    duration_ms: int | None = None,
    user_id: int = DEFAULT_USER,
) -> CardState:
    """
    Apply one answer: schedule it, log it, store it.

    `at` is an aware UTC instant and `local_day` is the learner's calendar day.
    They are separate arguments on purpose. Deriving the day from the instant is
    how a session in Brazil gets filed under tomorrow's date, and this codebase
    has already shipped that bug once in its predecessor.
    """
    if at.tzinfo is None:
        raise ValueError("`at` must be timezone-aware; pass an aware UTC datetime")
    day = local_day or at.date()

    existing = get_state(con, card_id, user_id=user_id)
    state_before = existing.state if existing else backend.new_state()
    elapsed = _elapsed_days(existing.last if existing else None, day)

    state_after = backend.review(dict(state_before), rating, at)
    due = backend.due_at(state_after)
    passed = rating.passed

    updated = CardState(
        card_id=card_id,
        algo=backend.name,
        algo_version=backend.version,
        state=state_after,
        due=due.isoformat() if due else None,
        last=day.isoformat(),
        interval=backend.interval_days(state_after),
        seen=(existing.seen if existing else 0) + 1,
        correct=(existing.correct if existing else 0) + (1 if passed else 0),
        wrong=(existing.wrong if existing else 0) + (0 if passed else 1),
        # Counted here rather than read out of `state`: the column has to mean the
        # same thing whichever backend wrote the row.
        lapses=(existing.lapses if existing else 0) + (0 if passed else 1),
        retired_at=existing.retired_at if existing else None,
        retired_reason=existing.retired_reason if existing else None,
        suspended_at=existing.suspended_at if existing else None,
        user_id=user_id,
    )

    with con:
        con.execute(
            "INSERT INTO review_log(user_id,card_id,rating,review_datetime,day,"
            "review_duration_ms,elapsed_days,algo,state_before,mode,form,answer) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                user_id,
                card_id,
                int(rating),
                at.isoformat(),
                day.isoformat(),
                duration_ms,
                elapsed,
                backend.name,
                json.dumps(state_before, ensure_ascii=False),
                mode,
                form,
                answer,
            ),
        )
    save_state(con, updated)
    return updated


def recent_ratings(
    con: sqlite3.Connection, limit: int = 20, *, user_id: int = DEFAULT_USER
) -> list[Rating]:
    """The most recent answers, newest first. Used by the new-material gate."""
    rows = con.execute(
        "SELECT rating FROM review_log WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    )
    return [Rating(r["rating"]) for r in rows]


def count_on(con: sqlite3.Connection, day: date, *, user_id: int = DEFAULT_USER) -> int:
    row = con.execute(
        "SELECT COUNT(*) AS n FROM review_log WHERE user_id = ? AND day = ?",
        (user_id, day.isoformat()),
    ).fetchone()
    return int(row["n"])


def first_seen_on(con: sqlite3.Connection, day: date, *, user_id: int = DEFAULT_USER) -> int:
    """
    How many cards were met for the very first time today.

    Not "answers today": a card introduced yesterday and reviewed today is not a
    new introduction, and counting it as one would make the daily introduction
    cap drift shut over a long session.
    """
    row = con.execute(
        "SELECT COUNT(*) AS n FROM (SELECT card_id FROM review_log WHERE user_id = ? "
        "GROUP BY card_id HAVING MIN(day) = ?)",
        (user_id, day.isoformat()),
    ).fetchone()
    return int(row["n"])
