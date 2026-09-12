"""
The one place a card becomes JSON.

Invariant 2 of ARCHITECTURE.md lives here: while a question is open its answer is
not in the payload -- not hidden by CSS, not filtered in the browser, absent.
That rule cannot be true in one view and false in another, so there is exactly
one function that serialises an open question, and every view calls it.

Which fields are safe is not decided here either. `NoteType.visible_before`
already composes it from both directions -- a card's `ask` fields are the
question, its `expect` field is the answer -- and a second filter written beside
it is precisely how the two would come to disagree.
"""

from __future__ import annotations

import random
from typing import Any

from .. import presenters
from ..content.models import Card, Note, NoteType
from ..core.forms import FORMS
from ..core.protocols import PresentationContext
from ..store.cards import CardState

#: Forms this build can render -- a capability list, and deliberately a separate
#: thing from `core.forms.FORMS`, which is the vocabulary. `choice` was for a
#: long time not among them: a multiple choice has to put the answer on the
#: screen beside its distractors, and serving it needed its own decision about
#: what "open question" means for a selection, plus precomputed distractors.
#:
#: `GRADER_FORMS` is re-exported here because this module is where forms are
#: chosen; it is defined in `core` so that the write path, which must refuse a
#: form no grader can mark, does not have to import `web`. It is deliberately
#: **not** consulted by `renderable_forms` yet -- that would change what is
#: served for `phrase`, whose declared `wordbank` has exactly this problem and
#: has been unreachable behind `flashcard` since it was written. See issue #57.
SUPPORTED_FORMS: tuple[str, ...] = FORMS

#: A multiple choice needs enough wrong answers to be a question rather than a
#: coin toss -- and a coin toss reads as knowledge to the scheduler, which then
#: opens the new-material gate on it.
MIN_CHOICE_OPTIONS = 3

#: A one-word word bank is the answer with extra steps.
MIN_WORDBANK_TOKENS = 2

#: Askable by anything. Reached only by a card whose every declared form this
#: build cannot render, which is a content problem, not a reason to serve nothing.
FALLBACK_FORM = "typein"


def answer_tokens(note: Note, expect: str) -> list[str]:
    """The first accepted answer, split into the pieces a word bank offers."""
    accepted = note.answers(expect)
    return accepted[0].split() if accepted else []


def renderable_forms(
    card: Card, note: Note, notetype: NoteType, *, distractors: list[str] | None = None
) -> tuple[str, ...]:
    """
    The card's declared forms that this build can actually put on a screen.

    A capability filter and nothing else: it answers "can this be rendered at
    all", never "how hard should it be right now". That second question belongs
    to `presenters/`, which chooses from what this returns.

    `choice` is capability-gated on having real distractors. Padding a short list
    with filler to make the form appear would be worse than not offering it: the
    options would be eliminable without knowing anything, so the card would be
    answered correctly regardless, and the scheduler would read that as knowledge.
    """
    tokens = len(answer_tokens(note, notetype.cards[card.template].expect))
    available = len(distractors or [])

    def renderable(form: str) -> bool:
        if form not in SUPPORTED_FORMS:
            return False
        if form == "wordbank":
            return tokens >= MIN_WORDBANK_TOKENS
        if form == "choice":
            return available >= MIN_CHOICE_OPTIONS - 1
        return True

    return tuple(form for form in card.forms if renderable(form))


def choose_form(
    card: Card, note: Note, notetype: NoteType, *, distractors: list[str] | None = None
) -> str:
    """
    The form this card would be asked in with no presenter in the way.

    Declaration order in the note type is the author's preference, honoured as
    far as this build can.
    """
    forms = renderable_forms(card, note, notetype, distractors=distractors)
    return forms[0] if forms else FALLBACK_FORM


