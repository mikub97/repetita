"""
Scheduler registry.

Which scheduler a course uses is configuration, not a code change. Third-party
backends register through the `repetita.srs` entry-point group.
"""

from __future__ import annotations

from ..core.protocols import SchedulerBackend
from .fsrs_backend import FSRSScheduler
from .leitner import LeitnerScheduler
from .sm2 import SM2Scheduler

_BUILTIN: dict[str, SchedulerBackend] = {
    s.name: s for s in (SM2Scheduler(), FSRSScheduler(), LeitnerScheduler())
}

# Adding FSRS does not switch anything over to it. Which scheduler a course uses
# is configuration, and the choice for the author's own study is an evidence-based
# one to be made on his own review log (ADR-0003), not on a benchmark.
DEFAULT = "sm2"


def get(name: str | None = None) -> SchedulerBackend:
    key = name or DEFAULT
    try:
        return _BUILTIN[key]
    except KeyError:
        raise LookupError(
            f"unknown scheduler {key!r}; available: {', '.join(sorted(_BUILTIN))}"
        ) from None


def names() -> list[str]:
    return sorted(_BUILTIN)


__all__ = ["DEFAULT", "FSRSScheduler", "LeitnerScheduler", "SM2Scheduler", "get", "names"]
