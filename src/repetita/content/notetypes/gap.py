"""
One word missing from a sentence in the target language -- the core drill.
"""

from __future__ import annotations

from ..models import CardTemplate, NoteType
from ._spec import field as f

gap = NoteType(
    name="gap",
    fields={
        "prompt": f(required=True),
        "answers": f("text_list", required=True, visibility="after"),
        "cue": f(),
        "hint": f(),
        "translation": f(),
        "options": f("text_list", visibility="after"),
        "distractors": f("text_list", visibility="after"),
        "explain": f(visibility="after"),
        "audio": f("audio", visibility="after"),
    },
    cards={
        "fill": CardTemplate(
            ask=("prompt", "cue"),
            expect="answers",
            grader="typed",
            forms=("choice", "typein"),
        ),
    },
)
