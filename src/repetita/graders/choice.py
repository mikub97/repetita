"""Selection answers (multiple choice, word bank) and learner self-assessment."""

from __future__ import annotations

from ..core.protocols import GradingOptions
from ..core.types import Judgement, Rating, Response
from .text import normalize


class ChoiceGrader:
    """
    Exact match against the accepted set, no partial credit.

    No HARD here on purpose: the learner picked from a list, so there is no
    accent to get slightly wrong and nothing to be generous about.
    """

    name = "choice"
    accepts = frozenset({"text"})

    def grade(self, response: Response, accepted: list[str], *, opts: GradingOptions) -> Judgement:
        given = normalize(response.choice or response.text or "", opts)
        for a in accepted:
            if given == normalize(a, opts):
                return Judgement(Rating.GOOD, matched=a)
        return Judgement(Rating.AGAIN, matched=accepted[0] if accepted else None)


class SelfGrader:
    """
    The learner says how it went.

    Used where the machine cannot judge: saying a phrase out loud, recalling a
    gesture. The client sends the rating; this grader only validates it. It is
    the one grader whose verdict is a claim rather than a measurement, and the
    review log records it as such via the form it was answered in.
    """

    name = "self"
    accepts = frozenset({"text"})

    def grade(self, response: Response, accepted: list[str], *, opts: GradingOptions) -> Judgement:
        raw = (response.choice or response.text or "").strip()
        try:
            rating = Rating(int(raw))
        except (TypeError, ValueError):
            rating = Rating.AGAIN
        return Judgement(rating, matched=accepted[0] if accepted else None)
