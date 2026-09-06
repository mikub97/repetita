"""Single-word (or short-phrase) typed answers."""

from __future__ import annotations

from ..core.protocols import GradingOptions
from ..core.types import Judgement, Rating, Response
from .text import normalize


class TypedGrader:
    """
    Grade a typed answer: GOOD if exact, HARD if only accents or case differ.

    The middle grade exists because "hoje" vs "hojé" is a real but minor error.
    Counting it as a full failure would reset months of scheduling over a missing
    tilde; counting it as correct would let accent mistakes ossify.
    """

    name = "typed"
    accepts = frozenset({"text"})

    def grade(self, response: Response, accepted: list[str], *, opts: GradingOptions) -> Judgement:
        given = response.text or response.choice or ""
        g = normalize(given, opts)
        if not g:
            return Judgement(Rating.AGAIN)

        for a in accepted:
            if g == normalize(a, opts):
                return Judgement(Rating.GOOD, matched=a)

        if opts.fold_accents:
            g_bare = normalize(given, opts, fold_accents=True)
            for a in accepted:
                if g_bare == normalize(a, opts, fold_accents=True):
                    return Judgement(Rating.HARD, matched=a)

        return Judgement(Rating.AGAIN, matched=accepted[0] if accepted else None)
