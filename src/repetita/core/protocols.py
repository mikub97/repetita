"""Extension points. Everything here is a Protocol so a plugin needs no import from us."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol, runtime_checkable

from .types import Judgement, Rating, Response


@dataclass(frozen=True, slots=True)
class GradingOptions:
    """
    Per-course grading behaviour.

    This is where the target language lives. The engine folds accents because a
    course asked it to, not because it knows about Portuguese.
    """

    #: Treat an accent-only difference as HARD rather than a miss. True for
    #: Portuguese, Spanish, French; wrong for languages where the diacritic is
    #: the whole distinction.
    fold_accents: bool = True
    ignore_punctuation: bool = True
    ignore_case: bool = True
    #: How many differing words a whole-sentence answer may have and still be
    #: HARD rather than a miss. Retyping ten words and losing three months of
    #: schedule to one article would make the type unusable.
    sentence_slack: int = 1
    #: Pairs treated as equal, e.g. ("ss", "ç"). Applied after normalisation.
    equivalences: tuple[tuple[str, str], ...] = ()


@runtime_checkable
class Grader(Protocol):
    """Decides whether a response is correct, and how badly it is wrong."""

    name: str
    #: Which channels of `Response` this grader can read: {"text"}, {"audio"}, …
    accepts: frozenset[str]

    def grade(
        self, response: Response, accepted: list[str], *, opts: GradingOptions
    ) -> Judgement: ...


@runtime_checkable
class SchedulerBackend(Protocol):
    """
    Decides when a card should be seen again.

    Implementations are pure: no clock, no database, no uninjected randomness.
    State is an opaque JSON-serialisable dict owned entirely by the backend --
    nothing outside it may read a key. The queue reads the denormalised `due`
    column instead, which is what lets a second backend be added without
    rewriting every SQL query.
    """

    name: str
    version: int

    def new_state(self) -> dict[str, Any]: ...

    def review(self, state: dict[str, Any], rating: Rating, at: datetime) -> dict[str, Any]: ...

    def due_at(self, state: dict[str, Any]) -> date | None: ...

    def interval_days(self, state: dict[str, Any]) -> int: ...

    def retrievability(self, state: dict[str, Any], at: datetime) -> float | None:
        """Probability of recall right now, or None if the model cannot say."""
        ...


@dataclass(frozen=True, slots=True)
class PresentationContext:
    """What a presenter knows when choosing how to ask a card."""

    seen: int
    lapses: int
    available_forms: tuple[str, ...] = field(default_factory=tuple)


@runtime_checkable
class Presenter(Protocol):
    """Chooses which form (typein, choice, wordbank, …) to show a card in now."""

    name: str

    def choose(self, declared_form: str, ctx: PresentationContext) -> str: ...
