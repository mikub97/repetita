"""
Sessions built from a priority list.

`daily` answers "what is owed, and what comes next in the course". This answers
"what is owed, and what does the learner want more of" -- the same debt, a
different choice about what to introduce alongside it.

The split is `daily`'s: pure functions that decide, and one thin shell that
touches the database. Nothing here reads a clock or invents randomness, so a
plan's effect can be reasoned about by reading it, and a preview of tomorrow
costs nothing.

What it does **not** change: the debt. Every owed card is still owed, in the same
order. A priority list decides which *new* material is woven in, and at what
share -- it cannot excuse you from review, because a plan that could would be a
way to avoid the only part that works.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from ..core.types import Rating
from ..store.cards import CardState, all_states
from ..store.plans import Plan, Priority
from .daily import (
    BATCH,
    NEW_EVERY,
    Session,
    bury_siblings,
    gated_introductions,
    introduction_order,
    scheduled_cards,
    weave,
)


#: A priority list is read as a Zipf curve: the top row gets about half again
#: what the second does, not a hair more. Dragging something to the top is a
#: strong statement, and a linear ramp makes it a weak one -- with ten rows,
#: linear gives the top 18% and the bottom 2%, which nobody experiences as
#: "this is what I want to work on".
def weights_from_ranks(
    priorities: tuple[Priority, ...] | list[Priority],
) -> dict[tuple[str, str], float]:
    """
    Turn an ordering into shares that sum to 1.

    An explicit `weight` overrides its rank; the rest of the list divides what is
    left, keeping their relative order. So a learner can pin "20% numbers" and go
    on dragging everything else around it.
    """
    if not priorities:
        return {}
    pinned = {(p.axis, p.value): p.weight for p in priorities if p.weight is not None}
    spare = max(0.0, 1.0 - sum(pinned.values()))
    free = [p for p in priorities if p.weight is None]

    raw = {(p.axis, p.value): 1.0 / (i + 1) for i, p in enumerate(free)}
    total = sum(raw.values())
    out = dict(pinned)
    if total:
        out.update({k: spare * v / total for k, v in raw.items()})
    return out


def allocate(
    sizes: dict[tuple[str, str], int], weights: dict[tuple[str, str], float], budget: int
) -> dict[tuple[str, str], int]:
    """
    Divide `budget` between buckets by weight, by largest remainder.

    Largest remainder rather than rounding each share independently: rounding
    loses or invents slots, and a session that asked for 10 cards and produced 9
    is the kind of off-by-one nobody investigates because it looks like a
    coincidence.

    A bucket never gets more than it holds, and what it cannot take flows to the
    others -- otherwise exhausting the top topic would shrink the whole session
    rather than moving effort down the list.
    """
    if budget <= 0 or not weights:
        return {}
    live = {k: w for k, w in weights.items() if sizes.get(k, 0) > 0 and w > 0}
    out: dict[tuple[str, str], int] = {k: 0 for k in live}

    remaining = budget
    while remaining > 0 and live:
        total = sum(live.values())
        exact = {k: remaining * w / total for k, w in live.items()}
        whole = {k: min(int(v), sizes[k] - out[k]) for k, v in exact.items()}
        given = sum(whole.values())
        for k, n in whole.items():
            out[k] += n
        remaining -= given

        if remaining > 0:
            # Hand out what rounding left over, biggest fractional part first.
            order = sorted(live, key=lambda k: (-(exact[k] - int(exact[k])), k))
            progressed = False
            for k in order:
                if remaining == 0:
                    break
                if out[k] < sizes[k]:
                    out[k] += 1
                    remaining -= 1
                    progressed = True
            if not progressed:
                break
        live = {k: w for k, w in live.items() if out[k] < sizes[k]}
    return {k: v for k, v in out.items() if v}


def bucket_cards(
    ordered: list[str],
    membership: dict[str, set[tuple[str, str]]],
    weights: dict[tuple[str, str], float],
) -> tuple[dict[tuple[str, str], list[str]], list[str]]:
    """
    Sort candidate cards into priority buckets, keeping content order.

    A card matching several priorities goes to the highest-weighted one it
    matches, and only there. Counting it in both would let one card satisfy two
    shares, and the mix would quietly stop matching the list.
    """
    buckets: dict[tuple[str, str], list[str]] = {k: [] for k in weights}
    rest: list[str] = []
    for card_id in ordered:
        keys = membership.get(card_id, set()) & set(weights)
        if not keys:
            rest.append(card_id)
            continue
        best = max(keys, key=lambda k: (weights[k], k))
        buckets[best].append(card_id)
    return buckets, rest


def matching(
    card_ids: list[str],
    membership: dict[str, set[tuple[str, str]]],
    weights: dict[tuple[str, str], float],
) -> list[str]:
    """Only the cards a plan actually asked for, in the order given."""
    if not weights:
        return list(card_ids)
    return [c for c in card_ids if membership.get(c, set()) & set(weights)]


def planned_introductions(
    ordered: list[str],
    membership: dict[str, set[tuple[str, str]]],
    priorities: tuple[Priority, ...],
    budget: int,
    *,
    scoped: bool = False,
) -> list[str]:
    """
    The new cards to introduce, in the mix the plan asks for.

    `scoped` decides what happens when the plan's own material runs out. Practice
    is scoped: you asked to work on these topics, so a short session is the
    honest answer and padding it with unrelated material would quietly turn
    "practise food and directions" into "practise whatever". Unscoped, the
    remainder follows on -- which is what you want when a plan is shaping a full
    session rather than carving one out.
    """
    weights = weights_from_ranks(priorities)
    if not weights:
        return ordered[:budget]
    buckets, rest = bucket_cards(ordered, membership, weights)
    shares = allocate({k: len(v) for k, v in buckets.items()}, weights, budget)

    picked: list[str] = []
    for key, n in sorted(shares.items(), key=lambda kv: -weights[kv[0]]):
        picked.extend(buckets[key][:n])
    if len(picked) < budget and not scoped:
        picked.extend(rest[: budget - len(picked)])
    # Content order within the session, so a plan changes *which* material
    # arrives rather than scrambling the order the course lays it out in.
    position = {card_id: i for i, card_id in enumerate(ordered)}
    return sorted(picked, key=lambda c: position[c])


def _as_int(value: object, fallback: int) -> int:
    """A knob is stored as JSON, so it arrives as whatever was written."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def order_by_priority(
    card_ids: list[str],
    membership: dict[str, set[tuple[str, str]]],
    weights: dict[tuple[str, str], float],
) -> list[str]:
    """
    Put the material a plan cares about first, keeping the order within each.

    This is what makes a priority list an order of practice and not only a mix.
    It applies to the **owed** cards too: everything owed is still served, and
    still in one session -- the list decides what you meet first, not what you
    get out of.

    Python's sort is stable, so cards of equal priority keep the order they
    arrived in, which for the debt is most-overdue-first. That ordering is not
    discarded; it becomes the tie-break.
    """
    if not weights:
        return list(card_ids)

    def key(card_id: str) -> float:
        keys = membership.get(card_id, set()) & set(weights)
        # Negated so the heaviest sorts first; unmatched material sorts last
        # rather than being dropped.
        return -max((weights[k] for k in keys), default=0.0)

    return sorted(card_ids, key=key)


