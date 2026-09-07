"""
Reading the predecessor's database.

The app this engine was extracted from stored one row per *exercise* in `items`,
one scheduling record per exercise in `progress`, and one row per answer in
`reviews`. This module turns that into notes, cards, `card_state` and
`review_log` without changing a single thing about what happened.

Three decisions shape everything here.

**Content comes from the predecessor's database, not from its YAML.** The
`items` table is the pool *after* the old validator ran, so it is exactly the
material that was being practised -- which is the only material a parity test can
say anything about. Re-parsing the YAML would re-run a different validator over
the same files and could produce a different pool, silently, on the one code path
where a difference is hardest to notice.

**History is copied, never recomputed.** No answer is replayed through a
scheduler. `card_state` is transcribed from `progress` as it stood, including the
six schedules that the predecessor's HARD bug reset. That is ADR-0004, and it is
the reason this importer writes `review_log` and `card_state` directly instead of
going through `record_answer`: `record_answer` would apply *today's* rules to
yesterday's answers, which is precisely the corrective rescheduling that was
decided against.

**Nothing is dropped for being inconvenient.** History whose exercise no longer
exists is still imported. The store is built for that -- `sync` leaves
`card_state` alone, so a card whose note has vanished keeps its record -- and 73
of the 454 real answers are of exactly this kind.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from ..content.loader import LoadResult, expand_cards
from ..content.models import Card, Course, LanguageSpec, LicenseSpec, Note, Problem
from ..content.notetypes import BUILTIN
from ..content.validate import check
from ..core.types import Rating
from ..srs import sm2
from ..store.cards import DEFAULT_USER, CardState, save_state, sync

#: Where a hub directory keeps its database. Passing the file directly is also
#: accepted, and is how you point this at a snapshot rather than a live study
#: database.
DB_IN_HUB = Path("data") / "roda.db"

REQUIRED_TABLES = ("items", "progress", "reviews")

#: The predecessor's exercise types, mapped to note types.
#:
#: `ordem` is deliberately absent. It was never a type -- it was a way of asking,
#: a scrambled sentence to be rebuilt, declared as a type only because the old
#: model had nowhere else to put it (ADR-0001). It becomes the `wordbank` form,
#: which is a property of how a card is presented, so nothing imports as it.
NOTETYPE_OF = {
    "gap": "gap",
    "cloze": "gap",
    "choice": "gap",
    "traducao": "sentence",
    "transformacao": "transform",
    "chunk": "phrase",
}

#: The card that carries the history.
#:
#: Every note type reachable from the predecessor has exactly one template, so
#: "the production card" is not a choice between siblings here -- it is the only
#: card the note has. It is named explicitly anyway, because it is also the id
#: used for history whose exercise no longer exists, where no card can be
#: generated to ask.
PRODUCTION_TEMPLATE = {"gap": "fill", "sentence": "produce", "transform": "apply", "phrase": "say"}

#: Legacy payload key -> note field, per note type.
#:
#: `source` (the passage sentence an exercise was cut from) has no field of its
#: own. It was an after-the-answer field in the predecessor, so folding it into
#: `explain` preserves both the text and its visibility; a collision with an
#: existing `explain` is reported rather than resolved.
FIELD_MAP: dict[str, dict[str, str]] = {
    "gap": {
        "prompt": "prompt",
        "answers": "answers",
        "cue": "cue",
        "hint": "hint",
        "translation": "translation",
        "explain": "explain",
        "options": "options",
        "distractors": "distractors",
        "source": "explain",
    },
    "sentence": {
        "prompt": "prompt",
        "answers": "answers",
        "translation": "translation",
        "explain": "explain",
    },
    "transform": {
        "prompt": "prompt",
        "instruction": "instruction",
        "answers": "answers",
        "translation": "translation",
        "explain": "explain",
    },
    "phrase": {
        "situation": "situation",
        "prompt": "situation",
        "translation": "translation",
        "target": "target",
        "explain": "explain",
    },
}

#: Keys that carry structure rather than content, and are read elsewhere.
STRUCTURAL = frozenset({"id", "type", "track", "topic", "level", "lesson", "ord"})

#: Fields declared `text_list` by the note types above.
LIST_FIELDS = frozenset({"answers", "options", "distractors"})

#: The predecessor's 0/2/3/5 quality scale, in the four grades this engine speaks.
GRADE_MAP = {0: Rating.AGAIN, 2: Rating.HARD, 3: Rating.GOOD, 5: Rating.EASY}

#: Where an answer was given. Translated because the project language is English;
#: an unrecognised value is carried through untouched rather than flattened.
MODE_MAP = {"sessao": "session", "ensaio": "rehearse", "reforco": "consolidate"}

#: How a card left the queue: earned over months, or claimed by the learner.
#: Both leave the queue, and the difference matters when a gap turns up later.
RETIRED_REASON_MAP = {"ganho": "earned", "declarado": "declared"}

#: Written into `review_log.algo` for every imported answer.
#:
#: Not `sm2`: these answers were scheduled under the predecessor's rules, where
#: HARD was handled identically to AGAIN. Recording that is what keeps ADR-0004's
#: promise that the six affected cards stay findable --
#: `SELECT card_id FROM review_log WHERE rating = 2 AND algo = 'sm2-legacy'`.
LEGACY_ALGO = "sm2-legacy"

#: `card_state.algo`, by contrast, is the live backend. The predecessor's record
#: has exactly the five scalars `sm2` keeps, so the next answer continues the
#: schedule rather than restarting it.
STATE_ALGO = sm2.NAME

#: The predecessor did not record which form an exercise was asked in.
UNKNOWN_FORM = "unknown"

DEFAULT_COURSE_ID = "pt-br-from-pl"


# --- opening the source ---------------------------------------------------


def resolve_source(source: Path | str) -> Path:
    """A hub directory, or the database inside one."""
    path = Path(source)
    return path / DB_IN_HUB if path.is_dir() else path


def open_legacy(source: Path | str) -> sqlite3.Connection:
    """Open the predecessor's database read-only, refusing anything else."""
    path = resolve_source(source)
    if not path.is_file():
        raise FileNotFoundError(f"no database at {path}")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    present = {r["name"] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if missing := [t for t in REQUIRED_TABLES if t not in present]:
        con.close()
        raise ValueError(f"{path} is not a hub database: no table {', '.join(missing)}")
    return con


# --- the plan -------------------------------------------------------------


@dataclass
class Plan:
    """Everything the import would do, computed before anything is written."""

    notes: list[Note] = field(default_factory=list)
    cards: list[Card] = field(default_factory=list)
    states: list[CardState] = field(default_factory=list)
    #: review_log rows, in the column order used by `_INSERT_REVIEW`.
    reviews: list[tuple[Any, ...]] = field(default_factory=list)
    #: Notes held back from the pool because they give away their own answer.
    #: Their history is still imported -- see ARCHITECTURE.md invariant 3.
    quarantined: list[Problem] = field(default_factory=list)
    #: Content that had nowhere to go, one line each.
    unmapped: list[str] = field(default_factory=list)
    #: History that could not be attached to a card id at all.
    orphaned: list[str] = field(default_factory=list)
    #: History whose exercise is gone, attached to the card id it would have had.
    inferred: list[str] = field(default_factory=list)

    states_new: int = 0
    reviews_skipped: int = 0
    course_id: str = DEFAULT_COURSE_ID

    def as_load_result(self) -> LoadResult:
        return LoadResult(course=_course(self.course_id), notes=self.notes, cards=self.cards)


def _course(course_id: str) -> Course:
    """
    A stand-in course row for `sync`.

    Only the id reaches the database; the rest exists so the model validates. The
    real course file supersedes this at cutover, and because content is a cache
    that costs nothing.
    """
    return Course(
        id=course_id,
        l2=LanguageSpec(code="pt", variant="pt-BR"),
        l1=LanguageSpec(code="pl"),
        license=LicenseSpec(name="CC BY-SA 4.0"),
    )


def _fields(
    payload: dict[str, Any], notetype: str, key: str, unmapped: list[str]
) -> dict[str, Any]:
    """Legacy payload -> note fields, reporting anything with nowhere to go."""
    mapping = FIELD_MAP[notetype]
    out: dict[str, Any] = {}
    for name, value in payload.items():
        if name in STRUCTURAL:
            continue
        target = mapping.get(name)
        if target is None:
            unmapped.append(f"{key}: no field for {name!r} on notetype {notetype!r}")
            continue
        if out.get(target):
            unmapped.append(f"{key}: {name!r} would overwrite {target!r}; kept the original")
            continue
        if target in LIST_FIELDS:
            items = value if isinstance(value, list) else [value]
            out[target] = [str(v) for v in items if str(v).strip()]
        elif value is not None and str(value).strip():
            out[target] = str(value)
    return out


def _note(row: sqlite3.Row, unmapped: list[str]) -> Note | None:
    notetype = NOTETYPE_OF.get(row["type"])
    if notetype is None:
        unmapped.append(f"{row['key']}: unknown exercise type {row['type']!r}")
        return None
    try:
        payload = json.loads(row["payload"])
    except json.JSONDecodeError as e:
        unmapped.append(f"{row['key']}: unreadable payload ({e})")
        return None

    lesson: date | None = None
    if raw := row["lesson"]:
        try:
            lesson = date.fromisoformat(str(raw))
        except ValueError:
            unmapped.append(f"{row['key']}: lesson {raw!r} is not a date")

    # track, topic and level all become tags. The predecessor kept them in three
    # columns because it had three uses for them; here a tag is the general form
    # of "something true about this note that a course may weight or filter on",
    # and `tag_weights` in course.yaml is already keyed by track.
    tags = tuple(dict.fromkeys(t for t in (row["track"], row["topic"], row["level"]) if t))

    return Note(
        id=row["key"],
        notetype=notetype,
        fields=_fields(payload, notetype, row["key"], unmapped),
        tags=tags,
        lesson=lesson,
        unit=row["pack"],
        ord=row["ord"],
        origin=f"hub:{row['pack']}",
    )


def _card_id(key: str, notetype: str) -> str:
    return f"{key}#{PRODUCTION_TEMPLATE[notetype]}"


def _infer_notetypes(rows: list[sqlite3.Row]) -> dict[str, str]:
    """
    The note type each pack is made of, where it is made of only one.

    History exists for sixteen exercises that have since been deleted from the
    content, so their type cannot be read -- but every one of them is from a pack
    whose surviving exercises are of a single type, which makes the card id they
    would have had a deduction rather than a guess. A mixed pack yields nothing
    and the history is reported instead.
    """
    by_pack: dict[str, Counter[str]] = {}
    for row in rows:
        if notetype := NOTETYPE_OF.get(row["type"]):
            by_pack.setdefault(row["pack"], Counter())[notetype] += 1
    return {pack: next(iter(c)) for pack, c in by_pack.items() if len(c) == 1}


def _state(row: sqlite3.Row, card_id: str) -> CardState:
    """
    One `progress` row, transcribed.

    The five scalars the predecessor's scheduler owned are exactly the five `sm2`
    keeps, so the state blob is a copy rather than a translation, and the next
    answer given in this engine continues the schedule instead of restarting it.
    """
    reason = row["retired_reason"]
    return CardState(
        card_id=card_id,
        algo=STATE_ALGO,
        algo_version=sm2.VERSION,
        state={
            "ease": float(row["ease"]),
            "interval": int(row["interval"]),
            "reps": int(row["reps"]),
            "lapses": int(row["lapses"]),
            "due": row["due"],
            "last": row["last"],
        },
        due=row["due"],
        last=row["last"],
        interval=int(row["interval"]),
        seen=int(row["seen"]),
        correct=int(row["correct"]),
        wrong=int(row["wrong"]),
        lapses=int(row["lapses"]),
        retired_at=row["retired_at"],
        retired_reason=RETIRED_REASON_MAP.get(reason, reason) if reason else None,
        suspended_at=row["suspended_at"],
    )


_INSERT_REVIEW = (
    "INSERT INTO review_log(user_id,card_id,rating,review_datetime,day,"
    "review_duration_ms,elapsed_days,algo,state_before,mode,form,answer) "
    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)"
)


