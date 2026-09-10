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


class SessionPolicy(Protocol):
    """
    What goes into a session.

    `ARCHITECTURE.md` has listed this as one of the four extension points since
    the beginning, and it was the only one without a protocol or a registry --
    `srs/`, `graders/` and `presenters/` all have `get()`/`names()`, while
    `build_session` was called directly from the web layer. A second policy is
    the moment that stops being a harmless omission.

    A policy takes an optional `plan`: `daily` ignores it and follows the course,
    `planned` reads a priority list out of it. Uniform signature so the caller
    picks a policy by name and does not branch on which one it got.
    """

    name: str

    def build(
        self,
        con: object,
        today: object,
        *,
        limit: int | None = None,
        plan: object | None = None,
        ratings: object | None = None,
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class PresentationContext:
    """
    What a presenter knows when choosing how to ask a card.

    Everything a presenter is allowed to see is here, by value. A presenter never
    reaches into a scheduler's `state` dict: that dict is private to the backend
    that wrote it (ADR-0003), so a presenter reading a key out of it would be a
    second backend-specific code path in a layer that is supposed to have none.
    A presenter needing something new gets a field here instead.
    """

    #: Answers ever given for this card, across lapses. **Not `reps`**: `reps`
    #: resets to zero every time the card is failed, so keying "have we met
    #: before" on it would send a hard card back to first-contact treatment
    #: forever.
    seen: int
    lapses: int
    #: Forms this build can actually render for this card, in the note type's
    #: declaration order. A presenter may only return one of these, or the
    #: declared form it was given.
    available_forms: tuple[str, ...] = field(default_factory=tuple)
    #: How many words the expected answer has, or 0 when it is not known. This is
    #: the shape signal: one word is a word, several are a sentence, and the two
    #: want different recognition forms.
    answer_tokens: int = 0


@runtime_checkable
class Presenter(Protocol):
    """Chooses which form (typein, choice, wordbank, …) to show a card in now."""

    name: str

    def choose(self, declared_form: str, ctx: PresentationContext) -> str: ...
