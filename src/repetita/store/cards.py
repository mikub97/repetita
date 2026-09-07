"""
Syncing content into the database, and reading and writing card state.

`sync` rebuilds the content tables wholesale and deliberately leaves
`card_state` alone. A card whose note vanishes from the course keeps its
history, so removing and re-adding a unit does not reset anything.
"""

from __future__ import annotations

import json
import sqlite3
import zlib
from dataclasses import dataclass
from datetime import date
from typing import Any

from ..content.loader import LoadResult

DEFAULT_USER = 1


def _csum(note_fields: dict[str, Any]) -> int:
    """Checksum of the first field, for finding accidental duplicates later."""
    first = next(iter(note_fields.values()), "")
    text = " ".join(first) if isinstance(first, list) else str(first)
    return zlib.crc32(text.strip().lower().encode("utf-8"))


def sync(con: sqlite3.Connection, result: LoadResult) -> tuple[int, int]:
    """Replace the content cache with what the course files currently say."""
    course = result.course.id if result.course else ""
    notes = [
        (
            n.id,
            course,
            n.unit,
            n.notetype,
            n.ord,
            json.dumps(list(n.tags), ensure_ascii=False),
            n.lesson.isoformat() if n.lesson else None,
            json.dumps(n.fields, ensure_ascii=False),
            _csum(n.fields),
        )
        for n in result.notes
    ]
    cards = [
        (
            c.id,
            c.note_id,
            c.template,
            c.notetype,
            c.grader,
            json.dumps(list(c.forms), ensure_ascii=False),
            1,
        )
        for c in result.cards
    ]
    with con:
        con.execute("DELETE FROM cards")
        con.execute("DELETE FROM notes")
        con.executemany(
            "INSERT INTO notes(id,course,unit,notetype,ord,tags,lesson,fields,csum) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            notes,
        )
        con.executemany(
            "INSERT INTO cards(id,note_id,template,notetype,grader,forms,scheduled) "
            "VALUES(?,?,?,?,?,?,?)",
            cards,
        )
    return len(notes), len(cards)


@dataclass(frozen=True, slots=True)
class CardState:
    """
    One card's scheduling record.

    `state` is opaque: it belongs to the backend named in `algo`, and nothing
    outside `srs/` may read a key out of it. Everything the queue and the
    counters need is a column.
    """

    card_id: str
    algo: str
    algo_version: int
    state: dict[str, Any]
    due: str | None = None
    last: str | None = None
    interval: int = 0
    seen: int = 0
    correct: int = 0
    wrong: int = 0
    lapses: int = 0
    retired_at: str | None = None
    retired_reason: str | None = None
    suspended_at: str | None = None
    user_id: int = DEFAULT_USER

    @property
    def is_new(self) -> bool:
        return self.seen == 0

    @property
    def is_active(self) -> bool:
        return self.retired_at is None and self.suspended_at is None

    def is_due(self, today: date) -> bool:
        """New cards are not 'due' -- they are introduced separately, under a gate."""
        if self.is_new or not self.is_active or self.due is None:
            return False
        return self.due <= today.isoformat()


def _row_to_state(row: sqlite3.Row) -> CardState:
    return CardState(
        card_id=row["card_id"],
        algo=row["algo"],
        algo_version=row["algo_version"],
        state=json.loads(row["state"]),
        due=row["due"],
        last=row["last"],
        interval=row["interval"],
        seen=row["seen"],
        correct=row["correct"],
        wrong=row["wrong"],
        lapses=row["lapses"],
        retired_at=row["retired_at"],
        retired_reason=row["retired_reason"],
        suspended_at=row["suspended_at"],
        user_id=row["user_id"],
    )


def get_state(
    con: sqlite3.Connection, card_id: str, *, user_id: int = DEFAULT_USER
) -> CardState | None:
    row = con.execute(
        "SELECT * FROM card_state WHERE user_id = ? AND card_id = ?", (user_id, card_id)
    ).fetchone()
    return _row_to_state(row) if row else None


def all_states(con: sqlite3.Connection, *, user_id: int = DEFAULT_USER) -> dict[str, CardState]:
    rows = con.execute("SELECT * FROM card_state WHERE user_id = ?", (user_id,))
    return {r["card_id"]: _row_to_state(r) for r in rows}


def save_state(con: sqlite3.Connection, cs: CardState) -> None:
    with con:
        con.execute(
            "INSERT INTO card_state(user_id,card_id,algo,algo_version,state,due,last,"
            "interval,seen,correct,wrong,lapses,retired_at,retired_reason,suspended_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(user_id, card_id) DO UPDATE SET "
            "algo=excluded.algo, algo_version=excluded.algo_version, "
            "state=excluded.state, due=excluded.due, last=excluded.last, "
            "interval=excluded.interval, seen=excluded.seen, correct=excluded.correct, "
            "wrong=excluded.wrong, lapses=excluded.lapses, "
            "retired_at=excluded.retired_at, retired_reason=excluded.retired_reason, "
            "suspended_at=excluded.suspended_at",
            (
                cs.user_id,
                cs.card_id,
                cs.algo,
                cs.algo_version,
                json.dumps(cs.state, ensure_ascii=False),
                cs.due,
                cs.last,
                cs.interval,
                cs.seen,
                cs.correct,
                cs.wrong,
                cs.lapses,
                cs.retired_at,
                cs.retired_reason,
                cs.suspended_at,
            ),
        )


def card_ids(con: sqlite3.Connection) -> list[str]:
    return [r["id"] for r in con.execute("SELECT id FROM cards WHERE scheduled = 1")]
