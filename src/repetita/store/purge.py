"""
Removing material for real, and saying what that costs first.

Archiving is the default here and stays the default: material that has left a
course keeps its rows so that the schedule behind it stays reachable, which is
why this schema has no foreign keys (ADR-0006). But *never delete* and *never
delete without knowing what you are deleting* are different rules, and only the
second one is true. A test set made by accident, a duplicated import, a year of
rubbish -- there was no way to be rid of any of it.

So: purge exists, it reports before it acts, and history is a separate decision
from material. Deleting an exercise leaves its answers in `review_log` pointing
at a card that no longer exists -- harmless, countable, and recoverable if the
exercise ever comes back under the same id -- and removing those too takes
`with_history`, which is one of the few things that still asks a person first.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from .users import DEFAULT_USER


@dataclass(frozen=True, slots=True)
class Doomed:
    """What a purge would remove, counted before anything happens."""

    notes: tuple[str, ...] = ()
    cards: int = 0
    #: History attached to those cards. Not removed unless asked for, and the
    #: number this reports is the whole argument for asking.
    answers: int = 0
    states: int = 0
    reports: int = 0
    live: tuple[str, ...] = field(default=())

    @property
    def history(self) -> int:
        return self.answers + self.states

    def __bool__(self) -> bool:
        return bool(self.notes)


def _chosen(
    con: sqlite3.Connection,
    *,
    note_id: str | None = None,
    unit: str | None = None,
    archived_before: str | None = None,
) -> list[sqlite3.Row]:
    if note_id:
        return list(con.execute("SELECT id, archived_at FROM notes WHERE id = ?", (note_id,)))
    if unit:
        return list(con.execute("SELECT id, archived_at FROM notes WHERE unit = ?", (unit,)))
    if archived_before:
        return list(
            con.execute(
                "SELECT id, archived_at FROM notes "
                "WHERE archived_at IS NOT NULL AND archived_at < ?",
                (archived_before,),
            )
        )
    return []


def what_would_go(
    con: sqlite3.Connection,
    *,
    note_id: str | None = None,
    unit: str | None = None,
    archived_before: str | None = None,
    user_id: int | None = DEFAULT_USER,
    all_users: bool = False,
) -> Doomed:
    """
    Count everything a purge would take, without taking any of it.

    The history counted is the same history `purge` would remove -- one person's
    by default -- because this number is shown to somebody who is deciding, and a
    count that includes rows the operation will not touch is worse than no count.
    """
    rows = _chosen(con, note_id=note_id, unit=unit, archived_before=archived_before)
    if not rows:
        return Doomed()
    ids = tuple(r["id"] for r in rows)
    live = tuple(r["id"] for r in rows if r["archived_at"] is None)
    marks = ",".join("?" for _ in ids)

    cards = [r["id"] for r in con.execute(f"SELECT id FROM cards WHERE note_id IN ({marks})", ids)]
    if not cards:
        return Doomed(notes=ids, live=live)
    cmarks = ",".join("?" for _ in cards)

    mine = "" if all_users else " AND user_id = ?"
    who: tuple[object, ...] = () if all_users else (user_id,)

    def count(table: str) -> int:
        row = con.execute(
            f"SELECT count(*) AS n FROM {table} WHERE card_id IN ({cmarks}){mine}",
            (*cards, *who),
        ).fetchone()
        return int(row["n"])

    return Doomed(
        notes=ids,
        live=live,
        cards=len(cards),
        answers=count("review_log"),
        states=count("card_state"),
        reports=count("card_reports"),
    )


def purge(
    con: sqlite3.Connection,
    *,
    note_id: str | None = None,
    unit: str | None = None,
    archived_before: str | None = None,
    with_history: bool = False,
    user_id: int | None = DEFAULT_USER,
    all_users: bool = False,
) -> Doomed:
    """
    Delete material outright. Returns what went.

    The caller is expected to have taken a snapshot and, where history is
    attached, to have asked. This function does what it is told -- it is the
    reporting above, and the command around it, that make that safe.

    **The material is shared; the history is not.** Notes, cards and distractors
    belong to the course, so removing them removes them for everybody -- that is
    what removing an exercise means. But `review_log`, `card_state` and
    `card_reports` are one person's, and this module had no notion of that: it
    deleted every user's rows for the card, so one person tidying up destroyed
    three people's history. Harmless while there was one account and total the
    day there were two.

    `all_users` is the way to mean it, and it is deliberately not the default.
    """
    going = what_would_go(
        con,
        note_id=note_id,
        unit=unit,
        archived_before=archived_before,
        user_id=user_id,
        all_users=all_users,
    )
    if not going:
        return going

    ids = list(going.notes)
    marks = ",".join("?" for _ in ids)
    cards = [r["id"] for r in con.execute(f"SELECT id FROM cards WHERE note_id IN ({marks})", ids)]
    cmarks = ",".join("?" for _ in cards) if cards else ""

    with con:
        if cards:
            if with_history:
                # The one place in this package that removes a review. It is
                # deliberate, counted, and reported -- which is the whole of the
                # difference between this and the thing the old rule forbade.
                #
                # Scoped to one person unless told otherwise: see the docstring.
                mine = "" if all_users else " AND user_id = ?"
                who: tuple[object, ...] = () if all_users else (user_id,)
                for table in ("review_log", "card_state", "card_reports"):
                    con.execute(
                        f"DELETE FROM {table} WHERE card_id IN ({cmarks}){mine}",
                        (*cards, *who),
                    )
            con.execute(f"DELETE FROM distractors WHERE card_id IN ({cmarks})", cards)
            con.execute(f"DELETE FROM card_handles WHERE card_id IN ({cmarks})", cards)
            con.execute(f"DELETE FROM cards WHERE id IN ({cmarks})", cards)
        con.execute(f"DELETE FROM note_facets WHERE note_id IN ({marks})", ids)
        # Staged edits are one person's too, and an unapplied edit of somebody
        # else's is theirs to discard.
        if all_users:
            con.execute(f"DELETE FROM pending_changes WHERE note_id IN ({marks})", ids)
        else:
            con.execute(
                f"DELETE FROM pending_changes WHERE note_id IN ({marks}) AND user_id = ?",
                (*ids, user_id),
            )
        con.execute(f"DELETE FROM notes WHERE id IN ({marks})", ids)
    return going
