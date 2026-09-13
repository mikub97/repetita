"""
In what order material arrives, when the course has more than one defensible one.

Every function here is pure: no sqlite, no clock, no global random number
generator. What they need from the database arrives as plain mappings rather
than as `CardState`, which is what keeps this module importable from anywhere
and "pure" a fact rather than a claim.

The reason this module exists at all is that the old answer was an accident.
`introduction_order` sorted by lesson date and fell back to `(unit, ord, id)`
where `unit` is the *directory name*, so with `lesson:` set in 14 of 63 files in
one course and none at all in the other three, new material was introduced in
alphabetical order of directory. In the Portuguese course that put capoeira
slang and the whole grammar ahead of the word for "table", while `course.yaml`
declared a sensible order that nothing read.

So: the course's declared path is one option, the old behaviour is another, and
which one runs is the learner's to choose.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from hashlib import blake2b

from .queue import UNPLACED, QueueCard

#: How new material is introduced.
ORDERINGS: tuple[str, ...] = ("lesson", "course", "axis", "plan", "shuffle")

#: How the debt is served. Reordering only -- what is owed is always all of it.
DEBT_ORDERINGS: tuple[str, ...] = ("overdue", "course", "plan", "weakest")


def _template_rank(template: str, templates: Sequence[str]) -> int:
    """
    Which sibling of a note goes first.

    A template the learner has not ranked sorts after every one they have, so
    expressing a preference about `produce` says nothing about the rest.
    """
    try:
        return templates.index(template)
    except ValueError:
        return len(templates)


def content_key(c: QueueCard, templates: Sequence[str] = ()) -> tuple[int, str, int, int, str]:
    """
    The order the course itself lays the material out in.

    `unit_ord` comes from `course.path`; `unit` is kept after it as the tie-break
    for the courses that declare no path, where every `unit_ord` is 0 and this
    degenerates to the key that was in use before any of this existed.

    `card_id` closes it. `(unit, ord)` is not unique -- `ord` counts within a
    file and a note makes several cards -- and ids here are unique and never
    change (CLAUDE.md rule 1), which is what makes this a total order rather than
    one that merely usually is.
    """
    return (c.unit_ord, c.unit, c.ord, _template_rank(c.template, templates), c.card_id)


def _lesson_key(c: QueueCard, templates: Sequence[str]) -> tuple[int, int, int, str, int, int, str]:
    """
    Freshest lesson first, then the course's order.

    Material with no lesson date sorts after every dated card -- that is the
    whole back catalogue, and letting it interleave with a lesson would defeat
    the point of dating one.
    """
    if c.lesson:
        try:
            return (0, -date.fromisoformat(c.lesson).toordinal(), *content_key(c, templates))
        except ValueError:
            pass
    return (1, 0, *content_key(c, templates))


def shuffle_key(card_id: str, seed: str) -> bytes:
    """
    A stable pseudo-random position.

    `blake2b` over the seed and the id rather than `random.shuffle`: the ordering
    has to be identical on every request within a day (a session is fetched more
    than once) and adding one card must not move the others, which a shuffle of
    the whole list does. It also touches no global random state, which the rest
    of this codebase forbids itself for scheduling reasons and which is worth
    keeping true here for the same reason -- see `srs/CLAUDE.md`.
    """
    return blake2b(f"{seed}\x00{card_id}".encode(), digest_size=16).digest()


def order_introductions(
    pool: Sequence[QueueCard],
    how: str = "lesson",
    *,
    today: date,
    templates: Sequence[str] = (),
    axis_rank: Mapping[str, int] | None = None,
    weight: Mapping[str, float] | None = None,
    seed: str = "",
) -> list[str]:
    """
    Every not-yet-answered card, in the exact order it would be introduced.

    The gate is not applied here -- see `daily.gated_introductions`. This decides
    sequence; the gate decides how much of it flows today.

    `how` is one of `ORDERINGS`; an unknown value is a programming error and
    raises, because silently falling back would make a misspelled setting look
    like a working one.
    """
    if how not in ORDERINGS:
        raise ValueError(f"unknown ordering {how!r}; available: {', '.join(ORDERINGS)}")

    ranks = axis_rank or {}
    weights = weight or {}

    def key(c: QueueCard) -> tuple[object, ...]:
        if how == "course":
            return content_key(c, templates)
        if how == "axis":
            # An unranked card sorts last rather than first: an axis says where
            # the material it covers belongs, not where everything else does.
            return (ranks.get(c.card_id, UNPLACED), *content_key(c, templates))
        if how == "plan":
            # Negated so the heaviest sorts first; content order breaks ties, so
            # a plan changes what you meet first without scrambling the course.
            return (-weights.get(c.card_id, 0.0), *content_key(c, templates))
        if how == "shuffle":
            return (shuffle_key(c.card_id, seed),)
        return _lesson_key(c, templates)

    return [c.card_id for c in sorted(pool, key=key)]


def order_debt(
    due: Sequence[str],
    how: str = "overdue",
    *,
    cards: Mapping[str, QueueCard],
    due_on: Mapping[str, str],
    interval: Mapping[str, int],
    lapses: Mapping[str, int],
    templates: Sequence[str] = (),
    weight: Mapping[str, float] | None = None,
    seed: str = "",
) -> list[str]:
    """
    The owed cards, reordered -- never filtered.

    Which cards are owed is not this function's business and it has no way to
    drop one: it returns a permutation of what it was given, and a test says so.

    `due` is expected to arrive most-overdue-first. Python's sort is stable, so
    that ordering is not discarded by any of the alternatives below; it becomes
    their tie-break.
    """
    if how not in DEBT_ORDERINGS:
        raise ValueError(f"unknown debt ordering {how!r}; available: {', '.join(DEBT_ORDERINGS)}")

    weights = weight or {}

    if how == "overdue":
        return sorted(due, key=lambda cid: due_on.get(cid, ""))
    if how == "course":
        return sorted(
            due,
            key=lambda cid: (
                content_key(cards[cid], templates) if cid in cards else (UNPLACED, "", 0, 0, cid)
            ),
        )
    if how == "plan":
        return sorted(due, key=lambda cid: -weights.get(cid, 0.0))
    # "weakest": the most lapsed first, shortest interval next -- the same key
    # `daily.build_session` uses for consolidation, so "weakest" means one thing
    # in this codebase rather than two.
    return sorted(due, key=lambda cid: (-lapses.get(cid, 0), interval.get(cid, 0)))