def _instant(raw: str) -> str:
    """
    The stored moment, made timezone-aware.

    The predecessor wrote a naive local timestamp, so the offset is genuinely not
    recorded and UTC here is a declared assumption rather than a fact. Nothing
    depends on it: `day` is stored separately, is the learner's real calendar
    day, and is what every counter and streak reads. It is copied verbatim.
    """
    try:
        stamp = datetime.fromisoformat(raw)
    except ValueError:
        return raw
    return (stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)).isoformat()


def _elapsed(day: str, previous: str | None) -> float | None:
    if previous is None:
        return None
    try:
        return float((date.fromisoformat(day) - date.fromisoformat(previous)).days)
    except ValueError:
        return None


def build_plan(
    legacy: sqlite3.Connection,
    target: sqlite3.Connection,
    *,
    course_id: str = DEFAULT_COURSE_ID,
    user_id: int = DEFAULT_USER,
) -> Plan:
    """Work out everything that would change, without changing any of it."""
    plan = Plan(course_id=course_id)

    # Row order is preserved on purpose. The queue sorts due cards by date and
    # Python's sort is stable, so ties fall out in table order -- which is the
    # predecessor's order only if the notes go in the way they came out.
    items = list(legacy.execute("SELECT * FROM items ORDER BY rowid"))
    notetype_of_pack = _infer_notetypes(items)

    known: dict[str, str] = {}
    #: Exercises that are still in the source but could not be turned into a
    #: note. Their type is known and simply has no home, so deducing one from
    #: their pack would be inventing an answer to a question that is not open.
    unimportable: set[str] = set()
    for row in items:
        note = _note(row, plan.unmapped)
        if note is None:
            unimportable.add(row["key"])
            continue
        known[note.id] = note.notetype

        problems = check(note, BUILTIN[note.notetype])
        plan.unmapped.extend(str(p) for p in problems if not p.fatal)
        if any(p.fatal for p in problems):
            # Quarantined: never served, so it cannot be practised even if
            # something downstream ignores this report. Its history still
            # imports below -- dropping that would be losing the record of
            # answers that really were given.
            plan.quarantined.extend(p for p in problems if p.fatal)
            continue

        plan.notes.append(note)
        plan.cards.extend(expand_cards(note, BUILTIN[note.notetype]))

    # Reported once per exercise, not once per row that mentions it: a card with
    # thirty answers behind it is one deduction, and repeating it thirty times
    # would bury everything else in the diff.
    resolved: dict[str, str | None] = {}

    def card_for(key: str) -> str | None:
        if key in resolved:
            return resolved[key]
        if notetype := known.get(key):
            resolved[key] = _card_id(key, notetype)
        elif key in unimportable:
            plan.orphaned.append(f"{key}: still in the source, but its type does not map")
            resolved[key] = None
        elif notetype := notetype_of_pack.get(pack := key.rsplit(".", 1)[0]):
            plan.inferred.append(f"{key}: exercise gone; type taken from pack {pack!r}")
            resolved[key] = _card_id(key, notetype)
        else:
            plan.orphaned.append(f"{key}: exercise gone and its pack gives no type")
            resolved[key] = None
        return resolved[key]

    existing_states = {
        r["card_id"]
        for r in target.execute("SELECT card_id FROM card_state WHERE user_id = ?", (user_id,))
    }
    for row in legacy.execute("SELECT * FROM progress ORDER BY key"):
        if card_id := card_for(row["key"]):
            plan.states.append(_state(row, card_id))
            plan.states_new += card_id not in existing_states

    seen_before = {
        (r["card_id"], r["review_datetime"])
        for r in target.execute(
            "SELECT card_id, review_datetime FROM review_log WHERE user_id = ? AND algo = ?",
            (user_id, LEGACY_ALGO),
        )
    }
    last_day: dict[str, str] = {}
    for row in legacy.execute("SELECT * FROM reviews ORDER BY id"):
        card_id = card_for(row["key"])
        if card_id is None:
            continue
        instant = _instant(row["at"])
        day = row["day"]
        elapsed = _elapsed(day, last_day.get(card_id))
        last_day[card_id] = day
        if (card_id, instant) in seen_before:
            # Already imported. `(key, at)` is unique in the source, so this is
            # the whole of idempotency for the log -- and it is what lets the
            # command run again at cutover to pick up only the delta.
            plan.reviews_skipped += 1
            continue
        rating = GRADE_MAP.get(row["grade"])
        if rating is None:
            plan.unmapped.append(f"{row['key']}: unknown grade {row['grade']!r}")
            continue
        mode = row["mode"]
        plan.reviews.append(
            (
                user_id,
                card_id,
                int(rating),
                instant,
                day,
                row["ms"],
                elapsed,
                LEGACY_ALGO,
                # Not knowable. The predecessor logged no state snapshot, and
                # replaying to reconstruct one is impossible: its intervals were
                # fuzzed by +-5% from an unseeded generator. A null says so;
                # a recomputed guess would not.
                None,
                MODE_MAP.get(mode, mode),
                UNKNOWN_FORM,
                row["answer"],
            )
        )
    return plan


