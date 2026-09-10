"""
Syncing content into the database, and reading and writing card state.

`sync` used to rebuild the content tables wholesale. As of ADR-0006 the database
owns the material, so it merges instead: a note that changed at the source is
updated, one that has gone is archived, and one edited here is left alone rather
than overwritten.

`card_state` is untouched by any of that, exactly as before. The guarantee that a
card whose note vanishes keeps its history is now maintained deliberately -- by
archiving rather than deleting -- where it used to fall out of the fact that
nothing linked the tables. It is the one property here worth breaking a release
over, so it is stated rather than assumed.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zlib
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from typing import Any

from ..content.distractors import build as build_distractors
from ..content.loader import LoadResult
from ..content.models import Course, Note, Unit
from ..core.protocols import SchedulerBackend

DEFAULT_USER = 1


def _csum(note_fields: dict[str, Any]) -> int:
    """Checksum of the first field, for finding accidental duplicates later."""
    first = next(iter(note_fields.values()), "")
    text = " ".join(first) if isinstance(first, list) else str(first)
    return zlib.crc32(text.strip().lower().encode("utf-8"))


def _content_hash(n: Note) -> str:
    """
    A fingerprint of the note *as authored*.

    `origin` is deliberately not in it. Moving a note to a different file is not
    a change to the material, and hashing the path would make reorganising a
    course look like an edit of every note in it -- which, once local edits are
    protected from being overwritten, would turn a tidy-up into a wall of
    conflicts.
    """
    payload = json.dumps(
        {
            "notetype": n.notetype,
            "fields": n.fields,
            "tags": list(n.tags),
            "lesson": n.lesson.isoformat() if n.lesson else None,
            "unit": n.unit,
            "ord": n.ord,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sync_course(con: sqlite3.Connection, course: Course, units: list[Unit], stamp: str) -> None:
    """
    The course row and its units.

    Units are merged like notes and archived rather than deleted, for the same
    reason: `notes.unit` joins on this id, and a unit that leaves the files still
    names the unit its notes were studied under.
    """
    with con:
        con.execute(
            "INSERT INTO courses(id,title,l1,l2,variant,license,grading,scheduler,"
            "tag_weights,format_version,imported_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET title=excluded.title,l1=excluded.l1,"
            "l2=excluded.l2,variant=excluded.variant,license=excluded.license,"
            "grading=excluded.grading,scheduler=excluded.scheduler,"
            "tag_weights=excluded.tag_weights,format_version=excluded.format_version,"
            "imported_at=excluded.imported_at",
            (
                course.id,
                json.dumps(course.title, ensure_ascii=False),
                course.l1.code,
                course.l2.code,
                course.l2.variant,
                json.dumps(course.license.model_dump(), ensure_ascii=False),
                json.dumps(course.grading.model_dump(), ensure_ascii=False),
                course.scheduler,
                json.dumps(course.tag_weights, ensure_ascii=False),
                course.format_version,
                stamp,
            ),
        )
        con.executemany(
            "INSERT INTO units(course,id,title,cefr,ord,requires,archived_at) "
            "VALUES(?,?,?,?,?,?,NULL) "
            "ON CONFLICT(course,id) DO UPDATE SET title=excluded.title,cefr=excluded.cefr,"
            "ord=excluded.ord,requires=excluded.requires,archived_at=NULL",
            [
                (
                    course.id,
                    u.id,
                    json.dumps(u.title, ensure_ascii=False),
                    u.cefr,
                    u.ord,
                    json.dumps(list(u.requires), ensure_ascii=False),
                )
                for u in units
            ],
        )
        live = {u.id for u in units}
        stale = [
            r["id"]
            for r in con.execute(
                "SELECT id FROM units WHERE course = ? AND archived_at IS NULL", (course.id,)
            )
            if r["id"] not in live
        ]
        con.executemany(
            "UPDATE units SET archived_at = ? WHERE course = ? AND id = ?",
            [(stamp, course.id, i) for i in stale],
        )


@dataclass(frozen=True, slots=True)
class SyncReport:
    """What an import did. `conflicted` is the part a person has to look at."""

    notes: int
    cards: int
    added: int = 0
    updated: int = 0
    archived: int = 0
    #: Notes changed at the source *and* edited here. Neither version is lost and
    #: neither is chosen: the local one stays, and the id is reported so someone
    #: can decide. Silently picking a winner is the one behaviour that would make
    #: this unsafe to run.
    conflicted: tuple[str, ...] = field(default_factory=tuple)


def sync(con: sqlite3.Connection, result: LoadResult, *, now: datetime | None = None) -> SyncReport:
    """
    Merge what the course files say into the material the database owns.

    Four outcomes per note, and the third is the reason this is not an upsert:

    * not here yet          -> inserted
    * unchanged at source   -> left alone (un-archived if it had gone and came back)
    * edited here, and the source changed too -> **conflict**; the local note
      stays and the id is reported
    * changed at source     -> updated

    A note that has disappeared from the files is archived, never deleted.
    Deleting would orphan `card_state` rows whose card ids no longer resolve to
    anything, and the history behind them is not recoverable from the content.
    """
    course = result.course.id if result.course else ""
    stamp = (now or datetime.now(UTC)).isoformat()

    if result.course:
        _sync_course(con, result.course, result.units, stamp)

    existing = {
        r["id"]: r
        for r in con.execute("SELECT id, content_hash, edited_at, archived_at FROM notes")
    }
    added = updated = 0
    conflicted: list[str] = []
    seen: set[str] = set()

    with con:
        for n in result.notes:
            seen.add(n.id)
            digest = _content_hash(n)
            row = existing.get(n.id)
            values = (
                course,
                n.unit,
                n.notetype,
                n.ord,
                json.dumps(list(n.tags), ensure_ascii=False),
                n.lesson.isoformat() if n.lesson else None,
                json.dumps(n.fields, ensure_ascii=False),
                _csum(n.fields),
                n.origin,
                digest,
            )
            if row is None:
                con.execute(
                    "INSERT INTO notes(course,unit,notetype,ord,tags,lesson,fields,csum,"
                    "origin,content_hash,created_at,updated_at,id) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (*values, stamp, stamp, n.id),
                )
                added += 1
            elif row["content_hash"] == digest:
                # Nothing to write. Except: material that was archived and has
                # come back is not an edit, it is a return, and it must go back
                # into circulation on exactly the schedule it left with.
                if row["archived_at"]:
                    con.execute(
                        "UPDATE notes SET archived_at = NULL, updated_at = ? WHERE id = ?",
                        (stamp, n.id),
                    )
            elif row["edited_at"] is not None:
                conflicted.append(n.id)
            else:
                con.execute(
                    "UPDATE notes SET course=?,unit=?,notetype=?,ord=?,tags=?,lesson=?,"
                    "fields=?,csum=?,origin=?,content_hash=?,updated_at=?,archived_at=NULL "
                    "WHERE id=?",
                    (*values, stamp, n.id),
                )
                updated += 1

        gone = [i for i, r in existing.items() if i not in seen and r["archived_at"] is None]
        con.executemany("UPDATE notes SET archived_at = ? WHERE id = ?", [(stamp, i) for i in gone])

        # A conflicted note keeps the cards it has: the incoming ones were
        # expanded from the source version, which is not the version that is
        # still on the row.
        held = set(conflicted)
        incoming = [c for c in result.cards if c.note_id not in held]
        con.executemany(
            "INSERT INTO cards(id,note_id,template,notetype,grader,forms,scheduled,archived_at) "
            "VALUES(?,?,?,?,?,?,1,NULL) "
            "ON CONFLICT(id) DO UPDATE SET note_id=excluded.note_id,template=excluded.template,"
            "notetype=excluded.notetype,grader=excluded.grader,forms=excluded.forms,"
            "archived_at=NULL",
            [
                (
                    c.id,
                    c.note_id,
                    c.template,
                    c.notetype,
                    c.grader,
                    json.dumps(list(c.forms), ensure_ascii=False),
                )
                for c in incoming
            ],
        )
        live = {c.id for c in incoming}
        stale = [
            r["id"]
            for r in con.execute("SELECT id, note_id FROM cards WHERE archived_at IS NULL")
            if r["id"] not in live and r["note_id"] not in held
        ]
        con.executemany(
            "UPDATE cards SET archived_at = ? WHERE id = ?", [(stamp, i) for i in stale]
        )

        # Distractors stay wholesale. They are derived from the material rather
        # than authored in it, nobody can edit one, and they are deterministic --
        # so there is nothing here for a merge to protect.
        con.execute("DELETE FROM distractors")
        con.executemany(
            "INSERT INTO distractors(card_id,text,source,rank) VALUES(?,?,?,?)",
            [
                (d.card_id, d.text, d.source, d.rank)
                for d in build_distractors(
                    result.cards,
                    result.notes,
                    result.notetypes,
                    lang=result.course.l2.code if result.course else None,
                )
            ],
        )

    n_notes = con.execute("SELECT COUNT(*) AS n FROM notes WHERE archived_at IS NULL").fetchone()
    n_cards = con.execute("SELECT COUNT(*) AS n FROM cards WHERE archived_at IS NULL").fetchone()
    return SyncReport(
        notes=int(n_notes["n"]),
        cards=int(n_cards["n"]),
        added=added,
        updated=updated,
        archived=len(gone),
        conflicted=tuple(conflicted),
    )


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
    return [
        r["id"]
        for r in con.execute("SELECT id FROM cards WHERE scheduled = 1 AND archived_at IS NULL")
    ]


def distractors_for(con: sqlite3.Connection, card_id: str, limit: int) -> list[str]:
    """The best `limit` wrong answers for a card, best first."""
    rows = con.execute(
        "SELECT text FROM distractors WHERE card_id = ? ORDER BY rank LIMIT ?",
        (card_id, limit),
    )
    return [r["text"] for r in rows]


def distractor_counts(con: sqlite3.Connection) -> dict[str, int]:
    """How many each card has, for validation and for reporting."""
    rows = con.execute("SELECT card_id, COUNT(*) AS n FROM distractors GROUP BY card_id")
    return {r["card_id"]: int(r["n"]) for r in rows}


DECLARED = "declared"
EARNED = "earned"


def declare_known(
    con: sqlite3.Connection,
    card_id: str,
    day: date,
    *,
    backend: SchedulerBackend,
    user_id: int = DEFAULT_USER,
) -> CardState | None:
    """
    "I already know this." Take the card out of the queue on the learner's word.

    Recorded as `declared`, never as `earned`. The two land a card in the same
    place and mean opposite things -- one is months of correct answers, the other
    is one click -- and the difference is what makes the claim checkable later
    rather than indistinguishable from evidence.

    A card that earned its way out is left alone: re-declaring it would relabel
    evidence as a claim, which is the wrong direction.

    A card never answered has no row yet, and that is the *commonest* case for
    this button -- meeting a new card and already knowing the word. So one is
    created. `backend` is needed only to say who owns the empty state, since
    nothing outside `srs/` may invent the shape of that blob.
    """
    state = get_state(con, card_id, user_id=user_id)
    if state is None:
        state = CardState(
            card_id=card_id,
            algo=backend.name,
            algo_version=backend.version,
            state=backend.new_state(),
            user_id=user_id,
        )
    elif state.retired_at is not None:
        return state
    updated = replace(state, retired_at=day.isoformat(), retired_reason=DECLARED)
    save_state(con, updated)
    return updated


def undo_known(
    con: sqlite3.Connection, card_id: str, *, user_id: int = DEFAULT_USER
) -> CardState | None:
    """
    Take back the claim.

    Only a `declared` retirement can be undone here. One that was earned is not
    a mistake to reverse -- putting it back would need the same evidence that
    took it out, which belongs to a review flow rather than to an undo button.

    Nothing about the schedule is touched. The card returns exactly as it was,
    because saying "actually, I don't know it" is not the same as getting it
    wrong, and should not cost an interval.
    """
    state = get_state(con, card_id, user_id=user_id)
    if state is None or state.retired_reason != DECLARED:
        return state
    updated = replace(state, retired_at=None, retired_reason=None)
    save_state(con, updated)
    return updated


def declared_count(con: sqlite3.Connection, *, user_id: int = DEFAULT_USER) -> int:
    row = con.execute(
        "SELECT COUNT(*) AS n FROM card_state WHERE user_id = ? AND retired_reason = ?",
        (user_id, DECLARED),
    ).fetchone()
    return int(row["n"])
