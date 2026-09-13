"""
What a session is made of, and what one card looks like on the way into it.

These two shapes live here rather than in `daily` because `ordering` needs
`QueueCard` and `daily` needs `ordering`: one of the three had to stop importing
the others. `daily` re-exports both, so `daily.QueueCard` and `policies.Session`
keep meaning what they meant.

Nothing here touches sqlite, a clock or a random number. A `QueueCard` is a row
that has already been read; a `Session` is an answer that has already been
decided.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Where a unit sorts when the course has not placed it. Above the 999 that
#: `store/material.py` gives a set written in the app, so material somebody
#: created this morning still sorts before material the course never mentions.
UNPLACED = 999_999


@dataclass(frozen=True, slots=True)
class QueueCard:
    card_id: str
    note_id: str
    unit: str
    ord: int
    lesson: str | None
    #: The unit's position in `course.path`. `0` is what a course that declares
    #: no path gives *every* unit -- see `content/loader.py:_load_unit` -- so the
    #: default here is not a placeholder: it is the value that makes the ordering
    #: key degenerate to exactly what it was before the path was read at all.
    unit_ord: int = 0
    #: Which card of the note this is. Decides which sibling is introduced first
    #: when a learner has said they care; alphabetical by accident when not.
    template: str = ""


@dataclass(frozen=True, slots=True)
class Session:
    cards: list[str] = field(default_factory=list)
    has_more: bool = False
    #: True when the owed and new work is done and only reinforcement is left --
    #: used to tell the learner where the plan ends and extra begins.
    consolidating: bool = False
    #: Cards held back because a sibling from the same note is in this session.
    #: Reported rather than silent: a learner who counts the queue and finds it
    #: shorter than the debt deserves to know why.
    buried: int = 0
    #: Owed cards a focus excluded (ADR-0018). Reported for the same reason as
    #: `buried`, and harder: burying delays a card by a day, a focus can hide it
    #: for as long as the focus lasts. A guardrail that stays quiet when it is
    #: not biting teaches you to forget it exists, so this is surfaced even at 0
    #: whenever a focus is set.
    hidden: int = 0
    #: The selector that hid them, so the screen can say what it is and offer to
    #: drop it without the client having to ask a second endpoint.
    focus: str = ""