def scheduling(
    cards: list[Card], srs_flag: dict[str, bool], has_history: set[str]
) -> dict[str, bool]:
    """
    Which cards the queue may draw on.

    `srs: false` governed INTRODUCTION only in the predecessor: a song was never
    opened at you unprompted, but once you had answered it, it was scheduled like
    anything else. This engine has a single `scheduled` boolean where the
    predecessor had that distinction, so the faithful projection of one onto the
    other is "the pack introduces it, or it is already in play". It is stable
    across re-runs because history only ever grows.

    This is the one mapping in the importer that is an approximation rather than
    a transcription, and it is the reason `tests/test_parity.py` pins it.
    """
    return {
        card.id: srs_flag.get(card.note_id, True) or card.note_id in has_history for card in cards
    }


def apply_plan(plan: Plan, target: sqlite3.Connection, scheduled: dict[str, bool]) -> None:
    """Write the plan. Every step is an upsert or a de-duplicated append."""
    sync(target, plan.as_load_result())
    with target:
        target.executemany(
            "UPDATE cards SET scheduled = ? WHERE id = ?",
            [(int(v), cid) for cid, v in scheduled.items()],
        )
        target.executemany(_INSERT_REVIEW, plan.reviews)
    for state in plan.states:
        save_state(target, state)


