"""
The values that pass between a learner's answer and a scheduler's decision.

`Response` is deliberately wider than "a string the learner typed". Speaking
practice is on the roadmap (ROADMAP.md), and retrofitting an audio answer into
graders written against `str` would mean rewriting every grader and the whole
answer path at once. The seam costs one dataclass now.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class Rating(IntEnum):
    """
    How well an answer went, in the four grades every modern scheduler speaks.

    These are FSRS's values (1-4) rather than SM-2's 0-5, because they are the
    smaller, better-defined set: SM-2's 0/1/2 and 4/5 were never distinguishable
    in practice by anything but a self-grading human, and the app's own typed
    grading only ever produced three of the six.

    `HARD` earns its place as more than a courtesy: an answer differing only by
    an accent, or a ten-word sentence with one word off, is a real mistake that
    must not reset months of scheduling. Without a middle grade the only options
    are "forgive it" (and let the error ossify) or "fail it" (and make the
    exercise type unusable).
    """

    AGAIN = 1
    HARD = 2
    GOOD = 3
    EASY = 4

    @property
    def passed(self) -> bool:
        return self >= Rating.HARD


@dataclass(frozen=True, slots=True)
class Response:
    """
    What the learner submitted.

    Exactly one channel is normally populated. `audio` is accepted by the API and
    stored, but no grader consumes it yet -- see ROADMAP.md 12.1.
    """

    text: str | None = None
    audio: bytes | None = None
    mime: str | None = None
    ms: int | None = None
    #: For forms where the answer is a selection rather than free input
    #: (multiple choice, word bank), the raw choice as the client sent it.
    choice: str | None = None

    @property
    def is_empty(self) -> bool:
        return not (self.text or "").strip() and self.audio is None and not self.choice


@dataclass(frozen=True, slots=True)
class Token:
    """One position in a word-by-word comparison, for display."""

    given: str | None
    expected: str | None
    #: ok | accent | missing | extra | wrong
    kind: str


@dataclass(frozen=True, slots=True)
class Judgement:
    """
    A grader's verdict.

    Carries the rating for the scheduler and the diff for the learner. The diff
    is not decoration: typing ten words and being told only "wrong" is a
    punishment rather than feedback, and the exercise type stops being usable.
    """

    rating: Rating
    #: The accepted answer the response was scored against -- whichever it came
    #: closest to, so listing a second phrasing can only ever help.
    matched: str | None = None
    diff: list[Token] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.rating.passed