@dataclass(frozen=True, slots=True)
class Preview:
    """What a plan would serve, without serving it."""

    cards: list[str]
    by_priority: dict[str, int]
    unplanned: int


def membership_of(con: sqlite3.Connection, card_ids: list[str]) -> dict[str, set[tuple[str, str]]]:
    """Which (axis, value) pairs each card belongs to, facets and built-ins."""
    if not card_ids:
        return {}
    out: dict[str, set[tuple[str, str]]] = {c: set() for c in card_ids}
    rows = con.execute(
        "SELECT c.id AS card_id, f.axis AS axis, f.value AS value "
        "FROM cards c JOIN note_facets f ON f.note_id = c.note_id "
        "WHERE c.archived_at IS NULL"
    )
    for r in rows:
        if r["card_id"] in out:
            out[r["card_id"]].add((r["axis"], r["value"]))
    for r in con.execute(
        "SELECT c.id AS card_id, n.unit AS unit, c.notetype AS notetype, c.template AS template "
        "FROM cards c JOIN notes n ON n.id = c.note_id WHERE c.archived_at IS NULL"
    ):
        if r["card_id"] in out:
            out[r["card_id"]] |= {
                ("unit", r["unit"]),
                ("notetype", r["notetype"]),
                ("template", r["template"]),
            }
    return out


