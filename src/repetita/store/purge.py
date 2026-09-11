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
) -> Doomed:
    """Count everything a purge would take, without taking any of it."""
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

    def count(table: str) -> int:
        row = con.execute(
            f"SELECT count(*) AS n FROM {table} WHERE card_id IN ({cmarks})", cards
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
) -> Doomed:
    """
    Delete material outright. Returns what went.

    The caller is expected to have taken a snapshot and, where history is
    attached, to have asked. This function does what it is told -- it is the
    reporting above, and the command around it, that make that safe.
    """
    going = what_would_go(con, note_id=note_id, unit=unit, archived_before=archived_before)
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
                con.execute(f"DELETE FROM review_log WHERE card_id IN ({cmarks})", cards)
                con.execute(f"DELETE FROM card_state WHERE card_id IN ({cmarks})", cards)
                con.execute(f"DELETE FROM card_reports WHERE card_id IN ({cmarks})", cards)
            con.execute(f"DELETE FROM distractors WHERE card_id IN ({cmarks})", cards)
            con.execute(f"DELETE FROM card_handles WHERE card_id IN ({cmarks})", cards)
            con.execute(f"DELETE FROM cards WHERE id IN ({cmarks})", cards)
        con.execute(f"DELETE FROM note_facets WHERE note_id IN ({marks})", ids)
        con.execute(f"DELETE FROM pending_changes WHERE note_id IN ({marks})", ids)
        con.execute(f"DELETE FROM notes WHERE id IN ({marks})", ids)
    return going
