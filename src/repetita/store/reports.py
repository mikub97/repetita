"""
Reporting an exercise as broken.

A report is a claim about the *material*, not about the learner's memory of it.
That distinction is the whole design: answering a card is evidence and moves the
schedule, declaring one known is a claim and takes it out of the queue, and this
is a third thing again -- a statement that the question itself is wrong.

It is kept out of `review_log` for the same reason `declare_known` is: the log is
a record of answers given, and an accuracy figure computed from it decides how
fast new material arrives. It is kept out of `notes`/`cards` because those are a
cache rebuilt from the very files the report is complaining about.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from dataclasses import replace as dc_replace
from datetime import UTC, date, datetime
from typing import Any

from ..core.protocols import SchedulerBackend
from .cards import DEFAULT_USER, CardState, get_state, save_state

#: The reasons a learner may give, as codes rather than sentences -- UI text is
#: translated, and the engine ships no UI strings as Python literals.
#:
#: Each one earns its place by naming the key an author would edit, which is what
#: makes triage mechanical instead of interpretive:
#:
#:   also_correct    what I typed was right too  -> append to `answers:`
#:   wrong_answer    the expected answer is wrong -> fix `answers:`
#:   ambiguous       the gap accepts other words -> tighten `cue:`
#:   typo            the text is misspelled      -> fix `prompt:` / `translation:`
#:   bad_translation the L1 side is wrong        -> fix `translation:`
#:   bad_options     the choices are rubbish     -> add curated `distractors:`
#:   other           -- free text only
#:
#: An unrecognised reason is refused rather than folded into `other`: a report
#: nobody can act on is worse than no report, because it looks like one.
REASONS = (
    "also_correct",
    "wrong_answer",
    "ambiguous",
    "typo",
    "bad_translation",
    "bad_options",
    "other",
)


@dataclass(frozen=True, slots=True)
class Snapshot:
    """
    What was on screen when the report was made.

    Held by value, not by reference to `notes`. The content tables are rebuilt on
    every load, so a report that pointed at them would describe whatever the file
    says *now* -- which, by the time anyone triages it, is quite possibly the
    text that was written to fix it.
    """

    note_id: str
    template: str
    form: str
    fields: dict[str, Any]
    origin: str = ""
    unit: str = ""


@dataclass(frozen=True, slots=True)
class Report:
    id: int
    card_id: str
    reason: str
    note: str | None
    reported_at: str
    day: str
    note_id: str
    template: str
    form: str
    origin: str
    unit: str
    fields: dict[str, Any]
    given: str | None
    resolved_at: str | None
    #: True when this report is what suspended the card, so undo can put back
    #: exactly what it took and nothing else.
    suspended: bool = False
    user_id: int = DEFAULT_USER


def _row_to_report(row: sqlite3.Row) -> Report:
    return Report(
        id=int(row["id"]),
        card_id=row["card_id"],
        reason=row["reason"],
        note=row["note"],
        reported_at=row["reported_at"],
        day=row["day"],
        note_id=row["note_id"],
        template=row["template"],
        form=row["form"],
        origin=row["origin"] or "",
        unit=row["unit"] or "",
        fields=json.loads(row["fields"]),
        given=row["given"],
        resolved_at=row["resolved_at"],
        suspended=bool(row["suspended"]),
        user_id=int(row["user_id"]),
    )


def last_answer(
    con: sqlite3.Connection,
    card_id: str,
    *,
    day: date | None = None,
    user_id: int = DEFAULT_USER,
) -> str | None:
    """
    The most recent thing the learner typed for this card, on `day` if given.

    For `also_correct` this is the entire point: the word to add to `answers:` is
    the one they just had rejected, and asking them to type it again in order to
    report it would be asking them to do the machine's job.

    The day restriction is what keeps it honest. A card reported *before* it was
    answered would otherwise be annotated with an answer from last week, which
    reads as the thing that provoked the report and is not.
    """
    sql = "SELECT answer FROM review_log WHERE user_id = ? AND card_id = ?"
    args: list[Any] = [user_id, card_id]
    if day is not None:
        sql += " AND day = ?"
        args.append(day.isoformat())
    row = con.execute(sql + " ORDER BY id DESC LIMIT 1", tuple(args)).fetchone()
    return row["answer"] if row else None


def report_card(
    con: sqlite3.Connection,
    card_id: str,
    reason: str,
    day: date,
    *,
    backend: SchedulerBackend,
    snapshot: Snapshot,
    note: str | None = None,
    user_id: int = DEFAULT_USER,
) -> Report:
    """
    Record a report, and take the card out of the queue while it stands.

    Suspended, not retired. They are separate lanes in `CardState.is_active` and
    they mean opposite things: retired is "done with this", suspended is "not
    now". A broken exercise is not one the learner has finished with, and
    collapsing the two would make a content bug indistinguishable from progress.

    A card never answered has no state row, and that is a perfectly ordinary way
    to meet a broken exercise -- on its first showing. So one is created, exactly
    as `declare_known` does. `backend` is needed only to say who owns the empty
    state blob, since nothing outside `srs/` may invent its shape.
    """
    if reason not in REASONS:
        raise ValueError(f"unknown reason {reason!r}")

    # Whether this report is what takes the card out of rotation. A card can
    # already be suspended for another reason -- `importers/hub` carries one
    # across from the predecessor -- and an undo must put back only what it took.
    existing = get_state(con, card_id, user_id=user_id)
    applied = existing is None or existing.suspended_at is None

    with con:
        cur = con.execute(
            "INSERT INTO card_reports(user_id,card_id,reason,note,reported_at,day,"
            "note_id,template,form,origin,unit,fields,given,suspended) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                user_id,
                card_id,
                reason,
                note or None,
                datetime.now(UTC).isoformat(),
                day.isoformat(),
                snapshot.note_id,
                snapshot.template,
                snapshot.form,
                snapshot.origin,
                snapshot.unit,
                json.dumps(snapshot.fields, ensure_ascii=False),
                last_answer(con, card_id, day=day, user_id=user_id),
                int(applied),
            ),
        )
    if applied:
        _suspend(con, card_id, day, backend=backend, user_id=user_id)

    row = con.execute("SELECT * FROM card_reports WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _row_to_report(row)


def _suspend(
    con: sqlite3.Connection,
    card_id: str,
    day: date,
    *,
    backend: SchedulerBackend,
    user_id: int = DEFAULT_USER,
) -> CardState:
    state = get_state(con, card_id, user_id=user_id)
    if state is None:
        state = CardState(
            card_id=card_id,
            algo=backend.name,
            algo_version=backend.version,
            state=backend.new_state(),
            user_id=user_id,
        )
    updated = dc_replace(state, suspended_at=day.isoformat())
    save_state(con, updated)
    return updated


def withdraw_report(
    con: sqlite3.Connection, card_id: str, *, user_id: int = DEFAULT_USER
) -> CardState | None:
    """
    Take back the most recent open report on a card, and unsuspend it.

    Nothing about the schedule is touched. Saying "actually that exercise is
    fine" is not the same as getting it wrong and must not cost an interval --
    the same rule `undo_known` follows.

    The card is only unsuspended once *no* report on it is still open. Two
    reports are two complaints, and answering one of them does not answer the
    other.
    """
    row = con.execute(
        "SELECT id, suspended FROM card_reports "
        "WHERE user_id = ? AND card_id = ? AND resolved_at IS NULL ORDER BY id DESC LIMIT 1",
        (user_id, card_id),
    ).fetchone()
    if row is None:
        return get_state(con, card_id, user_id=user_id)

    applied = bool(row["suspended"])
    with con:
        con.execute("DELETE FROM card_reports WHERE id = ?", (int(row["id"]),))
    return _unsuspend_if_free(con, card_id, applied=applied, user_id=user_id)


def resolve_report(
    con: sqlite3.Connection, report_id: int, day: date, *, user_id: int = DEFAULT_USER
) -> None:
    """
    Mark a report dealt with, and let the card back into the queue if it was the
    last one open against it.

    Resolving keeps the row; withdrawing removes it. The difference is whether
    the report was right: a fixed exercise leaves a record of having been broken,
    a mistaken tap should not.
    """
    row = con.execute(
        "SELECT card_id, suspended FROM card_reports WHERE user_id = ? AND id = ?",
        (user_id, report_id),
    ).fetchone()
    if row is None:
        return
    card_id = row["card_id"]

    applied = bool(row["suspended"])
    with con:
        con.execute(
            "UPDATE card_reports SET resolved_at = ? WHERE user_id = ? AND id = ?",
            (day.isoformat(), user_id, report_id),
        )
    _unsuspend_if_free(con, card_id, applied=applied, user_id=user_id)


def _unsuspend_if_free(
    con: sqlite3.Connection, card_id: str, *, applied: bool, user_id: int
) -> CardState | None:
    """
    Let the card back in, but only if this report is why it was out.

    Two conditions, and both are needed. `applied` says this report is what set
    `suspended_at` -- without it, closing a report would resurrect a card the
    importer had deliberately parked. The open count says nothing else is still
    complaining: two reports are two complaints, and answering one of them does
    not answer the other.
    """
    state = get_state(con, card_id, user_id=user_id)
    if state is None or not applied:
        return state
    if open_report_count(con, card_id=card_id, user_id=user_id):
        return state
    updated = dc_replace(state, suspended_at=None)
    save_state(con, updated)
    return updated


def open_reports(con: sqlite3.Connection, *, user_id: int = DEFAULT_USER) -> list[Report]:
    rows = con.execute(
        "SELECT * FROM card_reports WHERE user_id = ? AND resolved_at IS NULL ORDER BY id",
        (user_id,),
    )
    return [_row_to_report(r) for r in rows]


def all_reports(con: sqlite3.Connection, *, user_id: int = DEFAULT_USER) -> list[Report]:
    rows = con.execute("SELECT * FROM card_reports WHERE user_id = ? ORDER BY id", (user_id,))
    return [_row_to_report(r) for r in rows]


def open_report_count(
    con: sqlite3.Connection, *, card_id: str | None = None, user_id: int = DEFAULT_USER
) -> int:
    """How many reports are still open, in total or against one card."""
    sql = "SELECT COUNT(*) AS n FROM card_reports WHERE user_id = ? AND resolved_at IS NULL"
    args: list[Any] = [user_id]
    if card_id is not None:
        sql += " AND card_id = ?"
        args.append(card_id)
    row = con.execute(sql, tuple(args)).fetchone()
    return int(row["n"])
