"""
A whole sentence produced from an L1 prompt.
"""

from __future__ import annotations

from ..models import CardTemplate, NoteType
from ._spec import field as f

sentence = NoteType(
    name="sentence",
    fields={
        "prompt": f(required=True),
        "answers": f("text_list", required=True, visibility="after"),
        "translation": f(),
        "explain": f(visibility="after"),
        "audio": f("audio", visibility="after"),
    },
    cards={
        "produce": CardTemplate(
            ask=("prompt",),
            expect="answers",
            grader="sentence",
            forms=("wordbank", "typein"),
        ),
    },
)