def served_form(
    card: Card,
    note: Note,
    notetype: NoteType,
    *,
    state: CardState | None = None,
    distractors: list[str] | None = None,
) -> str:
    """
    The form this card is actually asked in right now.

    Capability first, then policy: the presenter chooses among the forms that can
    be rendered, so it can never ask for one nothing can draw. `state` is the
    card's progress *before* the answer being served or recorded -- a card is
    presented according to what was known when the question was put, and the
    review log records the form the learner actually saw.
    """
    context = PresentationContext(
        seen=state.seen if state else 0,
        lapses=state.lapses if state else 0,
        available_forms=renderable_forms(card, note, notetype, distractors=distractors),
        answer_tokens=len(answer_tokens(note, notetype.cards[card.template].expect)),
    )
    return presenters.get().choose(choose_form(card, note, notetype), context)


def shuffled(items: list[str], rng: random.Random) -> list[str]:
    """
    Reorder, and guarantee the order actually changed.

    A word bank handed back in the right order is not an exercise, and on a
    two-word sentence a fair shuffle produces one half the time.
    """
    out = list(items)
    if len(out) < 2:
        return out
    rng.shuffle(out)
    if out == items:
        out.append(out.pop(0))
    return out


def public_card(
    card: Card,
    note: Note,
    notetype: NoteType,
    *,
    handle: str,
    rng: random.Random | None = None,
    state: CardState | None = None,
    distractors: list[str] | None = None,
) -> dict[str, Any]:
    """
    A card with its question open. **This payload never contains its answer.**

    The word bank ships shuffled tokens and never the assembled sentence:
    reassembling it is the entire exercise, and sending it would put the answer
    in the DOM by another route.

    `handle` is an opaque token, not the card id, and neither the card id nor the
    note id appears here. Ids are authored from the material -- `obrigado#produce`
    carries its own answer -- and no field filter can help, because an id is not
    a field. See `handles.py`.
    """
    template = notetype.cards[card.template]
    form = served_form(card, note, notetype, state=state, distractors=distractors)
    payload: dict[str, Any] = {
        "id": handle,
        "notetype": card.notetype,
        "template": card.template,
        "form": form,
        "ask": [name for name in template.ask if note.fields.get(name)],
        "fields": {
            name: note.fields[name]
            for name in notetype.visible_before(card.template)
            if note.fields.get(name)
        },
    }
    if form == "choice":
        # The one form that must put the answer on screen. It is not a leak: a
        # multiple choice IS the answer among others, and the learner still has
        # to know which. Shuffled, so position carries nothing, and the options
        # come from the precomputed table rather than being invented here.
        accepted = note.answers(template.expect)
        options = [accepted[0], *(distractors or [])[: MIN_CHOICE_OPTIONS - 1]]
        payload["options"] = shuffled(options, rng or random.Random())
    elif form == "wordbank":
        payload["tokens"] = shuffled(answer_tokens(note, template.expect), rng or random.Random())
    return payload


def revealed(note: Note, notetype: NoteType, template: str) -> dict[str, Any]:
    """
    Everything withheld while the question was open, for the verdict screen.

    The exact complement of `visible_before`, rather than a list of its own: two
    hand-maintained lists are how a field ends up in neither, or in both.
    """
    before = set(notetype.visible_before(template))
    return {name: value for name, value in note.fields.items() if name not in before and value}


#: A flag per language, for the course picker.
#:
#: In `web/` and not in `content/` on purpose. CLAUDE.md rule 4 keeps
#: language-specific *behaviour* out of the engine -- how a word is graded, what
#: counts as an accent, which forms a verb has. This is none of those: it is a
#: picture next to a name, and nothing downstream reads it. A course that wants
#: a different one says so in `course.yaml` the day that matters; until then a
#: learner who studies Brazilian Portuguese should see the Brazilian flag rather
#: than a language code.
#:
#: Keyed by variant first, so pt-BR and pt-PT do not share one.
FLAGS = {
    "pt-BR": "🇧🇷",
    "pt-PT": "🇵🇹",
    "pt": "🇧🇷",
    "it": "🇮🇹",
    "es": "🇪🇸",
    "en": "🇬🇧",
    "fr": "🇫🇷",
    "de": "🇩🇪",
    "pl": "🇵🇱",
}


def flag_for(code: str, variant: str | None = None) -> str:
    """The flag for a language, or a globe when nothing is known about it."""
    if variant and (found := FLAGS.get(variant)):
        return found
    return FLAGS.get(code, "🌍")
