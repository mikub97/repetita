"""
Recording answers.

Every answer produces exactly one `review_log` row and one updated `card_state`.
The log is append-only and never edited: it is the record of what happened, not
of what the current rules would have done.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import date, datetime

from ..core.protocols import SchedulerBackend
from ..core.retirement import earned
from ..core.types import Rating
from .cards import DEFAULT_USER, IN_COURSE, CardState, get_state, save_state


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
    plan_revision_id: int | None = None,
    user_id: int = DEFAULT_USER,
) -> CardState:
    """
    Apply one answer: schedule it, log it, store it.

    `at` is an aware UTC instant and `local_day` is the learner's calendar day.
    They are separate arguments on purpose. Deriving the day from the instant is
    how a session in Brazil gets filed under tomorrow's date, and this codebase
    has already shipped that bug once in its predecessor.

    `plan_revision_id` says which revision of which study plan chose to serve
    this card. It is recorded now rather than when someone wants it: ADR-0003
    exists precisely for the case where a sequence was thrown away and could not
    be reconstructed, and "did making it harder help?" cannot be answered from
    aggregates.
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

    # The log goes in before the retirement check, so the answer just given
    # counts toward the clean run it is asked about.
    with con:
        con.execute(
            "INSERT INTO review_log(user_id,card_id,rating,review_datetime,day,"
            "review_duration_ms,elapsed_days,algo,state_before,mode,form,answer,"
            "plan_revision_id) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                plan_revision_id,
            ),
        )
    # A card that has reached the ceiling with a clean run has nothing left to
    # prove and leaves the queue. `earned` is deliberately outside the scheduler:
    # it reads the denormalised interval and this card's own recent ratings, so
    # every backend gets the same answer without holding an opinion.
    if updated.retired_at is None and earned(
        updated.interval, recent_ratings_for(con, card_id, user_id=user_id)
    ):
        updated = replace(updated, retired_at=day.isoformat(), retired_reason="earned")

    save_state(con, updated)
    return updated


def recent_ratings(
    con: sqlite3.Connection,
    limit: int = 20,
    *,
    user_id: int = DEFAULT_USER,
    course: str | None = None,
) -> list[Rating]:
    """
    The most recent answers, newest first. Used by the new-material gate.

    Scoped by course when one is given, because the gate decides whether a
    learner is ready for new material *in that course*. A bad run in Italian is
    not evidence about Portuguese, and unscoped it was exactly that.
    """
    sql = "SELECT rating FROM review_log WHERE user_id = ?"
    args: tuple[object, ...] = (user_id,)
    if course:
        sql += f" AND {IN_COURSE}"
        args += (course,)
    sql += " ORDER BY id DESC LIMIT ?"
    return [Rating(r["rating"]) for r in con.execute(sql, (*args, limit))]


def count_on(
    con: sqlite3.Connection, day: date, *, user_id: int = DEFAULT_USER, course: str | None = None
) -> int:
    sql = "SELECT COUNT(*) AS n FROM review_log WHERE user_id = ? AND day = ?"
    args: tuple[object, ...] = (user_id, day.isoformat())
    if course:
        sql += f" AND {IN_COURSE}"
        args += (course,)
    return int(con.execute(sql, args).fetchone()["n"])


def first_seen_on(
    con: sqlite3.Connection, day: date, *, user_id: int = DEFAULT_USER, course: str | None = None
) -> int:
    """
    How many cards were met for the very first time today.

    Not "answers today": a card introduced yesterday and reviewed today is not a
    new introduction, and counting it as one would make any daily introduction
    count drift over a long session. This is the unrestricted number -- the
    lesson introduction cap is counted with `lesson_first_seen_on`.
    """
    sql = "SELECT COUNT(*) AS n FROM (SELECT card_id FROM review_log WHERE user_id = ?"
    args: tuple[object, ...] = (user_id,)
    if course:
        sql += f" AND {IN_COURSE}"
        args += (course,)
    sql += " GROUP BY card_id HAVING MIN(day) = ?)"
    return int(con.execute(sql, (*args, day.isoformat())).fetchone()["n"])


def lesson_first_seen_on(
    con: sqlite3.Connection,
    day: date,
    *,
    since: date,
    user_id: int = DEFAULT_USER,
    course: str | None = None,
) -> int:
    """
    How many cards from a lesson dated `since` or later were met for the first
    time today.

    The narrower sibling of `first_seen_on`, and the one the lesson introduction
    cap is counted with: that budget exists to stop a forty-word lesson landing
    in one evening, so only lesson material may spend it. Charging it for the
    back catalogue met today shrinks the lesson's allowance for reasons that have
    nothing to do with the lesson, and on a day with a real backlog closes the
    exemption altogether.

    `since` is a date rather than a number of days because how recent a lesson
    has to be is a policy question (`policies.daily.LESSON_FRESH_DAYS`), and the
    store holds no tuning constants. A lesson dated in the future is included, as
    `lesson_is_fresh` includes it.
    """
    row = con.execute(
        "SELECT COUNT(*) AS n FROM ("
        "  SELECT r.card_id FROM review_log r"
        "  JOIN cards c ON c.id = r.card_id"
        "  JOIN notes n ON n.id = c.note_id"
        "  WHERE r.user_id = ? AND n.lesson IS NOT NULL AND n.lesson >= ?"
        + ("  AND n.course = ?" if course else "")
        + "  GROUP BY r.card_id HAVING MIN(r.day) = ?)",
        (user_id, since.isoformat(), *((course,) if course else ()), day.isoformat()),
    ).fetchone()
    return int(row["n"])


def recent_ratings_for(
    con: sqlite3.Connection, card_id: str, *, limit: int = 10, user_id: int = DEFAULT_USER
) -> list[Rating]:
    """One card's own ratings, newest first."""
    rows = con.execute(
        "SELECT rating FROM review_log WHERE user_id = ? AND card_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, card_id, limit),
    )
    return [Rating(r["rating"]) for r in rows]
