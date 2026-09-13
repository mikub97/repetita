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
from datetime import date, timedelta

from ..core.types import Rating
from ..store.cards import CardState, all_states
from ..store.users import DEFAULT_USER
from .ordering import order_debt, order_introductions
from .queue import UNPLACED, QueueCard, Session

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


def gate_open(ratings: list[Rating], threshold: float = GATE_THRESHOLD) -> bool:
    """
    Should new material be introduced right now?

    Too little evidence means open: the gate is a brake for evidence of overload,
    not a hurdle to clear before starting.

    `threshold` is a parameter rather than only a constant because it is one of
    the few dials whose right value is a fact about a person -- someone happy at
    60% correct is not someone else at 85%, and both are studying properly.
    """
    if len(ratings) < GATE_MIN_ANSWERS:
        return True
    recent = ratings[:GATE_WINDOW]
    return sum(1 for r in recent if r.passed) / len(recent) >= threshold


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


def scheduled_cards(
    con: sqlite3.Connection,
    course: str | None = None,
    *,
    user_id: int | None = None,
) -> list[QueueCard]:
    """
    Every card in the queue, in content order.

    `course` scopes it, and scoping it here is what scopes the whole module:
    `build_session`, `owed_count` and `forecast` all read the queue through this
    one function. `None` means every course in the database -- which is what the
    CLI and the parity tests want, and never what the serving path wants, since
    a session mixing two languages is not a session.

    `user_id` narrows it again, to the sets that account studies. Every account
    was being served every set in the course, so Karolina's session drew from
    Radek's material and Małgosia's. A set belongs to the lessons it came from,
    and a queue that ignores that is four people sharing one backlog.

    An account that has never chosen gets all of it, and that is why `studying`
    returns `None` rather than a list: no choice adds no clause, so a database
    that predates the table behaves exactly as it did. `user_id=None` means the
    same for a caller with no account in hand -- the CLI, the parity tests --
    rather than meaning "nobody".

    An account that has chosen *none* gets an empty queue, which is different
    and is a thing somebody can ask for by turning every set off.

    The `ORDER BY` is load-bearing. `build_session` sorts the debt by due date
    and Python's sort is stable, so cards owed on the *same* day -- which is most
    of a real backlog, not an edge case -- come out in the order this function
    returned them in. Without an `ORDER BY` that is whichever way the planner
    drives the join: stable for one database and one SQLite build, but free to
    change under an upgrade, an added index or an `ANALYZE`, with nothing in the
    app able to explain why the day's backlog now arrives in a different order.

    `u.ord` leads that key, and until recently nothing read it. This paragraph
    used to claim the order was "the order the course itself lays the material
    out in"; it was the alphabetical order of the *directory name*. `course.path`
    has been parsed since the first course and joined by nothing, so with
    `lesson:` set in 14 of 63 files in one course and in none at all in the other
    three, new material arrived in alphabetical order -- which in the Portuguese
    course put capoeira slang and the whole grammar ahead of the word for
    "table". The join is LEFT and the `COALESCE` deliberate: a unit can have no
    row here at all, and an INNER JOIN would drop its cards out of a query that
    `owed_count` and `forecast` also read, shrinking somebody's debt silently.

    `c.id` closes the key -- `(unit, ord)` is not unique, since `ord` counts
    within a file and a note can produce several cards -- and it is a total
    tie-break precisely because ids here are unique and never change (CLAUDE.md
    rule 1). Across courses (`course=None`, which is the CLI and the parity
    tests) the order is still arbitrary, because two courses have no common
    sequence and inventing one would be pretending they do. Sorting by due date
    is left in `build_session`: schedule is progress, and this query reads
    content.
    """
    mine: tuple[str, ...] | None = None
    if user_id is not None and course:
        # Only within a named course: "the sets I study" is a statement about
        # one course, and asking it of every course at once has no answer.
        from ..store.users import studying

        mine = studying(con, user_id, course)

    sql = (
        "SELECT c.id, c.note_id, c.template, n.unit, n.ord, n.lesson, "
        f"COALESCE(u.ord, {UNPLACED}) AS unit_ord "
        "FROM cards c JOIN notes n ON n.id = c.note_id "
        "LEFT JOIN units u ON u.course = n.course AND u.id = n.unit "
        "WHERE c.scheduled = 1 AND c.archived_at IS NULL "
    )
    params: tuple[object, ...] = ()
    if course:
        sql += "AND n.course = ? "
        params += (course,)
    if mine is not None:
        if not mine:
            # Chosen, and chosen nothing. `IN ()` is a syntax error in SQLite
            # and `IN (NULL)` silently matches nothing while looking like a bug,
            # so the empty case is answered here rather than in SQL.
            return []
        sql += f"AND n.unit IN ({','.join('?' for _ in mine)}) "
        params += mine
    rows = con.execute(sql + "ORDER BY unit_ord, n.unit, n.ord, c.id", params)
    return [
        QueueCard(
            r["id"],
            r["note_id"],
            r["unit"],
            r["ord"],
            r["lesson"],
            r["unit_ord"],
            r["template"],
        )
        for r in rows
    ]


