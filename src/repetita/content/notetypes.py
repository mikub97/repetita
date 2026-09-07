"""
Built-in note types.

A note type declares two things: which fields a note may carry, and which cards
those fields generate. Everything language-specific stays out -- these describe
*shapes of knowledge*, not Portuguese.

`visibility` is the anti-leak contract (see CLAUDE.md in this package): a field
marked "before" is shown while the question is open, so it must never contain the
answer. Adding a field means choosing one; there is no third option.

`requires` on a card template means the card is simply not generated when the
field is absent. That is how optional content degrades instead of erroring: a
note with no audio yields no listening card, and gains one the day audio is built
for it, with no edit to the content.
"""

from __future__ import annotations

from .models import CardTemplate, FieldSpec, NoteType


def _f(type_: str = "text", *, required: bool = False, visibility: str = "before") -> FieldSpec:
    return FieldSpec(type=type_, required=required, visibility=visibility)  # type: ignore[arg-type]


_AFTER = {"visibility": "after"}

BUILTIN: dict[str, NoteType] = {
    # One word missing from a sentence in the target language -- the core drill.
    "gap": NoteType(
        name="gap",
        fields={
            "prompt": _f(required=True),
            "answers": _f("text_list", required=True, visibility="after"),
            "cue": _f(),
            "hint": _f(),
            "translation": _f(),
            "options": _f("text_list", visibility="after"),
            "distractors": _f("text_list", visibility="after"),
            "explain": _f(visibility="after"),
            "audio": _f("audio", visibility="after"),
        },
        cards={
            "fill": CardTemplate(
                ask=("prompt", "cue"),
                expect="answers",
                grader="typed",
                forms=("choice", "typein"),
            ),
        },
    ),
    # A whole sentence produced from an L1 prompt.
    "sentence": NoteType(
        name="sentence",
        fields={
            "prompt": _f(required=True),
            "answers": _f("text_list", required=True, visibility="after"),
            "translation": _f(),
            "explain": _f(visibility="after"),
            "audio": _f("audio", visibility="after"),
        },
        cards={
            "produce": CardTemplate(
                ask=("prompt",),
                expect="answers",
                grader="sentence",
                forms=("wordbank", "typein"),
            ),
        },
    ),
    # A sentence plus an instruction saying what to change about it.
    "transform": NoteType(
        name="transform",
        fields={
            "prompt": _f(required=True),
            "instruction": _f(required=True),
            "answers": _f("text_list", required=True, visibility="after"),
            "translation": _f(),
            "explain": _f(visibility="after"),
            "audio": _f("audio", visibility="after"),
        },
        cards={
            "apply": CardTemplate(
                ask=("prompt", "instruction"),
                expect="answers",
                grader="sentence",
                forms=("wordbank", "typein"),
            ),
        },
    ),
    # Something to say out loud, judged by the learner. The one place where
    # self-assessment is honest, because nothing else can hear it yet.
    "phrase": NoteType(
        name="phrase",
        fields={
            "situation": _f(required=True),
            "translation": _f(),
            "target": _f(required=True, visibility="after"),
            "explain": _f(visibility="after"),
            "audio": _f("audio", visibility="after"),
        },
        cards={
            "say": CardTemplate(
                ask=("situation", "translation"),
                expect="target",
                grader="self",
                forms=("flashcard", "wordbank"),
            ),
        },
    ),
    # A word, tested in as many directions as the note supports. This is where
    # the note->card multiplication earns its keep: one entry, three cards.
    "vocab": NoteType(
        name="vocab",
        fields={
            "l2": _f(required=True, visibility="after"),
            "l1": _f(required=True),
            "pos": _f(),
            "example_l2": _f(visibility="after"),
            "example_l1": _f(),
            "distractors": _f("text_list", visibility="after"),
            "explain": _f(visibility="after"),
            "audio": _f("audio", visibility="after"),
        },
        cards={
            "recognize": CardTemplate(
                ask=("l2",),
                expect="l1",
                grader="typed",
                forms=("choice", "typein", "flashcard"),
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
    ),
    # Show a picture, name it in the target language.
    "picture": NoteType(
        name="picture",
        fields={
            "image": _f("image", required=True),
            "l2": _f(required=True, visibility="after"),
            "l1": _f(),
            "distractors": _f("text_list", visibility="after"),
            "explain": _f(visibility="after"),
            "audio": _f("audio", visibility="after"),
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
    ),
}
