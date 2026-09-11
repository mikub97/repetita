"""
The forms an exercise can be asked in, and which grader can mark which.

A *form* is how a question is put: typed, rebuilt from a word bank, chosen from a
list, or said out loud and self-rated. ADR-0001 keeps this separate from *when* a
card is asked and from *what* it is about, and ADR-0010 lets one exercise state a
preference among them.

Here rather than beside the renderers, because the pairing below is a fact about
grading rather than about drawing: `store/` has to refuse a preference that
cannot be marked, and `store/` may not import `web/`.
"""

from __future__ import annotations

#: Every form this engine knows the name of.
FORMS: tuple[str, ...] = ("choice", "typein", "wordbank", "flashcard")

#: Which of them a grader can actually mark.
#:
#: `self` reads a number out of the payload -- the learner's own rating of how it
#: went -- so a flashcard is the only thing it can be shown as. Put a `typed`
#: card in a flashcard and every answer is the string "3", scored AGAIN; put a
#: `self` card in a word bank and the assembled sentence is not a number, scored
#: AGAIN. Neither shows any sign of being wrong from the outside, which is why
#: this is a rule the write path enforces rather than advice.
GRADER_FORMS: dict[str, tuple[str, ...]] = {
    "self": ("flashcard",),
    "typed": ("typein", "wordbank", "choice"),
    "sentence": ("typein", "wordbank", "choice"),
    "choice": ("choice", "typein"),
}


def markable(grader: str) -> tuple[str, ...]:
    """The forms `grader` can mark. An unknown grader is assumed to take text."""
    return GRADER_FORMS.get(grader, ("typein", "wordbank", "choice"))
