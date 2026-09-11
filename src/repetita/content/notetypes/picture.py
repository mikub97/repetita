"""
Show a picture, name it in the target language.
"""

from __future__ import annotations

from ..models import CardTemplate, NoteType
from ._spec import field as f

picture = NoteType(
    name="picture",
    fields={
        "image": f("image", required=True),
        "l2": f(required=True, visibility="after"),
        "l1": f(),
        "distractors": f("text_list", visibility="after"),
        "explain": f(visibility="after"),
        "audio": f("audio", visibility="after"),
    },
    cards={
        "name_it": CardTemplate(
            ask=("image",),
            expect="l2",
            grader="typed",
            forms=("typein", "choice"),
            requires=("image",),
        ),
    },
)
