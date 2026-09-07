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

from collections.abc import Callable
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
#: expect", which no heuristic beats. `morphology` is a candidate sharing a stem
#: with the answer -- for a Romance language that is the conjugation paradigm
#: (`saio` against `sai`, `saem`, `saímos`), which is the hardest kind of option
#: to eliminate without knowing the grammar. `same_unit` is everything else from
#: the lesson at hand.
SOURCES = ("curated", "morphology", "same_unit")

#: Shared leading characters at which a candidate counts as morphological.
MIN_STEM = 2

#: ...but only for an answer long enough to have a stem at all. Two characters of
#: `sa|io` and `sa|em` is a real paradigm; one character of `o` and `ontem` is a
#: coincidence, and counting it ranks `ontem`, `outubro` and `onze` above `as` as
#: options for an article. A length floor separates the two; a bare count cannot.
MIN_STEMMABLE = 4

#: Beyond this, more shared prefix says nothing extra -- and without a cap a
#: near-duplicate of the answer would outrank every genuine alternative.
STEM_CAP = 8

#: Zipf-scale distance treated as "about as common". Options far rarer than the
#: answer are eliminable on feel alone, which is the failure this whole module
#: exists to avoid.
FREQ_BUCKET = 1.0

_OPTS = GradingOptions()


def _shared_stem(a: str, b: str) -> int:
    """
    Leading characters two answers share, or 0 if too few to mean anything.

    Both thresholds are load-bearing. A short answer has no stem to share: `o`
    and `ontem` agree on one letter by coincidence, and honouring it ranks
    `ontem`, `outubro` and `onze` above `as` as options for an article -- the
    opposite of what the signal is for. A long answer needs only two: `sa|io`
    against `sa|em` is a real conjugation paradigm.

    Below either threshold the ordering falls back to commonness and length,
    which is what actually helps for short words.
    """
    if len(a) < MIN_STEMMABLE:
        return 0
    n = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        n += 1
    return min(n, STEM_CAP) if n >= MIN_STEM else 0


def _frequency_reader(lang: str | None) -> Callable[[str], float] | None:
    """
    A zipf-frequency function for `lang`, or None if unavailable.

    `wordfreq` is an optional extra: it is 63MB and this works without it. Its
    absence changes the ORDER of options within a tier, never which options are
    eligible or what they are labelled -- so a course built with it and one built
    without differ in polish, not in correctness.
    """
    if not lang:
        return None
    try:
        from wordfreq import zipf_frequency
    except ImportError:
        return None

    def read(word: str) -> float:
        try:
            return float(zipf_frequency(word, lang))
        except (LookupError, ValueError):
            return 0.0

    return read


@dataclass(frozen=True, slots=True)
class Distractor:
    card_id: str
    text: str
    source: str
    rank: int


def _accepted(note: Note, expect: str) -> set[str]:
    return {normalize(a, _OPTS) for a in note.answers(expect)}


def _candidates(
    card: Card,
    note: Note,
    notetype: NoteType,
    pool: list[tuple[Note, str]],
    frequency: Callable[[str], float] | None = None,
) -> list[tuple[str, str]]:
    """
    Other notes' answers to the same question, best first, each labelled.

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
    answer = normalize(target[0], _OPTS)
    want = frequency(answer.split()[0]) if frequency and answer else 0.0

    seen: set[str] = set()
    scored: list[tuple[int, int, int, int, str]] = []
    for other, unit in pool:
        if other.id == note.id:
            continue
        for value in other.answers(expect):
            key = normalize(value, _OPTS)
            if not key or key in banned or key in seen:
                continue
            seen.add(key)
            gap = 0
            if frequency and want:
                gap = int(abs(frequency(key.split()[0]) - want) / FREQ_BUCKET)
            # In the order it matters: the lesson at hand, then a shared stem
            # (which for a Romance language is the conjugation paradigm), then
            # comparable commonness, then similar length. Text last, purely so
            # the result never depends on dict ordering.
            scored.append(
                (
                    0 if unit == note.unit else 1,
                    -_shared_stem(answer, key),
                    gap,
                    abs(len(value) - width),
                    value,
                )
            )
    scored.sort()
    return [("morphology" if -stem else "same_unit", value) for _, stem, _, _, value in scored]


def build(
    cards: list[Card],
    notes: list[Note],
    notetypes: dict[str, NoteType],
    *,
    lang: str | None = None,
) -> list[Distractor]:
    """
    Every card's distractors, in the order they should be offered.

    `lang` is the target language of the course, used only to look up how common
    a word is. The engine holds no language knowledge of its own: it asks for the
    course's code and passes it through.
    """
    frequency = _frequency_reader(lang)
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
        for source, value in _candidates(card, note, notetype, pool, frequency):
            if len(chosen) >= KEEP:
                break
            key = normalize(value, _OPTS)
            if key not in taken:
                taken.add(key)
                chosen.append((source, value))

        out.extend(
            Distractor(card_id=card.id, text=value, source=source, rank=i)
            for i, (source, value) in enumerate(chosen[:KEEP])
        )
    return out
