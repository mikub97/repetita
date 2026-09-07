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
from ..core.protocols import PresentationContext
from ..store.cards import CardState

#: Forms this build can render. `choice` is deliberately not among them: a
#: multiple choice has to put the answer on the screen beside its distractors,
#: which is the one form the invariant above cannot hold for. Serving it needs
#: its own decision about what "open question" means for a selection, plus
#: precomputed distractors -- both out of scope here, and neither is a thing to
#: settle by quietly shipping the answer in the meantime.
SUPPORTED_FORMS: tuple[str, ...] = ("typein", "wordbank", "flashcard")

#: A one-word word bank is the answer with extra steps.
MIN_WORDBANK_TOKENS = 2

#: Askable by anything. Reached only by a card whose every declared form this
#: build cannot render, which is a content problem, not a reason to serve nothing.
FALLBACK_FORM = "typein"


def answer_tokens(note: Note, expect: str) -> list[str]:
    """The first accepted answer, split into the pieces a word bank offers."""
    accepted = note.answers(expect)
    return accepted[0].split() if accepted else []


def renderable_forms(card: Card, note: Note, notetype: NoteType) -> tuple[str, ...]:
    """
    The card's declared forms that this build can actually put on a screen.

    A capability filter and nothing else: it answers "can this be rendered at
    all", never "how hard should it be right now". That second question belongs
    to `presenters/`, which chooses from what this returns.
    """
    tokens = len(answer_tokens(note, notetype.cards[card.template].expect))
    return tuple(
        form
        for form in card.forms
        if form in SUPPORTED_FORMS and not (form == "wordbank" and tokens < MIN_WORDBANK_TOKENS)
    )


def choose_form(card: Card, note: Note, notetype: NoteType) -> str:
    """
    The form this card would be asked in with no presenter in the way.

    Declaration order in the note type is the author's preference, honoured as
    far as this build can.
    """
    forms = renderable_forms(card, note, notetype)
    return forms[0] if forms else FALLBACK_FORM


def served_form(
    card: Card, note: Note, notetype: NoteType, *, state: CardState | None = None
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
        available_forms=renderable_forms(card, note, notetype),
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
    form = served_form(card, note, notetype, state=state)
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
    if form == "wordbank":
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
