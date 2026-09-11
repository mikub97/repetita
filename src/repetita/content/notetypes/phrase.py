"""
Something to say out loud, judged by the learner.

The one place where self-assessment is honest, because nothing else can hear it
yet.
"""

from __future__ import annotations

from ..models import CardTemplate, NoteType
from ._spec import field as f

phrase = NoteType(
    name="phrase",
    fields={
        "situation": f(required=True),
        "translation": f(),
        "target": f(required=True, visibility="after"),
        "explain": f(visibility="after"),
        "audio": f("audio", visibility="after"),
    },
    cards={
        "say": CardTemplate(
            ask=("situation", "translation"),
            expect="target",
            grader="self",
            # Flashcard only: `self` reads a rating out of the payload, and a
            # word bank submits assembled text, which it would score as AGAIN
            # every time. Declared here since this type was written and
            # unreachable behind `flashcard`, so nobody ever met it -- the
            # registry's own checks are what finally said so.
            forms=("flashcard",),
        ),
    },
)
