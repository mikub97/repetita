"""
The daily session: what to study today, in what order.

Three sources, in priority order:

1. **Due** -- everything the schedule says is owed, most overdue first.
2. **New** -- introductions, filtered by a gate, and WOVEN INTO the due cards
   rather than queued behind them.
3. **Consolidation** -- the weakest material, ahead of schedule, and only once
   the first two are exhausted.

Every constant here has a row in `docs/tuning.md` saying which symptom it treats
and, where one exists, the measurement that set it. They are conclusions from a
year of real use, not defaults. If you add one here, add it there too, or it may
as well not exist.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

from ..core.types import Rating
from ..store.cards import CardState, all_states

# --- tuning ---------------------------------------------------------------

# How fast new material is allowed in. NOT a daily count: a count is blind --
# identical on a day when everything sticks and a day when nothing does. This
# opens the tap while recent answers hold up and closes it on evidence of
# overload, which also means it regulates itself once an intensive course ends.
GATE_WINDOW = 20
GATE_THRESHOLD = 0.75
GATE_MIN_ANSWERS = 8

# Material from a recent lesson jumps the gate entirely. The cap is what stops a
# forty-word lesson landing in one evening; without it "exempt from the gate"
# would just be "no gate at all" on lesson days.
LESSON_FRESH_DAYS = 3
LESSON_INTRO_CAP = 12

# One new card after every NEW_EVERY owed ones. Straight concatenation was the
# bug this replaces: with 45 cards owed and a batch capped at 40, that day's
# lesson did not appear in the batch at all. The debt is neither reduced nor
# deferred by weaving -- only reordered.
NEW_EVERY = 3

# A day counts as done at zero owed OR at this many answers. The second route
# exists because with a real backlog the first can be unreachable, and a streak
# that can never move measures nothing.
DAILY_TARGET = 30

BATCH = 40
MATURE_DAYS = 21


def gate_open(ratings: list[Rating]) -> bool:
    """
    Should new material be introduced right now?

    Too little evidence means open: the gate is a brake for evidence of overload,
    not a hurdle to clear before starting.
    """
    if len(ratings) < GATE_MIN_ANSWERS:
        return True
    recent = ratings[:GATE_WINDOW]
    return sum(1 for r in recent if r.passed) / len(recent) >= GATE_THRESHOLD


def lesson_is_fresh(lesson: str | None, today: date, days: int = LESSON_FRESH_DAYS) -> bool:
    """
    Is this material recent enough to jump the gate?

    Material with no lesson date is never fresh -- that is the entire back
    catalogue, and letting it through a closed gate would empty the exemption of
    meaning. A lesson dated in the future counts as fresh: writing tomorrow's
    date into a file is a statement about what is current, and second-guessing it
    would just be surprising.
    """
    if not lesson:
        return False
    try:
        return (today - date.fromisoformat(lesson)).days <= days
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class QueueCard:
    card_id: str
    note_id: str
    unit: str
    ord: int
    lesson: str | None


@dataclass(frozen=True, slots=True)
class Session:
    cards: list[str] = field(default_factory=list)
    has_more: bool = False
    #: True when the owed and new work is done and only reinforcement is left --
    #: used to tell the learner where the plan ends and extra begins.
    consolidating: bool = False


def scheduled_cards(con: sqlite3.Connection) -> list[QueueCard]:
    """
    Every card in the queue, in content order.

    The `ORDER BY` is load-bearing. `build_session` sorts the debt by due date
    and Python's sort is stable, so cards owed on the *same* day -- which is most
    of a real backlog, not an edge case -- come out in the order this function
    returned them in. Without an `ORDER BY` that is whichever way the planner
    drives the join: stable for one database and one SQLite build, but free to
    change under an upgrade, an added index or an `ANALYZE`, with nothing in the
    app able to explain why the day's backlog now arrives in a different order.

    Content order, and not another total order, because it is the order the
    course itself lays the material out in, and because `introduction_order`
    already breaks its ties the same way: one notion of "next" in this module
    rather than two. `c.id` closes it -- `(unit, ord)` is not unique, since `ord`
    counts within a file and a note can produce several cards -- and it is a
    total tie-break precisely because ids here are unique and never change
    (CLAUDE.md rule 1). Sorting by due date is left in `build_session`: schedule
    is progress, and this query reads content.
    """
    rows = con.execute(
        "SELECT c.id, c.note_id, n.unit, n.ord, n.lesson "
        "FROM cards c JOIN notes n ON n.id = c.note_id WHERE c.scheduled = 1 "
        "ORDER BY n.unit, n.ord, c.id"
    )
    return [QueueCard(r["id"], r["note_id"], r["unit"], r["ord"], r["lesson"]) for r in rows]


def introduction_order(cards: list[QueueCard], states: dict[str, CardState]) -> list[str]:
    """
    Every not-yet-answered card, in the exact order it would be introduced.

    Lesson date first, newest lesson first, with the undated back catalogue after
    every dated one -- which is what makes today's material arrive today. The
    gate is NOT applied here; see `gated_introductions`.
    """
    pool = [c for c in cards if (s := states.get(c.card_id)) is None or (s.is_new and s.is_active)]

    def key(c: QueueCard) -> tuple[int, int, str, int, str]:
        if c.lesson:
            try:
                return (0, -date.fromisoformat(c.lesson).toordinal(), c.unit, c.ord, c.card_id)
            except ValueError:
                pass
        return (1, 0, c.unit, c.ord, c.card_id)

    return [c.card_id for c in sorted(pool, key=key)]


def gated_introductions(
    ordered: list[str],
    cards: list[QueueCard],
    ratings: list[Rating],
    today: date,
    lesson_introduced_today: int = 0,
) -> list[str]:
    """
    Apply the gate, with the lesson exemption.

    Gate open: everything flows, with no daily quota. Gate shut: only material
    from a recent lesson gets through, and at most `LESSON_INTRO_CAP` of it per
    day. The exemption exists because the gate was measured shutting on days when
    accuracy sat around 65%, which is most days early on -- and the one thing it
    must never hold back is the lesson just attended.

    `lesson_introduced_today` is what the *lesson* has already spent, not every
    card met for the first time today. Nothing but lesson material may spend a
    budget whose only job is to stop a forty-word lesson landing in one evening.
    """
    if gate_open(ratings):
        return ordered
    budget = LESSON_INTRO_CAP - lesson_introduced_today
    if budget <= 0:
        return []
    lessons = {c.card_id: c.lesson for c in cards}
    fresh = [cid for cid in ordered if lesson_is_fresh(lessons.get(cid), today)]
    return fresh[:budget]


def weave(due: list[str], new: list[str], every: int = NEW_EVERY) -> list[str]:
    """
    One new card after every `every` owed ones.

    The debt is not reduced or postponed by this. Every owed card is still in the
    list, in the same relative order; only the position of new material changes.
    """
    if not new:
        return list(due)
    out: list[str] = []
    pending = list(new)
    for i, card in enumerate(due, start=1):
        out.append(card)
        if i % every == 0 and pending:
            out.append(pending.pop(0))
    out.extend(pending)
    return out


def bury_siblings(queue: list[str], cards: list[QueueCard]) -> list[str]:
    """
    Keep at most one card per note in a session, deferring the rest.

    Required by the note->card model (ADR-0001): three cards from one note in one
    session is near-worthless as evidence, because the second and third are
    answered from the first rather than from memory.
    """
    note_of = {c.card_id: c.note_id for c in cards}
    seen: set[str] = set()
    kept, buried = [], []
    for cid in queue:
        note = note_of.get(cid, cid)
        if note in seen:
            buried.append(cid)
        else:
            seen.add(note)
            kept.append(cid)
    return kept + buried


def build_session(
    con: sqlite3.Connection, today: date, limit: int = BATCH, *, ratings: list[Rating] | None = None
) -> Session:
    from ..store.reviews import lesson_first_seen_on, recent_ratings

    cards = scheduled_cards(con)
    states = all_states(con)
    grades = recent_ratings(con, GATE_WINDOW) if ratings is None else ratings

    due = [c.card_id for c in cards if (s := states.get(c.card_id)) and s.is_due(today)]
    due.sort(key=lambda cid: states[cid].due or "")

    # Only fresh-lesson introductions are charged to the lesson budget -- the
    # same window `lesson_is_fresh` uses, so what spends the budget is exactly
    # what the budget is for.
    spent = lesson_first_seen_on(con, today, since=today - timedelta(days=LESSON_FRESH_DAYS))
    picked = gated_introductions(introduction_order(cards, states), cards, grades, today, spent)

    consolidation: list[str] = []
    if not due and not picked:
        # Not a top-up of already-mastered material: that is what made mastered
        # cards keep reappearing in an earlier design. Only things genuinely not
        # known yet can show up here.
        weak = [
            (cid, s)
            for cid, s in states.items()
            if s.is_active and not s.is_new and s.interval < MATURE_DAYS
        ]
        weak.sort(key=lambda kv: (-kv[1].lapses, kv[1].interval))
        consolidation = [cid for cid, _ in weak]

    queue = bury_siblings(weave(due, picked) + consolidation, cards)
    return Session(
        cards=queue[:limit],
        has_more=len(queue) > limit,
        consolidating=bool(consolidation) and not due and not picked,
    )


# --- counters: one number per idea ----------------------------------------


def owed_count(con: sqlite3.Connection, today: date) -> int:
    """
    The debt, and nothing else.

    Deliberately not the size of the session batch: that is a slice capped at
    `BATCH`, so on a day with a lot of material it pins at 40 and stops moving
    while the per-card counter goes on counting down. Two numbers for one idea,
    disagreeing.
    """
    states = all_states(con)
    return sum(1 for c in scheduled_cards(con) if (s := states.get(c.card_id)) and s.is_due(today))


def day_done(con: sqlite3.Connection, today: date) -> bool:
    from ..store.reviews import count_on

    return owed_count(con, today) == 0 or count_on(con, today) >= DAILY_TARGET


def forecast(con: sqlite3.Connection, today: date, days: int = 14) -> list[int]:
    """Cumulative owed count for each of the next `days` days."""
    states = all_states(con)
    known = {c.card_id for c in scheduled_cards(con)}
    return [
        sum(
            1
            for cid, s in states.items()
            if cid in known and s.is_due(today + timedelta(days=offset))
        )
        for offset in range(days)
    ]