# --- the command ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Report:
    plan: Plan
    scheduled_off: int
    dry_run: bool

    @property
    def counts(self) -> dict[str, int]:
        return {
            "notes": len(self.plan.notes),
            "cards": len(self.plan.cards),
            "card_state": len(self.plan.states),
            "review_log": len(self.plan.reviews),
        }


def import_hub(
    source: Path | str,
    target: sqlite3.Connection,
    *,
    course_id: str = DEFAULT_COURSE_ID,
    dry_run: bool = False,
    user_id: int = DEFAULT_USER,
) -> Report:
    """Bring one hub database's material and history into a repetita database."""
    legacy = open_legacy(source)
    try:
        plan = build_plan(legacy, target, course_id=course_id, user_id=user_id)
        has_history = {r["key"] for r in legacy.execute("SELECT key FROM progress WHERE seen > 0")}
        srs_flag = {r["key"]: bool(r["srs"]) for r in legacy.execute("SELECT key, srs FROM items")}
    finally:
        legacy.close()

    scheduled = scheduling(plan.cards, srs_flag, has_history)
    if not dry_run:
        apply_plan(plan, target, scheduled)
    return Report(
        plan=plan, scheduled_off=sum(1 for v in scheduled.values() if not v), dry_run=dry_run
    )


def render(report: Report, *, verbose: bool = False) -> str:
    """The diff a dry run prints. Same text after a real run, in the past tense."""
    plan = report.plan
    verb = "would add" if report.dry_run else "added"
    lines = [
        f"course {plan.course_id}",
        f"  notes       {len(plan.notes):>5}",
        f"  cards       {len(plan.cards):>5}  ({report.scheduled_off} not scheduled)",
        f"  card_state  {len(plan.states):>5}  ({plan.states_new} new, "
        f"{len(plan.states) - plan.states_new} updated)",
        f"  review_log  {len(plan.reviews):>5}  {verb}, {plan.reviews_skipped} already present",
    ]

    def section(title: str, entries: list[str]) -> None:
        if not entries:
            return
        lines.append("")
        lines.append(f"{title} ({len(entries)}):")
        shown = entries if verbose else entries[:10]
        lines.extend(f"  {e}" for e in shown)
        if len(entries) > len(shown):
            lines.append(f"  ... and {len(entries) - len(shown)} more (--verbose)")

    section("QUARANTINED -- imported as history, never served", [str(p) for p in plan.quarantined])
    section("history whose exercise is gone, card id deduced from its pack", plan.inferred)
    section("history that could not be attached to any card", plan.orphaned)
    section("content with nowhere to go", plan.unmapped)
    return "\n".join(lines)
