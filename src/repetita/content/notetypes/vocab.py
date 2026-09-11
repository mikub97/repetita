"""
A word, tested in as many directions as the note supports.

Where the note->card multiplication earns its keep: one entry, three cards.

Note that `l1` is the prompt for `produce` and the answer for `recognize`. A
field's `visibility` is the type's default; a card's own `expect` always wins,
which `NoteType.visible_before` handles.
"""

from __future__ import annotations

from ..models import CardTemplate, NoteType
from ._spec import field as f

vocab = NoteType(
    name="vocab",
    fields={
        "l2": f(required=True, visibility="after"),
        "l1": f(required=True),
        "pos": f(),
        "example_l2": f(visibility="after"),
        "example_l1": f(),
        "distractors": f("text_list", visibility="after"),
        "explain": f(visibility="after"),
        "audio": f("audio", visibility="after"),
    },
    cards={
        "recognize": CardTemplate(
            ask=("l2",),
            expect="l1",
            grader="typed",
            # No flashcard: `typed` compares text, and a flashcard submits the
            # learner's own 1-4 rating, which it would mark against the answer
            # and fail. Recognising a word by self-assessment is a reasonable
            # exercise -- it would need a `self`-graded card of its own, which is
            # a design decision rather than a missing form.
            forms=("choice", "typein"),
        ),
        "produce": CardTemplate(
            ask=("l1",),
            expect="l2",
            grader="typed",
            forms=("choice", "typein", "wordbank"),
        ),
        "listen": CardTemplate(
            ask=("audio",),
            expect="l2",
            grader="typed",
            forms=("typein",),
            requires=("audio",),
        ),
    },
)
