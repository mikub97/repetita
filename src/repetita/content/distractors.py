"""
Choosing the wrong answers.

This looks like a presentation detail and is not. A multiple choice whose other
options are obviously wrong is answered by elimination, so it is answered
correctly whatever the learner knows. That inflates measured accuracy, and
accuracy is what opens the new-material gate -- so weak distractors make the app
introduce material faster than anyone can absorb it. The scheduler is fed by
this file.

Generation is deterministic: the same content must produce the same options.
Anything random here would churn the database on every rebuild and make a
content diff unreadable. Shuffling happens when the card is served.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.protocols import GradingOptions
from ..graders.text import normalize
from .models import Card, Note, NoteType

#: How many to keep per card. More than a question needs, so that serving can
#: drop any that later collide with a changed answer without falling short.
KEEP = 6

#: Below this a multiple choice is not worth offering: two options is a coin
#: toss, and a coin toss reads as knowledge to the scheduler.
MIN_OPTIONS = 3

#: Sources, best first. `curated` is the author saying "this is the mistake I
#: expect", which no heuristic beats.
SOURCES = ("curated", "same_unit")

_OPTS = GradingOptions()


@dataclass(frozen=True, slots=True)
class Distractor:
    card_id: str
    text: str
    source: str
    rank: int


def _accepted(note: Note, expect: str) -> set[str]:
    return {normalize(a, _OPTS) for a in note.answers(expect)}


def _candidates(
    card: Card, note: Note, notetype: NoteType, pool: list[tuple[Note, str]]
) -> list[str]:
    """
    Other notes' answers to the same question, nearest in length first.

    Same field of the same note type, so the options are the same kind of thing:
    articles against articles, verb forms against verb forms. Drawn from the same
    unit before the wider course, because options from the lesson at hand read as
    a real question while options from anywhere read as noise and can be
    eliminated without knowing anything.
    """
    expect = notetype.cards[card.template].expect
    target = note.answers(expect)
    if not target:
        return []
    banned = _accepted(note, expect)
    width = len(target[0])

    seen: set[str] = set()
    scored: list[tuple[int, int, str]] = []
    for other, unit in pool:
        if other.id == note.id:
            continue
        for value in other.answers(expect):
            key = normalize(value, _OPTS)
            if not key or key in banned or key in seen:
                continue
            seen.add(key)
            # Same unit first, then closest in length, then alphabetical so the
            # result does not depend on dict ordering.
            scored.append((0 if unit == note.unit else 1, abs(len(value) - width), value))
    scored.sort()
    return [value for _, _, value in scored]


def build(cards: list[Card], notes: list[Note], notetypes: dict[str, NoteType]) -> list[Distractor]:
    """Every card's distractors, in the order they should be offered."""
    by_id = {n.id: n for n in notes}
    out: list[Distractor] = []

    for card in cards:
        note = by_id.get(card.note_id)
        notetype = notetypes.get(card.notetype)
        if note is None or notetype is None:
            continue
        if "choice" not in notetype.cards[card.template].forms:
            continue

        expect = notetype.cards[card.template].expect
        banned = _accepted(note, expect)
        chosen: list[tuple[str, str]] = []
        taken: set[str] = set()

        for value in note.answers("distractors"):
            key = normalize(value, _OPTS)
            if key and key not in banned and key not in taken:
                taken.add(key)
                chosen.append(("curated", value))

        pool = [(n, n.unit) for n in notes if n.notetype == note.notetype]
        for value in _candidates(card, note, notetype, pool):
            if len(chosen) >= KEEP:
                break
            key = normalize(value, _OPTS)
            if key not in taken:
                taken.add(key)
                chosen.append(("same_unit", value))

        out.extend(
            Distractor(card_id=card.id, text=value, source=source, rank=i)
            for i, (source, value) in enumerate(chosen[:KEEP])
        )
    return out