def build_planned_session(
    con: sqlite3.Connection,
    plan: Plan,
    today: date,
    limit: int | None = None,
    *,
    ratings: list[Rating] | None = None,
    course: str | None = None,
) -> Session:
    """
    Today's queue under a plan. The debt first, the plan's mix woven into it.
    """
    from ..store.reviews import recent_ratings

    # `plan.course` has been stored since ADR-0007 and read by nothing. An
    # explicit `course` wins over it, so the caller that knows which course the
    # request is about does not have to trust a plan row to agree.
    course = course or plan.course or None
    cards = scheduled_cards(con, course)
    states: dict[str, CardState] = all_states(con, course=course)
    grades = recent_ratings(con, 20, course=course) if ratings is None else ratings

    every = _as_int(plan.knobs.get("new_every"), NEW_EVERY)
    batch = limit if limit is not None else _as_int(plan.knobs.get("batch"), BATCH)

    due = [c.card_id for c in cards if (s := states.get(c.card_id)) and s.is_due(today)]
    due.sort(key=lambda cid: states[cid].due or "")

    ordered = introduction_order(cards, states)
    weights = weights_from_ranks(plan.priorities)

    # The gate is a brake on material arriving *unasked*: it opens while recent
    # answers hold up and closes on evidence of overload, which is exactly right
    # for a queue the learner did not choose. A scoped practice is the opposite
    # situation -- they named these topics and pressed the button -- and applying
    # the brake there answers "practise food" with three cards while thirty-eight
    # sit available, which is not a protection, it is a refusal.
    #
    # It is bounded either way: `batch` still caps the session, the material is
    # still only what the plan asked for, and anything taken on shows up in
    # tomorrow's queue where the gate does apply. Choosing to work hard on a
    # topic is the learner's to make, like "I know this".
    allowed = ordered if weights else gated_introductions(ordered, cards, grades, today)
    membership = membership_of(con, [*allowed, *due])

    # Practising a plan serves the plan's material. Owed cards from *these*
    # topics come first, because answering something you already owe is worth
    # more than meeting something new -- but owed cards from elsewhere are not
    # dragged in. They are not excused either: the debt is the Study tab's, it
    # is unchanged by any of this, and it is one click away.
    due = order_by_priority(matching(due, membership, weights), membership, weights)
    picked = planned_introductions(
        allowed, membership, plan.priorities, budget=batch, scoped=bool(weights)
    )

    queue, buried = bury_siblings(weave(due, picked, every), cards)
    return Session(
        cards=queue[:batch],
        has_more=len(queue) > batch,
        consolidating=False,
        buried=len(buried),
    )


def preview(
    con: sqlite3.Connection,
    plan: Plan,
    today: date,
    budget: int = 20,
    *,
    course: str | None = None,
) -> Preview:
    """
    What the plan would introduce, without touching anything.

    Cheap because the decision is pure -- which is what makes tweaking the knobs
    feel safe rather than like a commitment.
    """
    course = course or plan.course or None
    cards = scheduled_cards(con, course)
    states = all_states(con, course=course)
    ordered = introduction_order(cards, states)
    membership = membership_of(con, ordered)
    weights = weights_from_ranks(plan.priorities)
    buckets, _unplanned_pool = bucket_cards(ordered, membership, weights)
    shares = allocate({k: len(v) for k, v in buckets.items()}, weights, budget)
    # `scoped` exactly as `build_planned_session` sets it. A preview that pads
    # with material the session will not serve is worse than no preview: it is
    # the one screen whose whole job is to be believed.
    picked = planned_introductions(
        ordered, membership, plan.priorities, budget, scoped=bool(weights)
    )
    return Preview(
        cards=picked,
        by_priority={f"{a}={v}": n for (a, v), n in shares.items()},
        unplanned=len([c for c in picked if not membership.get(c, set()) & set(weights)]),
    )


__all__ = [
    "Preview",
    "allocate",
    "bucket_cards",
    "build_planned_session",
    "matching",
    "membership_of",
    "order_by_priority",
    "planned_introductions",
    "preview",
    "weights_from_ranks",
]
