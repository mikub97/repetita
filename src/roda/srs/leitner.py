"""
Leitner boxes -- the simplest scheduler that could possibly work.

This exists to keep `SchedulerBackend` honest. A protocol with one
implementation is not an abstraction, it is a description of that
implementation; a protocol with two, where the second shares no state shape with
the first, is a real seam. If a change to the protocol cannot be satisfied here
in fifteen lines, the protocol has grown a dependency on FSRS's memory model and
should be reconsidered.

It is also genuinely the right scheduler for a paper card box or a one-session
game mode -- one integer of state, explainable to a child. It is strictly worse
than SM-2 for actual study, and nothing here pretends otherwise.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from ..core.types import Rating

NAME = "leitner"
VERSION = 1

#: Days between reviews for each box. Box 0 is "today, again".
BOXES = (0, 1, 3, 7, 21, 60)


def new_state() -> dict[str, Any]:
    return {"box": 0, "due": None, "last": None}


def review(state: dict[str, Any], rating: Rating, at: datetime) -> dict[str, Any]:
    today = at.date()
    box = int(state.get("box", 0))
    box = min(box + 1, len(BOXES) - 1) if rating.passed else 0
    return {
        "box": box,
        "due": (today + timedelta(days=BOXES[box])).isoformat(),
        "last": today.isoformat(),
    }


def due_at(state: dict[str, Any]) -> date | None:
    raw = state.get("due")
    return date.fromisoformat(raw) if raw else None


def interval_days(state: dict[str, Any]) -> int:
    return BOXES[int(state.get("box", 0))]


def retrievability(state: dict[str, Any], at: datetime) -> float | None:
    return None


class LeitnerScheduler:
    name = NAME
    version = VERSION

    new_state = staticmethod(new_state)
    review = staticmethod(review)
    due_at = staticmethod(due_at)
    interval_days = staticmethod(interval_days)
    retrievability = staticmethod(retrievability)
