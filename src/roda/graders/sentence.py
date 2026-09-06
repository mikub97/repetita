"""Whole-sentence answers, graded word by word."""

from __future__ import annotations

from ..core.protocols import GradingOptions
from ..core.types import Judgement, Rating, Response, Token
from .text import tokens


def diff(given: str, answer: str, opts: GradingOptions) -> list[Token]:
    """
    Position-by-position comparison against one expected answer.

    Returned for display, so the learner can see WHICH word is wrong.

    Deliberately positional rather than a real edit distance: an inserted word
    misaligns everything after it and the display then blames words that are
    fine. Proper alignment needs an LCS, and until that is worth building,
    over-reporting on a shifted sentence is the honest failure mode -- the
    sentence IS wrong, the reporting is just blunter than ideal.
    """
    g, a = tokens(given, opts), tokens(answer, opts)
    g_bare = tokens(given, opts, fold_accents=True)
    a_bare = tokens(answer, opts, fold_accents=True)
    out: list[Token] = []
    for i in range(max(len(g), len(a))):
        gi = g[i] if i < len(g) else None
        ai = a[i] if i < len(a) else None
        if gi is not None and ai is not None and gi == ai:
            kind = "ok"
        elif gi is not None and ai is not None and g_bare[i] == a_bare[i]:
            kind = "accent"
        elif gi is None:
            kind = "missing"
        elif ai is None:
            kind = "extra"
        else:
            kind = "wrong"
        out.append(Token(given=gi, expected=ai, kind=kind))
    return out


class SentenceGrader:
    """
    Grade a whole-sentence answer against whichever accepted answer it comes
    closest to, so listing a second phrasing can only ever help.

    Accent-only differences never cost more than HARD, on the same reasoning as
    the typed grader. Up to `opts.sentence_slack` genuinely differing words is
    also HARD rather than a miss.
    """

    name = "sentence"
    accepts = frozenset({"text"})

    def grade(self, response: Response, accepted: list[str], *, opts: GradingOptions) -> Judgement:
        given = response.text or ""
        if not tokens(given, opts):
            first = accepted[0] if accepted else ""
            return Judgement(Rating.AGAIN, matched=first or None, diff=diff(given, first, opts))

        best: tuple[int, int, str, list[Token]] | None = None
        for a in accepted:
            d = diff(given, a, opts)
            wrong = sum(1 for t in d if t.kind not in ("ok", "accent"))
            accents = sum(1 for t in d if t.kind == "accent")
            if best is None or (wrong, accents) < (best[0], best[1]):
                best = (wrong, accents, a, d)

        assert best is not None
        wrong, accents, matched, d = best
        if wrong == 0:
            rating = Rating.GOOD if (accents == 0 or not opts.fold_accents) else Rating.HARD
        elif wrong <= opts.sentence_slack:
            rating = Rating.HARD
        else:
            rating = Rating.AGAIN
        return Judgement(rating, matched=matched, diff=d)