def unseen(cards: list[QueueCard], states: dict[str, CardState]) -> list[QueueCard]:
    """The pool an introduction can be drawn from: never answered, not retired."""
    return [c for c in cards if (s := states.get(c.card_id)) is None or (s.is_new and s.is_active)]


def introduction_order(
    cards: list[QueueCard],
    states: dict[str, CardState],
    how: str = "lesson",
    *,
    today: date | None = None,
    templates: tuple[str, ...] = (),
    axis_rank: dict[str, int] | None = None,
    weight: dict[str, float] | None = None,
    seed: str = "",
) -> list[str]:
    """
    Every not-yet-answered card, in the exact order it would be introduced.

    The default is `lesson` -- freshest lesson first, then the course's own path
    -- which is what this function did when it did only one thing. The gate is
    NOT applied here; see `gated_introductions`.

    `today` is optional only so the default ordering keeps its old two-argument
    call, which a good deal of the test suite uses. `lesson` does not read it.
    """
    return order_introductions(
        unseen(cards, states),
        how,
        today=today or date.min,
        templates=templates,
        axis_rank=axis_rank,
        weight=weight,
        seed=seed,
    )


def gated_introductions(
    ordered: list[str],
    cards: list[QueueCard],
    ratings: list[Rating],
    today: date,
    lesson_introduced_today: int = 0,
    threshold: float = GATE_THRESHOLD,
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
    if gate_open(ratings, threshold):
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


def bury_siblings(queue: list[str], cards: list[QueueCard]) -> tuple[list[str], list[str]]:
    """
    Split a queue into at most one card per note, and the siblings held back.

    Required by the note->card model (ADR-0001): three cards from one note in one
    session is near-worthless as evidence, because the second and third are
    answered from the first rather than from memory.

    Held back means **not served today**, not "served later in the same queue".
    An earlier version moved siblings to the end of the list, which read as
    burying and was not: the whole queue ships in one batch, so both siblings
    still reached the learner in the same sitting -- the exact thing this
    prevents. A card held back today is due tomorrow, unchanged and unpenalised;
    over three days a three-card note is seen three times, once each.
    """
    note_of = {c.card_id: c.note_id for c in cards}
    seen: set[str] = set()
    kept: list[str] = []
    buried: list[str] = []
    for cid in queue:
        note = note_of.get(cid, cid)
        if note in seen:
            buried.append(cid)
        else:
            seen.add(note)
            kept.append(cid)
    return kept, buried


def _knob_int(value: object, fallback: int) -> int:
    """A knob is stored as JSON, so it arrives as whatever somebody wrote."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _knob_float(value: object, fallback: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def build_session(
    con: sqlite3.Connection,
    today: date,
    limit: int = BATCH,
    *,
    ratings: list[Rating] | None = None,
    course: str | None = None,
    user_id: int = DEFAULT_USER,
    introductions: str = "lesson",
    debt: str = "overdue",
    every: int = NEW_EVERY,
    threshold: float = GATE_THRESHOLD,
    consolidation: bool = True,
    templates: tuple[str, ...] = (),
    axis_rank: dict[str, int] | None = None,
    weight: dict[str, float] | None = None,
    seed: str = "",
    recipe: object | None = None,
) -> Session:
    """
    Today's queue, for one person.

    `user_id` reaches every read of a schedule below it. Without it this built
    the owner's queue for whoever asked -- which with one account was invisible
    and with four is somebody studying another person's due cards and writing
    answers against their own.

    Every keyword after it defaults to what this function did before any of them
    existed, so calling it the old way is not merely supported -- it is the same
    computation, and a test asserts that rather than assuming it.

    None of them can change *which* cards are owed. `introductions` and `debt`
    choose an order, `every` and `threshold` and `consolidation` choose how much
    flows; the debt itself is settled by the schedule and is not a preference.

    `recipe` is a `context.Recipe` and fills the same keywords in from a saved
    style, so the serving path has one thing to pass rather than nine. Passing
    both is allowed and the recipe wins: a caller holding one is the caller that
    knows what the learner asked for.
    """
    from ..store.reviews import lesson_first_seen_on, recent_ratings

    if recipe is not None:
        style = recipe.style  # type: ignore[attr-defined]
        knobs = style.knobs
        introductions = style.introductions
        debt = style.debt
        every = _knob_int(knobs.get("new_every"), NEW_EVERY)
        threshold = _knob_float(knobs.get("gate_threshold"), GATE_THRESHOLD)
        consolidation = bool(knobs.get("consolidation", True))
        templates = recipe.templates  # type: ignore[attr-defined]
        axis_rank = recipe.axis_rank  # type: ignore[attr-defined]
        weight = recipe.weight or weight  # type: ignore[attr-defined]
        seed = recipe.seed  # type: ignore[attr-defined]
        if limit == BATCH:
            # Only when the caller did not ask for a size: an explicit `limit` is
            # a fact about the request (a preview asking for twenty), and a knob
            # is a preference about a session. The request wins.
            limit = _knob_int(knobs.get("batch"), BATCH)

    cards = scheduled_cards(con, course, user_id=user_id)
    states = all_states(con, course=course, user_id=user_id)
    grades = (
        recent_ratings(con, GATE_WINDOW, course=course, user_id=user_id)
        if ratings is None
        else ratings
    )

    by_id = {c.card_id: c for c in cards}
    due = [c.card_id for c in cards if (s := states.get(c.card_id)) and s.is_due(today)]
    due = order_debt(
        due,
        debt,
        cards=by_id,
        due_on={cid: states[cid].due or "" for cid in due},
        interval={cid: states[cid].interval for cid in due},
        lapses={cid: states[cid].lapses for cid in due},
        templates=templates,
        weight=weight,
    )

    # Only fresh-lesson introductions are charged to the lesson budget -- the
    # same window `lesson_is_fresh` uses, so what spends the budget is exactly
    # what the budget is for.
    spent = lesson_first_seen_on(
        con,
        today,
        since=today - timedelta(days=LESSON_FRESH_DAYS),
        course=course,
        user_id=user_id,
    )
    picked = gated_introductions(
        introduction_order(
            cards,
            states,
            introductions,
            today=today,
            templates=templates,
            axis_rank=axis_rank,
            weight=weight,
            seed=seed,
        ),
        cards,
        grades,
        today,
        spent,
        threshold,
    )

    top_up: list[str] = []
    if consolidation and not due and not picked:
        # Not a top-up of already-mastered material: that is what made mastered
        # cards keep reappearing in an earlier design. Only things genuinely not
        # known yet can show up here.
        weak = [
            (cid, s)
            for cid, s in states.items()
            if s.is_active and not s.is_new and s.interval < MATURE_DAYS
        ]
        weak.sort(key=lambda kv: (-kv[1].lapses, kv[1].interval))
        top_up = [cid for cid, _ in weak]

    queue, buried = bury_siblings(weave(due, picked, every) + top_up, cards)
    return Session(
        cards=queue[:limit],
        has_more=len(queue) > limit,
        consolidating=bool(top_up) and not due and not picked,
        buried=len(buried),
    )


# --- counters: one number per idea ----------------------------------------


def owed_count(
    con: sqlite3.Connection,
    today: date,
    *,
    course: str | None = None,
    user_id: int = DEFAULT_USER,
) -> int:
    """
    The debt, and nothing else.

    Deliberately not the size of the session batch: that is a slice capped at
    `BATCH`, so on a day with a lot of material it pins at 40 and stops moving
    while the per-card counter goes on counting down. Two numbers for one idea,
    disagreeing.
    """
    states = all_states(con, course=course, user_id=user_id)
    return sum(
        1
        for c in scheduled_cards(con, course, user_id=user_id)
        if (s := states.get(c.card_id)) and s.is_due(today)
    )


def day_done(
    con: sqlite3.Connection,
    today: date,
    *,
    course: str | None = None,
    user_id: int = DEFAULT_USER,
    target: int = DAILY_TARGET,
) -> bool:
    from ..store.reviews import count_on

    return (
        owed_count(con, today, course=course, user_id=user_id) == 0
        or count_on(con, today, course=course, user_id=user_id) >= target
    )


def forecast(
    con: sqlite3.Connection,
    today: date,
    days: int = 14,
    *,
    course: str | None = None,
    user_id: int = DEFAULT_USER,
) -> list[int]:
    """Cumulative owed count for each of the next `days` days."""
    states = all_states(con, course=course, user_id=user_id)
    known = {c.card_id for c in scheduled_cards(con, course, user_id=user_id)}
    return [
        sum(
            1
            for cid, s in states.items()
            if cid in known and s.is_due(today + timedelta(days=offset))
        )
        for offset in range(days)
    ]
