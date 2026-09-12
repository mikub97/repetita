"""
Session policies: what goes into today's queue.

A registry, mirroring `srs/`, `graders/` and `presenters/`. Which policy runs is
configuration -- a learner with an active study plan gets `planned`, everyone
else gets `daily` -- and the web layer picks by name rather than branching on
which one it got.
"""

from __future__ import annotations

import sqlite3
from datetime import date

from ..core.types import Rating
from ..store.users import DEFAULT_USER
from .daily import build_session, day_done, forecast, gate_open, owed_count
from .ordering import DEBT_ORDERINGS, ORDERINGS
from .planned import Preview, build_planned_session, preview
from .queue import QueueCard, Session


class _Daily:
    """The course's own order: due first, then whatever comes next."""

    name = "daily"

    def build(
        self,
        con: sqlite3.Connection,
        today: date,
        *,
        limit: int | None = None,
        plan: object | None = None,
        ratings: list[Rating] | None = None,
        course: str | None = None,
        user_id: int = DEFAULT_USER,
    ) -> Session:
        # `plan` is accepted and ignored on purpose: the caller should not have
        # to know which policy it is holding.
        from .daily import BATCH

        return build_session(
            con,
            today,
            limit if limit is not None else BATCH,
            ratings=ratings,
            course=course,
            user_id=user_id,
        )


class _Planned:
    """A priority list: the same debt, a different mix of new material."""

    name = "planned"

    def build(
        self,
        con: sqlite3.Connection,
        today: date,
        *,
        limit: int | None = None,
        plan: object | None = None,
        ratings: list[Rating] | None = None,
        course: str | None = None,
        user_id: int = DEFAULT_USER,
    ) -> Session:
        if plan is None:
            # Falling back rather than raising: a plan can be deleted between a
            # page load and an answer, and a learner should get their session.
            return _Daily().build(
                con, today, limit=limit, ratings=ratings, course=course, user_id=user_id
            )
        return build_planned_session(
            con,
            plan,  # type: ignore[arg-type]
            today,
            limit,
            ratings=ratings,
            course=course,
            user_id=user_id,
        )


_BUILTIN: dict[str, _Daily | _Planned] = {p.name: p for p in (_Daily(), _Planned())}

DEFAULT = "daily"


def get(name: str | None = None) -> _Daily | _Planned:
    key = name or DEFAULT
    try:
        return _BUILTIN[key]
    except KeyError:
        raise LookupError(
            f"unknown session policy {key!r}; available: {', '.join(sorted(_BUILTIN))}"
        ) from None


def names() -> list[str]:
    return sorted(_BUILTIN)


__all__ = [
    "DEBT_ORDERINGS",
    "DEFAULT",
    "ORDERINGS",
    "Preview",
    "QueueCard",
    "Session",
    "build_planned_session",
    "build_session",
    "day_done",
    "forecast",
    "gate_open",
    "get",
    "names",
    "owed_count",
    "preview",
]
