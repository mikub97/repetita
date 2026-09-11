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
from ..content.facets import classify
from ..content.loader import LoadResult
from ..content.models import Course, FacetAxis, Facets, FamilySpec, Note, Unit
from ..core.buckets import bucket_of
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


def _sync_course(
    con: sqlite3.Connection,
    course: Course,
    units: list[Unit],
    stamp: str,
    family: object = None,
) -> None:
    """
    The course row and its units.

    Units are merged like notes and archived rather than deleted, for the same
    reason: `notes.unit` joins on this id, and a unit that leaves the files still
    names the unit its notes were studied under.
    """
    with con:
        con.execute(
            "INSERT INTO courses(id,title,l1,l2,variant,license,grading,scheduler,"
            "tag_weights,format_version,imported_at,family) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET title=excluded.title,l1=excluded.l1,"
            "l2=excluded.l2,variant=excluded.variant,license=excluded.license,"
            "grading=excluded.grading,scheduler=excluded.scheduler,"
            "tag_weights=excluded.tag_weights,format_version=excluded.format_version,"
            "imported_at=excluded.imported_at,family=excluded.family",
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
                json.dumps(family, ensure_ascii=False) if family else None,
            ),
        )
        con.executemany(
            "INSERT INTO units(course,id,title,cefr,ord,requires,archived_at) "
            "VALUES(?,?,?,?,?,?,NULL) "
            # `title` is deliberately not overwritten when the unit was renamed
            # here: the course files have no `unit.yaml` in most courses, so the
            # incoming title is usually empty and would wipe the name someone
            # typed. `cefr`, `ord` and `requires` still come from the files,
            # which are the only place they are authored.
            "ON CONFLICT(course,id) DO UPDATE SET "
            "title=CASE WHEN units.edited_at IS NULL THEN excluded.title ELSE units.title END,"
            "cefr=excluded.cefr,ord=excluded.ord,requires=excluded.requires,archived_at=NULL",
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
        # A unit made or renamed here has no directory to be found in, so an
        # import must not conclude it has gone. Third time this rule has been
        # needed -- notes, then cards, now units -- and it is the same rule each
        # time: whoever created a thing owns it, and an import leaves it alone.
        live = {u.id for u in units}
        stale = [
            r["id"]
            for r in con.execute(
                "SELECT id FROM units WHERE course = ? AND archived_at IS NULL "
                "AND edited_at IS NULL",
                (course.id,),
            )
            if r["id"] not in live
        ]
        con.executemany(
            "UPDATE units SET archived_at = ? WHERE course = ? AND id = ?",
            [(stamp, course.id, i) for i in stale],
        )


def _sync_facet_config(con: sqlite3.Connection, course: str, facets: Facets) -> None:
    """The axes and their declared values, replaced wholesale."""
    with con:
        con.execute("DELETE FROM facet_values WHERE course = ?", (course,))
        con.execute("DELETE FROM facet_axes WHERE course = ?", (course,))
        con.executemany(
            "INSERT INTO facet_axes(course,axis,title,ordered,catch_all,max_per_note,ord) "
            "VALUES(?,?,?,?,?,?,?)",
            [
                (
                    course,
                    name,
                    json.dumps(axis.title, ensure_ascii=False),
                    int(axis.ordered),
                    int(axis.catch_all),
                    axis.max_per_note,
                    i,
                )
                for i, (name, axis) in enumerate(facets.axes.items())
            ],
        )
        con.executemany(
            "INSERT INTO facet_values(course,axis,value,ord) VALUES(?,?,?,?)",
            [
                (course, name, value, i)
                for name, axis in facets.axes.items()
                for i, value in enumerate(axis.values)
            ],
        )


def _backfill_buckets(con: sqlite3.Connection) -> int:
    """
    Give a bucket to any state that has none.

    A column added by a migration starts NULL, and `COALESCE(bucket, 'new')`
    would then report every card a learner has ever answered as new -- a
    catalogue that is confidently wrong about their own progress. Backfilling in
    SQL was the alternative and is worse: it would put a second definition of
    "mature" in a CASE expression, which is the exact shape of the bug ADR-0002
    records.

    Cheap because it only ever touches rows that are missing one, so it is a
    no-op on every start after the first.
    """
    rows = con.execute("SELECT * FROM card_state WHERE bucket IS NULL").fetchall()
    if not rows:
        return 0
    with con:
        con.executemany(
            "UPDATE card_state SET bucket = ? WHERE user_id = ? AND card_id = ?",
            [(bucket_of(_row_to_state(r)), r["user_id"], r["card_id"]) for r in rows],
        )
    return len(rows)


def facets_from_db(con: sqlite3.Connection, course: str) -> Facets:
    """
    Reconstruct a course's axes from the database.

    Needed because the database owns the material (ADR-0006): a tag edited here
    has to be re-filed without the course files being present, and reading them
    would file it under what the file still says rather than what the note now
    says.
    """
    axes: dict[str, FacetAxis] = {}
    values: dict[str, list[str]] = {}
    for row in con.execute(
        "SELECT axis, value FROM facet_values WHERE course = ? ORDER BY axis, ord", (course,)
    ):
        values.setdefault(row["axis"], []).append(row["value"])
    for row in con.execute("SELECT * FROM facet_axes WHERE course = ? ORDER BY ord", (course,)):
        axes[row["axis"]] = FacetAxis(
            values=tuple(values.get(row["axis"], ())),
            title=json.loads(row["title"]),
            ordered=bool(row["ordered"]),
            catch_all=bool(row["catch_all"]),
            max_per_note=row["max_per_note"],
        )
    aliases = {
        r["old"]: r["new"]
        for r in con.execute("SELECT old, new FROM tag_aliases WHERE course = ?", (course,))
    }
    row = con.execute("SELECT family FROM courses WHERE id = ?", (course,)).fetchone()
    family = FamilySpec(**json.loads(row["family"])) if row and row["family"] else None
    return Facets(axes=axes, aliases=aliases, family=family)


def reclassify(con: sqlite3.Connection, course: str | None = None) -> int:
    """
    Re-file every note, and re-bucket every card.

    Run after editing tags, or after changing a bucketing threshold -- both leave
    denormalised values describing a world that has moved.
    """
    if course is None:
        row = con.execute("SELECT id FROM courses LIMIT 1").fetchone()
        course = row["id"] if row else ""
    _rebuild_note_facets(con, course, facets_from_db(con, course))
    states = all_states(con)
    with con:
        con.executemany(
            "UPDATE card_state SET bucket = ? WHERE user_id = ? AND card_id = ?",
            [(bucket_of(cs), cs.user_id, cs.card_id) for cs in states.values()],
        )
    return len(states)


def _rebuild_note_facets(con: sqlite3.Connection, course: str, facets: Facets) -> None:
    """
    Classify every live note's tags onto axes.

    Derived from the `notes` rows rather than from the incoming course files,
    which matters under ADR-0006: a note edited here keeps its own tags, and
    classifying the file's version would file it under something it no longer
    says. The database is the owner, so the database is what gets read.

    Values not declared on any axis still land here when a catch-all exists, so
    adding a topic is an edit to a note rather than to a course's configuration.
    """
    with con:
        con.execute("DELETE FROM note_facets")
        if not facets.axes:
            return
        rows = con.execute(
            "SELECT id, tags FROM notes WHERE course = ? AND archived_at IS NULL", (course,)
        ).fetchall()
        pairs = [
            (r["id"], axis, value)
            for r in rows
            for axis, values in classify(json.loads(r["tags"]), facets)[0].items()
            for value in values
        ]
        con.executemany(
            "INSERT OR IGNORE INTO note_facets(note_id,axis,value) VALUES(?,?,?)", pairs
        )


@dataclass(frozen=True, slots=True)
class SyncReport:
    """What an import did. `conflicted` is the part a person has to look at."""

    notes: int
    cards: int
    added: int = 0
    updated: int = 0
    archived: int = 0
    #: Notes that had left the files and have come back. Counted apart from
    #: `updated` because nothing about them changed -- they returned.
    restored: int = 0
    #: Notes changed at the source *and* edited here. Neither version is lost and
    #: neither is chosen: the local one stays, and the id is reported so someone
    #: can decide. Silently picking a winner is the one behaviour that would make
    #: this unsafe to run.
    conflicted: tuple[str, ...] = field(default_factory=tuple)


def merge_decision(row: sqlite3.Row | None, digest: str) -> str:
    """
    What an import should do with one note: add, unchanged, update or conflict.

    Pulled out so the preview and the import itself cannot disagree. They are
    the same question asked twice -- once to show a person what will happen, once
    to make it happen -- and two copies would eventually answer it differently,
    which is precisely the failure a preview exists to prevent.
    """
    if row is None:
        return "add"
    if row["content_hash"] == digest:
        # Unchanged by hash, but not necessarily a no-op: a note that left the
        # files and has come back is restored. Saying "nothing will happen" and
        # then restoring it is the preview/import disagreement this function
        # exists to make impossible, so the case is named rather than folded in.
        if row["archived_at"] and row["edited_at"] is None:
            return "restore"
        return "unchanged"
    if row["edited_at"] is not None:
        return "conflict"
    return "update"


@dataclass(frozen=True, slots=True)
class Clash:
    """One note changed in the files and edited here, with both versions."""

    note_id: str
    file: dict[str, Any]
    mine: dict[str, Any]


def preview_import(con: sqlite3.Connection, result: LoadResult) -> tuple[SyncReport, list[Clash]]:
    """
    What an import would do, without doing any of it.

    Reads only. An import that can overwrite work should be answerable before it
    runs, and the answer has to come from the same decision the run will make.
    """
    existing = {
        r["id"]: r
        for r in con.execute("SELECT id, content_hash, edited_at, archived_at, fields FROM notes")
    }
    added = updated = restored = 0
    clashes: list[Clash] = []
    seen: set[str] = set()
    for n in result.notes:
        seen.add(n.id)
        row = existing.get(n.id)
        decision = merge_decision(row, _content_hash(n))
        if decision == "add":
            added += 1
        elif decision == "update":
            updated += 1
        elif decision == "restore":
            restored += 1
        elif decision == "conflict" and row is not None:
            clashes.append(Clash(n.id, dict(n.fields), json.loads(row["fields"])))
    gone = [i for i, r in existing.items() if i not in seen and r["archived_at"] is None]
    return (
        SyncReport(
            notes=len(result.notes),
            cards=len(result.cards),
            added=added,
            updated=updated,
            archived=len(gone),
            restored=restored,
            conflicted=tuple(c.note_id for c in clashes),
        ),
        clashes,
    )


def sync(
    con: sqlite3.Connection,
    result: LoadResult,
    *,
    now: datetime | None = None,
    take_file: frozenset[str] | set[str] | None = None,
) -> SyncReport:
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
        _sync_course(
            con,
            result.course,
            result.units,
            stamp,
            family=result.facets.family.model_dump() if result.facets.family else None,
        )
        _sync_facet_config(con, result.course.id, result.facets)

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
                # Nothing to write. Except: material that was archived because it
                # left the files and has now come back is a return, not an edit,
                # and it goes back into circulation on exactly the schedule it
                # left with.
                #
                # `edited_at` is what separates that from a deliberate removal. A
                # note taken out in the app is still in the course files -- that
                # is the ordinary case, not a rare one -- so un-archiving on the
                # strength of the file being there would undo every deletion on
                # the next import, and the learner would watch material they had
                # removed reappear with no explanation.
                if row["archived_at"] and row["edited_at"] is None:
                    con.execute(
                        "UPDATE notes SET archived_at = NULL, updated_at = ? WHERE id = ?",
                        (stamp, n.id),
                    )
            elif row["edited_at"] is not None and n.id not in (take_file or ()):
                conflicted.append(n.id)
            else:
                # `edited_at` is cleared when the file wins over a local edit:
                # the note now says exactly what the file says, so leaving it
                # marked edited-here would raise the same conflict again on
                # every future import, forever.
                con.execute(
                    "UPDATE notes SET course=?,unit=?,notetype=?,ord=?,tags=?,lesson=?,"
                    "fields=?,csum=?,origin=?,content_hash=?,updated_at=?,archived_at=NULL,"
                    "edited_at=NULL WHERE id=?",
                    (*values, stamp, n.id),
                )
                updated += 1

        gone = [i for i, r in existing.items() if i not in seen and r["archived_at"] is None]
        con.executemany("UPDATE notes SET archived_at = ? WHERE id = ?", [(stamp, i) for i in gone])

        # Whoever owns a note owns its cards.
        #
        # For a note edited here, the row is the truth and the file is not, so
        # its cards must be expanded from the row -- which `material._reexpand`
        # already does on every edit. Recomputing them from the file undoes that
        # work, and does it invisibly: giving a `vocab` note an `audio` field
        # creates `#listen`, and the reload at the end of the same request took
        # it straight back out again. Archiving a note was worse -- the file
        # expansion upserted its cards with `archived_at = NULL`, leaving cards
        # live whose note has gone, which `/api/session` drops in silence while
        # `owed_count()` goes on counting them.
        #
        # `conflicted` is a subset of this: a conflict is one reason a row
        # disagrees with its file, and a plain local edit is the ordinary one.
        held = set(conflicted) | {
            r["id"] for r in con.execute("SELECT id FROM notes WHERE edited_at IS NOT NULL")
        }
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

        _rebuild_note_facets(con, course, result.facets)

    _backfill_buckets(con)

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
            "interval,seen,correct,wrong,lapses,retired_at,retired_reason,suspended_at,"
            "bucket) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(user_id, card_id) DO UPDATE SET "
            "algo=excluded.algo, algo_version=excluded.algo_version, "
            "state=excluded.state, due=excluded.due, last=excluded.last, "
            "interval=excluded.interval, seen=excluded.seen, correct=excluded.correct, "
            "wrong=excluded.wrong, lapses=excluded.lapses, "
            "retired_at=excluded.retired_at, retired_reason=excluded.retired_reason, "
            "suspended_at=excluded.suspended_at, bucket=excluded.bucket",
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
                # Denormalised on every write, from the one definition in
                # `core.buckets`. Nothing recomputes it in a query.
                bucket_of(cs),
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
