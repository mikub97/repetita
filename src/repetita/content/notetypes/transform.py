"""
A sentence plus an instruction saying what to change about it.
"""

from __future__ import annotations

from ..models import CardTemplate, NoteType
from ._spec import field as f

transform = NoteType(
    name="transform",
    fields={
        "prompt": f(required=True),
        "instruction": f(required=True),
        "answers": f("text_list", required=True, visibility="after"),
        "translation": f(),
        "explain": f(visibility="after"),
        "audio": f("audio", visibility="after"),
    },
    cards={
        "apply": CardTemplate(
            ask=("prompt", "instruction"),
            expect="answers",
            grader="sentence",
            forms=("wordbank", "typein"),
        ),
    },
)
