"""
Which stage of learning a card is at.

One definition, used by the writer that denormalises it onto `card_state` and by
anything that reads it back. The threshold matters: `MATURE_DAYS` decides what
counts as known, and ADR-0002 is the record of what happens when a constant that
encodes a policy is defined in one place and acted on in another -- `HARD` was
documented as a near-miss for a year while the scheduler treated it as a failure,
and no test noticed because the suite tested the constant rather than the policy.

So the bucket is computed here and stored, never recomputed in a WHERE clause.
A second definition in SQL is exactly the shape of that bug.
"""

from __future__ import annotations

from typing import Protocol

#: Beyond this interval a card is holding on its own. Shared with
#: `policies.daily.MATURE_DAYS`, which is the same idea and must not drift --
#: `docs/tuning.md` carries the reasoning.
MATURE_DAYS = 21

NEW = "new"
LEARNING = "learning"
YOUNG = "young"
MATURE = "mature"
SUSPENDED = "suspended"
RETIRED = "retired"

#: The order a person reads them in: least known first.
ORDER = (NEW, LEARNING, YOUNG, MATURE, SUSPENDED, RETIRED)


class _Stateish(Protocol):
    # Read-only properties rather than bare attributes: `CardState` is a frozen
    # dataclass, and a protocol with mutable attributes would not accept one.
    @property
    def seen(self) -> int: ...
    @property
    def interval(self) -> int: ...
    @property
    def lapses(self) -> int: ...
    @property
    def retired_at(self) -> str | None: ...
    @property
    def suspended_at(self) -> str | None: ...


def bucket_of(state: _Stateish | None) -> str:
    """
    A card's stage, from the columns already denormalised beside its blob.

    Reads no key out of `state` -- that blob belongs to the scheduler backend
    (ADR-0003), and a bucket that parsed it would break the moment a second
    backend wrote a different shape.

    Retired outranks suspended, and both outrank progress: a card that has left
    the queue has left it, and reporting it as `young` would put it in counts of
    material a learner is actually working through.
    """
    if state is None:
        return NEW
    if state.retired_at:
        return RETIRED
    if state.suspended_at:
        return SUSPENDED
    if state.seen == 0:
        return NEW
    # A card that has lapsed is being learned again, not "young going on
    # mature". Treating a relapse as progress is how a leech hides in a count.
    if state.interval == 0 or (state.lapses and state.interval < 2):
        return LEARNING
    return YOUNG if state.interval < MATURE_DAYS else MATURE
