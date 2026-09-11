"""
A short name for an exercise.

Three screens need to refer to one exercise in a few characters -- the board,
the confirm drawer, the plan preview -- and until this existed they all fell
back to the note id, which says nothing: `gram-atras-passado-ainda.tempo-01`.

**The rule was chosen by measurement, and the obvious ordering lost.** Reaching
for the cue head first looks right, because it is already parsed as the family
key -- `morar` for `"morar -- imperfeito, eu"`. Measured across 757 real notes it
is twice as bad on both axes that matter:

    cue head first     median 10 chars, max 72, 39% repeat within a set
    answer first       median  6 chars, max 20, 20% repeat within a set

which is not surprising once said out loud: a family key is *designed* to be
shared. Six forms of `treinar` have one. It names a family, not an exercise.

So the answer comes first. For `gap` notes -- 639 of the 757 -- the answer
already is a name: `atras`, `morava`, `cafe`, a median of five characters, with
only two of them over the cap. For the sentence-shaped types the answer is
truncated, which at least says *which* sentence.

Nothing here knows any language. It reads whichever field the note type names as
its answer, and counts characters.
"""

from __future__ import annotations

from .facets import family_of
from .models import Facets, Note, NoteType

#: Long enough for `depois de amanha` (16) and `ao lado direito` (15), short
#: enough that a column of them still scans as a list of names. Two of 639 gap
#: answers exceed it.
MAX = 20

#: Where a truncation lands mid-word, back up to the last space rather than
#: cutting a word in half -- unless that would leave almost nothing.
_MIN_AFTER_TRIM = 8


def shorten(text: str, limit: int = MAX) -> str:
    """Trim to `limit`, at a word boundary where one is close enough."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    if space >= _MIN_AFTER_TRIM:
        cut = cut[:space]
    return cut.rstrip(" ,.;:") + "…"


def derive(note: Note, nt: NoteType | None, facets: Facets | None = None) -> str:
    """
    The short name for this exercise.

    The answer, then the family key, then the id. The last is a floor rather
    than a choice: a note whose type the course no longer declares has no answer
    to read, and a row with no name at all is worse than a row named awkwardly.

    Where a note type declares several cards there are several answers, and
    which one names the note is genuinely arbitrary -- so this takes the first
    declared, which is at least stable across runs and machines. Every note type
    in the course this was built for has exactly one card; where the pick reads
    badly, that is what typing a name in the inspector is for.
    """
    if nt is not None and nt.cards:
        template = next(iter(nt.cards.values()))
        answers = note.answers(template.expect)
        if answers and answers[0].strip():
            return shorten(answers[0].strip())

    if facets is not None:
        family = family_of(note, facets)
        if family and family[0]:
            return shorten(family[0])

    # The part after the unit prefix: `morar-eu` rather than the whole id.
    _, _, suffix = note.id.partition(".")
    return shorten(suffix or note.id)
