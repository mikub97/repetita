"""
The exercise types, and the registry they live in.

A note type declares two things: which fields a note may carry, and which cards
those fields generate. Everything language-specific stays out -- these describe
*shapes of knowledge*, not Portuguese.

One module per type, and a registry in the shape the rest of the package already
uses (`graders/`, `srs/`, `presenters/`). That matters beyond tidiness: the
project's promise is that *"a new scheduler, grader, presenter or session policy
is a new file plus a registry entry"*, and the exercise type was the one
extension point that was not like that -- six of them in a single dict that only
Python could change. Now a course can declare one (`notetypes.yaml`) and a type
is a file.

`requires` on a card template means the card is simply not generated when the
field is absent. That is how optional content degrades instead of erroring: a
note with no audio yields no listening card, and gains one the day audio is built
for it, with no edit to the content.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from ..models import NoteType, Problem
from .gap import gap
from .phrase import phrase
from .picture import picture
from .sentence import sentence
from .transform import transform
from .vocab import vocab

_BUILTIN: dict[str, NoteType] = {
    t.name: t for t in (gap, sentence, transform, phrase, vocab, picture)
}


def get(name: str) -> NoteType:
    try:
        return _BUILTIN[name]
    except KeyError:
        raise LookupError(
            f"unknown exercise type {name!r}; available: {', '.join(sorted(_BUILTIN))}"
        ) from None


def names() -> list[str]:
    return sorted(_BUILTIN)


def builtin() -> dict[str, NoteType]:
    """A fresh copy, because a course may add to it."""
    return dict(_BUILTIN)


def declared(raw: Any, origin: str = "notetypes.yaml") -> tuple[dict[str, NoteType], list[Problem]]:
    """
    Read a course's own exercise types.

    Shaped like the built-ins: `{name: {fields: {...}, cards: {...}}}`. A course
    that declares one it gets wrong is told which part, by name -- before the
    material using it reaches anybody.
    """
    if raw is None:
        return {}, []
    if not isinstance(raw, dict):
        return {}, [
            Problem(
                origin=origin, note_id=None, kind="schema", detail="expected a mapping of types"
            )
        ]

    out: dict[str, NoteType] = {}
    problems: list[Problem] = []
    for name, body in raw.items():
        if not isinstance(body, dict):
            problems.append(
                Problem(origin=origin, note_id=None, kind="schema", detail=f"{name}: not a mapping")
            )
            continue
        try:
            nt = NoteType(name=str(name), **body)
        except ValidationError as e:
            detail = "; ".join(
                f"{'.'.join(str(x) for x in i['loc'])}: {i['msg']}" for i in e.errors()
            )
            problems.append(
                Problem(origin=origin, note_id=None, kind="schema", detail=f"{name}: {detail}")
            )
            continue
        found = problems_with(nt)
        problems.extend(
            Problem(origin=origin, note_id=None, kind=p.kind, detail=p.detail) for p in found
        )
        if not any(p.fatal for p in found):
            out[nt.name] = nt
    return out, problems


def problems_with(nt: NoteType) -> list[Problem]:
    """
    Everything wrong with a declaration, said before it can serve anything.

    None of this was checked while the six types were Python literals: a typo in
    a grader name was a `LookupError` at answer time, on a card in front of a
    learner. Once a course can write one, that stops being unreachable.
    """
    from ...core.forms import FORMS, markable
    from ...graders import names as grader_names

    def bad(detail: str) -> Problem:
        return Problem(origin=nt.name, note_id=None, kind="shape", detail=detail)

    out: list[Problem] = []
    if not nt.cards:
        out.append(bad("declares no cards, so nothing would ever be asked"))

    known = set(nt.fields)
    graders = set(grader_names())
    for template, card in nt.cards.items():
        for named in (*card.ask, *card.requires):
            if named not in known:
                out.append(bad(f"card {template!r} names a field {named!r} the type does not have"))
        if card.expect not in known:
            out.append(bad(f"card {template!r} expects {card.expect!r}, which is not a field"))
        # And deliberately *not* "the expect field must not be visible before".
        # That check was written here first and the built-ins refused it: in
        # `vocab`, `l1` is the prompt for `produce` and the answer for
        # `recognize`, which is the ordinary shape of a two-way vocabulary card.
        # `NoteType.visible_before` already excludes a card's own `expect`
        # unconditionally, so the field-level setting is a default and the card
        # always wins. A second opinion here would have forbidden the most common
        # exercise shape there is.
        if card.grader not in graders:
            known_graders = ", ".join(sorted(graders))
            out.append(
                bad(f"card {template!r} names grader {card.grader!r}; there is: {known_graders}")
            )
        else:
            can = markable(card.grader)
            for form in card.forms:
                if form not in FORMS:
                    out.append(bad(f"card {template!r}: there is no {form!r} exercise"))
                elif form not in can:
                    out.append(
                        bad(
                            f"card {template!r}: the {card.grader!r} grader cannot judge a {form!r}"
                        )
                    )
        if not card.forms:
            out.append(bad(f"card {template!r} declares no forms, so nothing could draw it"))
    return out


__all__ = ["builtin", "declared", "get", "names", "problems_with"]
